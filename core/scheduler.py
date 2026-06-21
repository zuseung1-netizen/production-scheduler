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
    ActualRepo, ConfigRepo, MaterialDemandRepo, AllocationRepo, _now
)
from data.crp_excel import crp_manager


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
        self.rooms      = RoomRepo.rooms()
        self.room_procs = {r: RoomRepo.processes_for_room(r) for r in self.rooms}
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

    # ── Public API ───────────────────────────────────────────────────────────

    def auto_plan(self, date_from: str, date_to: str,
                  progress_cb=None) -> Dict:
        self._reload_masters()
        PlanRepo.delete_unlocked(date_from, date_to)
        open_sos = self._sorted_open_sos()
        slot_map = self._build_slot_map(date_from, date_to)
        report   = {"planned": 0, "skipped": 0, "late": [],
                    "routing_errors": [], "material_plans": 0}

        # 1. Campaign grouping: merge same-SKU SOs within max_consolidation_days
        #    into virtual combined SOs (earliest due date is the cutoff).
        #    SKUs with campaign_mode=0 are planned individually.
        campaign_sos = self._apply_campaign_grouping(open_sos)
        total = len(campaign_sos)

        for idx, so in enumerate(campaign_sos):
            if progress_cb:
                progress_cb(idx, total, so)
            self._plan_so(so, slot_map, date_from, date_to, report)

        # 2. Plan derived Material demand
        self._plan_all_materials(slot_map, date_from, date_to, report)

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
                if len(cluster) == 1:
                    merged.append(cluster[0])
                else:
                    # Build virtual SO: earliest due date, highest priority, combined qty
                    earliest_due = min(s["due_date"] for s in cluster)
                    priorities   = [s["priority"] for s in cluster if s.get("priority") is not None]
                    best_pri     = min(priorities) if priorities else None
                    total_qty    = sum(int(s["qty"]) for s in cluster)
                    # Use first SO's fields as base, override combined fields
                    base = dict(cluster[0])
                    base["qty"]        = total_qty
                    base["due_date"]   = earliest_due
                    base["priority"]   = best_pri
                    base["_campaign_members"] = cluster  # keep refs for history
                    merged.append(base)

        # Re-sort merged + individual by same priority key as _sorted_open_sos
        def _key(so):
            p = so.get("priority")
            return ((0, p) if p is not None else (1, 0),
                    so.get("received_at", ""))

        all_sos = sorted(merged + individual_sos, key=_key)
        return all_sos

    def pull_forward(self, date_from: str, date_to: str) -> Dict:
        self._reload_masters()
        slot_map = self._build_slot_map(date_from, date_to)

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

        moved = 0
        plans = sorted(all_plans,
                       key=lambda p: (p["plan_date"], p["shift_no"]),
                       reverse=True)
        for plan in plans:
            if plan["is_locked"]:
                continue
            if plan["entity_type"] == "SKU":
                so = SORepo.get(plan["so_number"], plan["sku_code"],
                                plan["line_item"])
                if not so:
                    continue
                earliest = self._earliest_start(so)
            else:
                # Material: don't pull before today
                earliest = date.today()

            new_slot = self._find_earlier_slot(plan, slot_map, earliest,
                                               date_from)
            if new_slot:
                uom = (self.sku_map.get(plan["sku_code"], {}).get("uom", 1)
                       if plan["entity_type"] == "SKU"
                       else self.mat_map.get(plan["entity_code"], {}).get("uom", 1))
                uom = uom or 1
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
            actual_total = ActualRepo.actual_qty(so_no, sku_c, li)

            if actual_total >= so["qty"]:
                # Fully produced → delete remaining plans
                for p in plans:
                    PlanRepo.delete(p["plan_id"], reason="replan_completed")
                    report["deleted"].append({
                        "plan_id": p["plan_id"], "so_number": so_no,
                        "sku_code": sku_c, "line_item": li,
                        "reason": "SO fully produced"
                    })
            elif actual_total > 0:
                # Partially produced → delete then re-plan remainder
                for p in plans:
                    PlanRepo.delete(p["plan_id"], reason="replan_partial")
                    report["deleted"].append({
                        "plan_id": p["plan_id"], "so_number": so_no,
                        "sku_code": sku_c, "line_item": li,
                        "reason": f"Partial ({actual_total}/{so['qty']}) — re-planning"
                    })
                before = plan_report["planned"]
                self._plan_so(so, slot_map, date_from, date_to, plan_report)
                new_slots = plan_report["planned"] - before
                report["replanned"].append({
                    "so_number": so_no, "sku_code": sku_c, "line_item": li,
                    "actual_qty": actual_total,
                    "remaining_qty": so["qty"] - actual_total,
                    "new_slots": new_slots,
                })

        report["errors"] = (
            [{"so": x["so"], "reason": x["reason"]} for x in plan_report["routing_errors"]] +
            [{"so": x["so"], "reason": f"late: {x.get('reason','')}"}
             for x in plan_report["late"]]
        )
        return report

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
        return due - timedelta(days=int(sku.get("post_lead_days") or 0))

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
                 force: bool = False):
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
            eligible  = RoomRepo.rooms_for_process(step["process_name"], allowed)
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

            candidates = self._candidates(
                slot_map, step["process_name"], eligible, d0, upper_d, upper_s)

            # Final step has no slots in constrained window → fall back to full
            # date range so production is always scheduled, even if it will be late.
            if not candidates and si == len(steps) - 1 and not force:
                report["late"].append({
                    "so": so["so_number"], "sku": sku_code,
                    "line": so["line_item"], "reason": "overdue",
                    "unplanned_qty": qty_to_plan,
                })
                d0 = _date(date_from)
                d1 = _date(date_to)
                upper_d, upper_s = d1, shift_max
                candidates = self._candidates(
                    slot_map, step["process_name"], eligible, d0, upper_d, upper_s)

            allocated: List[Tuple] = []
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
                avail_inner = slot_map.get(key, 0.0)
                avail_sku   = inner_to_sku(avail_inner, uom)
                if avail_sku <= 0:
                    continue
                qty_this = min(step_rem, avail_sku)
                PlanRepo.insert({
                    "entity_type":   "SKU",
                    "entity_code":   sku_code,
                    "so_number":     so["so_number"],
                    "sku_code":      sku_code,
                    "line_item":     so["line_item"],
                    "process_name":  proc_name,
                    "process_seq":   seq,
                    "is_final_seq":  1 if is_final else 0,
                    "room_code":     room_code,
                    "plan_date":     ds,
                    "shift_no":      sno,
                    "qty_planned":   qty_this,
                    "memo":          "[FINAL]" if is_final else f"[SEQ-{seq}]",
                })
                slot_map[key] = max(0.0, avail_inner - sku_to_inner(qty_this, uom))
                self._block_room_shift(slot_map, ds, room_code, proc_name, sno)
                self._record_placed(room_code, proc_name, ds, sno, sku_code)
                allocated.append((ds, sno, room_code, qty_this))
                step_rem -= qty_this
                report["planned"] += 1

            step_slots[seq] = allocated
            if step_rem > 0 and si == len(steps) - 1:
                report["late"].append({
                    "so": so["so_number"], "sku": sku_code,
                    "line": so["line_item"],
                    "unplanned_qty": step_rem,
                    "reason": "capacity_exceeded"})

    # ── Material Planning ────────────────────────────────────────────────────

    def _plan_all_materials(self, slot_map: Dict[SlotKey, float],
                             date_from: str, date_to: str, report: Dict):
        """
        Collect material demands from all SKU plans that have
        requires_material_code on a process step, then plan each material.
        """
        # Clear previously auto-generated (unlocked) material plans first —
        # otherwise every auto_plan() run re-derives the same demand and
        # stacks a fresh duplicate layer on top of the last run's plans.
        PlanRepo.delete_unlocked_material(date_from, date_to)

        # Gather demands: material_code -> [(due_date, qty, so_no, sku, li)]
        demands: Dict[str, List[Dict]] = defaultdict(list)

        sku_plans = PlanRepo.all(date_from, date_to, entity_type="SKU")
        for plan in sku_plans:
            sku_code = plan["sku_code"]
            steps = ProcessRoutingRepo.for_entity("SKU", sku_code)
            step  = next((s for s in steps
                          if s["process_seq"] == plan["process_seq"]), None)
            if not step or not step.get("requires_material_code"):
                continue
            mat_code = step["requires_material_code"]
            sku      = self.sku_map.get(sku_code, {})
            uom      = int(sku.get("uom") or 1)
            # Material qty = plan qty (in SKU EA) * uom
            mat_qty  = plan["qty_planned"] * uom

            so = SORepo.get(plan["so_number"], sku_code, plan["line_item"])
            due = so["due_date"] if so else plan["plan_date"]

            demands[mat_code].append({
                "due_date":   due,
                "qty":        mat_qty,
                "so_number":  plan["so_number"],
                "sku_code":   sku_code,
                "line_item":  plan["line_item"],
            })

        for mat_code, demand_list in demands.items():
            self._plan_material(mat_code, demand_list, slot_map,
                                date_from, date_to, report)

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
            mat_latest   = earliest_due - timedelta(days=post_lead)
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
            eligible = RoomRepo.rooms_for_process(step["process_name"], allowed)
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
                d0_eff, upper_d, upper_s)

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

    def _candidates(self, slot_map, process_name, eligible_rooms,
                    d0, d_upper, shift_upper):
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

        if self.assign_mode == "UPH":
            def uph_key(item):
                ds, sno, room, rp = item
                hc = self._hc_dist_cache.get((ds, sno), {}).get(
                    (room, process_name), 0)
                return (-_date(ds).toordinal(), -sno, -calc_uph(rp, hc))
            raw.sort(key=uph_key)
        else:
            def cap_key(item):
                ds, sno, room, rp = item
                c = slot_map.get((ds, room, process_name, sno), 0.0)
                return (-_date(ds).toordinal(), -sno, -c)
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
        """Returns {(date_str, shift_no): {(room, proc): hc}} for UI preview."""
        self._reload_masters()
        urgency = self._compute_urgency()
        result: Dict[Tuple[str,int], Dict[Tuple[str,str], int]] = {}
        cur, d1 = _date(date_from), _date(date_to)
        while cur <= d1:
            ds = _ds(cur)
            for shift in self.shifts:
                sno = shift["shift_no"]
                active_rps: List[Tuple[str, List[Dict]]] = []
                for room in self.rooms:
                    cal = CalendarRepo.get_slot(ds, sno, room)
                    if cal and (not cal["is_open"] or cal["is_hold"]):
                        continue
                    if crp_manager.is_held(ds, room, sno):
                        continue
                    rps = self.room_procs.get(room, [])
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
            result[ds] = min((r / a * 100) if a > 0 else 0.0, 100.0)
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
            cutoff = _ds(_date(so["due_date"]) - timedelta(days=post_lead))

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
            cutoff = _ds(_date(so["due_date"]) - timedelta(days=post_lead))
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
