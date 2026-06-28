"""
Scheduling engine v3 — with Material (semi-finished) planning.

Material planning flow:
 1. After SKU plans are placed, collect all process steps that have
    requires_material_code set.
 2. For each material, gather all demanding SO-LineItems and their
    material quantities (sku.uom * plan_qty).
 3. Group demands whose due dates are within material_due_merge_days
    of each other → single merged material plan.
 4. Plan the material backward from (earliest due_date_in_group
    - material.post_lead_days), using the material's own process routing.
 5. Material plans are stored with entity_type='MATERIAL' and
    material_group_id linking to material_demand_group rows.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from data.repositories import (
    SKURepo, MaterialRepo, ProcessRoutingRepo, RoomRepo,
    ShiftRepo, CalendarRepo, SORepo, PlanRepo,
    ActualRepo, LotSampleRepo, ConfigRepo, MaterialDemandRepo, AllocationRepo, _now
)
from data.crp_excel import crp_manager
from utils.workdays import sub_workdays


# ─── helpers ────────────────────────────────────────────────────────────────

def _date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def _ds(d: date) -> str:
    return d.strftime("%Y-%m-%d")

def _shift_hours(shift: Dict) -> float:
    fmt = "%H:%M"
    t0 = datetime.strptime(shift["start_time"], fmt)
    t1 = datetime.strptime(shift["end_time"], fmt)
    h  = (t1 - t0).total_seconds() / 3600
    return h if h > 0 else h + 24

def calc_uph(room_proc: Dict, hc: int) -> float:
    if room_proc["process_type"] == "AUTO":
        return float(room_proc.get("uph_fixed") or 0)
    hc_c = max(room_proc.get("hc_min") or 1,
               min(room_proc.get("hc_max") or hc, hc))
    return float(room_proc.get("upph") or 0) * hc_c

def shift_capacity_inner(room_proc: Dict, shift: Dict, hc: int) -> float:
    return calc_uph(room_proc, hc) * _shift_hours(shift)

def inner_to_sku(inner: float, uom: int) -> int:
    return int(inner // max(uom, 1))

def sku_to_inner(qty: int, uom: int) -> int:
    return qty * max(uom, 1)

SlotKey = Tuple[str, str, str, int]   # (date_str, room_code, process_name, shift_no)


# ─── Scheduler ───────────────────────────────────────────────────────────────

class Scheduler:

    def __init__(self):
        self.shifts:     List[Dict] = []
        self.rooms:      List[str]  = []
        self.room_procs: Dict[str, List[Dict]] = {}
        self.sku_map:    Dict[str, Dict] = {}
        self.mat_map:    Dict[str, Dict] = {}
        self.max_pull_days:       int = 45
        self.assign_mode:         str = "CAPACITY"
        self.mat_due_merge_days:  int = 21
        self._hc_dist_cache: Dict[Tuple[str, int], Dict[Tuple[str,str], int]] = {}
        self._last_urgency:  Dict[str, float] = {}
        # (room_code, process_name) → changeover_shifts
        self._changeover_shifts: Dict[Tuple[str, str], int] = {}
        # Tracks which entity_code is placed in each (room, proc, date, shift) slot
        self._placed_sku: Dict[Tuple[str, str, str, int], str] = {}
        self._reload_masters()

    def _reload_masters(self):
        self._hc_dist_cache = {}   # CRP data may have changed
        self.shifts     = ShiftRepo.all()

        # Load all room rows in one query (was N+1: rooms() then processes_for_room() per room)
        _all_room_rows  = RoomRepo.all()
        _seen: set      = set()
        self.rooms      = [r["room_code"] for r in _all_room_rows
                           if r["room_code"] not in _seen and not _seen.add(r["room_code"])]
        self.room_procs = {}
        for r in _all_room_rows:
            self.room_procs.setdefault(r["room_code"], []).append(dict(r))

        # Pre-build process→rooms lookup used inside the planning loop (avoids per-step DB queries)
        self._proc_room_map: Dict[str, List[Dict]] = {}
        for r in _all_room_rows:
            self._proc_room_map.setdefault(r["process_name"], []).append(dict(r))

        self.sku_map    = {s["sku_code"]: s for s in SKURepo.all()}
        self.mat_map    = {m["material_code"]: m for m in MaterialRepo.all()}
        try:
            self.max_pull_days = int(ConfigRepo.get("max_pull_days", "45"))
        except ValueError:
            self.max_pull_days = 45
        self.assign_mode = ConfigRepo.get("room_assign_mode", "CAPACITY").upper()
        try:
            self.mat_due_merge_days = int(
                ConfigRepo.get("material_due_merge_days", "21"))
        except ValueError:
            self.mat_due_merge_days = 21
        self._changeover_shifts = {
            (room, rp["process_name"]): int(rp.get("changeover_shifts") or 0)
            for room in self.rooms
            for rp in self.room_procs[room]
        }
        # Room type exclusivity: how many routing steps allow each room type.
        # Lower count = more exclusive = should be tried first by default.
        excl: Dict[str, int] = {}
        for r in ProcessRoutingRepo.all():
            for rt in (r.get("allowed_room_types") or "").split(","):
                rt = rt.strip()
                if rt:
                    excl[rt] = excl.get(rt, 0) + 1
        self._room_type_exclusivity: Dict[str, int] = excl

    def _rooms_for_process(self, process_name: str,
                           allowed_types: List[str]) -> List[Dict]:
        """In-memory lookup replacing per-step RoomRepo.rooms_for_process() DB calls."""
        rows = self._proc_room_map.get(process_name, [])
        return [r for r in rows if r["room_type"] in allowed_types] if allowed_types else rows

    # ── Public API ───────────────────────────────────────────────────────────

    def auto_plan(self, date_from: str, date_to: str,
                  progress_cb=None) -> Dict:
        crp_manager.refresh()   # always reload Excel before planning so stale in-memory HC is never used
        self._reload_masters()

        # Frozen zone: plans within frozen_days of today are never touched
        frozen_days = int(ConfigRepo.get("frozen_days", "0"))
        if frozen_days > 0:
            frozen_cutoff = (date.today() + timedelta(days=frozen_days)).isoformat()
            effective_from = max(date_from, frozen_cutoff)
        else:
            effective_from = date_from

        PlanRepo.delete_unlocked(effective_from, date_to)
        open_sos = self._sorted_open_sos()
        slot_map = self._build_slot_map(effective_from, date_to)
        report   = {"planned": 0, "skipped": 0, "late": [],
                    "routing_errors": [], "material_plans": 0}

        # M2: Warn about MANUAL rooms with UPPH=0 — they produce zero capacity silently
        for room_code, rps in self.room_procs.items():
            for rp in rps:
                if rp["process_type"] == "MANUAL" and not float(rp.get("upph") or 0):
                    report["routing_errors"].append({
                        "so": None,
                        "reason": f"Config: {room_code}/{rp['process_name']} UPPH=0 — no capacity generated"
                    })

        # 1. Campaign grouping: merge same-SKU SOs within max_consolidation_days
        #    into virtual combined SOs (earliest due date is the cutoff).
        #    SKUs with campaign_mode=0 are planned individually.
        campaign_sos = self._apply_campaign_grouping(open_sos)
        total = len(campaign_sos)

        # Pre-compute production_needed for all SOs so we can pass
        # campaign_extra_rem (future same-SKU demand) into _plan_so().
        from data.repositories import AllocationRepo as _AR
        _needed: Dict[Tuple[str, str, str], int] = {
            (s["so_number"], s["sku_code"], s["line_item"]):
                _AR.production_needed(s["so_number"], s["sku_code"], s["line_item"])
            for s in campaign_sos
        }

        for idx, so in enumerate(campaign_sos):
            if progress_cb:
                progress_cb(idx, total, so)
            # Sum of production_needed for future same-SKU SOs in this campaign.
            # _plan_so() uses this to decide whether closing mode should engage.
            campaign_extra_rem = sum(
                _needed.get((s["so_number"], s["sku_code"], s["line_item"]), 0)
                for s in campaign_sos[idx + 1:]
                if s["sku_code"] == so["sku_code"]
            )
            self._plan_so(so, slot_map, effective_from, date_to, report,
                          campaign_extra_rem=campaign_extra_rem)

        # 2. Plan derived Material demand
        self._plan_all_materials(slot_map, effective_from, date_to, report)

        # 3. Weekly reorganize: group same-SKU plans within each ISO week
        if ConfigRepo.get("weekly_reorganize_enabled", "1") == "1":
            reorg = self.weekly_reorganize(effective_from, date_to)
            report["reorg_moved"]   = reorg["moved"]
            report["reorg_frozen"]  = reorg.get("frozen", 0)
            report["reorg_skipped"] = reorg.get("skipped_groups", 0)
        else:
            report["reorg_moved"] = 0

        # 4. Campaign pass: pull fragmented same-SKU plans into earliest available slot
        #    Pass slot_map (already built above) to skip redundant CRP re-read.
        if ConfigRepo.get("campaign_pass_enabled", "1") == "1":
            cp = self.campaign_pass(effective_from, date_to, slot_full_cap=slot_map)
            report["campaign_moved"] = cp["moved"]
        else:
            report["campaign_moved"] = 0

        return report

    def _apply_campaign_grouping(self, open_sos: List[Dict]) -> List[Dict]:
        """
        Groups same-SKU SOs whose due dates are within max_consolidation_days
        into a single virtual SO (combined qty, earliest due date, highest priority).
        SKUs with campaign_mode=0 bypass grouping.
        Returns the final ordered list of SOs to plan.
        """
        max_days = int(ConfigRepo.get("max_consolidation_days", "7") or 7)

        campaign_sos: List[Dict] = []   # SOs that will be merged
        individual_sos: List[Dict] = [] # SOs planned independently

        for so in open_sos:
            sku = self.sku_map.get(so["sku_code"], {})
            if int(sku.get("campaign_mode", 1)) == 0:
                individual_sos.append(so)
            else:
                campaign_sos.append(so)

        # Group campaign SOs by sku_code, then cluster by due date proximity
        from itertools import groupby
        sku_groups: Dict[str, List[Dict]] = {}
        for so in campaign_sos:
            sku_groups.setdefault(so["sku_code"], []).append(so)

        merged: List[Dict] = []
        for sku_code, sos in sku_groups.items():
            # Sort by due date for windowing
            sos_sorted = sorted(sos, key=lambda s: s["due_date"])
            clusters: List[List[Dict]] = []
            for so in sos_sorted:
                placed = False
                for cluster in clusters:
                    anchor_due = cluster[0]["due_date"]
                    gap = (date.fromisoformat(so["due_date"])
                           - date.fromisoformat(anchor_due)).days
                    if abs(gap) <= max_days:
                        cluster.append(so)
                        placed = True
                        break
                if not placed:
                    clusters.append([so])

            for cluster in clusters:
                # Plan each SO individually, priority first then due-date.
                # Opt-A+B adjacency bonus + SKU clustering achieves physical
                # grouping without mixing SO identifiers in plan records.
                merged.extend(sorted(cluster, key=lambda s: (
                    s["priority"] if s.get("priority") is not None else 9999,
                    s["due_date"],
                )))

        # Re-sort merged + individual by same priority key as _sorted_open_sos
        def _key(so):
            p = so.get("priority")
            return ((0, p) if p is not None else (1, 0),
                    so.get("received_at", ""))

        all_sos = sorted(merged + individual_sos, key=_key)
        # Opt-B: cluster same-SKU SOs consecutively within each priority tier
        return self._group_same_sku_within_priority(all_sos)

    def pull_forward(self, date_from: str, date_to: str) -> Dict:
        self._reload_masters()

        # Read configurable parameters
        util_thresh    = float(ConfigRepo.get("pull_forward_util_threshold", "90"))
        lookahead_days = int(ConfigRepo.get("pull_forward_lookahead_days", "14"))
        max_early_days = int(ConfigRepo.get("pull_forward_max_early_days", "30"))

        slot_map = self._build_slot_map(date_from, date_to)

        # Snapshot total capacity after locked plans (used for utilization calc)
        cap_for_util: Dict[SlotKey, float] = dict(slot_map)

        # _build_slot_map only subtracts locked plans.
        # Pull-forward works on existing unlocked plans, so we must also
        # subtract them — otherwise the slot search ignores capacity already
        # consumed by other plans and produces overcapacity moves.
        all_plans = PlanRepo.all(date_from, date_to)
        for p in all_plans:
            if p["is_locked"]:
                continue
            uom = self._entity_uom(p)
            key: SlotKey = (p["plan_date"], p["room_code"],
                            p["process_name"], p["shift_no"])
            if key in slot_map:
                slot_map[key] = max(0.0,
                    slot_map[key] - sku_to_inner(p["qty_planned"], uom))

        sku_repo_all = {s["sku_code"]: s for s in SKURepo.all()}
        so_cache: Dict[Tuple, Dict] = {}

        moved = 0
        # Process latest-to-earliest so that campaign-extension candidates
        # (same entity, same room/process, adjacent slots) are evaluated first.
        plans = sorted(all_plans,
                       key=lambda p: (p["plan_date"], p["shift_no"]),
                       reverse=True)
        for plan in plans:
            if plan["is_locked"]:
                continue

            plan_dt = _date(plan["plan_date"])

            if plan["entity_type"] == "SKU":
                so_key = (plan["so_number"], plan["sku_code"], plan["line_item"])
                if so_key not in so_cache:
                    so_cache[so_key] = SORepo.get(*so_key)
                so = so_cache[so_key]
                if not so:
                    continue
                earliest = self._earliest_start(so)

                # max_early_days: for final-seq plans, don't pull so far that
                # finished goods sit in warehouse more than max_early_days
                # before due date.
                if plan.get("is_final_seq"):
                    sku = sku_repo_all.get(plan["sku_code"], {})
                    post_lead = int(sku.get("post_lead_days") or 0)
                    due = _date(so["due_date"])
                    min_date = sub_workdays(due, post_lead) - timedelta(days=max_early_days)
                    earliest = max(earliest, min_date)
            else:
                earliest = date.today()

            new_slot = self._find_earlier_slot_pf(
                plan, slot_map, cap_for_util,
                util_thresh, lookahead_days, earliest, date_from)
            if new_slot:
                uom = self._entity_uom(plan)
                old_key = (plan["plan_date"], plan["room_code"],
                           plan["process_name"], plan["shift_no"])
                slot_map[old_key] = (slot_map.get(old_key, 0)
                                     + sku_to_inner(plan["qty_planned"], uom))
                nd, ns, nr = new_slot
                nkey = (nd, nr, plan["process_name"], ns)
                slot_map[nkey] = max(
                    0, slot_map.get(nkey, 0)
                    - sku_to_inner(plan["qty_planned"], uom))
                PlanRepo.update(plan["plan_id"],
                                {"plan_date": nd, "shift_no": ns,
                                 "room_code": nr},
                                reason="pull_forward")
                moved += 1
        return {"moved": moved}

    # ── Per-line Pull Forward ────────────────────────────────────────────────

    def _dry_run_plan_so(
        self, so: Dict, sim_slot_map: Dict[SlotKey, float],
        d0: date, d1: date,
    ) -> List[Dict]:
        """Simulate _plan_so without DB writes. sim_slot_map is mutated in-place."""
        sku_code = so["sku_code"]
        sku = self.sku_map.get(sku_code)
        if not sku:
            return []
        valid, _ = ProcessRoutingRepo.validate("SKU", sku_code)
        if not valid:
            return []
        steps = ProcessRoutingRepo.for_entity("SKU", sku_code)
        uom = int(sku.get("uom") or 1)
        from data.repositories import AllocationRepo
        prod_needed = AllocationRepo.production_needed(
            so["so_number"], sku_code, so["line_item"])
        if prod_needed <= 0:
            return []

        shift_max = max((sh["shift_no"] for sh in self.shifts), default=1)
        step_slots: Dict[int, List[Tuple]] = {}
        result: List[Dict] = []
        qty_to_plan = prod_needed

        for si in range(len(steps) - 1, -1, -1):
            step = steps[si]
            seq = step["process_seq"]
            is_final = bool(step["is_final_seq"])
            allowed = [t.strip() for t in step["allowed_room_types"].split(",") if t.strip()]
            eligible = self._rooms_for_process(step["process_name"], allowed)
            if not eligible:
                return []

            if si == len(steps) - 1:
                upper_d, upper_s = d1, shift_max
            else:
                nxt = step_slots.get(steps[si + 1]["process_seq"], [])
                if nxt:
                    fd, fs = min(nxt, key=lambda x: (x[0], x[1]))[:2]
                    min_gap_s = int(steps[si + 1].get("min_gap_shifts") or 0)
                    upper_d, upper_s = self._find_pre_cutoff(
                        fd if isinstance(fd, str) else _ds(fd), fs, min_gap_s)
                else:
                    upper_d, upper_s = d1, shift_max

            candidates = self._candidates(
                sim_slot_map, step["process_name"], eligible, d0, upper_d, upper_s,
                sku_code, step.get("room_type_priority") or "")

            step_allocated: List[Tuple] = []
            step_rem = qty_to_plan
            proc_name = step["process_name"]

            for ds, sno, room_code, rp in candidates:
                if step_rem <= 0:
                    break
                co = self._changeover_shifts.get((room_code, proc_name), 0)
                if co > 0 and self._has_changeover_conflict(
                        room_code, proc_name, ds, sno, co, sku_code):
                    continue
                key: SlotKey = (ds, room_code, proc_name, sno)
                avail_inner = sim_slot_map.get(key, 0.0)
                avail_sku = inner_to_sku(avail_inner, uom)
                if avail_sku <= 0:
                    continue
                qty_this = min(step_rem, avail_sku)
                result.append({
                    "date": ds, "shift_no": sno, "room_code": room_code,
                    "process_name": proc_name, "process_seq": seq,
                    "is_final": is_final, "qty": qty_this,
                })
                sim_slot_map[key] = max(0.0, avail_inner - sku_to_inner(qty_this, uom))
                self._block_room_shift(sim_slot_map, ds, room_code, proc_name, sno)
                # Bug #2 fix: record placement so changeover checks in subsequent
                # dry-runs (e.g. displaced SOs) see this simulated block.
                self._record_placed(room_code, proc_name, ds, sno, sku_code)
                step_allocated.append((ds, sno, room_code, rp))
                step_rem -= qty_this

            if step_rem > 0 and is_final:
                return []  # could not fully schedule final step

            step_slots[seq] = step_allocated

        return result

    def _find_earliest_completion(
        self, so_no: str, sku_code: str, line_item: str
    ) -> Optional[str]:
        """
        Find the earliest date the SO can complete given current free capacity.
        Builds slot_map once for today→today+90 and iterates day-by-day.
        Returns date string or None if not schedulable within 90 days.
        """
        self._reload_masters()
        d0 = date.today()
        horizon = _ds(d0 + timedelta(days=90))
        so = SORepo.get(so_no, sku_code, line_item)
        if not so:
            return None

        slot_map = self._build_slot_map(_ds(d0), horizon)
        # Return own plans' capacity to the pool
        for p in PlanRepo.for_so(so_no, sku_code, line_item):
            if p["is_locked"]:
                continue
            uom = self._entity_uom(p)
            key: SlotKey = (p["plan_date"], p["room_code"],
                            p["process_name"], p["shift_no"])
            slot_map[key] = slot_map.get(key, 0.0) + sku_to_inner(p["qty_planned"], uom)

        for days in range(1, 91):
            d1 = d0 + timedelta(days=days)
            sim_so = {**so, "due_date": _ds(d1)}
            sim_map = dict(slot_map)
            slots = self._dry_run_plan_so(sim_so, sim_map, d0, d1)
            if slots:
                final = [s for s in slots if s["is_final"]]
                return max(s["date"] for s in final) if final else _ds(d1)
        return None

    def simulate_single_pull_forward(
        self, so_no: str, sku_code: str, line_item: str,
        target_date: str, allow_push: bool,
    ) -> Dict:
        """
        Dry-run: backward-schedule so_no/sku/li to finish by target_date.
        allow_push=True: displaced unlocked plans are each dry-run rescheduled
        to verify they can still meet their own due dates. Push is blocked if
        any displaced SO cannot be rescheduled on time.
        Returns {feasible, final_date, displaced, error}.
        """
        self._reload_masters()
        so = SORepo.get(so_no, sku_code, line_item)
        if not so:
            return {"feasible": False, "final_date": None,
                    "displaced": [], "error": "SO not found"}

        d0 = date.today()
        d1 = _date(target_date)
        if d1 < d0:
            return {"feasible": False, "final_date": None,
                    "displaced": [], "error": "Target date is in the past"}

        # Wide horizon covers displaced SO due dates so they can be rescheduled
        # beyond target_date if needed.
        all_open = SORepo.all(status="OPEN")
        max_due = max(
            (_date(s["due_date"]) for s in all_open if s.get("due_date")),
            default=d1 + timedelta(days=60),
        )
        wide_d1   = max(d1, max_due) + timedelta(days=1)
        wide_horizon = _ds(wide_d1)

        slot_map = self._build_slot_map(_ds(d0), wide_horizon)

        # Return this SO's own unlocked plans to the pool
        own_plans = [p for p in PlanRepo.for_so(so_no, sku_code, line_item)
                     if not p["is_locked"]]
        for p in own_plans:
            uom = self._entity_uom(p)
            key: SlotKey = (p["plan_date"], p["room_code"],
                            p["process_name"], p["shift_no"])
            slot_map[key] = slot_map.get(key, 0.0) + sku_to_inner(p["qty_planned"], uom)

        # For allow_push: also return other unlocked plans to pool (can be displaced)
        other_plans: List[Dict] = []
        if allow_push:
            for p in PlanRepo.all(_ds(d0), wide_horizon):
                if p["is_locked"]:
                    continue
                if (p["so_number"] == so_no and p["sku_code"] == sku_code
                        and p["line_item"] == line_item):
                    continue
                other_plans.append(p)
                uom = self._entity_uom(p)
                key = (p["plan_date"], p["room_code"],
                       p["process_name"], p["shift_no"])
                slot_map[key] = slot_map.get(key, 0.0) + sku_to_inner(p["qty_planned"], uom)

        # Dry-run target SO up to target_date (sim_map is mutated in-place)
        sim_so = {**so, "due_date": target_date}
        sim_map = dict(slot_map)
        slots = self._dry_run_plan_so(sim_so, sim_map, d0, d1)

        if not slots:
            return {"feasible": False, "final_date": None,
                    "displaced": [],
                    "error": "Cannot fit in available capacity before target date"}

        final_slots = [s for s in slots if s["is_final"]]
        final_date = max(s["date"] for s in final_slots) if final_slots else None

        # Identify and validate displaced plans
        displaced: List[Dict] = []
        if allow_push and other_plans:
            used_keys = {(s["date"], s["room_code"], s["process_name"], s["shift_no"])
                         for s in slots}
            seen_so_keys: set = set()
            candidates_to_check: List[tuple] = []

            for p in other_plans:
                pk = (p["plan_date"], p["room_code"],
                      p["process_name"], p["shift_no"])
                if pk not in used_keys:
                    continue
                so_key = (p["so_number"], p["sku_code"], p["line_item"])
                if so_key in seen_so_keys:
                    continue
                seen_so_keys.add(so_key)
                d_so = SORepo.get(p["so_number"], p["sku_code"], p["line_item"])
                if not d_so:
                    continue
                candidates_to_check.append((so_key, d_so))

            # Sort displaced SOs by priority/due — most critical replanned first
            def _disp_key(item):
                _, ds = item
                pri = ds.get("priority")
                return ((0, pri) if pri is not None else (1, 0),
                        ds.get("due_date", ""))
            candidates_to_check.sort(key=_disp_key)

            # sim_map already has target SO's capacity consumed.
            # Dry-run each displaced SO sequentially in remaining capacity.
            for (so_no_d, sku_d, li_d), d_so in candidates_to_check:
                due     = d_so.get("due_date", "")
                cdd     = d_so.get("committed_due_date") or ""
                check   = cdd if cdd else due
                d1_d    = _date(check) if check else wide_d1

                d_plans = [x for x in PlanRepo.for_so(so_no_d, sku_d, li_d)
                           if x.get("is_final_seq")]
                cur_comp = (max(x["plan_date"] for x in d_plans)
                            if d_plans else None)
                status_before = ("LATE" if cur_comp and check and cur_comp > check
                                 else "ON TIME")

                replan_slots = self._dry_run_plan_so(d_so, sim_map, d0, d1_d)

                if not replan_slots:
                    new_comp      = None
                    can_reschedule = False
                    status_after  = "CANNOT REPLAN"
                else:
                    fin = [s for s in replan_slots if s.get("is_final")]
                    new_comp = max(s["date"] for s in fin) if fin else None
                    can_reschedule = bool(
                        new_comp and (not check or new_comp <= check))
                    status_after = "ON TIME" if can_reschedule else "LATE"

                displaced.append({
                    "so_number":          so_no_d,
                    "sku_code":           sku_d,
                    "line_item":          li_d,
                    "priority":           d_so.get("priority"),   # Bug #1 fix
                    "customer_name":      d_so.get("customer_name") or "",
                    "due_date":           due,
                    "committed_due_date": cdd,
                    "current_completion": cur_comp or "",
                    "new_completion":     new_comp or "",
                    "status_before":      status_before,
                    "status_after":       status_after,
                    "can_reschedule":     can_reschedule,
                })

            # Block push if any displaced SO cannot meet its due date
            blocking = [d for d in displaced if not d["can_reschedule"]]
            if blocking:
                names = ", ".join(
                    f"{d['so_number']}/{d['sku_code']}" for d in blocking[:3])
                return {
                    "feasible": False,
                    "final_date": final_date,
                    "displaced": displaced,
                    "error": (f"Push blocked: displaced SO(s) cannot be rescheduled "
                              f"within their due dates — {names}"),
                }

        return {"feasible": True, "final_date": final_date,
                "displaced": displaced, "error": None}

    def apply_single_pull_forward(
        self, so_no: str, sku_code: str, line_item: str,
        target_date: str, allow_push: bool,
        displaced: List[Dict],
    ) -> Dict:
        """
        Apply pull forward: delete this SO's unlocked plans (+ displaced if
        allow_push), re-plan target SO, then automatically re-plan displaced SOs
        in priority order using remaining capacity.
        """
        self._reload_masters()
        so = SORepo.get(so_no, sku_code, line_item)
        if not so:
            return {"success": False, "error": "SO not found"}

        d0 = date.today()
        # Wide horizon so displaced SOs can be rescheduled beyond target_date
        all_open = SORepo.all(status="OPEN")
        max_due = max(
            (_date(s["due_date"]) for s in all_open if s.get("due_date")),
            default=_date(target_date) + timedelta(days=60),
        )
        wide_d1      = max(_date(target_date), max_due) + timedelta(days=1)
        wide_horizon = _ds(wide_d1)

        # Delete unlocked plans for target SO and all displaced SOs
        PlanRepo.delete_unlocked_for_so(so_no, sku_code, line_item)
        if allow_push:
            for d_item in displaced:
                PlanRepo.delete_unlocked_for_so(
                    d_item["so_number"], d_item["sku_code"], d_item["line_item"])

        slot_map = self._build_slot_map(_ds(d0), wide_horizon)
        report   = {"planned": 0, "skipped": 0, "late": [],
                    "routing_errors": [], "material_plans": 0}

        # 1. Plan target SO first (force=True so it lands exactly at target_date)
        sim_so = {**so, "due_date": target_date}
        self._plan_so(sim_so, slot_map, _ds(d0), target_date, report, force=True)

        # 2. Auto-replan displaced SOs in priority order using remaining capacity
        replanned = 0
        if allow_push:
            def _disp_sort(item):
                pri = item.get("priority")
                return ((0, pri) if pri is not None else (1, 0),
                        item.get("due_date", ""))
            for d_item in sorted(displaced, key=_disp_sort):
                d_so = SORepo.get(d_item["so_number"], d_item["sku_code"],
                                  d_item["line_item"])
                if not d_so:
                    continue
                due = d_so.get("committed_due_date") or d_so.get("due_date") or wide_horizon
                self._plan_so(d_so, slot_map, _ds(d0), due, report)
                replanned += 1

        return {"success": True, "planned": report["planned"],
                "displaced_count": len(displaced),
                "replanned": replanned, "error": None}

    def replan_after_actuals(self, date_from: str, date_to: str) -> Dict:
        self._reload_masters()
        report = {"deleted": [], "replanned": [], "errors": []}

        slot_map    = self._build_slot_map(date_from, date_to)
        plan_report = {"planned": 0, "skipped": 0, "late": [], "routing_errors": []}

        # Group unlocked SKU plans by SO key
        so_plans: Dict[tuple, list] = {}
        for plan in PlanRepo.all():
            if plan["is_locked"] or plan["entity_type"] != "SKU":
                continue
            key = (plan["so_number"], plan["sku_code"], plan["line_item"])
            so_plans.setdefault(key, []).append(plan)

        for (so_no, sku_c, li), plans in so_plans.items():
            so = SORepo.get(so_no, sku_c, li)
            if not so:
                continue
            # Use net qty (actual - sample - reject) as completion criterion (M1)
            net_total = LotSampleRepo.net_qty(so_no, sku_c, li)

            if net_total >= so["qty"]:
                # Fully produced (net of QC deductions) → delete remaining plans
                for p in plans:
                    PlanRepo.delete(p["plan_id"], reason="replan_completed")
                    report["deleted"].append({
                        "plan_id": p["plan_id"], "so_number": so_no,
                        "sku_code": sku_c, "line_item": li,
                        "reason": "SO fully produced (net)"
                    })
            elif net_total > 0:
                # Partially produced → delete then re-plan remainder
                for p in plans:
                    PlanRepo.delete(p["plan_id"], reason="replan_partial")
                    report["deleted"].append({
                        "plan_id": p["plan_id"], "so_number": so_no,
                        "sku_code": sku_c, "line_item": li,
                        "reason": f"Partial ({net_total}/{so['qty']} net) — re-planning"
                    })
                before = plan_report["planned"]
                self._plan_so(so, slot_map, date_from, date_to, plan_report)
                new_slots = plan_report["planned"] - before
                report["replanned"].append({
                    "so_number": so_no, "sku_code": sku_c, "line_item": li,
                    "actual_qty": net_total,
                    "remaining_qty": so["qty"] - net_total,
                    "new_slots": new_slots,
                })

        report["errors"] = (
            [{"so": x["so"], "reason": x["reason"]} for x in plan_report["routing_errors"]] +
            [{"so": x["so"], "reason": f"late: {x.get('reason','')}"}
             for x in plan_report["late"]]
        )
        return report

    def weekly_reorganize(self, date_from: str, date_to: str) -> Dict:
        """Post-pass: within each ISO week, reorder unlocked SKU plans per (room, process)
        to group same-SKU plans consecutively, minimising changeovers.

        Constraints vs defragment:
        - No cross-week movement (plans stay in their ISO week)
        - Directional: new_date >= original_date (only forward within week)
        - gap_ok() per-plan: min_gap_shifts respected
        - Locked / consolidated plans not moved
        - Topological order within each week (lower process_seq first)

        Returns {"moved": N, "frozen": M, "skipped_groups": K}.
        """
        from collections import defaultdict as _dd
        from data.repositories import ProcessRoutingRepo as _PRR

        self._reload_masters()
        shift_nos = sorted(s["shift_no"] for s in self.shifts)

        # ── routing maps ──────────────────────────────────────────────────────
        all_routing = _PRR.all()
        routing_by_proc: Dict[Tuple, Dict] = {}
        routing_by_seq:  Dict[str, Dict[int, Dict]] = {}
        for step in all_routing:
            routing_by_proc[(step["entity_code"], step["process_name"])] = step
            routing_by_seq.setdefault(step["entity_code"], {})[step["process_seq"]] = step

        # ── hard deadline per (so, sku, line) ────────────────────────────────
        hard_deadline: Dict[Tuple, str] = {}
        for so in SORepo.all("OPEN"):
            k = (so["so_number"], so["sku_code"], so["line_item"])
            sku = self.sku_map.get(so["sku_code"], {})
            lead = int(sku.get("post_lead_days") or 0)
            dl = sub_workdays(date.fromisoformat(so["due_date"]), lead).isoformat()
            hard_deadline[k] = dl

        def _deadline(p: Dict) -> str:
            return hard_deadline.get(
                (p["so_number"], p["sku_code"], p["line_item"]), date_to)

        # ── all SKU plans in range ────────────────────────────────────────────
        all_plans = [p for p in PlanRepo.all(date_from, date_to)
                     if p.get("entity_type") != "MATERIAL"]

        # ── global slot index ─────────────────────────────────────────────────
        all_date_shifts = sorted(
            {(p["plan_date"], p["shift_no"]) for p in all_plans},
            key=lambda ds: (ds[0], shift_nos.index(ds[1]) if ds[1] in shift_nos else 0)
        )
        slot_global_idx: Dict[Tuple, int] = {ds: i for i, ds in enumerate(all_date_shifts)}

        def _sidx(date_str: str, shift_no: int) -> int:
            return slot_global_idx.get((date_str, shift_no), 0)

        # ── plan_slot_map: updated after each group (for gap checks) ──────────
        plan_slot_map: Dict[Tuple, Tuple] = {}
        for p in all_plans:
            step = routing_by_proc.get((p["entity_code"], p["process_name"]))
            if step:
                key = (p["so_number"], p["line_item"],
                       p["entity_code"], step["process_seq"])
                plan_slot_map[key] = (p["plan_date"], p["shift_no"])

        # ── gap validation ────────────────────────────────────────────────────
        def _gap_ok(plan: Dict, new_date: str, new_shift: int) -> bool:
            step = routing_by_proc.get((plan["entity_code"], plan["process_name"]))
            if not step:
                return True
            seq  = step["process_seq"]
            ec   = plan["entity_code"]
            so   = plan["so_number"]
            line = plan["line_item"]
            new_idx = _sidx(new_date, new_shift)

            if seq > 1:
                pre_slot = plan_slot_map.get((so, line, ec, seq - 1))
                if pre_slot:
                    gap_needed = 1 + int(step.get("min_gap_shifts") or 0)
                    if new_idx < _sidx(*pre_slot) + gap_needed:
                        return False

            post_step = routing_by_seq.get(ec, {}).get(seq + 1)
            if post_step:
                post_slot = plan_slot_map.get((so, line, ec, seq + 1))
                if post_slot:
                    gap_needed = 1 + int(post_step.get("min_gap_shifts") or 0)
                    if _sidx(*post_slot) < new_idx + gap_needed:
                        return False
            return True

        # ── group by (iso_year, iso_week, room, process) ──────────────────────
        groups: Dict[Tuple, List[Dict]] = _dd(list)
        for p in all_plans:
            d = date.fromisoformat(p["plan_date"])
            iso_year, iso_week, _ = d.isocalendar()
            groups[(iso_year, iso_week,
                    p["room_code"], p["process_name"])].append(p)

        def _group_min_seq(plans: List[Dict]) -> int:
            seqs = [routing_by_proc[(p["entity_code"], p["process_name"])]["process_seq"]
                    for p in plans
                    if (p["entity_code"], p["process_name"]) in routing_by_proc]
            return min(seqs) if seqs else 999

        # Sort: ISO week ascending, then topological (process_seq) within week
        sorted_groups = sorted(
            groups.items(),
            key=lambda item: (item[0][0], item[0][1], _group_min_seq(item[1]))
        )

        moved = 0
        frozen = 0
        skipped = 0

        for (iso_year, iso_week, room, proc), plans in sorted_groups:
            unlocked = [p for p in plans
                        if not p["is_locked"] and not p.get("is_consolidated")]
            if len(unlocked) < 2:
                continue

            # Already optimal (no SKU switches)?
            sorted_now = sorted(unlocked, key=lambda p: _sidx(p["plan_date"], p["shift_no"]))
            skus_now = [p["sku_code"] for p in sorted_now]
            if all(skus_now[i] == skus_now[i - 1] for i in range(1, len(skus_now))):
                continue

            # Changeover budget for this room+process
            co_shifts = self._changeover_shifts.get((room, proc), 0)

            # Earliest allowable start per plan (predecessor gap)
            def _earliest(p: Dict) -> int:
                step = routing_by_proc.get((p["entity_code"], p["process_name"]))
                if not step or step["process_seq"] <= 1:
                    return 0
                pre_slot = plan_slot_map.get(
                    (p["so_number"], p["line_item"],
                     p["entity_code"], step["process_seq"] - 1))
                if not pre_slot:
                    return 0
                return _sidx(*pre_slot) + 1 + int(step.get("min_gap_shifts") or 0)

            # Deadline slot index: latest slot whose date <= deadline
            def _dl_sidx(p: Dict) -> int:
                dl = _deadline(p)
                best = _sidx(p["plan_date"], p["shift_no"])  # fallback: orig
                for d, s in all_date_shifts:
                    if d <= dl:
                        best = _sidx(d, s)
                return best

            # Phase 1: freeze tight plans (slack < 1 + co_shifts) at original slot
            # tight = plan can't afford to be pushed back even one changeover window
            tight_ids: set = set()
            for p in unlocked:
                orig_idx = _sidx(p["plan_date"], p["shift_no"])
                slack = _dl_sidx(p) - orig_idx
                if slack < 1 + co_shifts:
                    tight_ids.add(p["plan_id"])

            tight_plans = [p for p in unlocked if p["plan_id"] in tight_ids]
            loose_plans = [p for p in unlocked if p["plan_id"] not in tight_ids]

            # Update plan_slot_map for frozen plans first (gap checks for loose plans need them)
            for p in tight_plans:
                step = routing_by_proc.get((p["entity_code"], p["process_name"]))
                if step:
                    plan_slot_map[(p["so_number"], p["line_item"],
                                   p["entity_code"], step["process_seq"])] = \
                        (p["plan_date"], p["shift_no"])
            frozen += len(tight_ids)

            if not loose_plans:
                skipped += 1
                continue

            # Phase 2: greedy consolidation for loose plans
            # Pool = slots that belong to loose plans (tight plans keep their slots)
            remaining_pool = sorted(
                [(p["plan_date"], p["shift_no"]) for p in loose_plans],
                key=lambda ds: _sidx(*ds)
            )

            # Qty budget per slot — prevents moving a high-qty plan into a
            # low-capacity slot just because the count allows it.
            # Budget = total qty of loose plans originally in that slot.
            slot_loose_budget: Dict[Tuple, int] = _dd(int)
            for p in loose_plans:
                slot_loose_budget[(p["plan_date"], p["shift_no"])] += p["qty_planned"]
            slot_loose_used: Dict[Tuple, int] = _dd(int)

            # Sort loose plans for SKU consolidation: (sku, earliest_start, deadline, orig)
            desired_loose = sorted(loose_plans, key=lambda p: (
                p["sku_code"],
                _earliest(p),
                _deadline(p),
                _sidx(p["plan_date"], p["shift_no"]),
            ))

            loose_assignments: List[Tuple[Dict, str, int]] = []
            for plan in desired_loose:
                dl = _deadline(plan)
                placed = False
                for i, (nd, ns) in enumerate(remaining_pool):
                    if nd > dl:                     # would miss deadline
                        continue
                    # Capacity check: don't put more qty in a slot than it held
                    # for loose plans originally (prevents swap-induced overcapacity).
                    slot_k = (nd, ns)
                    if slot_loose_used[slot_k] + plan["qty_planned"] > slot_loose_budget[slot_k]:
                        continue
                    if _gap_ok(plan, nd, ns):
                        slot_loose_used[slot_k] += plan["qty_planned"]
                        loose_assignments.append((plan, nd, ns))
                        remaining_pool.pop(i)
                        placed = True
                        break
                if not placed:
                    # Fallback: anchor at original slot (deadline or gap constraint blocked all moves)
                    orig_k = (plan["plan_date"], plan["shift_no"])
                    slot_loose_used[orig_k] += plan["qty_planned"]
                    loose_assignments.append((plan, plan["plan_date"], plan["shift_no"]))
                    try:
                        remaining_pool.remove((plan["plan_date"], plan["shift_no"]))
                    except ValueError:
                        pass
                    frozen += 1

            # Apply loose assignments and update plan_slot_map
            for plan, new_date, new_shift in loose_assignments:
                step = routing_by_proc.get((plan["entity_code"], plan["process_name"]))
                if step:
                    plan_slot_map[(plan["so_number"], plan["line_item"],
                                   plan["entity_code"], step["process_seq"])] = \
                        (new_date, new_shift)
                if plan["plan_date"] == new_date and plan["shift_no"] == new_shift:
                    continue
                PlanRepo.update(plan["plan_id"],
                                {"plan_date": new_date, "shift_no": new_shift},
                                reason="weekly_reorganize")
                moved += 1

        return {"moved": moved, "frozen": frozen, "skipped_groups": skipped}

    # ── Campaign Pass ─────────────────────────────────────────────────────────
    def campaign_pass(self, date_from: str, date_to: str,
                      slot_full_cap: Dict[SlotKey, float] = None) -> Dict:
        """Post-pass: pull unlocked same-SKU/Room/Process plans from later dates
        into earlier dates when capacity permits.

        Improvements over v1:
        ① Sliding-window clustering — compares against the *previous* plan's date
          so chains like 7/01→7/04→7/07 (window=3) form one cluster, not two.
        ② slot_full_cap can be passed in from auto_plan to avoid rebuilding
          the CRP-based capacity map (no redundant Excel re-read).
        ③ FFD ordering — movable plans sorted by qty descending before placement
          so large blocks claim space first and small blocks fill the gaps.
        ④ Bulk DB write — all moves committed in a single transaction with
          plan_history records (PlanRepo.bulk_campaign_update).

        Returns {"moved": N}.
        """
        from collections import defaultdict as _dd

        window = int(ConfigRepo.get("campaign_pass_window_days", "7"))
        if window <= 0:
            return {"moved": 0}

        # ② Reuse slot_full_cap from caller when available; build only if standalone
        if slot_full_cap is None:
            self._reload_masters()
            slot_full_cap = self._build_slot_map(date_from, date_to)

        # All unlocked, non-consolidated SKU plans in range
        all_plans = [
            p for p in PlanRepo.all(date_from, date_to, entity_type="SKU")
            if not p["is_locked"] and not p.get("is_consolidated")
        ]

        # Current inner-unit usage per slot for unlocked plans
        slot_used: Dict[SlotKey, float] = _dd(float)
        for p in all_plans:
            uom = self._entity_uom(p)
            key: SlotKey = (p["plan_date"], p["room_code"],
                            p["process_name"], p["shift_no"])
            slot_used[key] += sku_to_inner(p["qty_planned"], uom)

        # Group by (entity_code, room_code, process_name)
        groups: Dict[Tuple, List[Dict]] = _dd(list)
        for p in all_plans:
            groups[(p["entity_code"], p["room_code"], p["process_name"])].append(p)

        pending: List[Tuple[int, str, int]] = []   # (plan_id, new_date, new_shift)

        for (entity_code, room_code, process_name), plans in groups.items():
            if len(plans) < 2:
                continue

            sku_obj = self.sku_map.get(entity_code, {})
            uom = int(sku_obj.get("uom") or 1)

            plans.sort(key=lambda p: (p["plan_date"], p["shift_no"]))

            # ① Sliding-window clustering: compare against previous plan, not cluster[0]
            clusters: List[List[Dict]] = []
            current: List[Dict] = [plans[0]]
            for p in plans[1:]:
                d_prev = date.fromisoformat(current[-1]["plan_date"])
                d_cur  = date.fromisoformat(p["plan_date"])
                if (d_cur - d_prev).days <= window:
                    current.append(p)
                else:
                    if len(current) > 1:
                        clusters.append(current[:])
                    current = [p]
            if len(current) > 1:
                clusters.append(current)

            for cluster in clusters:
                # Sorted target slots (earliest first) — recomputed per cluster
                # because plans may have moved within the session
                target_slots = sorted(
                    {(p["plan_date"], p["shift_no"]): p for p in cluster}.values(),
                    key=lambda p: (p["plan_date"], p["shift_no"])
                )

                # ③ FFD: movable plans sorted by qty descending
                movable = sorted(cluster[1:],
                                 key=lambda p: p["qty_planned"], reverse=True)

                for later in movable:
                    inner_qty: float = sku_to_inner(later["qty_planned"], uom)
                    later_key: SlotKey = (later["plan_date"], room_code,
                                          process_name, later["shift_no"])

                    for tgt in target_slots:
                        if tgt["plan_date"] >= later["plan_date"]:
                            continue  # only pull earlier
                        e_key: SlotKey = (tgt["plan_date"], room_code,
                                          process_name, tgt["shift_no"])
                        remaining = (slot_full_cap.get(e_key, 0.0)
                                     - slot_used.get(e_key, 0.0))
                        if remaining >= inner_qty:
                            slot_used[later_key] -= inner_qty
                            slot_used[e_key]     += inner_qty
                            pending.append(
                                (later["plan_id"], tgt["plan_date"], tgt["shift_no"]))
                            later["plan_date"] = tgt["plan_date"]
                            later["shift_no"]  = tgt["shift_no"]
                            break

        # ④ Bulk write: single transaction, history preserved
        moved = PlanRepo.bulk_campaign_update(pending)
        return {"moved": moved}

    # ── Defer-and-Fill Pass ───────────────────────────────────────────────────
    def defer_and_fill_pass(self, date_from: str, date_to: str) -> Dict:
        """Post-pass: defer low-urgency unlocked plans one shift later to free
        early slots, then forward-fill those slots with more urgent / unplanned SOs.

        Phase 1 (Defer): For each unlocked SKU plan (earliest first), move it
        to the next shift when:
          (a) the SO's due date is ≥ defer_buffer_days away,
          (b) the next slot has sufficient free capacity, and
          (c) no changeover conflict at the next slot.

        Phase 2 (Fill): Forward-fill under-planned OPEN SOs (most urgent first)
        into the freed slots.

        Config keys:
          defer_and_fill_enabled  (int 0/1, default 1)
          defer_buffer_days       (int, default 7) — minimum due-date buffer required
        """
        if not int(ConfigRepo.get("defer_and_fill_enabled", "1")):
            return {"deferred": 0, "filled": 0}

        defer_buffer = int(ConfigRepo.get("defer_buffer_days", "7"))
        today = date.today()

        self._reload_masters()
        slot_map = self._build_slot_map(date_from, date_to)

        # Subtract unlocked plans so slot_map reflects free capacity
        all_plans = PlanRepo.all(date_from, date_to, entity_type="SKU")
        for p in all_plans:
            if p["is_locked"]:
                continue
            uom = self._entity_uom(p)
            key: SlotKey = (p["plan_date"], p["room_code"],
                            p["process_name"], p["shift_no"])
            if key in slot_map:
                slot_map[key] = max(0.0,
                    slot_map[key] - sku_to_inner(p["qty_planned"], uom))

        # SO due-date cache (only OPEN SOs)
        so_cache: Dict[Tuple, Dict] = {}
        for so in SORepo.all(status="OPEN"):
            k = (so["so_number"], so["sku_code"], so["line_item"])
            so_cache[k] = so

        # ── Phase 1: Defer ────────────────────────────────────────────────────
        deferred = 0
        unlocked = [p for p in all_plans if not p["is_locked"]]
        # earliest-first so we try to free the most-congested early slots
        unlocked.sort(key=lambda p: (p["plan_date"], p["shift_no"]))

        for plan in unlocked:
            so_key = (plan["so_number"], plan["sku_code"], plan["line_item"])
            so = so_cache.get(so_key)
            if not so or not so.get("due_date"):
                continue

            # Skip if SO is urgent (not enough buffer)
            buffer_days = (_date(so["due_date"]) - today).days
            if buffer_days < defer_buffer:
                continue

            nds, nsno = self._next_slot(plan["plan_date"], plan["shift_no"])
            if nds > date_to:
                continue

            uom = self._entity_uom(plan)
            inner_qty = sku_to_inner(plan["qty_planned"], uom)
            nkey: SlotKey = (nds, plan["room_code"], plan["process_name"], nsno)
            avail = slot_map.get(nkey, 0.0)
            if avail < inner_qty:
                continue  # next slot doesn't fit the plan

            # Changeover guard at next slot
            co_shifts = self._changeover_shifts.get(
                (plan["room_code"], plan["process_name"]), 0)
            entity_code = plan.get("entity_code") or plan["sku_code"]
            if co_shifts and self._has_changeover_conflict(
                    plan["room_code"], plan["process_name"],
                    nds, nsno, co_shifts, entity_code):
                continue

            # Execute: free old slot, consume new slot, update DB
            old_key: SlotKey = (plan["plan_date"], plan["room_code"],
                                plan["process_name"], plan["shift_no"])
            slot_map[old_key] = slot_map.get(old_key, 0.0) + inner_qty
            slot_map[nkey] = max(0.0, avail - inner_qty)

            PlanRepo.update(plan["plan_id"],
                            {"plan_date": nds, "shift_no": nsno},
                            reason="defer_and_fill")
            self._record_placed(plan["room_code"], plan["process_name"],
                                nds, nsno, entity_code)
            deferred += 1

        # ── Phase 2: Fill ─────────────────────────────────────────────────────
        report_fill: Dict = {"planned": 0, "late": [], "skipped": 0,
                             "routing_errors": []}
        from data.repositories import AllocationRepo as _AR
        for so in self._sorted_open_sos():
            prod_needed = _AR.production_needed(
                so["so_number"], so["sku_code"], so["line_item"])
            planned_qty = PlanRepo.final_planned_qty(
                so["so_number"], so["sku_code"], so["line_item"])
            if prod_needed - planned_qty <= 0:
                continue
            # Forward-fill: use earliest available slots first so freed early
            # capacity is consumed before late capacity.
            self._plan_so(so, slot_map, date_from, date_to,
                          report_fill, forward_fill=True)

        return {"deferred": deferred, "filled": report_fill["planned"]}

    def defragment(self, date_from: str, date_to: str) -> Dict:
        """Post-pass: reorder unlocked SKU plans within each (room, process) to
        pack same-SKU blocks consecutively, minimising changeovers.

        Improvements over v1:
        - Topological order: groups with lower process_seq are defragged first
          so predecessor slots are stable when checking gap constraints.
        - gap_ok per-plan: each reassignment is validated against min_gap_shifts
          of the routing step. Plans that would violate a gap are frozen in place;
          only the failing plan is frozen, not the whole group.
        - Smart sort key: (sku, earliest_allowable_start, deadline) — gap-constrained
          plans are front-loaded to reduce downstream violations.
        - plan_slot_map updated after each group so later groups see current positions.

        Returns {"moved": N, "skipped_groups": M, "gap_frozen": K}.
        """
        from collections import defaultdict as _dd
        from data.repositories import ProcessRoutingRepo as _PRR

        self._reload_masters()
        shift_nos = sorted(s["shift_no"] for s in self.shifts)

        # ── routing maps ──────────────────────────────────────────────────────
        all_routing = _PRR.all()
        # (entity_code, process_name) → step dict
        routing_by_proc: Dict[Tuple, Dict] = {}
        # entity_code → {process_seq → step}
        routing_by_seq: Dict[str, Dict[int, Dict]] = {}
        for step in all_routing:
            routing_by_proc[(step["entity_code"], step["process_name"])] = step
            routing_by_seq.setdefault(step["entity_code"], {})[step["process_seq"]] = step

        # ── hard deadline per (so, sku, line) ────────────────────────────────
        hard_deadline: Dict[Tuple, str] = {}
        for so in SORepo.all("OPEN"):
            k = (so["so_number"], so["sku_code"], so["line_item"])
            sku = self.sku_map.get(so["sku_code"], {})
            lead = int(sku.get("post_lead_days") or 0)
            dl = sub_workdays(date.fromisoformat(so["due_date"]), lead).isoformat()
            hard_deadline[k] = dl

        def _deadline(p: Dict) -> str:
            return hard_deadline.get(
                (p["so_number"], p["sku_code"], p["line_item"]), date_to)

        # ── all SKU plans in range ────────────────────────────────────────────
        all_plans = [p for p in PlanRepo.all(date_from, date_to)
                     if p.get("entity_type") != "MATERIAL"]

        # ── global slot index: chronological order for gap arithmetic ─────────
        all_date_shifts = sorted(
            {(p["plan_date"], p["shift_no"]) for p in all_plans},
            key=lambda ds: (ds[0], shift_nos.index(ds[1]) if ds[1] in shift_nos else 0)
        )
        slot_global_idx: Dict[Tuple, int] = {ds: i for i, ds in enumerate(all_date_shifts)}

        def _sidx(date_str: str, shift_no: int) -> int:
            return slot_global_idx.get((date_str, shift_no), 0)

        # ── plan_slot_map: (so, line, entity_code, process_seq) → (date, shift)
        # Mutable: updated after each group so later groups see current positions.
        plan_slot_map: Dict[Tuple, Tuple] = {}
        for p in all_plans:
            step = routing_by_proc.get((p["entity_code"], p["process_name"]))
            if step:
                key = (p["so_number"], p["line_item"],
                       p["entity_code"], step["process_seq"])
                plan_slot_map[key] = (p["plan_date"], p["shift_no"])

        # ── gap validation ─────────────────────────────────────────────────────
        def _gap_ok(plan: Dict, new_date: str, new_shift: int) -> bool:
            step = routing_by_proc.get((plan["entity_code"], plan["process_name"]))
            if not step:
                return True
            seq = step["process_seq"]
            ec  = plan["entity_code"]
            so  = plan["so_number"]
            line = plan["line_item"]
            new_idx = _sidx(new_date, new_shift)

            # Predecessor: new_idx must be >= pre_idx + 1 + step.min_gap_shifts
            if seq > 1:
                pre_slot = plan_slot_map.get((so, line, ec, seq - 1))
                if pre_slot:
                    gap_needed = 1 + int(step.get("min_gap_shifts") or 0)
                    if new_idx < _sidx(*pre_slot) + gap_needed:
                        return False

            # Successor: post_idx must still be >= new_idx + 1 + post_step.min_gap_shifts
            post_step = routing_by_seq.get(ec, {}).get(seq + 1)
            if post_step:
                post_slot = plan_slot_map.get((so, line, ec, seq + 1))
                if post_slot:
                    gap_needed = 1 + int(post_step.get("min_gap_shifts") or 0)
                    if _sidx(*post_slot) < new_idx + gap_needed:
                        return False

            return True

        # ── group by (room, process), sort in topological (process_seq) order ─
        groups: Dict[Tuple, List[Dict]] = _dd(list)
        for p in all_plans:
            groups[(p["room_code"], p["process_name"])].append(p)

        def _group_min_seq(plans: List[Dict]) -> int:
            seqs = []
            for p in plans:
                step = routing_by_proc.get((p["entity_code"], p["process_name"]))
                if step:
                    seqs.append(step["process_seq"])
            return min(seqs) if seqs else 999

        sorted_groups = sorted(groups.items(), key=lambda item: _group_min_seq(item[1]))

        moved = 0
        skipped = 0
        gap_frozen = 0

        for (room, proc), plans in sorted_groups:
            unlocked = [p for p in plans if not p["is_locked"]]
            if len(unlocked) < 2:
                continue

            # Already optimal (no SKU switches)?
            sorted_now = sorted(unlocked, key=lambda p: _sidx(p["plan_date"], p["shift_no"]))
            skus_now = [p["sku_code"] for p in sorted_now]
            if all(skus_now[i] == skus_now[i - 1] for i in range(1, len(skus_now))):
                continue

            # Chronological slot pool (may have duplicates for stacked plans)
            slot_pool = sorted(
                [(p["plan_date"], p["shift_no"]) for p in unlocked],
                key=lambda ds: _sidx(*ds)
            )

            # Earliest allowable start per plan (predecessor-constrained)
            def _earliest(p: Dict) -> int:
                step = routing_by_proc.get((p["entity_code"], p["process_name"]))
                if not step or step["process_seq"] <= 1:
                    return 0
                pre_slot = plan_slot_map.get(
                    (p["so_number"], p["line_item"],
                     p["entity_code"], step["process_seq"] - 1))
                if not pre_slot:
                    return 0
                return _sidx(*pre_slot) + 1 + int(step.get("min_gap_shifts") or 0)

            # Sort: (sku, earliest_allowable_start, deadline, original_slot)
            desired = sorted(unlocked, key=lambda p: (
                p["sku_code"],
                _earliest(p),
                _deadline(p),
                _sidx(p["plan_date"], p["shift_no"]),
            ))

            # Greedy assignment with per-plan gap check
            remaining_pool = list(slot_pool)  # mutable copy
            assignments: List[Tuple[Dict, str, int]] = []

            for plan in desired:
                placed = False
                for i, (nd, ns) in enumerate(remaining_pool):
                    if _gap_ok(plan, nd, ns):
                        assignments.append((plan, nd, ns))
                        remaining_pool.pop(i)
                        placed = True
                        break
                if not placed:
                    # Freeze at original slot
                    orig = (plan["plan_date"], plan["shift_no"])
                    assignments.append((plan, orig[0], orig[1]))
                    try:
                        remaining_pool.remove(orig)
                    except ValueError:
                        pass
                    gap_frozen += 1

            # Validate deadlines (skip whole group if any violation)
            if any(new_date > _deadline(plan) for plan, new_date, _ in assignments):
                skipped += 1
                continue

            # Apply and update plan_slot_map
            for plan, new_date, new_shift in assignments:
                step = routing_by_proc.get((plan["entity_code"], plan["process_name"]))
                if step:
                    plan_slot_map[(plan["so_number"], plan["line_item"],
                                   plan["entity_code"], step["process_seq"])] = \
                        (new_date, new_shift)
                if plan["plan_date"] == new_date and plan["shift_no"] == new_shift:
                    continue
                PlanRepo.update(plan["plan_id"],
                                {"plan_date": new_date, "shift_no": new_shift},
                                reason="defragment")
                moved += 1

        return {"moved": moved, "skipped_groups": skipped, "gap_frozen": gap_frozen}

    def detect_conflicts(self, date_from: str, date_to: str) -> List[Dict]:
        # _reload_masters() is intentionally NOT called here —
        # it fired on auto_plan/pull_forward already, and calling it on every
        # Gantt refresh adds ~8 DB round-trips per tab switch.
        slot_planned: Dict[SlotKey, int] = defaultdict(int)
        rs_procs: Dict[Tuple[str, str, int], set] = defaultdict(set)

        for plan in PlanRepo.all(date_from, date_to):
            uom = self._entity_uom(plan)
            key: SlotKey = (plan["plan_date"], plan["room_code"],
                            plan["process_name"], plan["shift_no"])
            slot_planned[key] += sku_to_inner(plan["qty_planned"], uom)
            rs_procs[(plan["plan_date"], plan["room_code"],
                      plan["shift_no"])].add(plan["process_name"])

        conflicts = []
        conflict_keys: set = set()

        # 1. Capacity overrun
        for key, planned_inner in slot_planned.items():
            d, room, proc, sno = key
            cap = self._slot_capacity_inner(d, room, proc, sno)
            if planned_inner > cap:
                conflicts.append({
                    "plan_date": d, "room_code": room,
                    "process_name": proc, "shift_no": sno,
                    "planned_inner": planned_inner,
                    "capacity_inner": cap,
                    "overrun_inner": planned_inner - cap,
                })
                conflict_keys.add(key)

        # 2. Multiple processes in same room-shift (line clearance violation)
        for (ds, room, sno), procs in rs_procs.items():
            if len(procs) > 1:
                for proc in procs:
                    k = (ds, room, proc, sno)
                    if k not in conflict_keys:
                        conflicts.append({
                            "plan_date": ds, "room_code": room,
                            "process_name": proc, "shift_no": sno,
                            "conflict_type": "multi_process",
                            "processes": sorted(procs),
                        })
                        conflict_keys.add(k)

        return conflicts

    # ── SKU Planning ─────────────────────────────────────────────────────────

    def _group_same_sku_within_priority(self, sos: List[Dict]) -> List[Dict]:
        """(Opt-B) Within each priority tier, cluster same-SKU SOs consecutively.
        SKU order within a tier is determined by first-occurrence position."""
        if not sos:
            return sos
        result: List[Dict] = []
        i = 0
        while i < len(sos):
            pri = sos[i].get("priority")
            tier: List[Dict] = []
            while i < len(sos) and sos[i].get("priority") == pri:
                tier.append(sos[i])
                i += 1
            # Record first-seen position for each SKU in this tier
            sku_first: Dict[str, int] = {}
            for so in tier:
                sk = so["sku_code"]
                if sk not in sku_first:
                    sku_first[sk] = len(sku_first)
            tier.sort(key=lambda s: (sku_first[s["sku_code"]], s.get("due_date", "")))
            result.extend(tier)
        return result

    def _is_same_sku_adjacent(self, room: str, process_name: str,
                               ds: str, sno: int, sku_code: str) -> bool:
        """(Opt-A) Return True if the slot immediately before or after (ds, sno)
        already has sku_code placed in the same room/process."""
        sns = sorted(s["shift_no"] for s in self.shifts)
        if not sns or sno not in sns:
            return False
        idx = sns.index(sno)
        # Next slot (chronologically later — already filled when backward-scanning)
        if idx + 1 < len(sns):
            nds, nsno = ds, sns[idx + 1]
        else:
            nds = _ds(_date(ds) + timedelta(days=1))
            nsno = sns[0]
        if self._placed_sku.get((room, process_name, nds, nsno)) == sku_code:
            return True
        # Prev slot (chronologically earlier)
        if idx > 0:
            pds, psno = ds, sns[idx - 1]
        else:
            pds = _ds(_date(ds) - timedelta(days=1))
            psno = sns[-1]
        return self._placed_sku.get((room, process_name, pds, psno)) == sku_code

    def _sorted_open_sos(self) -> List[Dict]:
        sos = SORepo.all(status="OPEN")
        def key(so):
            p = so.get("priority")
            return ((0, p) if p is not None else (1, 0),
                    so.get("received_at", ""))
        return sorted(sos, key=key)

    def _build_slot_map(self, date_from: str,
                         date_to: str) -> Dict[SlotKey, float]:
        slot_map: Dict[SlotKey, float] = {}
        self._hc_dist_cache.clear()
        self._last_urgency = self._compute_urgency()

        # Pre-fetch all calendar unavailable slots in one query (O(1) lookup replaces N×M×K calls)
        _unavail = {
            (r["cal_date"], r["shift_no"], r["room_code"])
            for r in CalendarRepo.get_unavailable_slots(date_from, date_to)
        }
        # Pre-fetch partial-hold deductions: (date, shift, room) -> deduct_minutes
        _deduct: Dict[Tuple[str, int, str], int] = {
            (r["cal_date"], r["shift_no"], r["room_code"]): int(r["deduct_minutes"])
            for r in CalendarRepo.get_deduct_slots(date_from, date_to)
        }

        cur, d1 = _date(date_from), _date(date_to)
        while cur <= d1:
            ds = _ds(cur)
            for shift in self.shifts:
                sno = shift["shift_no"]

                # Collect active room/procs for this date/shift
                active_rps: List[Tuple[str, List[Dict]]] = []
                for room in self.rooms:
                    if (ds, sno, room) in _unavail:
                        continue
                    if crp_manager.is_held(ds, room, sno):
                        continue
                    rps = self.room_procs.get(room, [])
                    if rps:
                        active_rps.append((room, rps))

                # Distribute total shift HC among active rooms/processes
                hc_dist = self._compute_hc_distribution(
                    ds, sno, active_rps, self._last_urgency)
                self._hc_dist_cache[(ds, sno)] = hc_dist

                for room, rps in active_rps:
                    for rp in rps:
                        hc = hc_dist.get((room, rp["process_name"]), 0)
                        if rp["process_type"] == "MANUAL":
                            if hc < (rp.get("hc_min") or 1):
                                continue
                        else:
                            hc_fixed = rp.get("hc_fixed") or 1
                            if hc < hc_fixed:
                                continue
                            hc = hc_fixed
                        cap = shift_capacity_inner(rp, shift, hc)
                        # Apply partial-hold deduction (e.g. mandatory training)
                        deduct_m = _deduct.get((ds, sno, room), 0)
                        if deduct_m > 0:
                            shift_mins = shift["shift_hours"] * 60
                            cap *= max(0.0, (shift_mins - deduct_m) / shift_mins)
                        if cap > 0:
                            slot_map[(ds, room, rp["process_name"], sno)] = cap

            cur += timedelta(days=1)

        # Subtract locked plans, block their room-shifts, and seed _placed_sku
        self._placed_sku = {}
        locked_rs: Dict[Tuple[str, str, int], str] = {}
        for plan in PlanRepo.all(date_from, date_to):
            if not plan["is_locked"]:
                continue
            uom = self._entity_uom(plan)
            key: SlotKey = (plan["plan_date"], plan["room_code"],
                            plan["process_name"], plan["shift_no"])
            if key in slot_map:
                slot_map[key] = max(
                    0.0, slot_map[key] - sku_to_inner(plan["qty_planned"], uom))
            rs = (plan["plan_date"], plan["room_code"], plan["shift_no"])
            locked_rs.setdefault(rs, plan["process_name"])
            ec = plan.get("entity_code") or plan.get("sku_code", "")
            self._placed_sku[(plan["room_code"], plan["process_name"],
                              plan["plan_date"], plan["shift_no"])] = ec

        for (ds, room, sno), proc in locked_rs.items():
            self._block_room_shift(slot_map, ds, room, proc, sno)

        return slot_map

    def _earliest_start(self, so: Dict) -> date:
        due      = _date(so["due_date"])
        earliest = due - timedelta(days=self.max_pull_days)
        if so.get("start_no_earlier"):
            try:
                earliest = max(earliest, _date(so["start_no_earlier"]))
            except ValueError:
                pass
        return earliest

    def _latest_finish(self, so: Dict) -> date:
        due = _date(so["due_date"])
        sku = self.sku_map.get(so["sku_code"], {})
        return sub_workdays(due, int(sku.get("post_lead_days") or 0))

    def force_plan_so(self, so_number: str, sku_code: str, line_item: str,
                      date_from: str, date_to: str) -> Dict:
        """Force-plan a specific SO ignoring the window_closed constraint.
        Clears ALL existing unlocked plans for this SO (no date filter) so
        stale plans from previous days don't inflate the planned_qty guard.
        """
        self._reload_masters()
        so = SORepo.get(so_number, sku_code, line_item)
        if not so:
            return {"planned": 0, "skipped": 0, "late": [],
                    "routing_errors": [{"error": "SO not found"}]}
        # Clear ALL unlocked plans for this SO regardless of date
        PlanRepo.delete_unlocked_for_so(so_number, sku_code, line_item)
        slot_map = self._build_slot_map(date_from, date_to)
        report   = {"planned": 0, "skipped": 0, "late": [],
                    "routing_errors": [], "material_plans": 0}
        self._plan_so(so, slot_map, date_from, date_to, report, force=True)
        return report

    def _plan_so(self, so: Dict, slot_map: Dict[SlotKey, float],
                 date_from: str, date_to: str, report: Dict,
                 force: bool = False,
                 campaign_extra_rem: int = 0,
                 forward_fill: bool = False):
        sku_code = so["sku_code"]
        sku      = self.sku_map.get(sku_code)
        if not sku:
            report["skipped"] += 1
            return

        valid, msg = ProcessRoutingRepo.validate("SKU", sku_code)
        if not valid:
            report["routing_errors"].append(
                {"so": so["so_number"], "sku": sku_code,
                 "line": so["line_item"], "error": msg})
            report["skipped"] += 1
            return

        steps    = ProcessRoutingRepo.for_entity("SKU", sku_code)
        uom      = int(sku.get("uom") or 1)
        # production_needed = SO qty - inventory allocated - already produced
        # This ensures existing inventory is counted before scheduling production
        from data.repositories import AllocationRepo
        prod_needed = AllocationRepo.production_needed(
            so["so_number"], sku_code, so["line_item"])
        # Use final_planned_qty (is_final_seq=1 only) so multi-step SOs don't
        # over-count: a 2-step SKU with 600 units has planned_qty=1200 but
        # final_planned_qty=600, which is the correct "already planned" value.
        planned  = PlanRepo.final_planned_qty(so["so_number"], sku_code, so["line_item"])
        remaining = prod_needed - planned
        if remaining <= 0:
            return

        # FIX-2: minimum fill ratio — skip slots where available cap is a tiny
        # fraction of remaining qty (avoids collecting residual crumbs across
        # many slots).  Config key "min_slot_fill_ratio", default 0.1.
        min_fill_ratio = float(ConfigRepo.get("min_slot_fill_ratio", "0.1"))

        if force:
            # Ignore window constraints — plan from today to date_to
            d0 = _date(date_from)
            d1 = _date(date_to)
        else:
            earliest   = self._earliest_start(so)
            latest_fin = self._latest_finish(so)
            d0 = max(earliest, _date(date_from))
            d1 = min(latest_fin, _date(date_to))

            if d0 > d1:
                # Due date already passed — plan ASAP within the full horizon
                # and flag as late, but do NOT skip.
                report["late"].append({
                    "so": so["so_number"], "sku": sku_code,
                    "line": so["line_item"], "reason": "overdue",
                    "unplanned_qty": remaining})
                d0 = _date(date_from)
                d1 = _date(date_to)

        step_slots: Dict[int, List[Tuple]] = {}
        qty_to_plan = remaining

        for si in range(len(steps) - 1, -1, -1):
            step      = steps[si]
            seq       = step["process_seq"]
            is_final  = bool(step["is_final_seq"])
            allowed   = [t.strip() for t in
                         step["allowed_room_types"].split(",") if t.strip()]
            eligible  = self._rooms_for_process(step["process_name"], allowed)
            if not eligible:
                report["skipped"] += 1
                return

            shift_max = max((sh["shift_no"] for sh in self.shifts), default=1)
            if si == len(steps) - 1:
                upper_d, upper_s = d1, shift_max
            else:
                nxt = step_slots.get(steps[si+1]["process_seq"], [])
                if nxt:
                    fd, fs, _, _ = min(nxt, key=lambda x: (x[0], x[1]))
                    min_gap_s = int(steps[si+1].get("min_gap_shifts") or 0)
                    upper_d, upper_s = self._find_pre_cutoff(fd, fs, min_gap_s)
                else:
                    upper_d, upper_s = d1, shift_max

            rtp = step.get("room_type_priority") or ""
            candidates = self._candidates(
                slot_map, step["process_name"], eligible, d0, upper_d, upper_s,
                sku_code, rtp, forward=forward_fill)

            # Final step has no slots in constrained window → fall back to full
            # date range so production is always scheduled, even if it will be late.
            # deadline_d tracks the original SO deadline before any fallback extension.
            deadline_d = upper_d
            if not candidates and si == len(steps) - 1 and not force:
                report["late"].append({
                    "so": so["so_number"], "sku": sku_code,
                    "line": so["line_item"], "reason": "overdue",
                    "unplanned_qty": qty_to_plan,
                })
                d0 = _date(date_from)
                d1 = _date(date_to)
                upper_d, upper_s = d1, shift_max
                deadline_d = upper_d  # already at full range; no further overflow
                candidates = self._candidates(
                    slot_map, step["process_name"], eligible, d0, upper_d, upper_s,
                    sku_code, rtp, forward=forward_fill)

            allocated: List[Tuple] = []
            step_rem = qty_to_plan
            proc_name = step["process_name"]

            # Closing-mode: track per-shift placements so that when remaining
            # qty < total capacity across all candidate rooms for a shift,
            # we restrict to a single room (best by cap) to prevent overproduction/scrap.
            _closing_placed: Dict[Tuple[str, int], str] = {}  # (ds, sno) -> room chosen

            # Index-based loop so SKU-sticky can promote the chronologically
            # adjacent same-room slot to the next position after each placement.
            _ci = 0
            while _ci < len(candidates) and step_rem > 0:
                ds, sno, room_code, rp = candidates[_ci]

                # Closing-mode: restrict to a single best room only on the very
                # last shift of the campaign.  "Last shift" = total remaining qty
                # (this SO + future same-SKU SOs) fits within the best single
                # room's capacity for this one shift.
                _shift_key = (ds, sno)
                if _shift_key not in _closing_placed:
                    # FIX-1: use this SO's remaining qty only (not future SOs)
                    # so closing_mode fires whenever the SO fits in one slot.
                    _total_campaign_rem = step_rem
                    _best_single_cap = max(
                        (inner_to_sku(slot_map.get((_ds, _r, proc_name, _sno), 0.0), uom)
                         for _ds, _sno, _r, _ in candidates
                         if _ds == ds and _sno == sno),
                        default=0
                    )
                    _closing_mode = (
                        _best_single_cap > 0 and
                        _total_campaign_rem <= _best_single_cap
                    )
                else:
                    _closing_mode = True  # already chose one room for this shift

                if _closing_mode and _shift_key in _closing_placed:
                    if _closing_placed[_shift_key] != room_code:
                        _ci += 1
                        continue  # skip non-chosen rooms in this closing shift

                co = self._changeover_shifts.get((room_code, proc_name), 0)
                if co > 0 and self._has_changeover_conflict(
                        room_code, proc_name, ds, sno, co, sku_code):
                    _ci += 1
                    continue
                key: SlotKey = (ds, room_code, proc_name, sno)
                avail_inner = slot_map.get(key, 0.0)
                avail_sku   = inner_to_sku(avail_inner, uom)
                if avail_sku <= 0:
                    _ci += 1
                    continue
                # FIX-2: skip slots that hold only a tiny residual fraction of
                # remaining qty — prevents collecting crumbs across many slots.
                if (min_fill_ratio > 0
                        and avail_sku < step_rem * min_fill_ratio
                        and step_rem > avail_sku):
                    _ci += 1
                    continue
                qty_this = min(step_rem, avail_sku)

                if _closing_mode and _shift_key not in _closing_placed:
                    _closing_placed[_shift_key] = room_code

                PlanRepo.insert({
                    "entity_type":      "SKU",
                    "entity_code":      sku_code,
                    "so_number":        so["so_number"],
                    "sku_code":         sku_code,
                    "line_item":        so["line_item"],
                    "process_name":     proc_name,
                    "process_seq":      seq,
                    "is_final_seq":     1 if is_final else 0,
                    "room_code":        room_code,
                    "plan_date":        ds,
                    "shift_no":         sno,
                    "qty_planned":      qty_this,
                    "is_closing_shift": 1 if _closing_mode else 0,
                    "memo":             "[FINAL]" if is_final else f"[SEQ-{seq}]",
                })
                slot_map[key] = max(0.0, avail_inner - sku_to_inner(qty_this, uom))
                self._block_room_shift(slot_map, ds, room_code, proc_name, sno)
                self._record_placed(room_code, proc_name, ds, sno, sku_code)
                allocated.append((ds, sno, room_code, qty_this))
                step_rem -= qty_this
                report["planned"] += 1

                # SKU-sticky: promote the prev-shift same-room candidate so
                # the next iteration fills contiguous blocks in the same room
                # before spreading to other rooms or earlier dates.
                if step_rem > 0 and _ci + 1 < len(candidates):
                    prev_ds, prev_sno = self._prev_slot(ds, sno)
                    for _j in range(_ci + 1, len(candidates)):
                        c = candidates[_j]
                        if c[0] == prev_ds and c[1] == prev_sno and c[2] == room_code:
                            candidates.pop(_j)
                            candidates.insert(_ci + 1, c)
                            break

                _ci += 1

            # ── Forward overflow (final step only) ─────────────────────────────
            # When backward fill in [d0, deadline_d] is exhausted but capacity
            # remains past the SO deadline within the planning window, fill those
            # slots in forward order (earliest first) so that the planner can see
            # the actual expected completion date rather than seeing no plan at all.
            overflow_placed = 0
            if step_rem > 0 and is_final:
                overflow_d_start = deadline_d + timedelta(days=1)
                if overflow_d_start <= _date(date_to):
                    overflow_cands = self._candidates(
                        slot_map, proc_name, eligible,
                        overflow_d_start, _date(date_to), shift_max,
                        sku_code, rtp, forward=True)
                    _closing_placed_ov: Dict[Tuple[str, int], str] = {}
                    for ds, sno, room_code, rp in overflow_cands:
                        if step_rem <= 0:
                            break
                        _shift_key = (ds, sno)
                        if _shift_key not in _closing_placed_ov:
                            _total_campaign_rem_ov = step_rem  # FIX-1: SO-only
                            _best_ov_cap = max(
                                (inner_to_sku(slot_map.get((_ds, _r, proc_name, _sno), 0.0), uom)
                                 for _ds, _sno, _r, _ in overflow_cands
                                 if _ds == ds and _sno == sno),
                                default=0
                            )
                            _ov_closing = (
                                _best_ov_cap > 0 and
                                _total_campaign_rem_ov <= _best_ov_cap
                            )
                        else:
                            _ov_closing = True
                        if _ov_closing and _shift_key in _closing_placed_ov:
                            if _closing_placed_ov[_shift_key] != room_code:
                                continue
                        co = self._changeover_shifts.get((room_code, proc_name), 0)
                        if co > 0 and self._has_changeover_conflict(
                                room_code, proc_name, ds, sno, co, sku_code):
                            continue
                        key_ov: SlotKey = (ds, room_code, proc_name, sno)
                        avail_inner_ov = slot_map.get(key_ov, 0.0)
                        avail_sku_ov   = inner_to_sku(avail_inner_ov, uom)
                        if avail_sku_ov <= 0:
                            continue
                        # FIX-2: same crumb-skip in overflow section
                        if (min_fill_ratio > 0
                                and avail_sku_ov < step_rem * min_fill_ratio
                                and step_rem > avail_sku_ov):
                            continue
                        qty_ov = min(step_rem, avail_sku_ov)
                        if _ov_closing and _shift_key not in _closing_placed_ov:
                            _closing_placed_ov[_shift_key] = room_code
                        PlanRepo.insert({
                            "entity_type":      "SKU",
                            "entity_code":      sku_code,
                            "so_number":        so["so_number"],
                            "sku_code":         sku_code,
                            "line_item":        so["line_item"],
                            "process_name":     proc_name,
                            "process_seq":      seq,
                            "is_final_seq":     1,
                            "room_code":        room_code,
                            "plan_date":        ds,
                            "shift_no":         sno,
                            "qty_planned":      qty_ov,
                            "is_closing_shift": 1 if _ov_closing else 0,
                            "memo":             "[FINAL][LATE]",
                        })
                        slot_map[key_ov] = max(0.0, avail_inner_ov - sku_to_inner(qty_ov, uom))
                        self._block_room_shift(slot_map, ds, room_code, proc_name, sno)
                        self._record_placed(room_code, proc_name, ds, sno, sku_code)
                        allocated.append((ds, sno, room_code, qty_ov))
                        step_rem -= qty_ov
                        overflow_placed += qty_ov
                        report["planned"] += 1

            step_slots[seq] = allocated

            if is_final:
                if step_rem > 0:
                    report["late"].append({
                        "so": so["so_number"], "sku": sku_code,
                        "line": so["line_item"],
                        "unplanned_qty": step_rem,
                        "reason": "capacity_exceeded"})
                elif overflow_placed > 0:
                    # Fully planned but some/all slots land past the SO deadline.
                    report["late"].append({
                        "so": so["so_number"], "sku": sku_code,
                        "line": so["line_item"],
                        "unplanned_qty": 0,
                        "reason": "overflow_late",
                        "overflow_qty": overflow_placed})

        # ── M6: Qty Handoff Post-Pass ─────────────────────────────────────────
        # Each upstream step's actual planned qty caps the downstream step.
        # Example: step1 fills 1,500 due to capacity constraint, step2 (final)
        # fills 2,000 independently → trim step2 to 1,500 and restore capacity.
        if len(steps) > 1:
            step_actual: Dict[int, int] = {
                s["process_seq"]: sum(q for _, _, _, q in step_slots.get(s["process_seq"], []))
                for s in steps
            }
            for i in range(1, len(steps)):
                prev_seq = steps[i - 1]["process_seq"]
                curr_seq = steps[i]["process_seq"]
                prev_qty = step_actual.get(prev_seq, 0)
                curr_qty = step_actual.get(curr_seq, 0)
                if curr_qty > prev_qty:
                    excess = curr_qty - prev_qty
                    curr_proc = steps[i]["process_name"]
                    new_slots = self._trim_step_by_excess(
                        step_slots.get(curr_seq, []), excess, uom, curr_proc, slot_map)
                    step_slots[curr_seq] = new_slots
                    step_actual[curr_seq] = sum(q for _, _, _, q in new_slots)
                    PlanRepo.trim_step_excess(
                        so["so_number"], sku_code, so["line_item"], curr_seq, excess)

    # ── Material Planning ────────────────────────────────────────────────────

    def _collect_material_demands(
            self, plans: List[Dict], parent_entity_type: str
    ) -> Dict[str, List[Dict]]:
        """Extract requires_material_code demands from a list of plans.
        due_date = plan["plan_date"] so material must be ready before the
        consuming step runs (H4 fix — previously used SO due_date)."""
        demands: Dict[str, List[Dict]] = defaultdict(list)
        for plan in plans:
            ec = plan["entity_code"]
            steps = ProcessRoutingRepo.for_entity(parent_entity_type, ec)
            step = next((s for s in steps
                         if s["process_seq"] == plan["process_seq"]), None)
            if not step or not step.get("requires_material_code"):
                continue
            mat_code = step["requires_material_code"]
            # UoM conversion: parent qty × parent uom
            if parent_entity_type == "SKU":
                parent_obj = self.sku_map.get(ec, {})
            else:
                parent_obj = self.mat_map.get(ec, {})
            uom = int(parent_obj.get("uom") or 1)
            demands[mat_code].append({
                "due_date":  plan["plan_date"],   # H4: consuming step date, not SO date
                "qty":       plan["qty_planned"] * uom,
                "so_number": plan.get("so_number", ""),
                "sku_code":  plan.get("sku_code", ""),
                "line_item": plan.get("line_item", ""),
            })
        return demands

    def _plan_all_materials(self, slot_map: Dict[SlotKey, float],
                             date_from: str, date_to: str, report: Dict):
        """
        Multi-level BOM expansion: plan materials required by SKU steps,
        then recursively plan sub-materials required by those material steps.
        Stops when no new demand is found or MAX_LEVELS is reached.
        """
        PlanRepo.delete_unlocked_material(date_from, date_to)

        MAX_LEVELS = 6
        planned_codes: set = set()

        # Level 0: demands from SKU plans
        sku_plans = PlanRepo.all(date_from, date_to, entity_type="SKU")
        pending = self._collect_material_demands(sku_plans, "SKU")

        for _level in range(MAX_LEVELS):
            if not pending:
                break
            next_pending: Dict[str, List[Dict]] = defaultdict(list)
            for mat_code, demand_list in pending.items():
                if mat_code in planned_codes:
                    continue
                planned_codes.add(mat_code)
                self._plan_material(mat_code, demand_list, slot_map,
                                    date_from, date_to, report)
                # Collect sub-material demands from newly inserted material plans
                new_mat_plans = [p for p in PlanRepo.all(date_from, date_to, entity_type="MATERIAL")
                                 if p["entity_code"] == mat_code]
                sub = self._collect_material_demands(new_mat_plans, "MATERIAL")
                for sub_code, sub_demands in sub.items():
                    if sub_code not in planned_codes:
                        next_pending[sub_code].extend(sub_demands)
            pending = next_pending

    def _plan_material(self, mat_code: str, demands: List[Dict],
                        slot_map: Dict[SlotKey, float],
                        date_from: str, date_to: str, report: Dict):
        mat = self.mat_map.get(mat_code)
        if not mat:
            report["routing_errors"].append(
                {"material": mat_code, "error": "not in material_master"})
            return

        valid, msg = ProcessRoutingRepo.validate("MATERIAL", mat_code)
        if not valid:
            report["routing_errors"].append(
                {"material": mat_code, "error": msg})
            return

        # Sort demands by due_date, then merge within mat_due_merge_days
        demands_sorted = sorted(demands, key=lambda d: d["due_date"])
        groups = self._merge_material_demands(demands_sorted)

        for group in groups:
            group_id   = str(uuid.uuid4())[:8].upper()
            total_qty  = sum(d["qty"] for d in group)
            # Earliest due in group → material must be ready before this
            earliest_due = min(_date(d["due_date"]) for d in group)
            post_lead    = int(mat.get("post_lead_days") or 0)
            # Latest finish = earliest_due - post_lead_days
            mat_latest   = sub_workdays(earliest_due, post_lead)
            # Earliest start = mat_latest - max_pull_days
            mat_earliest = mat_latest - timedelta(days=self.max_pull_days)
            mat_earliest = max(mat_earliest, _date(date_from))

            # Record demand group
            for d in group:
                MaterialDemandRepo.insert_group_member({
                    "group_id":      group_id,
                    "material_code": mat_code,
                    "so_number":     d["so_number"],
                    "sku_code":      d["sku_code"],
                    "line_item":     d["line_item"],
                    "due_date":      d["due_date"],
                    "qty_required":  d["qty"],
                })

            # Plan the material using its own routing
            self._place_material_plans(
                mat_code, mat, total_qty, group_id,
                mat_earliest, mat_latest,
                slot_map, date_from, date_to, report)

    def _merge_material_demands(
            self, demands: List[Dict]) -> List[List[Dict]]:
        """
        Group demands whose due dates are within mat_due_merge_days of
        the first demand in the group.
        """
        if not demands:
            return []
        groups:  List[List[Dict]] = []
        current: List[Dict]       = [demands[0]]
        anchor   = _date(demands[0]["due_date"])

        for d in demands[1:]:
            diff = (_date(d["due_date"]) - anchor).days
            if diff <= self.mat_due_merge_days:
                current.append(d)
            else:
                groups.append(current)
                current = [d]
                anchor  = _date(d["due_date"])
        groups.append(current)
        return groups

    def _place_material_plans(self, mat_code: str, mat: Dict,
                               total_qty: int, group_id: str,
                               earliest: date, latest: date,
                               slot_map: Dict[SlotKey, float],
                               date_from: str, date_to: str,
                               report: Dict):
        steps    = ProcessRoutingRepo.for_entity("MATERIAL", mat_code)
        uom      = int(mat.get("uom") or 1)
        step_slots: Dict[int, List[Tuple]] = {}

        for si in range(len(steps) - 1, -1, -1):
            step     = steps[si]
            seq      = step["process_seq"]
            is_final = bool(step["is_final_seq"])
            allowed  = [t.strip() for t in
                        step["allowed_room_types"].split(",") if t.strip()]
            eligible = self._rooms_for_process(step["process_name"], allowed)
            if not eligible:
                return

            shift_max = max((sh["shift_no"] for sh in self.shifts), default=1)
            if si == len(steps) - 1:
                upper_d = min(latest, _date(date_to))
                upper_s = shift_max
            else:
                nxt = step_slots.get(steps[si+1]["process_seq"], [])
                if nxt:
                    fd, fs, _, _ = min(nxt, key=lambda x: (x[0], x[1]))
                    min_gap_s = int(steps[si+1].get("min_gap_shifts") or 0)
                    upper_d, upper_s = self._find_pre_cutoff(fd, fs, min_gap_s)
                else:
                    upper_d = min(latest, _date(date_to))
                    upper_s = shift_max

            d0_eff = max(earliest, _date(date_from))
            candidates = self._candidates(
                slot_map, step["process_name"], eligible,
                d0_eff, upper_d, upper_s, mat_code,
                step.get("room_type_priority") or "")

            allocated: List[Tuple] = []
            step_rem = total_qty
            proc_name = step["process_name"]
            for ds, sno, room_code, rp in candidates:
                if step_rem <= 0:
                    break
                co = self._changeover_shifts.get((room_code, proc_name), 0)
                if co > 0 and self._has_changeover_conflict(
                        room_code, proc_name, ds, sno, co, mat_code):
                    continue
                key: SlotKey = (ds, room_code, proc_name, sno)
                avail_inner = slot_map.get(key, 0.0)
                avail_qty   = inner_to_sku(avail_inner, uom)
                if avail_qty <= 0:
                    continue
                qty_this = min(step_rem, avail_qty)
                PlanRepo.insert({
                    "entity_type":       "MATERIAL",
                    "entity_code":       mat_code,
                    "so_number":         "",
                    "sku_code":          "",
                    "line_item":         "",
                    "process_name":      proc_name,
                    "process_seq":       seq,
                    "is_final_seq":      1 if is_final else 0,
                    "room_code":         room_code,
                    "plan_date":         ds,
                    "shift_no":          sno,
                    "qty_planned":       qty_this,
                    "material_group_id": group_id,
                    "memo": (f"[MAT-FINAL:{mat_code}]"
                             if is_final else f"[MAT:{mat_code} SEQ-{seq}]"),
                })
                slot_map[key] = max(0.0, avail_inner - sku_to_inner(qty_this, uom))
                self._block_room_shift(slot_map, ds, room_code, proc_name, sno)
                self._record_placed(room_code, proc_name, ds, sno, mat_code)
                allocated.append((ds, sno, room_code, qty_this))
                step_rem -= qty_this
                report["material_plans"] += 1

            step_slots[seq] = allocated

    # ── Shared slot utilities ────────────────────────────────────────────────

    def _trim_step_by_excess(
            self,
            slots: List[Tuple],
            excess_qty: int,
            uom: int,
            proc_name: str,
            slot_map: Dict[SlotKey, float],
    ) -> List[Tuple]:
        """Remove `excess_qty` from the end of `slots` (overflow/earliest first),
        restoring freed capacity back into slot_map.  Returns the updated list."""
        to_trim = excess_qty
        result: List[Tuple] = []
        for slot in reversed(slots):
            ds, sno, rc, qty = slot
            if to_trim <= 0:
                result.insert(0, slot)
                continue
            if qty <= to_trim:
                # Remove this slot entirely; restore capacity.
                key: SlotKey = (ds, rc, proc_name, sno)
                slot_map[key] = slot_map.get(key, 0.0) + sku_to_inner(qty, uom)
                to_trim -= qty
            else:
                # Partial trim.
                key = (ds, rc, proc_name, sno)
                slot_map[key] = slot_map.get(key, 0.0) + sku_to_inner(to_trim, uom)
                result.insert(0, (ds, sno, rc, qty - to_trim))
                to_trim = 0
        return result

    def _candidates(self, slot_map, process_name, eligible_rooms,
                    d0, d_upper, shift_upper, sku_code: str = "",
                    room_type_priority: str = "", forward: bool = False):
        eligible_codes = {r["room_code"] for r in eligible_rooms}
        rp_lookup      = {r["room_code"]: r for r in eligible_rooms}
        raw = []
        for (ds, room, proc, sno), cap in slot_map.items():
            if proc != process_name:
                continue
            if room not in eligible_codes:
                continue
            d = _date(ds)
            if d < d0 or d > d_upper:
                continue
            if d == d_upper and sno > shift_upper:
                continue
            if cap <= 0:
                continue
            raw.append((ds, sno, room, rp_lookup[room]))

        # Room type rank: lower = tried first within the same (date, shift).
        # Explicit priority list overrides the automatic exclusivity heuristic.
        if room_type_priority:
            priority_list = [t.strip() for t in room_type_priority.split(",") if t.strip()]
            def _rt_rank(room_type: str) -> int:
                try:
                    return priority_list.index(room_type)
                except ValueError:
                    return len(priority_list)
        else:
            def _rt_rank(room_type: str) -> int:
                # More exclusive room types (fewer SKUs allowed) come first.
                return self._room_type_exclusivity.get(room_type, 0)

        # forward=True: earliest date first (overflow / late planning).
        # forward=False (default): latest date first (backward fill).
        date_sign = 1 if forward else -1
        sno_sign  = 1 if forward else -1

        if self.assign_mode == "UPH":
            def uph_key(item):
                ds, sno, room, rp = item
                hc = self._hc_dist_cache.get((ds, sno), {}).get(
                    (room, process_name), 0)
                adj = (1 if sku_code and self._is_same_sku_adjacent(
                           room, process_name, ds, sno, sku_code) else 0)
                rt  = _rt_rank(rp.get("room_type", ""))
                return (date_sign * _date(ds).toordinal(), sno_sign * sno, -adj, rt, -calc_uph(rp, hc))
            raw.sort(key=uph_key)
        else:
            def cap_key(item):
                ds, sno, room, rp = item
                c   = slot_map.get((ds, room, process_name, sno), 0.0)
                adj = (1 if sku_code and self._is_same_sku_adjacent(
                           room, process_name, ds, sno, sku_code) else 0)
                rt  = _rt_rank(rp.get("room_type", ""))
                return (date_sign * _date(ds).toordinal(), sno_sign * sno, -adj, rt, -c)
            raw.sort(key=cap_key)
        return raw

    def _block_room_shift(self, slot_map: Dict[SlotKey, float],
                           date_str: str, room_code: str,
                           process_name: str, shift_no: int):
        """Zero out all OTHER processes for a given room+date+shift.
        Enforces the one-process-per-room-per-shift constraint."""
        for key in list(slot_map.keys()):
            kd, kr, kp, ks = key
            if kd == date_str and kr == room_code and ks == shift_no and kp != process_name:
                slot_map[key] = 0.0

    def _next_slot(self, ds: str, sno: int) -> Tuple[str, int]:
        """Return (date_str, shift_no) of the shift immediately after (ds, sno)."""
        sns = sorted(s["shift_no"] for s in self.shifts)
        if not sns or sno not in sns:
            return ds, sno
        idx = sns.index(sno)
        if idx + 1 < len(sns):
            return ds, sns[idx + 1]
        return str(_date(ds) + timedelta(days=1)), sns[0]

    def _prev_slot(self, ds: str, sno: int) -> Tuple[str, int]:
        """Return (date_str, shift_no) of the shift immediately before (ds, sno)."""
        sns = sorted(s["shift_no"] for s in self.shifts)
        if not sns or sno not in sns:
            return ds, sno
        idx = sns.index(sno)
        if idx > 0:
            return ds, sns[idx - 1]
        return _ds(_date(ds) - timedelta(days=1)), sns[-1]

    def _has_changeover_conflict(self, room_code: str, process_name: str,
                                  ds: str, sno: int,
                                  co_shifts: int, entity_code: str) -> bool:
        """Return True if placing entity_code at (ds, sno) violates the
        changeover gap — i.e., a different entity is already placed within
        the next co_shifts slots in the same room/process."""
        cur_ds, cur_sno = ds, sno
        for _ in range(co_shifts):
            cur_ds, cur_sno = self._next_slot(cur_ds, cur_sno)
            placed = self._placed_sku.get((room_code, process_name, cur_ds, cur_sno))
            if placed and placed != entity_code:
                return True
        return False

    def _record_placed(self, room_code: str, process_name: str,
                        ds: str, sno: int, entity_code: str):
        """Record a newly placed block so future changeover checks can see it."""
        self._placed_sku[(room_code, process_name, ds, sno)] = entity_code

    def _find_pre_cutoff(self, post_date_str: str, post_shift_no: int,
                          min_gap_shifts: int) -> Tuple[date, int]:
        """
        Return the latest (date, shift_no) a pre-process step may occupy,
        given that the post-process starts at (post_date_str, post_shift_no)
        and min_gap_shifts empty shifts must lie between them.

        Goes back (1 + min_gap_shifts) shift indices from the post-process:
          min_gap_shifts=0 → immediately preceding shift (adjacent OK)
          min_gap_shifts=1 → one empty shift gap between pre and post
          min_gap_shifts=2 → two empty shifts gap (≈ 1 day in a 2-shift system)
        """
        sns = sorted(s["shift_no"] for s in self.shifts)
        if not sns:
            return _date(post_date_str), post_shift_no
        d   = _date(post_date_str)
        sno = post_shift_no
        for _ in range(1 + min_gap_shifts):
            idx = sns.index(sno) if sno in sns else 0
            if idx > 0:
                sno = sns[idx - 1]
            else:
                d  -= timedelta(days=1)
                sno = sns[-1]
        return d, sno

    def _slot_capacity_inner(self, date_str: str, room_code: str,
                              process_name: str, shift_no: int) -> float:
        rp_list = [r for r in self.room_procs.get(room_code, [])
                   if r["process_name"] == process_name]
        if not rp_list:
            return 0.0
        sh_list = [s for s in self.shifts if s["shift_no"] == shift_no]
        if not sh_list:
            return 0.0
        hc = self.get_slot_hc(date_str, room_code, process_name, shift_no)
        return shift_capacity_inner(rp_list[0], sh_list[0], hc)

    def get_shift_hc_total(self, date_str: str, shift_no: int) -> tuple:
        """Returns (total_allocated_hc, crp_total_hc) for a shift.
        total_allocated = sum of HC distributed across all rooms.
        crp_total = raw HC entered in CRP Excel.
        """
        if (date_str, shift_no) not in self._hc_dist_cache:
            active_rps: List[Tuple[str, List[Dict]]] = []
            for room in self.rooms:
                cal = CalendarRepo.get_slot(date_str, shift_no, room)
                if cal and (not cal["is_open"] or cal["is_hold"]):
                    continue
                if crp_manager.is_held(date_str, room, shift_no):
                    continue
                rps = self.room_procs.get(room, [])
                if rps:
                    active_rps.append((room, rps))
            hc_dist = self._compute_hc_distribution(
                date_str, shift_no, active_rps, self._last_urgency)
            self._hc_dist_cache[(date_str, shift_no)] = hc_dist
        total_alloc = sum(self._hc_dist_cache[(date_str, shift_no)].values())
        crp_total = crp_manager.get_total_hc(date_str, shift_no)
        return (total_alloc, crp_total)

    def get_shift_hc_filtered(self, date_str: str, shift_no: int,
                               planned_combos: set) -> tuple:
        """Returns (allocated_hc, crp_total_hc) for a shift, counting only
        (room, process) pairs present in planned_combos (rooms with actual plans).
        Used for Gantt HC bar and Labor Utilization to stay in sync.
        """
        active_rps: List[Tuple[str, List[Dict]]] = []
        for room in self.rooms:
            cal = CalendarRepo.get_slot(date_str, shift_no, room)
            if cal and (not cal["is_open"] or cal["is_hold"]):
                continue
            if crp_manager.is_held(date_str, room, shift_no):
                continue
            rps = [rp for rp in self.room_procs.get(room, [])
                   if (room, rp["process_name"]) in planned_combos]
            if rps:
                active_rps.append((room, rps))
        hc_dist = self._compute_hc_distribution(
            date_str, shift_no, active_rps, self._last_urgency)
        total_alloc = sum(hc_dist.values())
        crp_total = crp_manager.get_total_hc(date_str, shift_no)
        return (total_alloc, crp_total)

    def get_slot_hc(self, date_str: str, room_code: str,
                    process_name: str, shift_no: int) -> int:
        """Return distributed HC for a specific room/process/shift slot."""
        if (date_str, shift_no) not in self._hc_dist_cache:
            active_rps: List[Tuple[str, List[Dict]]] = []
            for room in self.rooms:
                cal = CalendarRepo.get_slot(date_str, shift_no, room)
                if cal and (not cal["is_open"] or cal["is_hold"]):
                    continue
                if crp_manager.is_held(date_str, room, shift_no):
                    continue
                rps = self.room_procs.get(room, [])
                if rps:
                    active_rps.append((room, rps))
            hc_dist = self._compute_hc_distribution(
                date_str, shift_no, active_rps, self._last_urgency)
            self._hc_dist_cache[(date_str, shift_no)] = hc_dist
        return self._hc_dist_cache[(date_str, shift_no)].get(
            (room_code, process_name), 0)

    def _compute_hc_distribution(self, date_str: str, shift_no: int,
                                  active_rps: List[Tuple[str, List[Dict]]],
                                  urgency: Dict[str, float]) -> Dict[Tuple[str,str], int]:
        """
        Distribute total CRP shift HC among active rooms/processes.

        Phase 1 — AUTO rooms get their hc_fixed first.
        Phase 2 — MANUAL rooms: sorted by process urgency (SO 납기 기반),
                  highest-urgency rooms staffed first up to hc_max.
        """
        total_hc = crp_manager.get_total_hc(date_str, shift_no)
        if total_hc <= 0:
            return {}

        remaining = total_hc
        result: Dict[Tuple[str,str], int] = {}

        # Phase 1: AUTO processes need exactly hc_fixed
        for room, rps in active_rps:
            for rp in rps:
                if rp["process_type"] != "AUTO":
                    continue
                hc_fixed = int(rp.get("hc_fixed") or 1)
                if remaining >= hc_fixed:
                    result[(room, rp["process_name"])] = hc_fixed
                    remaining -= hc_fixed

        # Phase 2: MANUAL — sort by urgency (highest first), then distribute
        manual = [(room, rp) for room, rps in active_rps
                  for rp in rps if rp["process_type"] == "MANUAL"]
        if not manual or remaining <= 0:
            return result

        manual.sort(key=lambda x: urgency.get(x[1]["process_name"], 0.0),
                    reverse=True)

        total_min = sum(int(rp.get("hc_min") or 1) for _, rp in manual)

        if remaining >= total_min:
            # Assign hc_min to every MANUAL room first
            for room, rp in manual:
                result[(room, rp["process_name"])] = int(rp.get("hc_min") or 1)
            remaining -= total_min
            # Give extra HC to highest-urgency rooms up to hc_max
            if remaining > 0:
                for room, rp in manual:
                    if remaining <= 0:
                        break
                    hc_max = int(rp.get("hc_max") or 999)
                    hc_min = int(rp.get("hc_min") or 1)
                    extra = min(remaining, hc_max - hc_min)
                    if extra > 0:
                        result[(room, rp["process_name"])] += extra
                        remaining -= extra
        else:
            # Not enough for all minimums — staff highest-urgency rooms first
            for room, rp in manual:
                if remaining <= 0:
                    break
                hc_min = int(rp.get("hc_min") or 1)
                alloc = min(remaining, hc_min)
                result[(room, rp["process_name"])] = alloc
                remaining -= alloc

        return result

    def _compute_urgency(self) -> Dict[str, float]:
        """Urgency score per process_name based on open SO due dates.
        Higher score = more urgent. Late SOs get score 1000, otherwise 100/days_remaining.
        """
        today = date.today()
        urgency: Dict[str, float] = {}
        sku_steps: Dict[str, List[str]] = {}

        for so in SORepo.all(status="OPEN"):
            due = _date(so["due_date"])
            days = (due - today).days
            score = 1000.0 if days < 0 else (100.0 / max(days, 1))

            sku = so["sku_code"]
            if sku not in sku_steps:
                steps = ProcessRoutingRepo.for_entity("SKU", sku)
                sku_steps[sku] = [s["process_name"] for s in steps]

            for proc in sku_steps.get(sku, []):
                urgency[proc] = max(urgency.get(proc, 0.0), score)

        return urgency

    def compute_hc_distribution_preview(self, date_from: str, date_to: str) -> Dict:
        """Returns {(date_str, shift_no): {(room, proc): hc}} for UI preview.

        Only rooms/processes with actual production plans in each slot receive HC.
        """
        self._reload_masters()
        urgency = self._compute_urgency()
        result: Dict[Tuple[str,int], Dict[Tuple[str,str], int]] = {}

        # Pre-load all plans in range once → {(date, shift): {(room, proc)}}
        planned_combos: dict = {}
        for p in PlanRepo.all(date_from, date_to):
            key = (p["plan_date"], p["shift_no"])
            planned_combos.setdefault(key, set()).add(
                (p["room_code"], p["process_name"]))

        cur, d1 = _date(date_from), _date(date_to)
        while cur <= d1:
            ds = _ds(cur)
            for shift in self.shifts:
                sno = shift["shift_no"]
                has_plans = planned_combos.get((ds, sno), set())

                active_rps: List[Tuple[str, List[Dict]]] = []
                for room in self.rooms:
                    cal = CalendarRepo.get_slot(ds, sno, room)
                    if cal and (not cal["is_open"] or cal["is_hold"]):
                        continue
                    if crp_manager.is_held(ds, room, sno):
                        continue
                    # Only include processes that have actual plans in this slot
                    rps = [rp for rp in self.room_procs.get(room, [])
                           if (room, rp["process_name"]) in has_plans]
                    if rps:
                        active_rps.append((room, rps))

                hc_dist = self._compute_hc_distribution(
                    ds, sno, active_rps, urgency)
                if hc_dist:
                    result[(ds, sno)] = hc_dist
            cur += timedelta(days=1)
        return result

    # ── Capacity Analysis helpers ─────────────────────────────────────────────

    def compute_required_hc_from_plans(self, date_from: str, date_to: str) -> Dict[str, Dict[int, float]]:
        """Required HC per (date, shift) from current production plans (MANUAL processes only).
        Formula: qty_inner / (UPPH × shift_hours) per plan entry."""
        self._reload_masters()
        rp_lookup: Dict[Tuple[str, str], Dict] = {
            (room, rp["process_name"]): rp
            for room, rps in self.room_procs.items()
            for rp in rps
        }
        shift_hrs = {s["shift_no"]: _shift_hours(s) for s in self.shifts}
        result: Dict[str, Dict[int, float]] = {}
        for plan in PlanRepo.all(date_from, date_to):
            rp = rp_lookup.get((plan["room_code"], plan["process_name"]))
            if not rp or rp["process_type"] != "MANUAL":
                continue
            upph = float(rp.get("upph") or 0)
            if upph <= 0:
                continue
            sh   = shift_hrs.get(plan["shift_no"], 12.0)
            uom  = self._entity_uom(plan)
            req  = sku_to_inner(plan["qty_planned"], uom) / (upph * sh)
            ds, sno = plan["plan_date"], plan["shift_no"]
            result.setdefault(ds, {})[sno] = result.get(ds, {}).get(sno, 0.0) + req
        return result

    def get_available_hc_by_date(self, date_from: str, date_to: str) -> Dict[str, Dict[int, int]]:
        """Total HC per (date, shift) from CRP Excel."""
        d0, d1 = _date(date_from), _date(date_to)
        result: Dict[str, Dict[int, int]] = {}
        for (ds, sno), hc in crp_manager.get_all().items():
            if d0 <= _date(ds) <= d1:
                result.setdefault(ds, {})[sno] = int(hc)
        return result

    def compute_line_utilization(self, date_from: str, date_to: str) -> Dict:
        """
        Returns dict with:
          available: {date: int}   — open room-shift slot count per day
          used:      {date: int}   — room-shift slots used in current plans
          max_cap:   {date: float} — theoretical max capacity (hc_max) in inner units
          planned:   {date: float} — planned SKU units per day
        """
        self._reload_masters()
        available: Dict[str, int]   = {}
        max_cap:   Dict[str, float] = {}
        cur, end_dt = _date(date_from), _date(date_to)
        while cur <= end_dt:
            ds   = _ds(cur)
            slots = 0
            cap   = 0.0
            for room in self.rooms:
                for shift in self.shifts:
                    sno  = shift["shift_no"]
                    slot = CalendarRepo.get_slot(ds, sno, room)
                    if slot and (not slot["is_open"] or slot["is_hold"]):
                        continue
                    if crp_manager.is_held(ds, room, sno):
                        continue
                    slots += 1
                    for rp in self.room_procs.get(room, []):
                        hc_use = int(rp.get("hc_max") or rp.get("hc_fixed") or 0)
                        cap += shift_capacity_inner(rp, shift, hc_use)
            available[ds] = slots
            max_cap[ds]   = cap
            cur += timedelta(days=1)
        plans = PlanRepo.all(date_from, date_to)
        used_set: Dict[str, set] = {}
        planned:  Dict[str, float] = {}
        for plan in plans:
            ds = plan["plan_date"]
            used_set.setdefault(ds, set()).add((plan["room_code"], plan["shift_no"]))
            if plan["entity_type"] == "SKU":
                planned[ds] = planned.get(ds, 0.0) + plan["qty_planned"]
        return {
            "available": available,
            "used":      {ds: len(s) for ds, s in used_set.items()},
            "max_cap":   max_cap,
            "planned":   planned,
        }

    def _find_earlier_slot(self, plan, slot_map, earliest, date_from):
        plan_date    = _date(plan["plan_date"])
        uom          = self._entity_uom(plan)
        needed_inner = sku_to_inner(plan["qty_planned"], uom)
        candidates   = sorted(
            [(ds, room, proc, sno)
             for (ds, room, proc, sno) in slot_map.keys()
             if (room == plan["room_code"]
                 and proc == plan["process_name"]
                 and _date(ds) < plan_date
                 and _date(ds) >= earliest
                 and _date(ds) >= _date(date_from))],
            key=lambda x: (x[0], x[3]))
        for ds, room, proc, sno in candidates:
            if slot_map.get((ds, room, proc, sno), 0) >= needed_inner:
                return ds, sno, room
        return None

    def _find_earlier_slot_pf(
        self, plan, slot_map: Dict, cap_for_util: Dict,
        util_thresh: float, lookahead_days: int,
        earliest: date, date_from: str,
    ):
        """Pull-forward variant: adds utilization threshold + lookahead window."""
        plan_date    = _date(plan["plan_date"])
        uom          = self._entity_uom(plan)
        needed_inner = sku_to_inner(plan["qty_planned"], uom)
        d_from       = _date(date_from)
        candidates   = sorted(
            [(ds, room, proc, sno)
             for (ds, room, proc, sno) in slot_map.keys()
             if (room == plan["room_code"]
                 and proc == plan["process_name"]
                 and _date(ds) < plan_date
                 and _date(ds) >= earliest
                 and _date(ds) >= d_from
                 # Lookahead: max movement distance is lookahead_days
                 and (plan_date - _date(ds)).days <= lookahead_days)],
            key=lambda x: (x[0], x[3]))
        for ds, room, proc, sno in candidates:
            key      = (ds, room, proc, sno)
            remaining = slot_map.get(key, 0)
            if remaining < needed_inner:
                continue
            # Utilization check: only pull into under-utilized slots
            total_cap = cap_for_util.get(key, 0)
            if total_cap > 0:
                current_util = (total_cap - remaining) / total_cap * 100
                if current_util >= util_thresh:
                    continue
            return ds, sno, room
        return None

    def _entity_uom(self, plan: Dict) -> int:
        if plan["entity_type"] == "SKU":
            return int(self.sku_map.get(
                plan["sku_code"], {}).get("uom", 1) or 1)
        return int(self.mat_map.get(
            plan["entity_code"], {}).get("uom", 1) or 1)

    def compute_hc_utilization_by_date(self, date_from: str, date_to: str) -> Dict[str, float]:
        """Returns {date_str: hc_util_pct} where pct = required_hc / available_hc * 100."""
        req = self.compute_required_hc_from_plans(date_from, date_to)
        avl = self.get_available_hc_by_date(date_from, date_to)
        result: Dict[str, float] = {}
        cur, end = _date(date_from), _date(date_to)
        while cur <= end:
            ds = _ds(cur)
            r = sum(req.get(ds, {}).values())
            a = sum(avl.get(ds, {}).values())
            result[ds] = (r / a * 100) if a > 0 else 0.0  # uncapped: can exceed 100
            cur += timedelta(days=1)
        return result

    def compute_active_line_capacity(self, date_from: str, date_to: str) -> Dict[str, float]:
        """Capacity of rooms that have at least one plan, per day (inner units)."""
        self._reload_masters()
        plans = PlanRepo.all(date_from, date_to)
        active_rooms: Dict[str, set] = {}
        for plan in plans:
            active_rooms.setdefault(plan["plan_date"], set()).add(plan["room_code"])
        result: Dict[str, float] = {}
        for ds, rooms in active_rooms.items():
            cap = 0.0
            for room in rooms:
                for shift in self.shifts:
                    for rp in self.room_procs.get(room, []):
                        hc_use = int(rp.get("hc_max") or rp.get("hc_fixed") or 0)
                        cap += shift_capacity_inner(rp, shift, hc_use)
            result[ds] = cap
        return result

    def detect_bottlenecks(self, date_from: str, date_to: str) -> List[Dict]:
        """
        Find MANUAL rooms/processes constraining production.
        Returns list of {room_code, process_name, unmet_qty, affected_so_count, recommended_hc}
        sorted by unmet_qty descending.
        """
        self._reload_masters()
        shift_hrs = {s["shift_no"]: _shift_hours(s) for s in self.shifts}
        avl_hc = self.get_available_hc_by_date(date_from, date_to)

        rp_lookup: Dict[Tuple[str, str], Dict] = {
            (room, rp["process_name"]): rp
            for room, rps in self.room_procs.items()
            for rp in rps
        }

        # Find LATE SOs (final seq plan date > due_date - post_lead_days)
        sku_repo_all = {s["sku_code"]: s for s in SKURepo.all()}
        so_all = {(s["so_number"], s["sku_code"], s["line_item"]): s for s in SORepo.all()}
        plans = PlanRepo.all(date_from, date_to)

        # Group final-seq plans by SO
        final_plans: Dict[Tuple, List[Dict]] = {}
        all_plans: Dict[Tuple, List[Dict]] = {}
        for plan in plans:
            key = (plan["so_number"], plan["sku_code"], plan["line_item"])
            all_plans.setdefault(key, []).append(plan)
            if plan.get("is_final_seq"):
                final_plans.setdefault(key, []).append(plan)

        # Find LATE and UNPLANNED demand per room/process
        room_proc_unmet: Dict[Tuple[str, str], Dict] = {}

        for key, so in so_all.items():
            if so["status"] not in ("OPEN", "HOLD"):
                continue
            sku = sku_repo_all.get(so["sku_code"])
            if not sku:
                continue
            post_lead = int(sku.get("post_lead_days") or 0)
            cutoff = _ds(sub_workdays(_date(so["due_date"]), post_lead))

            fps = final_plans.get(key, [])
            last_final = max((p["plan_date"] for p in fps), default=None)
            is_late = last_final and last_final > cutoff

            if not (is_late or not fps):
                continue  # on time, skip

            # Find which room/process for final seq
            aps = all_plans.get(key, [])
            for plan in aps:
                rk = (plan["room_code"], plan["process_name"])
                rp = rp_lookup.get(rk)
                if not rp or rp.get("process_type") != "MANUAL":
                    continue
                upph = float(rp.get("upph") or 0)
                if upph <= 0:
                    continue
                uom = self._entity_uom(plan)
                unmet = sku_to_inner(plan["qty_planned"], uom)
                if rk not in room_proc_unmet:
                    room_proc_unmet[rk] = {"unmet_qty": 0.0, "so_keys": set()}
                room_proc_unmet[rk]["unmet_qty"] += unmet
                room_proc_unmet[rk]["so_keys"].add(key)

        # Also add unplanned demand
        for key, so in so_all.items():
            if so["status"] not in ("OPEN", "HOLD"):
                continue
            needed = AllocationRepo.production_needed(
                so["so_number"], so["sku_code"], so["line_item"])
            if needed <= 0:
                continue
            planned = PlanRepo.planned_qty(
                so["so_number"], so["sku_code"], so["line_item"])
            if planned >= needed:
                continue
            routing = ProcessRoutingRepo.for_entity("SKU", so["sku_code"])
            for step in routing:
                for allowed_type in (step.get("allowed_room_types") or "").split(","):
                    allowed_type = allowed_type.strip()
                    for room in self.rooms:
                        if not any(rp.get("room_type") == allowed_type
                                   for rp in self.room_procs.get(room, [])):
                            continue
                        for rp in self.room_procs.get(room, []):
                            if rp.get("process_name") != step["process_name"]:
                                continue
                            if rp.get("process_type") != "MANUAL":
                                continue
                            rk = (room, step["process_name"])
                            uom = int(sku_repo_all.get(so["sku_code"], {}).get("uom") or 1)
                            shortfall = sku_to_inner(needed - planned, uom)
                            if rk not in room_proc_unmet:
                                room_proc_unmet[rk] = {"unmet_qty": 0.0, "so_keys": set()}
                            room_proc_unmet[rk]["unmet_qty"] += shortfall
                            room_proc_unmet[rk]["so_keys"].add(key)
                        break

        # Build result sorted by unmet_qty
        result = []
        for (room, proc), info in sorted(
                room_proc_unmet.items(), key=lambda x: -x[1]["unmet_qty"]):
            rp = rp_lookup.get((room, proc))
            upph = float(rp.get("upph") or 1) if rp else 1.0
            total_shift_hrs = sum(shift_hrs.values()) * max(1, (
                (_date(date_to) - _date(date_from)).days + 1))
            rec_hc = int(info["unmet_qty"] / (upph * total_shift_hrs)) + 1
            result.append({
                "room_code":         room,
                "process_name":      proc,
                "unmet_qty":         info["unmet_qty"],
                "affected_so_count": len(info["so_keys"]),
                "recommended_hc":    rec_hc,
            })
        return result[:5]  # top 5 bottlenecks

    def simulate_hc_scenario(self, date_from: str, date_to: str,
                              bottlenecks: List[Dict], hc_added: int) -> Dict:
        """
        Estimate impact of adding hc_added people to bottleneck rooms.
        Returns {late_before, late_after, resolved_sos: [so_number], detail: {room: extra_cap}}
        """
        self._reload_masters()
        shift_hrs = {s["shift_no"]: _shift_hours(s) for s in self.shifts}
        sku_repo_all = {s["sku_code"]: s for s in SKURepo.all()}
        so_all = list(filter(lambda s: s["status"] in ("OPEN", "HOLD"), SORepo.all()))

        rp_lookup: Dict[Tuple[str, str], Dict] = {
            (room, rp["process_name"]): rp
            for room, rps in self.room_procs.items()
            for rp in rps
        }

        # Compute extra capacity from added HC, distributed across bottleneck rooms
        n_bottlenecks = max(1, len(bottlenecks))
        hc_per_room = max(1, hc_added // n_bottlenecks)
        days = max(1, (_date(date_to) - _date(date_from)).days + 1)
        n_shifts = max(1, len(self.shifts))
        extra_cap_by_room: Dict[str, float] = {}
        detail: Dict[str, int] = {}
        for bn in bottlenecks:
            rk = (bn["room_code"], bn["process_name"])
            rp = rp_lookup.get(rk)
            if not rp:
                continue
            upph = float(rp.get("upph") or 0)
            avg_shift_h = sum(shift_hrs.values()) / n_shifts if shift_hrs else 12.0
            extra_cap = upph * hc_per_room * avg_shift_h * days * n_shifts
            extra_cap_by_room[bn["room_code"]] = (
                extra_cap_by_room.get(bn["room_code"], 0) + extra_cap)
            detail[bn["room_code"]] = hc_per_room

        # Find current LATE SOs
        plans = PlanRepo.all(date_from, date_to)
        final_by_so: Dict[Tuple, str] = {}
        planned_qty_by_so: Dict[Tuple, float] = {}
        for plan in plans:
            key = (plan["so_number"], plan["sku_code"], plan["line_item"])
            if plan.get("is_final_seq"):
                cur = final_by_so.get(key)
                if cur is None or plan["plan_date"] > cur:
                    final_by_so[key] = plan["plan_date"]
            planned_qty_by_so[key] = planned_qty_by_so.get(key, 0) + plan.get("qty_planned", 0)

        late_before = 0
        late_after = 0
        resolved = []

        for so in so_all:
            sku = sku_repo_all.get(so["sku_code"])
            if not sku:
                continue
            post_lead = int(sku.get("post_lead_days") or 0)
            cutoff = _ds(sub_workdays(_date(so["due_date"]), post_lead))
            key = (so["so_number"], so["sku_code"], so["line_item"])
            last_final = final_by_so.get(key)
            if not last_final:
                continue  # unplanned — handled separately

            is_late_now = last_final > cutoff
            if not is_late_now:
                continue

            late_before += 1

            # Check if extra cap covers the late SO's planned qty
            so_plans = [p for p in plans
                        if p["so_number"] == so["so_number"]
                        and p["sku_code"] == so["sku_code"]
                        and p["line_item"] == so["line_item"]]
            uom = int(sku.get("uom") or 1)
            needed_inner = sku_to_inner(int(so["qty"]), uom)
            can_resolve = False
            for p in so_plans:
                room = p["room_code"]
                avail_extra = extra_cap_by_room.get(room, 0.0)
                if avail_extra >= needed_inner * 0.5:
                    can_resolve = True
                    break

            if can_resolve:
                resolved.append(so["so_number"])
            else:
                late_after += 1

        return {
            "late_before":  late_before,
            "late_after":   late_after,
            "resolved_sos": resolved,
            "detail":       detail,
        }


# Lazy singleton — not instantiated at import time.
# DB must be initialized before first use.
_scheduler_instance = None

def _get_scheduler() -> Scheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = Scheduler()
    return _scheduler_instance

# Proxy object so existing code (scheduler.auto_plan etc.) keeps working
class _SchedulerProxy:
    def __getattr__(self, name):
        return getattr(_get_scheduler(), name)

scheduler = _SchedulerProxy()
