"""
Pure-function unit tests for the scheduling engine.
No PyQt6, no real database, no CRP Excel needed.

Run: python -m pytest tests/test_pure.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from unittest.mock import patch, MagicMock

import pytest

from core.scheduler import (
    calc_uph,
    shift_capacity_inner,
    inner_to_sku,
    sku_to_inner,
    Scheduler,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _bare_scheduler(shift_nos=(1, 2), mat_merge_days=21):
    """Instantiate Scheduler without touching the DB."""
    s = object.__new__(Scheduler)
    s.shifts = [{"shift_no": n} for n in shift_nos]
    s.mat_due_merge_days = mat_merge_days
    return s


def _rp(proc_name, proc_type, *, upph=10.0, hc_min=2, hc_max=8,
        uph_fixed=100.0, hc_fixed=2):
    return {
        "process_name": proc_name,
        "process_type": proc_type,
        "upph": upph,
        "hc_min": hc_min,
        "hc_max": hc_max,
        "uph_fixed": uph_fixed,
        "hc_fixed": hc_fixed,
    }


def _shift(start, end):
    return {"start_time": start, "end_time": end}


def _demand(due_date, qty=100):
    return {"due_date": due_date, "qty": qty,
            "so_number": "SO1", "sku_code": "SKU1", "line_item": "001"}


# ─── calc_uph ─────────────────────────────────────────────────────────────────

class TestCalcUph:
    def test_manual_basic(self):
        rp = _rp("P", "MANUAL", upph=10.0, hc_min=2, hc_max=8)
        assert calc_uph(rp, hc=5) == 50.0

    def test_manual_clamps_hc_to_max(self):
        rp = _rp("P", "MANUAL", upph=10.0, hc_min=1, hc_max=4)
        assert calc_uph(rp, hc=10) == 40.0  # capped at hc_max=4

    def test_manual_raises_hc_to_min(self):
        rp = _rp("P", "MANUAL", upph=10.0, hc_min=3, hc_max=8)
        assert calc_uph(rp, hc=1) == 30.0  # lifted to hc_min=3

    def test_auto_ignores_hc(self):
        rp = _rp("P", "AUTO", uph_fixed=200.0)
        assert calc_uph(rp, hc=0) == 200.0
        assert calc_uph(rp, hc=99) == 200.0

    def test_auto_none_uph_fixed_returns_zero(self):
        rp = _rp("P", "AUTO", uph_fixed=None)
        assert calc_uph(rp, hc=5) == 0.0

    def test_manual_none_upph_returns_zero(self):
        rp = _rp("P", "MANUAL", upph=None, hc_min=1, hc_max=8)
        assert calc_uph(rp, hc=5) == 0.0


# ─── shift_capacity_inner ────────────────────────────────────────────────────

class TestShiftCapacityInner:
    def test_12h_day_shift(self):
        rp = _rp("P", "AUTO", uph_fixed=100.0)
        assert shift_capacity_inner(rp, _shift("08:00", "20:00"), hc=1) == 1200.0

    def test_12h_night_shift_crosses_midnight(self):
        rp = _rp("P", "AUTO", uph_fixed=100.0)
        assert shift_capacity_inner(rp, _shift("20:00", "08:00"), hc=1) == 1200.0

    def test_manual_with_hc(self):
        rp = _rp("P", "MANUAL", upph=5.0, hc_min=1, hc_max=99)
        # UPH = 5 * 4 = 20, shift = 8h → cap = 160
        assert shift_capacity_inner(rp, _shift("08:00", "16:00"), hc=4) == 160.0


# ─── unit conversion ─────────────────────────────────────────────────────────

class TestUomConversion:
    def test_sku_to_inner_basic(self):
        assert sku_to_inner(qty=10, uom=6) == 60

    def test_inner_to_sku_floor(self):
        assert inner_to_sku(inner=59, uom=6) == 9   # floor, not round

    def test_inner_to_sku_exact(self):
        assert inner_to_sku(inner=60, uom=6) == 10

    def test_uom_1_is_identity(self):
        assert sku_to_inner(100, 1) == 100
        assert inner_to_sku(100, 1) == 100

    def test_uom_zero_guarded(self):
        # max(uom, 1) must prevent ZeroDivisionError
        assert inner_to_sku(100, 0) == 100
        assert sku_to_inner(100, 0) == 100

    def test_roundtrip(self):
        for qty in [1, 10, 100, 999]:
            for uom in [1, 3, 6, 12]:
                assert inner_to_sku(sku_to_inner(qty, uom), uom) == qty


# ─── _find_pre_cutoff — 2-shift system ──────────────────────────────────────

class TestFindPreCutoff2Shift:
    """Shift system: Day=1 (08-20), Night=2 (20-08)."""

    S = _bare_scheduler(shift_nos=[1, 2])

    def test_gap0_from_day_goes_to_prev_night(self):
        d, sno = self.S._find_pre_cutoff("2026-01-02", 1, 0)
        assert (d, sno) == (date(2026, 1, 1), 2)

    def test_gap0_from_night_goes_to_same_day_day(self):
        d, sno = self.S._find_pre_cutoff("2026-01-02", 2, 0)
        assert (d, sno) == (date(2026, 1, 2), 1)

    def test_gap1_post_day_jan3(self):
        # 2 shifts back from Day(1)-Jan3:
        #   step1: Night(2)-Jan2
        #   step2: Day(1)-Jan2
        d, sno = self.S._find_pre_cutoff("2026-01-03", 1, 1)
        assert (d, sno) == (date(2026, 1, 2), 1)

    def test_gap1_post_night_jan3(self):
        # 2 shifts back from Night(2)-Jan3:
        #   step1: Day(1)-Jan3
        #   step2: Night(2)-Jan2
        d, sno = self.S._find_pre_cutoff("2026-01-03", 2, 1)
        assert (d, sno) == (date(2026, 1, 2), 2)

    def test_gap2_post_day_jan4(self):
        # 3 shifts back from Day(1)-Jan4:
        #   step1: Night(2)-Jan3
        #   step2: Day(1)-Jan3
        #   step3: Night(2)-Jan2
        d, sno = self.S._find_pre_cutoff("2026-01-04", 1, 2)
        assert (d, sno) == (date(2026, 1, 2), 2)

    def test_gap0_is_one_shift_not_zero(self):
        # gap=0 still goes back exactly 1 shift (adjacent is OK, not same slot)
        d1, s1 = self.S._find_pre_cutoff("2026-01-05", 1, 0)
        d2, s2 = self.S._find_pre_cutoff("2026-01-05", 1, 1)
        assert (d1, s1) != (d2, s2)  # gap0 and gap1 must differ

    def test_going_back_across_multiple_days(self):
        # gap=4 from Night(2)-Jan1: 5 shifts back crosses into Dec
        d, sno = self.S._find_pre_cutoff("2026-01-01", 2, 4)
        # 5 steps back: D1-Jan1, N2-Dec31, D1-Dec31, N2-Dec30, D1-Dec30
        assert d < date(2026, 1, 1)


# ─── _find_pre_cutoff — 3-shift system ───────────────────────────────────────

class TestFindPreCutoff3Shift:
    """Shift system: 1, 2, 3 (three shifts per day)."""

    S = _bare_scheduler(shift_nos=[1, 2, 3])

    def test_gap0_from_shift1_crosses_day(self):
        d, sno = self.S._find_pre_cutoff("2026-01-02", 1, 0)
        assert (d, sno) == (date(2026, 1, 1), 3)

    def test_gap0_from_shift2_stays_same_day(self):
        d, sno = self.S._find_pre_cutoff("2026-01-02", 2, 0)
        assert (d, sno) == (date(2026, 1, 2), 1)

    def test_gap0_from_shift3_stays_same_day(self):
        d, sno = self.S._find_pre_cutoff("2026-01-02", 3, 0)
        assert (d, sno) == (date(2026, 1, 2), 2)

    def test_gap1_from_shift2(self):
        # 2 shifts back from Shift2-Jan2:
        #   step1: Shift1-Jan2
        #   step2: Shift3-Jan1
        d, sno = self.S._find_pre_cutoff("2026-01-02", 2, 1)
        assert (d, sno) == (date(2026, 1, 1), 3)

    def test_gap2_from_shift3(self):
        # 3 shifts back from Shift3-Jan2:
        #   step1: Shift2-Jan2
        #   step2: Shift1-Jan2
        #   step3: Shift3-Jan1
        d, sno = self.S._find_pre_cutoff("2026-01-02", 3, 2)
        assert (d, sno) == (date(2026, 1, 1), 3)

    def test_gap1_equivalent_one_day_gap(self):
        # 3-shift system: gap=2 ≈ one full day gap
        d, sno = self.S._find_pre_cutoff("2026-01-03", 1, 2)
        # 3 steps back: Shift3-Jan2, Shift2-Jan2, Shift1-Jan2
        assert (d, sno) == (date(2026, 1, 2), 1)


# ─── _merge_material_demands ─────────────────────────────────────────────────

class TestMergeMaterialDemands:

    S = _bare_scheduler(mat_merge_days=21)

    def test_empty(self):
        assert self.S._merge_material_demands([]) == []

    def test_single(self):
        groups = self.S._merge_material_demands([_demand("2026-01-01")])
        assert len(groups) == 1 and len(groups[0]) == 1

    def test_within_window_merged(self):
        groups = self.S._merge_material_demands([
            _demand("2026-01-01"), _demand("2026-01-20"),  # 19 days apart
        ])
        assert len(groups) == 1

    def test_at_boundary_still_merged(self):
        groups = self.S._merge_material_demands([
            _demand("2026-01-01"), _demand("2026-01-22"),  # exactly 21 days
        ])
        assert len(groups) == 1

    def test_beyond_boundary_splits(self):
        groups = self.S._merge_material_demands([
            _demand("2026-01-01"), _demand("2026-01-23"),  # 22 days → split
        ])
        assert len(groups) == 2

    def test_three_demands_two_groups(self):
        groups = self.S._merge_material_demands([
            _demand("2026-01-01"),
            _demand("2026-01-10"),   # 9 days → same group as Jan-01
            _demand("2026-02-15"),   # far away → new group
        ])
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1

    def test_anchor_does_not_slide(self):
        # Anchor stays at first demand even if later demands are close to each other
        # Jan-01 is anchor; Jan-22 is 21 days from anchor (in); Jan-23 is 22 days (out)
        groups = self.S._merge_material_demands([
            _demand("2026-01-01"),
            _demand("2026-01-22"),   # 21d from anchor → same group
            _demand("2026-01-23"),   # 22d from anchor → new group (anchor didn't move)
        ])
        assert len(groups) == 2
        assert len(groups[0]) == 2

    def test_qty_preserved(self):
        demands = [_demand("2026-01-01", qty=50), _demand("2026-01-10", qty=30)]
        groups = self.S._merge_material_demands(demands)
        total = sum(d["qty"] for d in groups[0])
        assert total == 80


# ─── _compute_hc_distribution ────────────────────────────────────────────────

class TestHCDistribution:

    S = _bare_scheduler()

    def _run(self, active_rps, urgency, total_hc):
        with patch("core.scheduler.crp_manager") as mock_crp:
            mock_crp.get_total_hc.return_value = total_hc
            return self.S._compute_hc_distribution(
                "2026-01-01", 1, active_rps, urgency)

    def test_zero_hc_returns_empty(self):
        active = [("R1", [_rp("P", "AUTO", hc_fixed=3)])]
        assert self._run(active, {}, total_hc=0) == {}

    def test_auto_gets_hc_fixed_first(self):
        rp_auto = _rp("Filling", "AUTO", hc_fixed=3)
        rp_man = _rp("Packaging", "MANUAL", hc_min=2, hc_max=10)
        active = [("R1", [rp_auto]), ("R2", [rp_man])]
        result = self._run(active, {"Packaging": 100.0}, total_hc=10)
        assert result[("R1", "Filling")] == 3
        assert result[("R2", "Packaging")] == 7  # 10 - 3 = 7, within hc_max=10

    def test_manual_only_distributes_to_min_plus_extra(self):
        rp = _rp("P", "MANUAL", hc_min=2, hc_max=6)
        active = [("R1", [rp])]
        result = self._run(active, {"P": 50.0}, total_hc=5)
        assert result[("R1", "P")] == 5  # min=2 + extra=3, capped at hc_max=6? No: min(5-2=3, 6-2=4)=3 → 2+3=5

    def test_auto_hc_fixed_caps_at_available(self):
        # If hc_fixed=5 but only 3 HC available, AUTO doesn't get allocated
        # (the code only assigns if remaining >= hc_fixed)
        rp_auto = _rp("Filling", "AUTO", hc_fixed=5)
        active = [("R1", [rp_auto])]
        result = self._run(active, {}, total_hc=3)
        # remaining(3) < hc_fixed(5) → not allocated
        assert result.get(("R1", "Filling"), 0) == 0

    def test_insufficient_hc_staffs_highest_urgency_first(self):
        rp_a = _rp("ProcA", "MANUAL", hc_min=3, hc_max=8)
        rp_b = _rp("ProcB", "MANUAL", hc_min=3, hc_max=8)
        active = [("R1", [rp_a]), ("R2", [rp_b])]
        urgency = {"ProcA": 50.0, "ProcB": 200.0}   # ProcB more urgent
        result = self._run(active, urgency, total_hc=4)  # 4 < 3+3 minimum

        # ProcB (higher urgency) gets staffed first
        assert result.get(("R2", "ProcB"), 0) == 3
        assert result.get(("R1", "ProcA"), 0) == 1  # leftover 1

    def test_sufficient_hc_both_get_minimum_then_extra_to_urgent(self):
        rp_a = _rp("ProcA", "MANUAL", hc_min=2, hc_max=5)
        rp_b = _rp("ProcB", "MANUAL", hc_min=2, hc_max=5)
        active = [("R1", [rp_a]), ("R2", [rp_b])]
        urgency = {"ProcA": 30.0, "ProcB": 100.0}
        result = self._run(active, urgency, total_hc=8)  # plenty

        # Both get hc_min=2 first; extra 4 → ProcB gets min(4, 5-2=3)=3 → ProcB=5, ProcA gets 1
        assert result[("R2", "ProcB")] == 5   # maxed out at hc_max
        assert result[("R1", "ProcA")] == 3   # 2 min + 1 leftover extra
