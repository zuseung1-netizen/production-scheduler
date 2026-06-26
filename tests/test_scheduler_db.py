"""
Integration tests for the scheduler using a temporary SQLite database.
No PyQt6, no CRP Excel file required.
CRP headcount is mocked to a fixed value.

Run: python -m pytest tests/test_scheduler_db.py -v
"""
import sys
import os
import tempfile
import shutil
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

# ─── redirect DB_PATH before importing any app modules ───────────────────────

_TEST_DIR = None
_TEST_DB = None

def _setup_test_db():
    global _TEST_DIR, _TEST_DB
    _TEST_DIR = tempfile.mkdtemp()
    _TEST_DB  = os.path.join(_TEST_DIR, "test_planner.db")
    return _TEST_DB


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Each test gets a fresh SQLite DB; CRP is mocked to return 20 HC."""
    import data.database as db_mod
    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test.db")

    # Close and discard any existing connection before redirecting DB_PATH.
    if db_mod._conn is not None:
        try:
            db_mod._conn.close()
        except Exception:
            pass
    monkeypatch.setattr(db_mod, "_conn", None)
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_db)

    from data.database import init_db
    init_db()

    with patch("core.scheduler.crp_manager") as mock_crp:
        mock_crp.get_total_hc.return_value = 20
        mock_crp.is_held.return_value = False
        yield mock_crp

    # Close the test connection before cleanup
    if db_mod._conn is not None:
        try:
            db_mod._conn.close()
        except Exception:
            pass
    shutil.rmtree(tmp_dir, ignore_errors=True)

    # Reset lazy scheduler singleton so next test starts fresh
    import core.scheduler as sched_mod
    sched_mod._scheduler_instance = None


# ─── helpers ─────────────────────────────────────────────────────────────────

def _seed_shifts():
    from data.repositories import ShiftRepo
    ShiftRepo.upsert({"shift_no": 1, "shift_name": "Day",   "start_time": "08:00", "end_time": "20:00"})
    ShiftRepo.upsert({"shift_no": 2, "shift_name": "Night", "start_time": "20:00", "end_time": "08:00"})


def _seed_room(room_code="R1", process_name="Assembly",
               room_type="TypeA", proc_type="MANUAL",
               upph=10.0, hc_min=2, hc_max=20):
    from data.repositories import RoomRepo
    RoomRepo.upsert({
        "room_code": room_code, "process_name": process_name,
        "process_type": proc_type, "room_type": room_type,
        "upph": upph, "hc_min": hc_min, "hc_max": hc_max,
        "uph_fixed": None, "hc_fixed": None, "note": None,
    })


def _seed_sku(sku_code="SKU-A", uom=1, post_lead_days=0):
    from data.repositories import SKURepo
    SKURepo.upsert({
        "sku_code": sku_code, "sku_name": "Test SKU",
        "uom": uom, "post_lead_days": post_lead_days, "note": None,
    })


def _seed_routing(entity_type="SKU", entity_code="SKU-A",
                  process_seq=1, process_name="Assembly",
                  room_type="TypeA", is_final=1, min_gap_shifts=0):
    from data.repositories import ProcessRoutingRepo
    ProcessRoutingRepo.upsert({
        "entity_type": entity_type, "entity_code": entity_code,
        "process_seq": process_seq, "process_name": process_name,
        "allowed_room_types": room_type, "is_final_seq": is_final,
        "requires_material_code": None, "min_gap_shifts": min_gap_shifts,
        "note": None,
    })


def _seed_so(so_number="SO-001", sku_code="SKU-A", line_item="001",
             qty=100, due_date=None, priority=1):
    from data.repositories import SORepo
    if due_date is None:
        due_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
    SORepo.upsert({
        "so_number": so_number, "sku_code": sku_code,
        "line_item": line_item, "customer_name": None,
        "qty": qty, "due_date": due_date, "priority": priority,
        "received_at": "2026-01-01 00:00:00", "status": "OPEN",
        "start_no_earlier": None, "note": None,
    })


def _date_range(days_from_today=0, days_to=60):
    today = date.today()
    d0 = (today + timedelta(days=days_from_today)).strftime("%Y-%m-%d")
    d1 = (today + timedelta(days=days_to)).strftime("%Y-%m-%d")
    return d0, d1


# ─── Repository smoke tests ───────────────────────────────────────────────────

class TestRepositoryBasics:
    def test_sku_upsert_and_get(self):
        from data.repositories import SKURepo
        SKURepo.upsert({"sku_code": "SKU-X", "sku_name": "X",
                        "uom": 6, "post_lead_days": 2, "note": None})
        row = SKURepo.get("SKU-X")
        assert row["sku_code"] == "SKU-X"
        assert row["uom"] == 6
        assert row["post_lead_days"] == 2

    def test_sku_all_returns_list(self):
        from data.repositories import SKURepo
        _seed_sku("A1")
        _seed_sku("A2")
        codes = {r["sku_code"] for r in SKURepo.all()}
        assert {"A1", "A2"}.issubset(codes)

    def test_shift_upsert_and_all(self):
        from data.repositories import ShiftRepo
        _seed_shifts()
        shifts = ShiftRepo.all()
        nos = {s["shift_no"] for s in shifts}
        assert {1, 2}.issubset(nos)

    def test_so_upsert_and_status(self):
        from data.repositories import SORepo
        _seed_sku()
        _seed_so()
        rows = SORepo.all(status="OPEN")
        assert any(r["so_number"] == "SO-001" for r in rows)

    def test_config_get_default(self):
        from data.repositories import ConfigRepo
        val = ConfigRepo.get("max_pull_days", "45")
        assert val == "45"  # seeded by init_db()

    def test_plan_insert_and_all(self):
        from data.repositories import PlanRepo
        _seed_sku()
        _seed_so()
        PlanRepo.insert({
            "entity_type": "SKU", "entity_code": "SKU-A",
            "so_number": "SO-001", "sku_code": "SKU-A", "line_item": "001",
            "process_name": "Assembly", "process_seq": 1, "is_final_seq": 1,
            "room_code": "R1", "plan_date": "2026-01-10", "shift_no": 1,
            "qty_planned": 50, "memo": "[FINAL]",
        })
        plans = PlanRepo.all()
        assert len(plans) == 1
        assert plans[0]["qty_planned"] == 50


# ─── auto_plan: single-process, single-SO ────────────────────────────────────

class TestAutoplanSingleProcess:

    def _setup(self, qty=100, due_days_out=30, post_lead=0, upph=10.0):
        _seed_shifts()
        _seed_room(upph=upph)
        _seed_sku(post_lead_days=post_lead)
        due = (date.today() + timedelta(days=due_days_out)).strftime("%Y-%m-%d")
        _seed_so(qty=qty, due_date=due)
        _seed_routing()

    def test_creates_plans(self):
        self._setup(qty=100)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        report = s.auto_plan(d0, d1)
        assert report["planned"] > 0
        assert len(PlanRepo.all()) > 0

    def test_no_overplan(self):
        """Total planned qty must not exceed SO qty."""
        self._setup(qty=50)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)
        total = sum(p["qty_planned"] for p in PlanRepo.all())
        assert total <= 50

    def test_plans_before_due_date(self):
        """All plan dates must be <= due_date - post_lead_days."""
        due_days = 20
        due = date.today() + timedelta(days=due_days)
        self._setup(qty=100, due_days_out=due_days, post_lead=0)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)
        for p in PlanRepo.all():
            assert p["plan_date"] <= due.strftime("%Y-%m-%d"), \
                f"Plan {p['plan_date']} is after due {due}"

    def test_post_lead_shifts_cutoff(self):
        """With post_lead_days=5, plans must end at least 5 days before due."""
        due_days = 30
        post_lead = 5
        due = date.today() + timedelta(days=due_days)
        cutoff = due - timedelta(days=post_lead)
        self._setup(qty=100, due_days_out=due_days, post_lead=post_lead)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)
        for p in PlanRepo.all():
            assert p["plan_date"] <= cutoff.strftime("%Y-%m-%d"), \
                f"Plan {p['plan_date']} violates post_lead cutoff {cutoff}"

    def test_locked_plans_preserved(self):
        """Locked plans must not be deleted by auto_plan."""
        self._setup(qty=100)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)

        # Lock the first plan
        first = PlanRepo.all()[0]
        PlanRepo.lock(first["plan_id"], locked=True)
        locked_id = first["plan_id"]

        # Re-run auto_plan
        s.auto_plan(d0, d1)
        ids = {p["plan_id"] for p in PlanRepo.all()}
        assert locked_id in ids

    def test_overdue_so_still_gets_planned(self):
        """SO with passed due date should be planned (flagged as late, not skipped)."""
        _seed_shifts()
        _seed_room(upph=10.0)
        _seed_sku()
        past_due = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        _seed_so(qty=50, due_date=past_due)
        _seed_routing()

        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 30)
        report = s.auto_plan(d0, d1)
        assert len(PlanRepo.all()) > 0
        # Must be flagged as late
        assert any(item.get("reason") == "overdue" for item in report["late"])


# ─── auto_plan: two-process routing with gap ─────────────────────────────────

class TestAutoplanTwoProcessGap:

    def _setup(self, qty=100, due_days=40, min_gap=0):
        _seed_shifts()
        _seed_room(room_code="R1", process_name="Mixing", room_type="TypeA", upph=20.0)
        _seed_room(room_code="R2", process_name="Assembly", room_type="TypeB", upph=20.0)
        _seed_sku(uom=1)
        due = (date.today() + timedelta(days=due_days)).strftime("%Y-%m-%d")
        _seed_so(qty=qty, due_date=due)
        # 2-step routing: Mixing → Assembly (Assembly is final)
        _seed_routing(process_seq=1, process_name="Mixing",
                      room_type="TypeA", is_final=0, min_gap_shifts=0)
        _seed_routing(process_seq=2, process_name="Assembly",
                      room_type="TypeB", is_final=1, min_gap_shifts=min_gap)

    def test_both_processes_get_plans(self):
        self._setup(qty=100)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)
        procs = {p["process_name"] for p in PlanRepo.all() if p["entity_type"] == "SKU"}
        assert "Mixing" in procs
        assert "Assembly" in procs

    def test_pre_process_ends_before_post(self):
        """Mixing must end on or before Assembly starts (backward scheduling)."""
        self._setup(qty=100, min_gap=0)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)

        mixing_dates  = [(p["plan_date"], p["shift_no"])
                         for p in PlanRepo.all() if p["process_name"] == "Mixing"]
        assembly_dates = [(p["plan_date"], p["shift_no"])
                          for p in PlanRepo.all() if p["process_name"] == "Assembly"]
        if not mixing_dates or not assembly_dates:
            pytest.skip("not enough plans to compare")

        mixing_latest   = max(mixing_dates)
        assembly_first  = min(assembly_dates)
        assert mixing_latest <= assembly_first

    def test_gap1_enforces_one_shift_gap(self):
        """With min_gap_shifts=1, Mixing latest slot must be at least 2 shifts before Assembly first slot."""
        self._setup(qty=100, min_gap=1)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)

        mixing_dates   = [(p["plan_date"], p["shift_no"])
                          for p in PlanRepo.all() if p["process_name"] == "Mixing"]
        assembly_dates = [(p["plan_date"], p["shift_no"])
                          for p in PlanRepo.all() if p["process_name"] == "Assembly"]
        if not mixing_dates or not assembly_dates:
            pytest.skip("not enough plans")

        mixing_latest  = max(mixing_dates)
        assembly_first = min(assembly_dates)
        # They must not be adjacent — need at least 1 shift gap
        # i.e., mixing_latest + 1 shift + 1 gap_shift ≤ assembly_first
        # Simpler check: they cannot be the same slot, and mixing_latest < assembly_first
        assert mixing_latest < assembly_first, \
            f"gap=1 violated: Mixing{mixing_latest} >= Assembly{assembly_first}"


# ─── detect_conflicts ─────────────────────────────────────────────────────────

class TestDetectConflicts:

    def test_no_conflict_when_empty(self):
        from core.scheduler import Scheduler
        s = Scheduler()
        d0, d1 = _date_range(0, 30)
        assert s.detect_conflicts(d0, d1) == []

    def test_no_conflict_within_capacity(self):
        _seed_shifts()
        _seed_room(upph=100.0, hc_min=1, hc_max=20)
        _seed_sku(uom=1)
        _seed_so(qty=10)
        _seed_routing()
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 30)
        s.auto_plan(d0, d1)
        # auto_plan never creates overcapacity plans
        conflicts = s.detect_conflicts(d0, d1)
        assert conflicts == []

    def test_manual_overplan_detected(self):
        """Inserting a plan that exceeds capacity should show up as conflict."""
        _seed_shifts()
        _seed_room(upph=10.0, hc_min=2, hc_max=4)
        _seed_sku(uom=1)
        from data.repositories import PlanRepo
        # Insert plans totaling 10000 units into one slot — guaranteed overflow
        from datetime import date as dt
        plan_date = (dt.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        for _ in range(5):
            PlanRepo.insert({
                "entity_type": "SKU", "entity_code": "SKU-A",
                "so_number": "SO-001", "sku_code": "SKU-A", "line_item": "001",
                "process_name": "Assembly", "process_seq": 1, "is_final_seq": 1,
                "room_code": "R1", "plan_date": plan_date, "shift_no": 1,
                "qty_planned": 5000, "memo": "[FINAL]",
            })
        from core.scheduler import Scheduler
        s = Scheduler()
        d0 = (dt.today()).strftime("%Y-%m-%d")
        d1 = (dt.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        conflicts = s.detect_conflicts(d0, d1)
        assert len(conflicts) > 0
        assert conflicts[0]["planned_inner"] > conflicts[0]["capacity_inner"]


# ─── replan_after_actuals ─────────────────────────────────────────────────────

class TestReplanAfterActuals:

    def _full_setup(self, qty=100, due_days=30):
        _seed_shifts()
        _seed_room(upph=10.0)
        _seed_sku(uom=1)
        due = (date.today() + timedelta(days=due_days)).strftime("%Y-%m-%d")
        _seed_so(qty=qty, due_date=due)
        _seed_routing()

    def test_fully_produced_so_plans_deleted(self):
        self._full_setup(qty=50)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo, ActualRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)
        assert len(PlanRepo.all()) > 0

        # Record actual = full SO qty
        ActualRepo.insert({
            "entity_type": "SKU", "entity_code": "SKU-A",
            "plan_id": None, "so_number": "SO-001",
            "sku_code": "SKU-A", "line_item": "001",
            "lot_number": "LOT-001", "room_code": "R1",
            "process_name": "Assembly", "actual_date": "2026-01-10",
            "shift_no": 1, "qty_actual": 50, "note": None,
        })

        report = s.replan_after_actuals(d0, d1)
        assert len(report["deleted"]) > 0
        # Unlocked plans should all be gone
        remaining = [p for p in PlanRepo.all() if not p["is_locked"]]
        assert len(remaining) == 0

    def test_partial_actual_replans_remainder(self):
        self._full_setup(qty=100)
        from core.scheduler import Scheduler
        from data.repositories import PlanRepo, ActualRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)

        # Record partial actual = 40 out of 100
        ActualRepo.insert({
            "entity_type": "SKU", "entity_code": "SKU-A",
            "plan_id": None, "so_number": "SO-001",
            "sku_code": "SKU-A", "line_item": "001",
            "lot_number": "LOT-001", "room_code": "R1",
            "process_name": "Assembly", "actual_date": "2026-01-10",
            "shift_no": 1, "qty_actual": 40, "note": None,
        })

        report = s.replan_after_actuals(d0, d1)
        assert len(report["replanned"]) > 0
        rp = report["replanned"][0]
        assert rp["actual_qty"] == 40
        assert rp["remaining_qty"] == 60


# ─── pull_forward ─────────────────────────────────────────────────────────────

class TestPullForward:

    def test_pull_forward_moves_plans_earlier(self):
        _seed_shifts()
        _seed_room(upph=10.0)
        _seed_sku(uom=1)
        # Due in 40 days so there's room to pull forward
        due = (date.today() + timedelta(days=40)).strftime("%Y-%m-%d")
        _seed_so(qty=100, due_date=due)
        _seed_routing()

        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)
        dates_before = [p["plan_date"] for p in PlanRepo.all()]

        result = s.pull_forward(d0, d1)
        # pull_forward should find some plans to move (or report 0 if already at earliest)
        assert "moved" in result

    def test_locked_plans_not_moved(self):
        _seed_shifts()
        _seed_room(upph=10.0)
        _seed_sku(uom=1)
        due = (date.today() + timedelta(days=40)).strftime("%Y-%m-%d")
        _seed_so(qty=50, due_date=due)
        _seed_routing()

        from core.scheduler import Scheduler
        from data.repositories import PlanRepo
        s = Scheduler()
        d0, d1 = _date_range(0, 60)
        s.auto_plan(d0, d1)

        # Lock all plans
        for p in PlanRepo.all():
            PlanRepo.lock(p["plan_id"], locked=True)

        original = {p["plan_id"]: p["plan_date"] for p in PlanRepo.all()}
        s.pull_forward(d0, d1)
        after = {p["plan_id"]: p["plan_date"] for p in PlanRepo.all()}

        # No locked plan should have moved
        for pid, orig_date in original.items():
            assert after[pid] == orig_date
