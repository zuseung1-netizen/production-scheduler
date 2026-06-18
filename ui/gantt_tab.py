"""
Gantt Planner Tab — with consolidation support.

Changes:
 - Checkbox: text starts after checkbox (no overlap)
 - Utilization bar: 2 rows (capacity util + headcount util)
 - Due lines: drawn per-SO on relevant rows only, below util bar
 - Customer name shown in plan card
 - UTIL_H doubled to accommodate 2 rows
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QComboBox, QToolTip, QInputDialog,
    QMessageBox, QMenu, QDialog, QDialogButtonBox, QTextEdit,
    QApplication, QDateEdit, QFormLayout, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QLineEdit, QButtonGroup
)
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QPainter, QPainterPath, QColor, QFont, QFontMetrics, QPen, QBrush, QCursor
)

from data.repositories import (
    PlanRepo, SORepo, SKURepo, ShiftRepo, RoomRepo,
    CalendarRepo, ConfigRepo, MaterialDemandRepo
)
from core.scheduler import scheduler, sku_to_inner, shift_capacity_inner
from utils.excel_io import export_gantt_plan


# ─── Visual constants ─────────────────────────────────────────────────────────
PALETTE = [
    "#2E6FD8", "#D4570A", "#27A060", "#C0392B",
    "#6655CC", "#D49010", "#1A9E9E", "#8B5E3C",
    "#3A8FAA", "#8B479B", "#1E7A3C", "#CF4545",
]
CONSOL_BORDER   = QColor(255, 205, 0)
CONFLICT_DOT    = QColor(210, 30,  30)
GRID_LINE       = QColor(218, 220, 230)
GRID_WEEKEND    = QColor(245, 240, 240)
DUE_LINE        = QColor(205, 50,  50)
TODAY_LINE      = QColor(24,  128, 255)
HEADER_BG       = QColor(38,  68,  128)
HEADER_FG       = QColor(255, 255, 255)
HEADER_WEEKEND  = QColor(65,  40,  50)
UTIL_HIGH       = QColor(190, 40,  40)
UTIL_MED        = QColor(225, 140, 0)
UTIL_LOW        = QColor(55,  165, 85)
MAT_ACCENT      = QColor(108, 68,  168)   # material plan accent
LATE_ACCENT     = QColor(192, 40,  40)    # late plan accent

ROW_BG_A        = QColor(255, 255, 255)
ROW_BG_B        = QColor(246, 248, 254)

# ── New card design (white card + coloured left strip) ────────────────────────
CARD_BG         = QColor(255, 255, 255)
CARD_BORDER_CLR = QColor(225, 228, 236)
CARD_TEXT_L1    = QColor(22,  33,  61)
CARD_TEXT_L2    = QColor(58,  66,  85)
CARD_TEXT_L3    = QColor(139, 147, 168)
DUE_TAG_BG      = QColor(254, 243, 224)
DUE_TAG_FG      = QColor(185, 118, 10)
DUE_TAG_LATE_BG = QColor(251, 231, 231)
DUE_TAG_LATE_FG = QColor(194, 52,  47)
CHECK_FILL      = QColor(63,  124, 196, 30)

CARD_H     = 68   # card slot height (was 84)
DAY_W      = 84
SHIFT_W    = DAY_W
HEADER_H   = 52
UTIL_ROW_H = 18
UTIL_H     = UTIL_ROW_H   # single cap-row (was × 2)
DIM_COL_W  = 110
SKU_COL_W  = 72
Y_LABEL_W  = DIM_COL_W  # kept for any external references; canvas uses _y_label_w property

# ─── Y-axis dimension helpers ──────────────────────────────────────────────────
# Available dim names (shown in toolbar dropdowns)
Y_DIM_OPTIONS = ["—", "SO", "Line", "SKU", "Room", "Process", "Seq"]

def _dim_key(dim: str, plan: dict) -> str:
    """Return the sort-key value for a plan along one Y-axis dimension."""
    et = plan.get("entity_type", "SKU")
    if dim == "SO":
        return plan.get("so_number") or ("[MAT]" if et == "MATERIAL" else "—")
    if dim == "Line":
        return plan.get("line_item") or "—"
    if dim == "SKU":
        return (f"[MAT]{plan.get('entity_code','')}"
                if et == "MATERIAL" else (plan.get("sku_code") or "—"))
    if dim == "Room":
        return plan.get("room_code") or "—"
    if dim == "Process":
        return plan.get("process_name") or "—"
    if dim == "Seq":
        return f"{int(plan.get('process_seq') or 1):03d}"
    return "—"

def _dim_label(dim: str, key_val: str) -> str:
    """Human-readable label from the stored key value."""
    if dim == "Seq":
        return str(int(key_val)) if key_val.isdigit() else key_val
    return key_val
CHECKBOX_S  = 9    # checkbox visual size
CARD_RADIUS = 5
PILL_MARGIN = 3    # top padding before pill row
PILL_H      = 14   # pill row height (checkbox / conflict / badges)
PILL_GAP    = 2    # gap after pill row before text
TEXT_PAD_L  = 8    # left text offset (4px border + 4px gap)
CARD_TOP_H  = PILL_MARGIN + PILL_H + PILL_GAP  # = 19px, y-offset where l1 starts
CARD_BOT_H  = 0


def _color_for_key(key: str) -> QColor:
    return QColor(PALETTE[abs(hash(key)) % len(PALETTE)])


# ─── Consolidation engine ─────────────────────────────────────────────────────

class ConsolidationEngine:
    @staticmethod
    def validate(plans: List[Dict]) -> Tuple[bool, str]:
        if len(plans) < 2:
            return False, "Select at least 2 plan blocks."
        skus  = {p["sku_code"]     for p in plans}
        rooms = {p["room_code"]    for p in plans}
        procs = {p["process_name"] for p in plans}
        if len(skus) > 1:
            return False, f"All selected blocks must share the same SKU.\nFound: {', '.join(skus)}"
        if len(rooms) > 1:
            return False, f"All selected blocks must share the same room.\nFound: {', '.join(rooms)}"
        if len(procs) > 1:
            return False, f"All selected blocks must share the same process.\nFound: {', '.join(procs)}"
        return True, "OK"

    @staticmethod
    def consolidate(plans: List[Dict], shifts: List[Dict]) -> Tuple[bool, str]:
        ok, msg = ConsolidationEngine.validate(plans)
        if not ok:
            return False, msg

        group_id  = str(uuid.uuid4())[:8].upper()
        sku_code  = plans[0]["sku_code"]

        shift_order = {s["shift_no"]: i for i, s in
                       enumerate(sorted(shifts, key=lambda s: s["shift_no"]))}
        plans_sorted = sorted(
            plans,
            key=lambda p: (p["plan_date"],
                           shift_order.get(p["shift_no"], p["shift_no"])))

        from collections import defaultdict
        slot_groups: Dict[Tuple[str, int], List[Dict]] = defaultdict(list)
        for p in plans_sorted:
            slot_groups[(p["plan_date"], p["shift_no"])].append(p)

        merged_slots = []
        for (d, sno), group in sorted(slot_groups.items()):
            total_qty = sum(p["qty_planned"] for p in group)
            so_tags   = list({f"{p['so_number']}/{p['line_item']}" for p in group})
            if any(p["is_locked"] for p in group):
                return False, "Cannot consolidate locked plan blocks. Unlock first."
            anchor = group[0]
            memo_parts = [f"CONSOL-{group_id}"]
            if len(so_tags) > 1:
                memo_parts.append("SOs:" + ",".join(so_tags))
            merged_slots.append({
                "anchor_id": anchor["plan_id"],
                "delete_ids": [p["plan_id"] for p in group[1:]],
                "plan_date": d, "shift_no": sno,
                "qty": total_qty,
                "memo": " | ".join(memo_parts),
                "is_final_seq": any(p.get("is_final_seq") for p in group),
            })

        packed = ConsolidationEngine._pack_consecutive(merged_slots, shifts)

        for slot in packed:
            for did in slot.get("delete_ids", []):
                PlanRepo.delete(did, reason=f"consolidation-merge-{group_id}")
            PlanRepo.update(slot["anchor_id"], {
                "plan_date": slot["plan_date"],
                "shift_no":  slot["shift_no"],
                "qty_planned": slot["qty"],
                "is_consolidated": 1,
                "consolidation_group": group_id,
                "is_locked": 1,
                "memo": slot["memo"],
            }, reason=f"consolidation-{group_id}")

        return True, (f"Consolidation complete.\n"
                      f"Group: {group_id}  |  {len(packed)} shift block(s)  |  SKU: {sku_code}")

    @staticmethod
    def _pack_consecutive(merged_slots, shifts) -> List[Dict]:
        if not merged_slots:
            return []
        shift_seq = sorted(shifts, key=lambda s: s["shift_no"])
        shift_nos = [s["shift_no"] for s in shift_seq]
        start_date  = merged_slots[0]["plan_date"]
        start_shift = merged_slots[0]["shift_no"]
        cur_date  = datetime.strptime(start_date, "%Y-%m-%d").date()
        cur_shift_idx = shift_nos.index(start_shift) if start_shift in shift_nos else 0
        result = []
        for slot in merged_slots:
            result.append({**slot,
                           "plan_date": cur_date.strftime("%Y-%m-%d"),
                           "shift_no": shift_nos[cur_shift_idx]})
            cur_shift_idx += 1
            if cur_shift_idx >= len(shift_nos):
                cur_shift_idx = 0
                cur_date += timedelta(days=1)
        return result

    @staticmethod
    def break_group(group_id: str) -> Tuple[bool, str]:
        with_group = [p for p in PlanRepo.all()
                      if p.get("consolidation_group") == group_id]
        if not with_group:
            return False, f"Group {group_id} not found."
        for p in with_group:
            PlanRepo.update(p["plan_id"], {
                "is_consolidated": 0,
                "consolidation_group": None,
                "is_locked": 0,
            }, reason=f"break-consolidation-{group_id}")
        return True, f"Consolidation group {group_id} broken ({len(with_group)} blocks)."


# ─── Frozen date-axis header ──────────────────────────────────────────────────

class GanttHeaderWidget(QWidget):
    """Fixed header that stays at the top when the Gantt scrolls vertically.
    Horizontal scroll is synced with the QScrollArea scrollbar so dates pan
    correctly, while the Y-label corner remains anchored."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(HEADER_H)
        # Data references — set by GanttTab.refresh()
        self.shift_view   : bool       = False
        self.horizon_days : int        = 28
        self.start_date   : date       = date.today()
        self._shifts      : list       = []
        self._scroll_h    : int        = 0    # horizontal scrollbar value
        self._y_label_w   : int        = DIM_COL_W

    def sync_from(self, canvas: 'GanttCanvas'):
        """Copy display parameters from the canvas and repaint."""
        self.shift_view   = canvas.shift_view
        self.horizon_days = canvas.horizon_days
        self.start_date   = canvas.start_date
        self._shifts      = canvas._shifts
        self._y_label_w   = canvas._y_label_w
        self.update()

    def set_scroll_h(self, val: int):
        self._scroll_h = val
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        vw = self.width()

        bold9 = QFont(); bold9.setBold(True); bold9.setPointSize(9)
        f8    = QFont(); f8.setPointSize(8)

        yw = self._y_label_w

        # ── Date columns (clipped to right of Y-label, panned by scroll) ──
        p.save()
        p.setClipRect(yw, 0, max(0, vw - yw), HEADER_H)
        p.translate(-self._scroll_h, 0)

        # Background
        p.fillRect(0, 0, yw + self.horizon_days * self._col_w() + 200,
                   HEADER_H, HEADER_BG)

        if not self.shift_view:
            for col in range(self.horizon_days):
                d  = self.start_date + timedelta(days=col)
                x  = yw + col * DAY_W
                if d.weekday() >= 5:
                    p.fillRect(x, 0, DAY_W, HEADER_H, HEADER_WEEKEND)
                p.setPen(QPen(QColor(255, 255, 255, 30)))
                p.drawLine(x, 4, x, HEADER_H - 4)
                p.setPen(QPen(HEADER_FG))
                p.setFont(bold9)
                p.drawText(QRect(x + 2, 2, DAY_W - 4, HEADER_H // 2 - 1),
                           Qt.AlignmentFlag.AlignCenter, d.strftime("%m/%d"))
                p.setFont(f8)
                p.setPen(QPen(QColor(200, 215, 240)))
                p.drawText(QRect(x + 2, HEADER_H // 2, DAY_W - 4, HEADER_H // 2 - 2),
                           Qt.AlignmentFlag.AlignCenter, d.strftime("%a"))
        else:
            n = len(self._shifts)
            if n:
                for day in range(self.horizon_days):
                    d  = self.start_date + timedelta(days=day)
                    x0 = yw + day * n * SHIFT_W
                    p.setFont(bold9); p.setPen(QPen(HEADER_FG))
                    p.drawText(QRect(x0, 2, n * SHIFT_W, HEADER_H // 2 - 2),
                               Qt.AlignmentFlag.AlignCenter, d.strftime("%m/%d"))
                    p.setFont(f8)
                    for si, shift in enumerate(self._shifts):
                        sx = x0 + si * SHIFT_W
                        p.setPen(QPen(QColor(255, 255, 255, 30)))
                        p.drawLine(sx, HEADER_H // 2, sx, HEADER_H - 2)
                        p.setPen(QPen(QColor(200, 215, 240)))
                        p.drawText(QRect(sx, HEADER_H // 2, SHIFT_W, HEADER_H // 2),
                                   Qt.AlignmentFlag.AlignCenter,
                                   f"S{shift['shift_no']}")

        p.restore()

        # ── Y-label corner (always fixed at x=0) ──
        p.fillRect(0, 0, yw, HEADER_H, QColor(50, 82, 148))
        # Thin separator between corner and date columns
        p.setPen(QPen(QColor(255, 255, 255, 60)))
        p.drawLine(yw, 0, yw, HEADER_H)

        p.end()

    def _col_w(self) -> int:
        return SHIFT_W if self.shift_view else DAY_W


# ─── Gantt Canvas ─────────────────────────────────────────────────────────────

class GanttCanvas(QWidget):
    planMoved        = pyqtSignal(int, str, int, str)
    planSelected     = pyqtSignal(dict)
    selectionChanged = pyqtSignal(list)

    # Legacy mode constants kept for any external references
    Y_MODE_ROOM = "room"
    Y_MODE_SO   = "so"
    Y_MODE_SKU  = "sku"

    @property
    def _y_label_w(self) -> int:
        return max(1, len(self.y_dims)) * DIM_COL_W

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.parent_tab = None

        # y_dims: ordered list of dimension names, e.g. ["Room"] or ["SKU", "Process"]
        self.y_dims: List[str] = ["Room"]
        self.shift_view   = False
        self.horizon_days = 28
        self.start_date   : date = date.today()

        self._plans    : List[Dict] = []
        self._sos      : Dict       = {}
        self._skus     : Dict       = {}
        self._shifts   : List[Dict] = []
        self._rows     : List[str]  = []
        self._conflicts: List[Dict] = []
        self._search_filter: str    = ""

        self._checked  : Set[int] = set()
        self._drag_plan_id  : Optional[int]    = None
        self._drag_origin   : Optional[QPoint] = None
        self._drag_offset   : QPoint = QPoint(0, 0)
        self._drag_rect     : Optional[QRect]  = None
        self._drag_invalid  : bool             = False
        self._drag_split    : bool             = False   # Ctrl+drag → split mode
        # (room_code, process_name) pairs that are valid — populated in load_data
        self._room_proc_set: set               = set()

        self._cell_map      : Dict[int, QRect] = {}
        self._check_rects   : Dict[int, QRect] = {}
        self._check_hit_rects: Dict[int, QRect] = {}
        self._cap_map     : Dict[Tuple, Tuple] = {}
        # headcount util: (date_str, shift_no) -> (alloc, crp_total)
        self._hc_map      : Dict[Tuple, Tuple] = {}
        # plan_id -> (slot_index, total_in_slot) for vertical stacking
        self._plan_layout : Dict[int, Tuple[int, int]] = {}
        # O(1) row lookup built in _build_rows()
        self._row_index   : Dict[str, int] = {}
        # SO -> sorted row indices, precomputed in load_data()
        self._so_rows_cache: Dict[Tuple, List[int]] = {}
        # Calendar unavailability maps
        self._closed_map  : Dict[Tuple[int, int], str] = {}          # (ri,col)->status for hatching
        self._slot_closed : Dict[Tuple[str, str, int], str] = {}     # (room,date,shift)->status for badge
        # Per-row heights and cumulative Y positions (variable when multi-card slots)
        self._row_heights  : List[int] = []
        self._row_y_list   : List[int] = []
        self._total_body_h : int       = 0

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

    # ── Data loading ─────────────────────────────────────────────────────────

    def load_data(self, plans, sos, skus, shifts, conflicts, mat_groups=None):
        self._plans     = plans
        self._sos       = {(s["so_number"], s["sku_code"], s["line_item"]): s
                           for s in sos}
        self._skus      = {s["sku_code"]: s for s in skus}
        self._mat_groups: Dict[str, List[Dict]] = mat_groups or {}
        self._shifts   = sorted(shifts, key=lambda s: s["shift_no"])
        self._conflicts = conflicts
        valid_ids = {p["plan_id"] for p in plans}
        self._checked = self._checked & valid_ids
        self._room_proc_set = {(r["room_code"], r["process_name"]) for r in RoomRepo.all()}
        self._build_rows()
        # Precompute SO -> row indices for O(1) lookup in _draw_due_lines
        so_rows_tmp: Dict[Tuple, set] = {}
        for plan in self._plans:
            if plan.get("entity_type") == "MATERIAL":
                continue
            k = (plan["so_number"], plan["sku_code"], plan["line_item"])
            ri = self._row_index.get(self._plan_row_key(plan))
            if ri is not None:
                so_rows_tmp.setdefault(k, set()).add(ri)
        self._so_rows_cache = {k: sorted(v) for k, v in so_rows_tmp.items()}
        self._build_cap_map()
        self._build_layout_and_heights()
        self._build_hc_map()
        self._build_closed_map()
        self._update_size()
        self.update()

    def set_search_filter(self, text: str):
        self._search_filter = text
        self.update()

    def _plan_row_key(self, plan: Dict) -> str:
        """Return the pipe-joined row key for a plan based on current y_dims."""
        return "|".join(_dim_key(d, plan) for d in self.y_dims)

    def _build_rows(self):
        keys = {self._plan_row_key(p) for p in self._plans}
        # When single Room dim and no plans, show all configured rooms
        if self.y_dims == ["Room"] and not keys:
            keys = {r for r in RoomRepo.rooms()}
        self._rows = sorted(keys)
        # O(1) lookup dict
        self._row_index = {r: i for i, r in enumerate(self._rows)}

    def _build_cap_map(self):
        # Read room/shift data directly from DB so UPH/time edits reflect immediately
        # without needing to re-run auto_plan.
        rp_fresh = {(r["room_code"], r["process_name"]): r for r in RoomRepo.all()}
        sh_fresh = {s["shift_no"]: s for s in ShiftRepo.all()}

        self._cap_map = {}
        plan_inner: Dict[Tuple, int] = {}
        for p in self._plans:
            uom = self._skus.get(p["sku_code"], {}).get("uom", 1) or 1
            key = (p["plan_date"], p["room_code"], p["process_name"], p["shift_no"])
            plan_inner[key] = plan_inner.get(key, 0) + sku_to_inner(p["qty_planned"], uom)
        for key, used in plan_inner.items():
            d, room, proc, sno = key
            rp = rp_fresh.get((room, proc))
            sh = sh_fresh.get(sno)
            if rp and sh:
                hc = scheduler.get_slot_hc(d, room, proc, sno)
                cap = shift_capacity_inner(rp, sh, hc)
            else:
                cap = 1.0
            self._cap_map[key] = (used, max(cap, 1))

    def _build_layout_and_heights(self):
        """Assign vertical slot index to each plan (for stacking) and compute
        per-row heights based on the max number of plans in any slot of that row.
        """
        from collections import defaultdict
        # slot key: (plan_date, shift_no, row_key) — defines one visual cell
        slot_plans: Dict[Tuple, List[int]] = defaultdict(list)
        for p in self._plans:
            rk = self._plan_row_key(p)
            sk = (p["plan_date"], p["shift_no"], rk)
            slot_plans[sk].append(p["plan_id"])

        # Assign vertical index within each slot (sorted by plan_id for stability)
        self._plan_layout = {}
        for sk, pids in slot_plans.items():
            pids_sorted = sorted(pids)
            for i, pid in enumerate(pids_sorted):
                self._plan_layout[pid] = (i, len(pids_sorted))

        # Row heights: max cards-per-slot in each row × CARD_H
        row_max: Dict[str, int] = {}
        for (_, _, rk), pids in slot_plans.items():
            row_max[rk] = max(row_max.get(rk, 1), len(pids))

        self._row_y_list  = []
        self._row_heights = []
        y = self._body_top()
        for rk in self._rows:
            h = max(1, row_max.get(rk, 1)) * CARD_H
            self._row_y_list.append(y)
            self._row_heights.append(h)
            y += h
        self._total_body_h = y - self._body_top()

    def _build_hc_map(self):
        """Build headcount utilisation map: (date_str, shift_no) -> (alloc, crp_total).
        Denominator is CRP total HC, so ratio = people actually placed / people available.
        """
        self._hc_map = {}
        seen: Set[Tuple] = set()
        for p in self._plans:
            key = (p["plan_date"], p["shift_no"])
            if key in seen:
                continue
            seen.add(key)
            alloc, total = scheduler.get_shift_hc_total(
                p["plan_date"], p["shift_no"])
            self._hc_map[key] = (alloc, max(total, 1))

    def _build_closed_map(self):
        """Build two lookups for Y_MODE_ROOM:
        _closed_map: (row_idx, col_idx) -> 'closed'|'hold'  — used for grid hatching
        _slot_closed: (room_code, date_str, shift_no) -> 'closed'|'hold'  — used for per-plan badge
        In day view the col maps multiple shifts to one column; the slot lookup
        keeps exact shift granularity so plans on open shifts are not badged.
        """
        self._closed_map: Dict[Tuple[int, int], str] = {}
        self._slot_closed: Dict[Tuple[str, str, int], str] = {}
        if "Room" not in self.y_dims or not self._rows:
            return
        room_dim_idx = self.y_dims.index("Room")
        d0 = self.start_date.strftime("%Y-%m-%d")
        d1 = (self.start_date + timedelta(days=self.horizon_days - 1)).strftime("%Y-%m-%d")
        slots = CalendarRepo.get_unavailable_slots(d0, d1)
        for s in slots:
            room = s["room_code"]
            # Find all rows that include this room at the Room dimension
            for rk, ri in self._row_index.items():
                parts = rk.split("|")
                if len(parts) > room_dim_idx and parts[room_dim_idx] == room:
                    col = self._date_to_col(s["cal_date"], s["shift_no"])
                    if col is None:
                        continue
                    status = "hold" if s["is_hold"] else "closed"
                    key = (ri, col)
                    if key not in self._closed_map or status == "hold":
                        self._closed_map[key] = status
            self._slot_closed[(room, s["cal_date"], s["shift_no"])] = (
                "hold" if s["is_hold"] else "closed")

    # ── Geometry ──────────────────────────────────────────────────────────────

    def _col_count(self):
        return self.horizon_days * len(self._shifts) if self.shift_view else self.horizon_days

    def _col_w(self):
        return SHIFT_W if self.shift_view else DAY_W

    def _total_w(self):
        return self._y_label_w + self._col_count() * self._col_w()

    def _total_h(self):
        return self._body_top() + self._total_body_h + 20

    def _row_at_y(self, y: int) -> int:
        """Return the row index at pixel y, or -1 if outside all rows."""
        for ri, (ry, rh) in enumerate(zip(self._row_y_list, self._row_heights)):
            if ry <= y < ry + rh:
                return ri
        return -1

    def _update_size(self):
        self.setMinimumSize(self._total_w(), max(400, self._total_h()))

    def _body_top(self):
        """Y pixel where gantt body starts (below header + util bars)."""
        return HEADER_H + UTIL_H

    def _date_to_col(self, d: str, shift_no: int = 1) -> Optional[int]:
        try:
            delta = (datetime.strptime(d, "%Y-%m-%d").date() - self.start_date).days
        except ValueError:
            return None
        if delta < 0 or delta >= self.horizon_days:
            return None
        if self.shift_view and self._shifts:
            idx = next((i for i, s in enumerate(self._shifts)
                        if s["shift_no"] == shift_no), 0)
            return delta * len(self._shifts) + idx
        return delta

    def _col_to_date_shift(self, col: int) -> Tuple[date, int]:
        if self.shift_view and self._shifts:
            n = len(self._shifts)
            d = self.start_date + timedelta(days=col // n)
            sno = self._shifts[col % n]["shift_no"]
            return d, sno
        return self.start_date + timedelta(days=col), (
            self._shifts[0]["shift_no"] if self._shifts else 1)

    def _row_for_plan(self, plan: Dict) -> Optional[int]:
        return self._row_index.get(self._plan_row_key(plan))

    def _rows_for_so(self, so_no: str, sku: str, li: str) -> List[int]:
        """Return all row indices where this SO has plans."""
        rows = set()
        for p in self._plans:
            if p["so_number"] == so_no and p["sku_code"] == sku and p["line_item"] == li:
                ri = self._row_for_plan(p)
                if ri is not None:
                    rows.add(ri)
        return sorted(rows)

    def _plan_rect(self, plan: Dict) -> Optional[QRect]:
        col = self._date_to_col(plan["plan_date"], plan["shift_no"])
        row = self._row_for_plan(plan)
        if col is None or row is None or row >= len(self._row_y_list):
            return None
        slot_idx = self._plan_layout.get(plan["plan_id"], (0, 1))[0]
        col_w = self._col_w()
        x = self._y_label_w + col * col_w + 1
        w = col_w - 2
        y = self._row_y_list[row] + slot_idx * CARD_H + 2
        return QRect(x, y, w, CARD_H - 4)

    def _checkbox_rect(self, plan_rect: QRect) -> QRect:
        pill_y = plan_rect.y() + PILL_MARGIN
        return QRect(plan_rect.x() + 8,
                     pill_y + (PILL_H - CHECKBOX_S) // 2,
                     CHECKBOX_S, CHECKBOX_S)

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_grid(p)
        self._draw_header(p)
        self._draw_util_bars(p)
        self._draw_y_labels(p)
        self._draw_due_lines(p)
        self._draw_today_line(p)
        self._draw_plans(p)
        if self._drag_rect:
            self._draw_drag_ghost(p)
        p.end()

    def _draw_grid(self, p: QPainter):
        yw = self._y_label_w
        w, h = self._total_w(), self._total_h()
        # Alternating row backgrounds (variable height)
        for ri in range(len(self._rows)):
            y  = self._row_y_list[ri]
            rh = self._row_heights[ri]
            bg = ROW_BG_A if ri % 2 == 0 else ROW_BG_B
            p.fillRect(yw, y, w - yw, rh, bg)
        # Weekend column tint
        if not self.shift_view:
            for col in range(self.horizon_days):
                d = self.start_date + timedelta(days=col)
                if d.weekday() >= 5:
                    x = yw + col * DAY_W
                    p.fillRect(x, self._body_top(), DAY_W, h - self._body_top(),
                               GRID_WEEKEND)
        # Unavailable cells: closed=gray hatch, hold=orange hatch
        if self._closed_map:
            cw = self._col_w()
            bt = self._body_top()
            for (ri, col), status in self._closed_map.items():
                if ri >= len(self._row_y_list):
                    continue
                cy  = self._row_y_list[ri]
                crh = self._row_heights[ri]
                cx  = yw + col * cw
                if status == "hold":
                    base  = QColor(255, 160, 40, 55)
                    hatch = QColor(210, 120, 20, 110)
                else:
                    base  = QColor(160, 160, 165, 60)
                    hatch = QColor(120, 120, 128, 120)
                p.fillRect(cx, cy, cw, crh, base)
                old_pen = p.pen()
                p.setPen(QPen(hatch, 1))
                step = 8
                x0, y0, x1, y1 = cx, cy, cx + cw, cy + crh
                # diagonal lines top-left → bottom-right
                diag_range = range(-(crh), cw, step)
                for offset in diag_range:
                    ax = x0 + offset
                    p.drawLine(max(ax, x0), y0 if ax >= x0 else y0 + (x0 - ax),
                               min(ax + crh, x1), y0 + crh if ax + crh <= x1 else y1 - ((ax + crh) - x1))
                p.setPen(old_pen)
        # Grid lines
        p.setPen(QPen(GRID_LINE, 1))
        for col in range(self._col_count() + 1):
            x = self._y_label_w + col * self._col_w()
            p.drawLine(x, HEADER_H, x, h)
        for ri in range(len(self._rows)):
            y = self._row_y_list[ri]
            p.drawLine(0, y, w, y)
        y_end = self._body_top() + self._total_body_h
        p.drawLine(0, y_end, w, y_end)

    def _draw_header(self, p: QPainter):
        # Header background only — date text is rendered by GanttHeaderWidget
        # (the fixed overlay above the scroll area) so it stays frozen on scroll.
        p.fillRect(0, 0, self._total_w(), HEADER_H, HEADER_BG)
        p.fillRect(0, 0, self._y_label_w, HEADER_H, QColor(50, 82, 148))

    def _draw_util_bars(self, p: QPainter):
        """Draw single capacity-utilisation row below the date header."""
        y0 = HEADER_H
        yw = self._y_label_w

        # Background strip
        p.fillRect(yw, y0, self._col_count() * self._col_w(), UTIL_ROW_H,
                   QColor(30, 58, 110))

        col_cap: Dict[int, Tuple[float, float]] = {}
        for (ds, room, proc, sno), (used, cap) in self._cap_map.items():
            col = self._date_to_col(ds, sno)
            if col is None:
                continue
            cu, cc = col_cap.get(col, (0.0, 0.0))
            col_cap[col] = (cu + used, cc + cap)

        f7 = QFont("Segoe UI", 7)
        p.setFont(f7)
        for col, (used, cap) in col_cap.items():
            ratio = (used / cap) if cap > 0 else 0
            x     = yw + col * self._col_w()
            color = UTIL_HIGH if ratio > 0.9 else UTIL_MED if ratio > 0.6 else UTIL_LOW
            fill  = min(ratio, 1.0)
            bar_h = UTIL_ROW_H - 2
            p.fillRect(x + 1, y0 + 1, int((self._col_w() - 2) * fill), bar_h, color)
            if ratio > 0.9:
                label = f"{int(ratio*100)}%"
                p.setPen(QPen(Qt.GlobalColor.white))
                p.drawText(QRect(x+1, y0, self._col_w()-2, UTIL_ROW_H),
                           Qt.AlignmentFlag.AlignCenter, label)

    def _draw_y_labels(self, p: QPainter):
        """Draw Y-axis labels with N-depth spanning columns."""
        yw    = self._y_label_w
        ndims = len(self.y_dims)
        cw    = yw // ndims if ndims else yw   # width per dim column

        sep_group = QColor(185, 190, 208)
        sep_inner = QColor(215, 218, 230)
        grp_bgs   = [QColor(228, 237, 255), QColor(225, 244, 228)]
        fg_bold   = QColor(25, 45, 100)
        fg_normal = QColor(48, 52, 72)

        p.fillRect(0, self._body_top(), yw, self._total_body_h, QColor(245, 245, 250))

        if ndims == 1:
            # Simple flat list — no spanning needed
            f9 = QFont("Arial", 9)
            p.setFont(f9)
            for ri, rk in enumerate(self._rows):
                y  = self._row_y_list[ri]
                rh = self._row_heights[ri]
                bg = grp_bgs[ri % len(grp_bgs)]
                p.fillRect(0, y, yw, rh, bg)
                p.setPen(QPen(sep_inner))
                p.drawLine(0, y, yw, y)
                p.setPen(QPen(fg_normal))
                label = _dim_label(self.y_dims[0], rk.split("|")[0])
                p.drawText(QRect(6, y + 4, yw - 10, rh - 8),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           label)
            y_end = self._body_top() + self._total_body_h
            p.setPen(QPen(sep_group, 1))
            p.drawLine(0, y_end, yw, y_end)
            p.drawLine(yw - 1, self._body_top(), yw - 1, y_end)
            return

        # Multi-depth: for each depth d draw spanning group labels
        fb = QFont("Arial", 9); fb.setBold(True)
        f8 = QFont("Arial", 8)

        for d in range(ndims):
            x_col   = d * cw
            is_last = (d == ndims - 1)
            p.setFont(f8 if is_last else fb)

            # Group consecutive rows sharing the same prefix up to depth d
            groups: List[Tuple[tuple, List[int]]] = []
            for ri, rk in enumerate(self._rows):
                parts  = rk.split("|")
                prefix = tuple(parts[:d + 1])
                if not groups or groups[-1][0] != prefix:
                    groups.append((prefix, []))
                groups[-1][1].append(ri)

            for gi, (prefix, row_idxs) in enumerate(groups):
                bg    = grp_bgs[gi % len(grp_bgs)]
                y_top = self._row_y_list[row_idxs[0]]
                grp_h = sum(self._row_heights[ri] for ri in row_idxs)

                p.fillRect(x_col, y_top, cw, grp_h, bg)

                label = _dim_label(self.y_dims[d], prefix[d])
                p.setPen(QPen(fg_bold if not is_last else fg_normal))
                align = (Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
                         if not is_last
                         else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                p.drawText(QRect(x_col + 5, y_top + 4, cw - 8, grp_h - 8), align, label)

                # Top border for this group
                pen_style = sep_group if not is_last else sep_inner
                p.setPen(QPen(pen_style, 1))
                p.drawLine(x_col, y_top, x_col + cw, y_top)

                # Dotted separators within the last depth
                if is_last:
                    for ri in row_idxs[1:]:
                        y = self._row_y_list[ri]
                        p.setPen(QPen(sep_inner, 1, Qt.PenStyle.DotLine))
                        p.drawLine(x_col, y, x_col + cw, y)

        # Bottom border across full width
        y_end = self._body_top() + self._total_body_h
        p.setPen(QPen(sep_group, 1))
        p.drawLine(0, y_end, yw, y_end)

        # Vertical column dividers
        p.setPen(QPen(QColor(195, 198, 215), 1))
        for d in range(1, ndims):
            x = d * cw
            p.drawLine(x, self._body_top(), x, y_end)
        p.drawLine(yw - 1, self._body_top(), yw - 1, y_end)

    def _draw_due_lines(self, p: QPainter):
        """
        Draw due-date lines only across the rows where the SO has plans.
        Lines start at body_top (below util bars) and span only relevant rows.
        """
        pen = QPen(DUE_LINE, 1, Qt.PenStyle.DashLine)
        f7  = QFont("Arial", 7)
        p.setFont(f7)

        for (so_no, sku, li), so in self._sos.items():
            col = self._date_to_col(so["due_date"])
            if col is None:
                continue
            rows = self._so_rows_cache.get((so_no, sku, li))
            if not rows:
                continue

            x = self._y_label_w + col * self._col_w() + self._col_w() // 2

            for ri in rows:
                y_top = self._row_y_list[ri]
                y_bot = y_top + self._row_heights[ri]
                p.setPen(pen)
                p.drawLine(x, y_top, x, y_bot)

            # Label at top of first row
            top_row = rows[0]
            y_label = self._row_y_list[top_row] + 3
            p.setPen(QPen(DUE_LINE))
            label = so.get("customer_name") or so_no
            p.drawText(x + 2, y_label + 8, label[:12])

    def _draw_today_line(self, p: QPainter):
        col = self._date_to_col(date.today().strftime("%Y-%m-%d"))
        if col is None:
            return
        x = self._y_label_w + col * self._col_w()
        p.setPen(QPen(TODAY_LINE, 3))
        # Today line also starts at body_top
        p.drawLine(x, self._body_top(), x, self._total_h())

    def _draw_plans(self, p: QPainter):
        self._cell_map.clear()
        self._check_rects.clear()
        self._check_hit_rects.clear()
        conflict_slots = {
            (c["plan_date"], c["room_code"], c["process_name"], c["shift_no"])
            for c in self._conflicts
        }
        consol_groups: Dict[str, List[QRect]] = {}
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        f_l1 = QFont("Segoe UI", 9, QFont.Weight.Bold)
        f_l2 = QFont("Segoe UI", 8)
        f_l2.setWeight(QFont.Weight.Medium)
        f_l3 = QFont("Segoe UI", 7)
        f_tag = QFont("Segoe UI", 7, QFont.Weight.Bold)
        f_lock = QFont("Segoe UI", 7)

        for plan in self._plans:
            rect = self._plan_rect(plan)
            if not rect:
                continue
            self._cell_map[plan["plan_id"]] = rect
            cb_vis = self._checkbox_rect(rect)
            self._check_rects[plan["plan_id"]] = cb_vis
            cx = cb_vis.x() + cb_vis.width() // 2
            cy = cb_vis.y() + cb_vis.height() // 2
            self._check_hit_rects[plan["plan_id"]] = QRect(cx - 10, cy - 10, 20, 20)

            # Search filter: dim non-matching plans
            if self._search_filter:
                haystack = " ".join(filter(None, [
                    plan.get("so_number", ""), plan.get("sku_code", ""),
                    plan.get("entity_code", ""), plan.get("room_code", ""),
                    plan.get("process_name", ""),
                    (self._sos.get((plan["so_number"], plan["sku_code"],
                                    plan["line_item"])) or {}).get("customer_name", ""),
                ])).lower()
                if self._search_filter not in haystack:
                    p.setOpacity(0.15)
                else:
                    p.setOpacity(1.0)
            else:
                p.setOpacity(1.0)

            is_mat = plan.get("entity_type") == "MATERIAL"
            so = self._sos.get((plan["so_number"], plan["sku_code"], plan["line_item"]))
            is_late = (so and
                       datetime.strptime(so["due_date"], "%Y-%m-%d").date() < date.today()
                       and plan.get("qty_produced", 0) < plan["qty_planned"])
            accent = (MAT_ACCENT if is_mat
                      else LATE_ACCENT if is_late
                      else _color_for_key(plan["sku_code"]))

            # ── White card background ─────────────────────────────────────────
            card_path = QPainterPath()
            card_path.addRoundedRect(QRectF(rect), CARD_RADIUS, CARD_RADIUS)
            p.setBrush(QBrush(CARD_BG))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(card_path)

            # ── Left accent strip (4 px, clipped to card shape) ──────────────
            p.setClipPath(card_path)
            p.setBrush(QBrush(accent))
            p.drawRect(QRect(rect.x(), rect.y(), 4, rect.height()))
            p.setClipping(False)

            # ── Checked tint ──────────────────────────────────────────────────
            if plan["plan_id"] in self._checked:
                p.setBrush(QBrush(CHECK_FILL))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

            # ── Card border ───────────────────────────────────────────────────
            if plan["is_locked"]:
                p.setPen(QPen(QColor(170, 178, 200), 1.5, Qt.PenStyle.DashLine))
            else:
                p.setPen(QPen(CARD_BORDER_CLR, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

            # ── PILL ROW (top PILL_H px) ──────────────────────────────────────
            pill_y = rect.y() + PILL_MARGIN
            cb = cb_vis

            # Checkbox
            p.setPen(QPen(QColor(180, 185, 200), 1))
            p.setBrush(QBrush(QColor(40, 40, 40, 210)
                               if plan["plan_id"] in self._checked
                               else Qt.GlobalColor.white))
            p.drawRoundedRect(cb, 2, 2)
            if plan["plan_id"] in self._checked:
                p.setPen(QPen(Qt.GlobalColor.white, 1.5))
                p.drawLine(cb.x()+1, cb.y()+4, cb.x()+3, cb.y()+7)
                p.drawLine(cb.x()+3, cb.y()+7, cb.x()+7, cb.y()+2)

            # Conflict dot
            if (plan["plan_date"], plan["room_code"],
                    plan["process_name"], plan["shift_no"]) in conflict_slots:
                p.setBrush(QBrush(CONFLICT_DOT))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(cb.right() + 3,
                              pill_y + (PILL_H - 7) // 2, 7, 7)

            # Lock icon (pill-row right side)
            lock_right = rect.right() - 3
            if plan["is_locked"]:
                p.setPen(QPen(QColor(160, 168, 190)))
                p.setFont(f_lock)
                lk_r = QRect(rect.right() - 13, pill_y + (PILL_H - 10) // 2, 11, 10)
                p.drawText(lk_r, Qt.AlignmentFlag.AlignCenter, "🔒")
                lock_right = lk_r.x() - 2

            # CLOSED / HOLD badge (right of pill row, before lock)
            _cell_status = self._slot_closed.get(
                (plan["room_code"], plan["plan_date"], plan["shift_no"]))
            if _cell_status:
                _lbl   = "⚠HOLD" if _cell_status == "hold" else "⚠CLOSED"
                _clr   = QColor(220, 100, 0) if _cell_status == "hold" else QColor(180, 30, 30)
                _bw    = 44
                _bh    = PILL_H - 4
                _br    = QRect(lock_right - _bw, pill_y + 2, _bw, _bh)
                p.setBrush(QBrush(_clr))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(_br, 2, 2)
                p.setPen(QPen(Qt.GlobalColor.white))
                p.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
                p.drawText(_br, Qt.AlignmentFlag.AlignCenter, _lbl)

            # Due-date badge (amber/red pill, show when within 7 days of due)
            due = so["due_date"] if so else None
            if due:
                due_dt  = datetime.strptime(due, "%Y-%m-%d").date()
                days_to = (due_dt - date.today()).days
                if days_to <= 7:
                    due_str = f"{due_dt.month}.{due_dt.day}"
                    tag_bg  = DUE_TAG_LATE_BG if days_to < 0 else DUE_TAG_BG
                    tag_fg  = DUE_TAG_LATE_FG if days_to < 0 else DUE_TAG_FG
                    fw = QFontMetrics(f_tag).horizontalAdvance(due_str) + 8
                    fh = 12
                    tx = lock_right - fw - (2 if not plan["is_locked"] else 0)
                    ty_tag = pill_y + (PILL_H - fh) // 2
                    tr = QRect(tx, ty_tag, fw, fh)
                    p.setBrush(QBrush(tag_bg))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(tr, 3, 3)
                    p.setPen(QPen(tag_fg))
                    p.setFont(f_tag)
                    p.drawText(tr, Qt.AlignmentFlag.AlignCenter, due_str)

            # ── TEXT ZONE ─────────────────────────────────────────────────────
            tx  = rect.x() + TEXT_PAD_L
            tw  = rect.width() - TEXT_PAD_L - 3
            ty  = rect.y() + CARD_TOP_H   # = rect.y() + 19

            code = plan["entity_code"] if is_mat else plan["sku_code"]
            p.setPen(QPen(CARD_TEXT_L1))
            p.setFont(f_l1)
            p.drawText(QRect(tx, ty, tw, 13),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(f_l1).elidedText(
                           code, Qt.TextElideMode.ElideRight, tw))

            qty_str = str(plan["qty_planned"])
            p.setPen(QPen(CARD_TEXT_L2))
            p.setFont(f_l2)
            p.drawText(QRect(tx, ty + 14, tw, 11),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       qty_str)

            if not is_mat and plan.get("so_number"):
                so_str = plan["so_number"]
                p.setPen(QPen(CARD_TEXT_L3))
                p.setFont(f_l3)
                p.drawText(QRect(tx, ty + 25, tw, 10),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           QFontMetrics(f_l3).elidedText(
                               so_str, Qt.TextElideMode.ElideRight, tw))

            if plan.get("consolidation_group"):
                consol_groups.setdefault(plan["consolidation_group"], []).append(rect)

        # Reset opacity before drawing consolidation overlays
        p.setOpacity(1.0)

        # Consolidation gold border
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for grp_id, rects in consol_groups.items():
            p.setPen(QPen(CONSOL_BORDER, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for r in rects:
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 5, 5)

    def _draw_drag_ghost(self, p: QPainter):
        if self._drag_invalid:
            p.fillRect(self._drag_rect, QColor(220, 50, 50, 150))
            p.setPen(QPen(QColor(180, 0, 0), 2, Qt.PenStyle.DashLine))
            p.drawRect(self._drag_rect)
            p.setPen(QPen(Qt.GlobalColor.white))
            p.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            p.drawText(self._drag_rect, Qt.AlignmentFlag.AlignCenter, "✕ Not supported")
        elif self._drag_split:
            p.fillRect(self._drag_rect, QColor(34, 197, 94, 150))
            p.setPen(QPen(QColor(20, 140, 60), 2, Qt.PenStyle.DashLine))
            p.drawRect(self._drag_rect)
            p.setPen(QPen(Qt.GlobalColor.white))
            p.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            p.drawText(self._drag_rect, Qt.AlignmentFlag.AlignCenter, "✂ Split")
        else:
            p.fillRect(self._drag_rect, QColor(100, 149, 237, 140))
            p.setPen(QPen(QColor(30, 80, 200), 2, Qt.PenStyle.DashLine))
            p.drawRect(self._drag_rect)

    # ── Mouse events ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        pos = event.pos()
        if event.button() == Qt.MouseButton.LeftButton:
            for pid, hit_rect in self._check_hit_rects.items():
                if hit_rect.contains(pos):
                    self._toggle_check(pid)
                    return
            plan = self._plan_at(pos)
            if plan:
                self._drag_plan_id = plan["plan_id"]
                self._drag_origin  = pos
                self._drag_split   = bool(
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                rect = self._cell_map.get(plan["plan_id"])
                if rect:
                    self._drag_offset = pos - rect.topLeft()
                self.planSelected.emit(plan)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self._drag_plan_id and self._drag_origin:
            if (pos - self._drag_origin).manhattanLength() > 6:
                plan = next((p for p in self._plans
                             if p["plan_id"] == self._drag_plan_id), None)
                if plan:
                    rect = self._cell_map.get(plan["plan_id"])
                    if rect:
                        self._drag_rect = QRect(pos - self._drag_offset, rect.size())
                        if "Room" in self.y_dims:
                            row = self._row_at_y(pos.y())
                            if 0 <= row < len(self._rows):
                                rk = self._rows[row]
                                room_idx = self.y_dims.index("Room")
                                parts = rk.split("|")
                                target_room = parts[room_idx] if room_idx < len(parts) else ""
                                proc = plan.get("process_name") or ""
                                self._drag_invalid = bool(target_room) and (target_room, proc) not in self._room_proc_set
                            else:
                                self._drag_invalid = False
                        else:
                            self._drag_invalid = False
                        self.update()
        plan = self._plan_at(pos)
        if plan:
            is_mat = plan.get("entity_type") == "MATERIAL"
            if is_mat:
                gid     = plan.get("material_group_id")
                members = self._mat_groups.get(gid, []) if gid else []
                demand_lines = "\n".join(
                    f"  {m['so_number']} / {m['sku_code']} / {m['line_item']}"
                    f"  qty:{m['qty_required']}  due:{m['due_date']}"
                    for m in members) or "  (no demand group)"
                tip = (f"[MATERIAL PLAN]\n"
                       f"Material: {plan['entity_code']}\n"
                       f"Process: {plan['process_name']}  "
                       f"Room: {plan['room_code']}  Shift: {plan['shift_no']}\n"
                       f"Date: {plan['plan_date']}  "
                       f"Planned: {plan['qty_planned']}  Produced: {plan['qty_produced']}\n"
                       f"{'🔒 LOCKED' if plan['is_locked'] else 'unlocked'}\n"
                       f"─── Demand Group ({len(members)} SOs) ───\n"
                       f"{demand_lines}")
            else:
                so  = self._sos.get((plan["so_number"], plan["sku_code"],
                                      plan["line_item"]), {})
                grp = plan.get("consolidation_group") or "-"
                customer = so.get("customer_name") or ""
                tip = (f"SO: {plan['so_number']}  SKU: {plan['sku_code']}  "
                       f"Line: {plan['line_item']}\n"
                       f"Customer: {customer}\n"
                       f"Room: {plan['room_code']}  Process: {plan['process_name']}\n"
                       f"Date: {plan['plan_date']}  Shift: {plan['shift_no']}\n"
                       f"Planned: {plan['qty_planned']}  Produced: {plan['qty_produced']}\n"
                       f"Due: {so.get('due_date','')}  "
                       f"{'🔒 LOCKED' if plan['is_locked'] else 'unlocked'}\n"
                       f"Consol group: {grp}  "
                       f"{'⭐ FINAL' if plan.get('is_final_seq') else ''}")
            QToolTip.showText(QCursor.pos(), tip, self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_plan_id:
            if self._drag_rect:
                if self._drag_invalid:
                    QMessageBox.warning(
                        self, "Invalid Move",
                        f"This room does not support the plan's process.\n"
                        f"Cannot move here.")
                else:
                    center = self._drag_rect.center()
                    col    = (center.x() - self._y_label_w) // self._col_w()
                    if 0 <= col < self._col_count():
                        new_date, new_shift = self._col_to_date_shift(col)
                        plan = next((p for p in self._plans
                                     if p["plan_id"] == self._drag_plan_id), None)
                        if plan and not plan["is_locked"]:
                            new_room = plan["room_code"]
                            if "Room" in self.y_dims:
                                row = self._row_at_y(center.y())
                                if 0 <= row < len(self._rows):
                                    rk = self._rows[row]
                                    room_idx = self.y_dims.index("Room")
                                    parts = rk.split("|")
                                    if room_idx < len(parts):
                                        new_room = parts[room_idx]
                            date_changed = (new_date.strftime("%Y-%m-%d") != plan["plan_date"]
                                            or new_shift != plan["shift_no"])
                            room_changed = (new_room != plan["room_code"])
                            if self._drag_split:
                                self._do_split_drop(plan, new_date, new_shift, new_room)
                            elif date_changed or room_changed:
                                reason, ok = QInputDialog.getText(
                                    self, "Move Reason",
                                    f"Reason for moving plan #{self._drag_plan_id}?")
                                if ok:
                                    fields: Dict = {
                                        "plan_date": new_date.strftime("%Y-%m-%d"),
                                        "shift_no":  new_shift,
                                    }
                                    if room_changed:
                                        fields["room_code"] = new_room
                                    PlanRepo.update(self._drag_plan_id, fields,
                                                    reason=reason)
                                    self.planMoved.emit(
                                        self._drag_plan_id,
                                        plan["plan_date"],
                                        plan["shift_no"],
                                        reason)
                        elif plan and plan["is_locked"]:
                            QMessageBox.information(
                                self, "Locked",
                                "This plan is locked. Unlock it first.")
            self._drag_plan_id = None
            self._drag_rect    = None
            self._drag_invalid = False
            self._drag_split   = False
            self.update()

    def _do_split_drop(self, plan: Dict, new_date, new_shift: int, new_room: str):
        orig_qty = plan["qty_planned"]
        if orig_qty <= 1:
            QMessageBox.warning(self, "Cannot Split",
                                "Plan quantity is 1 — nothing to split.")
            return
        split_qty, ok = QInputDialog.getInt(
            self, "Split Quantity",
            f"Qty to split off (original: {orig_qty}):",
            value=orig_qty // 2, min=1, max=orig_qty - 1)
        if not ok:
            return
        # Reduce original
        PlanRepo.update(plan["plan_id"],
                        {"qty_planned": orig_qty - split_qty},
                        reason=f"split {split_qty} off to {new_date} S{new_shift}")
        # Create new plan at drop location (copy of original minus a few fields)
        new_plan = {k: plan[k] for k in plan if k not in
                    ("plan_id", "qty_planned", "qty_produced",
                     "plan_date", "shift_no", "room_code",
                     "created_at", "updated_at",
                     "is_locked", "is_consolidated", "consolidation_group",
                     "memo")}
        new_plan["qty_planned"]  = split_qty
        new_plan["qty_produced"] = 0
        new_plan["plan_date"]    = new_date.strftime("%Y-%m-%d")
        new_plan["shift_no"]     = new_shift
        new_plan["room_code"]    = new_room
        new_plan["is_locked"]    = 0
        new_plan["is_consolidated"] = 0
        new_plan["consolidation_group"] = None
        new_plan["memo"]         = f"Split from plan #{plan['plan_id']}"
        PlanRepo.insert(new_plan)
        if self.parent_tab:
            self.parent_tab.refresh()

    def _toggle_check(self, plan_id: int):
        if plan_id in self._checked:
            self._checked.discard(plan_id)
        else:
            self._checked.add(plan_id)
        self.selectionChanged.emit(list(self._checked))
        self.update()

    def _plan_at(self, pos: QPoint) -> Optional[Dict]:
        for plan in self._plans:
            rect = self._cell_map.get(plan["plan_id"])
            if rect and rect.contains(pos):
                return plan
        return None

    # ── Context menu ──────────────────────────────────────────────────────────

    def _context_menu(self, pos: QPoint):
        plan = self._plan_at(pos)
        menu = QMenu(self)
        if plan:
            pid    = plan["plan_id"]
            locked = plan["is_locked"]
            grp    = plan.get("consolidation_group")
            menu.addAction("🔓 Unlock" if locked else "🔒 Lock",
                           lambda: self._toggle_lock(pid))
            menu.addAction("✂ Split",     lambda: self._split_plan(plan))
            menu.addAction("⬅ Pull Out", lambda: self._pull_out(plan))
            if grp:
                menu.addAction(f"🔗 Break Consolidation ({grp})",
                               lambda: self._break_consol(grp))
            menu.addSeparator()
            menu.addAction("📝 Edit Memo",  lambda: self._edit_memo(plan))
            menu.addAction("🗑 Delete Plan", lambda: self._delete_plan(pid))
        else:
            col = (pos.x() - self._y_label_w) // self._col_w()
            row = self._row_at_y(pos.y())
            if ("Room" in self.y_dims
                    and 0 <= col < self._col_count()
                    and 0 <= row < len(self._rows)):
                d, sno = self._col_to_date_shift(col)
                rk = self._rows[row]
                room_idx = self.y_dims.index("Room")
                parts = rk.split("|")
                room = parts[room_idx] if room_idx < len(parts) else ""
                if room:
                    menu.addAction("➕ Add Plan",
                                   lambda: self._add_plan(
                                       d.strftime("%Y-%m-%d"), sno, room))
                menu.addSeparator()
            menu.addAction("🚫 Add Hard Block",
                           lambda: self._add_hard_block(col, row))
        menu.exec(QCursor.pos())

    def _toggle_lock(self, plan_id):
        plan = PlanRepo.get(plan_id)
        if plan:
            PlanRepo.lock(plan_id, not plan["is_locked"])
            if self.parent_tab: self.parent_tab.refresh()

    def _split_plan(self, plan):
        qty = plan["qty_planned"]
        if qty < 2:
            QMessageBox.information(self, "Split", "Qty too small."); return
        split_qty, ok = QInputDialog.getInt(
            self, "Split", f"First half qty (total {qty}):", qty//2, 1, qty-1)
        if not ok: return
        PlanRepo.update(plan["plan_id"], {"qty_planned": split_qty}, reason="split")
        # Remainder stays in the same slot — planner drags it to the desired position
        new_plan = {**plan, "qty_planned": qty - split_qty,
                    "is_locked": 0, "memo": "split-remainder",
                    "is_consolidated": 0, "consolidation_group": None}
        for k in ("plan_id", "created_at", "updated_at"): new_plan.pop(k, None)
        PlanRepo.insert(new_plan)
        if self.parent_tab: self.parent_tab.refresh()

    def _pull_out(self, plan):
        d      = datetime.strptime(plan["plan_date"], "%Y-%m-%d").date()
        next_d = (d + timedelta(days=1)).strftime("%Y-%m-%d")
        existing = [p for p in PlanRepo.for_so(
                        plan["so_number"], plan["sku_code"], plan["line_item"])
                    if p["plan_date"] == next_d
                    and p["room_code"] == plan["room_code"]
                    and p["process_name"] == plan["process_name"]
                    and p["shift_no"] == 1]
        if existing:
            PlanRepo.update(existing[0]["plan_id"],
                            {"qty_planned": existing[0]["qty_planned"] + plan["qty_planned"]},
                            reason="pull_out_merge")
        else:
            np = {**plan, "plan_date": next_d, "shift_no": 1,
                  "is_locked": 0, "memo": "pull-out",
                  "is_consolidated": 0, "consolidation_group": None}
            for k in ("plan_id", "created_at", "updated_at"): np.pop(k, None)
            PlanRepo.insert(np)
        PlanRepo.delete(plan["plan_id"], reason="pull_out")
        if self.parent_tab: self.parent_tab.refresh()

    def _break_consol(self, group_id):
        ok, msg = ConsolidationEngine.break_group(group_id)
        QMessageBox.information(self, "Break Consolidation", msg)
        if ok and self.parent_tab: self.parent_tab.refresh()

    def _edit_memo(self, plan):
        text, ok = QInputDialog.getText(
            self, "Memo", "Memo:", text=plan.get("memo", ""))
        if ok:
            PlanRepo.update(plan["plan_id"], {"memo": text})
            if self.parent_tab: self.parent_tab.refresh()

    def _delete_plan(self, plan_id):
        reason, ok = QInputDialog.getText(self, "Delete Plan", "Reason:")
        if ok:
            PlanRepo.delete(plan_id, reason=reason)
            if self.parent_tab: self.parent_tab.refresh()

    def _add_plan(self, plan_date: str, shift_no: int, room_code: str):
        dlg = AddPlanDialog(plan_date, shift_no, room_code, self)
        if dlg.exec():
            if self.parent_tab: self.parent_tab.refresh()

    def _add_hard_block(self, col, row):
        if 0 <= col < self._col_count() and 0 <= row < len(self._rows):
            d, sno = self._col_to_date_shift(col)
            if "Room" in self.y_dims:
                rk = self._rows[row]
                room_idx = self.y_dims.index("Room")
                parts = rk.split("|")
                room = parts[room_idx] if room_idx < len(parts) else ""
                if not room:
                    return
                CalendarRepo.set_slot(
                    d.strftime("%Y-%m-%d"), sno, room, is_open=1, is_hold=1)
                if self.parent_tab: self.parent_tab.refresh()

    def checked_plans(self) -> List[Dict]:
        return [p for p in self._plans if p["plan_id"] in self._checked]

    def clear_checks(self):
        self._checked.clear()
        self.selectionChanged.emit([])
        self.update()


# ─── Priority Conflict Dialog ─────────────────────────────────────────────────

class PriorityConflictDialog(QDialog):
    """Shows OPEN SOs without a priority so the planner can assign them before re-planning."""

    def __init__(self, failed_so: str, failed_sku: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Priority Assignment Required")
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)

        # Header message
        msg = QLabel(
            f"<b>{failed_so} / {failed_sku}</b> could not be scheduled — capacity exceeded.\n\n"
            "The orders below have no priority set, so scheduling order is ambiguous.\n"
            "Assign priorities, then click <b>Save &amp; Re-Plan</b>.\n"
            "<i>(Lower number = higher priority.)</i>"
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("padding:8px; background:#fff8e1; border:1px solid #f59e0b; border-radius:4px;")
        layout.addWidget(msg)

        # Table
        self._sos = [s for s in SORepo.all()
                     if s.get("status") == "OPEN" and not s.get("priority")]
        self.table = QTableWidget(len(self._sos), 5)
        self.table.setHorizontalHeaderLabels(["SO Number", "SKU", "Customer", "Due Date", "Priority"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        self._spinboxes: list[tuple[dict, QSpinBox]] = []
        for i, so in enumerate(self._sos):
            self.table.setItem(i, 0, QTableWidgetItem(so["so_number"]))
            self.table.setItem(i, 1, QTableWidgetItem(so.get("sku_code", "")))
            self.table.setItem(i, 2, QTableWidgetItem(so.get("customer_name") or ""))
            self.table.setItem(i, 3, QTableWidgetItem(so.get("due_date") or ""))
            spin = QSpinBox()
            spin.setRange(1, 9999)
            spin.setValue(so.get("priority") or 0)
            spin.setSpecialValueText("—")
            self.table.setCellWidget(i, 4, spin)
            self._spinboxes.append((so, spin))

        layout.addWidget(self.table)

        if not self._sos:
            msg.setText(
                f"<b>{failed_so} / {failed_sku}</b> could not be scheduled — capacity exceeded.\n\n"
                "All OPEN SOs already have priorities set.\n"
                "Try extending the planning horizon or reviewing CRP capacity."
            )

        # Buttons
        btns = QDialogButtonBox()
        self._btn_save = btns.addButton("Save && Re-Plan", QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_save.setEnabled(bool(self._sos))
        btns.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def save_priorities(self):
        for so, spin in self._spinboxes:
            pri = spin.value()
            if pri == 0:
                continue
            updated = dict(so)
            updated["priority"] = pri
            SORepo.upsert(updated)


# ─── Gantt Tab ────────────────────────────────────────────────────────────────

class GanttTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_topbar())
        layout.addWidget(self._build_viewbar())

        # ── Frozen date-axis header ───────────────────────────────────────────
        self.gantt_header = GanttHeaderWidget(self)
        layout.addWidget(self.gantt_header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self.canvas = GanttCanvas(self)
        self.canvas.parent_tab = self
        self.canvas.planMoved.connect(self._on_plan_moved)
        self.canvas.planSelected.connect(self._on_plan_selected)
        self.canvas.selectionChanged.connect(self._on_selection_changed)
        self.scroll.setWidget(self.canvas)
        self._on_dim_changed()

        self.scroll.horizontalScrollBar().valueChanged.connect(
            self.gantt_header.set_scroll_h)

        body.addWidget(self.scroll, stretch=1)

        self.unplanned_panel = self._build_unplanned_panel()
        self.unplanned_panel.setVisible(False)
        body.addWidget(self.unplanned_panel)

        layout.addLayout(body, stretch=1)

        self.detail_label = QLabel("Click a plan block to see details.")
        self.detail_label.setStyleSheet(
            "background:#f5f5f5; padding:4px; border-top:1px solid #ccc;")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMaximumHeight(48)
        layout.addWidget(self.detail_label)

        self.refresh()

    # ─── Top Bar (page title / search / KPI pills / action buttons) ──────────

    def _build_topbar(self) -> QWidget:
        w = QWidget()
        w.setObjectName("topbar")
        w.setFixedHeight(54)
        w.setStyleSheet(
            "QWidget#topbar { background: #ffffff; }"
            "QLabel { background: transparent; }"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(10)

        # Title + breadcrumb stack
        tcol = QVBoxLayout()
        tcol.setSpacing(1)
        t1 = QLabel("Gantt Planner")
        t1.setStyleSheet("font-size:14px; font-weight:700; color:#16213d;")
        t2 = QLabel("Plan / Gantt")
        t2.setStyleSheet("font-size:10px; color:#9aa1b3;")
        tcol.addWidget(t1)
        tcol.addWidget(t2)
        lay.addLayout(tcol)

        # Search box
        sf = QFrame()
        sf.setFixedSize(196, 30)
        sf.setStyleSheet(
            "QFrame { background:#f1f2f6; border-radius:6px; border:none; }")
        sfl = QHBoxLayout(sf)
        sfl.setContentsMargins(8, 0, 8, 0)
        sfl.setSpacing(4)
        sfl.addWidget(QLabel("🔍"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search SO, SKU, Customer…")
        self.search_edit.setStyleSheet(
            "border:none; background:transparent; font-size:11px; color:#3a4255;")
        self.search_edit.textChanged.connect(self._on_search_changed)
        sfl.addWidget(self.search_edit)
        lay.addWidget(sf)

        # KPI pills
        self._pill_ok   = self._make_kpi_pill("#e6f4ea", "#1d8a4a")
        self._pill_risk = self._make_kpi_pill("#fef3e0", "#b9760a")
        self._pill_late = self._make_kpi_pill("#fbe7e7", "#c2342f")
        lay.addWidget(self._pill_ok)
        lay.addWidget(self._pill_risk)
        lay.addWidget(self._pill_late)

        lay.addStretch()

        # Primary: Execute Plan
        btn_plan = QPushButton("▶  Execute Plan")
        btn_plan.setStyleSheet(
            "QPushButton { background:#2f5fd6; color:#fff; border:none; border-radius:5px;"
            " padding:6px 12px; font-size:11px; font-weight:600; }"
            "QPushButton:hover { background:#2451c2; }"
        )
        btn_plan.clicked.connect(self.run_auto_plan)
        lay.addWidget(btn_plan)

        # Outline: Pull Forward
        btn_pull = QPushButton("←  Pull Forward")
        btn_pull.setStyleSheet(
            "QPushButton { background:#fff; color:#3a4255; border:1px solid #d4d7e0;"
            " border-radius:5px; padding:6px 12px; font-size:11px; font-weight:600; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        btn_pull.clicked.connect(self.run_pull_forward)
        lay.addWidget(btn_pull)

        # Danger: Clear Plan
        btn_clear_plan = QPushButton("🗑  Clear Plan")
        btn_clear_plan.setStyleSheet(
            "QPushButton { background:#fff; color:#c2342f; border:1px solid #d4d7e0;"
            " border-radius:5px; padding:6px 12px; font-size:11px; font-weight:600; }"
            "QPushButton:hover { background:#fbe7e7; }"
        )
        btn_clear_plan.clicked.connect(self.run_clear_plan)
        lay.addWidget(btn_clear_plan)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedHeight(24)
        sep.setStyleSheet("background:#e2e4ea; border:none;")
        sep.setFixedWidth(1)
        lay.addWidget(sep)

        # Icon: Refresh CRP
        btn_crp = QPushButton("♻")
        btn_crp.setFixedSize(32, 32)
        btn_crp.setToolTip("Refresh CRP")
        btn_crp.setStyleSheet(
            "QPushButton { border:1px solid #e2e4ea; border-radius:5px; background:#fff;"
            " font-size:14px; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        btn_crp.clicked.connect(self._on_crp_refresh)
        lay.addWidget(btn_crp)

        # Icon: New Window
        btn_win = QPushButton("⧉")
        btn_win.setFixedSize(32, 32)
        btn_win.setToolTip("New Window (Ctrl+N)")
        btn_win.setStyleSheet(
            "QPushButton { border:1px solid #e2e4ea; border-radius:5px; background:#fff;"
            " font-size:14px; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        btn_win.clicked.connect(self._on_new_window)
        lay.addWidget(btn_win)

        # Bottom border drawn via a separator widget
        outer = QWidget()
        vl = QVBoxLayout(outer)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        vl.addWidget(w)
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background:#e2e4ea; border:none;")
        vl.addWidget(sep2)
        return outer

    @staticmethod
    def _make_kpi_pill(bg: str, fg: str) -> QLabel:
        lbl = QLabel("—")
        lbl.setStyleSheet(
            f"QLabel {{ background:{bg}; color:{fg}; border-radius:10px;"
            f" padding:3px 9px; font-size:10px; font-weight:700; }}"
        )
        lbl.setFixedHeight(22)
        return lbl

    def _update_kpi_pills(self):
        """Count OPEN SOs by schedule status and update KPI pill labels."""
        try:
            from datetime import date as _date, timedelta
            today = _date.today()
            sos = SORepo.all()
            on_time = at_risk = late = 0
            for so in sos:
                if so.get("status") != "OPEN":
                    continue
                due_str = so.get("due_date")
                if not due_str:
                    on_time += 1
                    continue
                due = _date.fromisoformat(due_str)
                days = (due - today).days
                if days < 0:
                    late += 1
                elif days <= 3:
                    at_risk += 1
                else:
                    on_time += 1
            self._pill_ok.setText(f"On time  {on_time}")
            self._pill_risk.setText(f"At risk  {at_risk}")
            self._pill_late.setText(f"Late  {late}")
        except Exception:
            pass

    def _on_search_changed(self, text: str):
        if hasattr(self, "canvas"):
            self.canvas.set_search_filter(text.strip().lower())

    def _on_crp_refresh(self):
        if self.main_window and hasattr(self.main_window, "_refresh_crp"):
            self.main_window._refresh_crp()

    def _on_new_window(self):
        if self.main_window and hasattr(self.main_window, "_detach_current_tab"):
            self.main_window._detach_current_tab()

    # ─── View Bar (Y-axis controls / horizon / export) ────────────────────────

    def _build_viewbar(self) -> QWidget:
        outer = QWidget()
        vl = QVBoxLayout(outer)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        w = QWidget()
        w.setObjectName("viewbar")
        w.setFixedHeight(46)
        w.setStyleSheet("QWidget#viewbar { background:#fafbfc; }")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(8)

        # "Y축" label
        lbl_y = QLabel("Y축")
        lbl_y.setStyleSheet("font-size:11px; color:#6b7280; font-weight:600;")
        lay.addWidget(lbl_y)

        # Preset buttons
        _PRESETS = [("Room", ["Room"]),
                    ("Room›Proc", ["Room", "Process"]),
                    ("SKU", ["SKU"])]
        self._preset_btns: List[QPushButton] = []
        _PRESET_CSS = (
            "QPushButton { font-size:10px; font-weight:600; padding:3px 9px;"
            " border-radius:4px; border:1px solid #d4d7e0; background:#fff; color:#3a4255; }"
            "QPushButton:checked { background:#dde9ff; border-color:#4f8df0; color:#2451c2; }"
            "QPushButton:hover:!checked { background:#f5f6fa; }"
        )
        for lbl, dims in _PRESETS:
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setStyleSheet(_PRESET_CSS)
            btn.clicked.connect(lambda _c, d=dims: self._apply_preset(d))
            self._preset_btns.append(btn)
            lay.addWidget(btn)

        # Separator
        lay.addWidget(self._vb_sep())

        # Dim selectors with ▸ arrows
        self._dim_combos: List[QComboBox] = []
        defaults = ["Room", "Process", "—", "—"]
        _DIM_CSS = (
            "QComboBox { background:#fff; border:1px solid #d4d7e0; border-radius:4px;"
            " padding:3px 5px; font-size:11px; color:#3a4255; }"
            "QComboBox:focus { border-color:#4f8df0; }"
        )
        for i, default_dim in enumerate(defaults):
            if i > 0:
                arr = QLabel("▸")
                arr.setStyleSheet("color:#b0b8cc; font-size:10px;")
                lay.addWidget(arr)
            cb = QComboBox()
            cb.addItems(Y_DIM_OPTIONS)
            cb.setCurrentText(default_dim)
            cb.setStyleSheet(_DIM_CSS)
            cb.currentTextChanged.connect(self._on_dim_changed)
            cb.setToolTip(f"Y-axis depth {i+1}")
            self._dim_combos.append(cb)
            lay.addWidget(cb)

        # Separator
        lay.addWidget(self._vb_sep())

        # Shift toggle pill
        self.shift_toggle = QPushButton("Shift")
        self.shift_toggle.setCheckable(True)
        self.shift_toggle.setFixedHeight(26)
        self.shift_toggle.setStyleSheet(
            "QPushButton { font-size:10px; font-weight:600; padding:3px 10px;"
            " border-radius:12px; border:1px solid #d4d7e0; color:#6b7280; background:#fff; }"
            "QPushButton:checked { background:#2f5fd6; color:#fff; border-color:#2f5fd6; }"
        )
        self.shift_toggle.toggled.connect(self._on_shift_toggle)
        lay.addWidget(self.shift_toggle)

        # Horizon segmented selector
        hz_outer = QFrame()
        hz_outer.setStyleSheet(
            "QFrame { background:#eef0f4; border-radius:5px; border:none; }")
        hz_outer.setFixedHeight(28)
        hz_lay = QHBoxLayout(hz_outer)
        hz_lay.setContentsMargins(2, 2, 2, 2)
        hz_lay.setSpacing(1)
        _HZ_BTN_CSS = (
            "QPushButton { font-size:10px; font-weight:600; padding:2px 8px;"
            " border-radius:4px; border:none; color:#6b7280; background:transparent; }"
            "QPushButton:checked { background:#fff; color:#16213d; }"
        )
        self._horizon_btns: Dict[str, QPushButton] = {}
        hz_grp = QButtonGroup(self)
        hz_grp.setExclusive(True)
        for lbl in ["2W", "4W", "6W", "3M"]:
            hb = QPushButton(lbl)
            hb.setCheckable(True)
            hb.setChecked(lbl == "4W")
            hb.setStyleSheet(_HZ_BTN_CSS)
            hb.clicked.connect(lambda _c, t=lbl: self._on_horizon_changed(t))
            hz_lay.addWidget(hb)
            hz_grp.addButton(hb)
            self._horizon_btns[lbl] = hb
        lay.addWidget(hz_outer)

        # Date picker
        from PyQt6.QtCore import QDate
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setFixedHeight(26)
        self.date_edit.setStyleSheet(
            "QDateEdit { border:1px solid #d4d7e0; border-radius:4px;"
            " padding:2px 6px; font-size:11px; color:#3a4255; }")
        self.date_edit.dateChanged.connect(lambda: self.refresh())
        lay.addWidget(self.date_edit)

        lay.addStretch()

        # Unplanned button with count badge (updated in refresh)
        self.btn_unplanned = QPushButton("📦 Unplanned  0")
        self.btn_unplanned.setCheckable(True)
        self.btn_unplanned.setFixedHeight(28)
        self.btn_unplanned.setToolTip("Show OPEN SOs with unplanned remaining quantity")
        self.btn_unplanned.setStyleSheet(
            "QPushButton { font-size:11px; font-weight:600; padding:4px 10px;"
            " border-radius:5px; border:1px solid #d4d7e0; background:#fff; color:#3a4255; }"
            "QPushButton:checked { background:#fef3e0; border-color:#e09a1f; color:#b9760a; }"
        )
        self.btn_unplanned.toggled.connect(self._on_toggle_unplanned)
        lay.addWidget(self.btn_unplanned)

        # Export
        btn_export = QPushButton("↓ Export")
        btn_export.setFixedHeight(28)
        btn_export.setToolTip("Export current plan as normalized Excel")
        btn_export.setStyleSheet(
            "QPushButton { font-size:11px; font-weight:600; padding:4px 10px;"
            " border-radius:5px; border:1px solid #d4d7e0; background:#fff; color:#3a4255; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        btn_export.clicked.connect(self._export_plan)
        lay.addWidget(btn_export)

        # Consolidate
        self.btn_consol = QPushButton("🔗 Consolidate (0)")
        self.btn_consol.setEnabled(False)
        self.btn_consol.setFixedHeight(28)
        self.btn_consol.setStyleSheet(
            "QPushButton { font-size:11px; font-weight:600; padding:4px 10px;"
            " border-radius:5px; border:1px solid #d4d7e0; background:#fff; color:#3a4255; }"
            "QPushButton:enabled:hover { background:#f5f6fa; }"
            "QPushButton:disabled { opacity:0.5; color:#aaa; }"
        )
        self.btn_consol.clicked.connect(self._consolidate)
        lay.addWidget(self.btn_consol)

        # Clear checks (hidden label, still functional)
        self.btn_clear = QPushButton("✖")
        self.btn_clear.setEnabled(False)
        self.btn_clear.setFixedSize(28, 28)
        self.btn_clear.setToolTip("Clear all checkbox selections")
        self.btn_clear.setStyleSheet(
            "QPushButton { border:1px solid #d4d7e0; border-radius:5px;"
            " background:#fff; color:#3a4255; font-size:12px; }"
            "QPushButton:enabled:hover { background:#f5f6fa; }"
        )
        self.btn_clear.clicked.connect(self._clear_checks)
        lay.addWidget(self.btn_clear)

        self.check_label = QLabel("")
        self.check_label.setStyleSheet("color:#555; font-size:10px;")
        lay.addWidget(self.check_label)

        vl.addWidget(w)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#e2e4ea; border:none;")
        vl.addWidget(sep)
        return outer

    @staticmethod
    def _vb_sep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setFixedHeight(22)
        sep.setStyleSheet("background:#e2e4ea; border:none;")
        return sep

    def _build_unplanned_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFixedWidth(340)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setStyleSheet("background:#fafafa; border-left:1px solid #ccc;")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(6, 6, 6, 6)
        pl.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel("📦 Unplanned Orders")
        title.setStyleSheet("font-weight:bold;")
        head.addWidget(title)
        head.addStretch()
        btn_close = QPushButton("✕")
        btn_close.setFixedWidth(24)
        btn_close.setToolTip("Close")
        btn_close.clicked.connect(self._close_unplanned_panel)
        head.addWidget(btn_close)
        pl.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;")

        self._unplanned_inner = QWidget()
        self._unplanned_inner.setStyleSheet("background:transparent;")
        self.unplanned_card_layout = QVBoxLayout(self._unplanned_inner)
        self.unplanned_card_layout.setContentsMargins(0, 2, 0, 2)
        self.unplanned_card_layout.setSpacing(6)
        self.unplanned_card_layout.addStretch()
        scroll.setWidget(self._unplanned_inner)
        pl.addWidget(scroll)

        self.unplanned_count_label = QLabel("0 unplanned order(s)")
        self.unplanned_count_label.setStyleSheet("color:#555; font-size:11px;")
        pl.addWidget(self.unplanned_count_label)

        return panel

    def _on_toggle_unplanned(self, checked: bool):
        self.unplanned_panel.setVisible(checked)
        if checked:
            self._refresh_unplanned_panel()

    def _close_unplanned_panel(self):
        self.btn_unplanned.setChecked(False)

    def _refresh_unplanned_panel(self):
        # Clear existing cards (keep the trailing stretch)
        layout = self.unplanned_card_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        rows = SORepo.unplanned()
        today_str = date.today().strftime("%Y-%m-%d")

        # Group step-level rows by order line
        from collections import OrderedDict
        groups: OrderedDict[tuple, list] = OrderedDict()
        for r in rows:
            key = (r["so_number"], r["sku_code"], r["line_item"])
            groups.setdefault(key, []).append(r)

        for (so_number, sku_code, line_item), steps in groups.items():
            rep = steps[0]  # representative row for SO-level fields
            card = self._make_unplanned_card(rep, steps, today_str)
            layout.insertWidget(layout.count() - 1, card)

        n_orders = len(groups)
        n_steps  = len(rows)
        self.unplanned_count_label.setText(
            f"{n_orders} unplanned order(s)  ·  {n_steps} step(s)")

    def _make_unplanned_card(self, rep: dict, steps: list, today_str: str) -> QFrame:
        so_number   = rep["so_number"]
        sku_code    = rep["sku_code"]
        line_item   = rep["line_item"]
        customer    = rep.get("customer_name") or ""
        due_date    = rep.get("due_date") or ""
        priority    = rep.get("priority")
        is_late     = bool(due_date and due_date < today_str)
        n_steps     = len(steps)
        remaining   = max(s.get("remaining_qty", 0) for s in steps)

        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        border_color = "#ef4444" if is_late else "#d1d5db"
        card.setStyleSheet(
            f"QFrame{{background:white; border:1px solid {border_color};"
            f" border-radius:6px; padding:0px;}}"
        )

        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(3)

        # Row 1: SO · SKU/Line  +  Plan button
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        lbl_so = QLabel(f"<b>{so_number}</b>  ·  {sku_code} / {line_item}")
        lbl_so.setStyleSheet("font-size:12px;")
        row1.addWidget(lbl_so, stretch=1)

        btn = QPushButton("▶ Plan")
        btn.setFixedHeight(24)
        btn.setFixedWidth(64)
        btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:white;border-radius:4px;"
            "font-size:11px; border:none;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:pressed{background:#1e40af;}"
        )
        btn.clicked.connect(lambda _c, rd=dict(rep): self._force_plan_row(rd))
        row1.addWidget(btn)
        cl.addLayout(row1)

        # Row 2: Customer · Due date
        due_color = "#ef4444" if is_late else "#6b7280"
        due_text  = f"Due: {due_date}" if due_date else "Due: —"
        cust_text = customer if customer else "—"
        lbl_meta = QLabel(f"{cust_text}  ·  <span style='color:{due_color};'>{due_text}</span>")
        lbl_meta.setStyleSheet("font-size:11px; color:#6b7280;")
        cl.addWidget(lbl_meta)

        # Row 3: Priority · Remaining · steps unplanned
        pri_text  = f"Pri: {priority}" if priority else "Pri: —"
        step_parts = []
        for s in steps:
            seq  = s.get("process_seq")
            name = s.get("process_name") or ""
            tag  = f"[{seq}] {name}" if seq is not None else name
            step_parts.append(tag)
        steps_text = "  ·  ".join(step_parts) if step_parts else "(no routing)"
        lbl_steps = QLabel(
            f"<span style='color:#9ca3af;'>{pri_text}  ·  Rem: {remaining}</span>"
            f"<br><span style='color:#b45309; font-size:10px;'>{steps_text}</span>"
        )
        lbl_steps.setStyleSheet("font-size:11px;")
        lbl_steps.setWordWrap(True)
        cl.addWidget(lbl_steps)

        return card

    def _force_plan_row(self, row_data: dict):
        d0, d1 = self._date_range()
        so_number = row_data["so_number"]
        sku_code  = row_data["sku_code"]
        line_item = row_data["line_item"]
        try:
            report = scheduler.force_plan_so(so_number, sku_code, line_item, d0, d1)
        except Exception as e:
            QMessageBox.warning(self, "Force Plan Error", str(e))
            return

        planned = report.get("planned", 0)
        late    = report.get("late", [])
        errors  = report.get("routing_errors", [])

        if planned > 0:
            self.refresh()
            msg = f"Force-planned {so_number} / {sku_code} / {line_item}: {planned} slot(s)"
            if self.main_window:
                self.main_window.notify(msg)
                self.main_window._check_conflicts_silent()
        elif errors:
            QMessageBox.warning(self, "Force Plan Failed",
                                "\n".join(e.get("error", str(e)) for e in errors))
        elif late:
            reason = late[0].get("reason", "window_closed")
            if reason == "capacity_exceeded":
                dlg = PriorityConflictDialog(so_number, sku_code, self)
                if dlg.exec() == QDialog.DialogCode.Accepted and dlg._sos:
                    dlg.save_priorities()
                    try:
                        report2 = scheduler.force_plan_so(so_number, sku_code, line_item, d0, d1)
                    except Exception as e:
                        QMessageBox.warning(self, "Force Plan Error", str(e))
                        return
                    if report2.get("planned", 0) > 0:
                        self.refresh()
                        msg = (f"Force-planned {so_number} / {sku_code} / {line_item}: "
                               f"{report2['planned']} slot(s)")
                        if self.main_window:
                            self.main_window.notify(msg)
                            self.main_window._check_conflicts_silent()
                    else:
                        QMessageBox.warning(self, "Force Plan Failed",
                                            "Re-planned after saving priorities but capacity is still insufficient.\n"
                                            "Check the planning horizon or CRP capacity settings.")
            else:
                QMessageBox.warning(self, "Force Plan Failed",
                                    f"Could not place plan: {reason}\n\n"
                                    "Check CRP capacity and calendar setup.")
        else:
            QMessageBox.information(self, "Force Plan",
                                    "Nothing to plan (already fully planned or no capacity).")

    def _date_range(self) -> Tuple[str, str]:
        from PyQt6.QtCore import QDate
        d0 = self.date_edit.date().toPyDate()
        try:
            weeks = int(ConfigRepo.get("plan_horizon_weeks", "4"))
        except Exception:
            weeks = 4
        d1 = d0 + timedelta(weeks=weeks)
        return d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")

    def refresh(self):
        d0, d1   = self._date_range()
        plans    = PlanRepo.all(d0, d1)
        sos      = SORepo.all()
        skus     = SKURepo.all()
        shifts   = ShiftRepo.all()
        conflicts = scheduler.detect_conflicts(d0, d1)

        # Build material demand group map in one bulk query
        group_ids = list({p["material_group_id"] for p in plans
                          if p.get("material_group_id")})
        mat_groups = MaterialDemandRepo.for_groups_bulk(group_ids)

        self.canvas.start_date   = datetime.strptime(d0, "%Y-%m-%d").date()
        self.canvas.horizon_days = (
            datetime.strptime(d1, "%Y-%m-%d").date() -
            datetime.strptime(d0, "%Y-%m-%d").date()).days
        self.canvas.load_data(plans, sos, skus, shifts, conflicts, mat_groups)

        # Sync frozen header with canvas data
        self.gantt_header.sync_from(self.canvas)

        # Update topbar KPI pills
        self._update_kpi_pills()

        # Update unplanned badge count
        try:
            n_unplanned = len({
                (r["so_number"], r["sku_code"], r["line_item"])
                for r in SORepo.unplanned()
            })
            self.btn_unplanned.setText(f"📦 Unplanned  {n_unplanned}")
        except Exception:
            pass

        if self.btn_unplanned.isChecked():
            self._refresh_unplanned_panel()

    def run_auto_plan(self):
        d0, d1 = self._date_range()
        try:
            scheduler._reload_masters()
            report = scheduler.auto_plan(d0, d1)
            self.refresh()
            msg = (f"Auto-plan: {report['planned']} slots, "
                   f"{len(report['late'])} late, "
                   f"{len(report.get('routing_errors', []))} routing errors.")
            if self.main_window:
                self.main_window.notify(msg)
                self.main_window._check_conflicts_silent()
        except Exception as e:
            QMessageBox.warning(self, "Auto Plan Error", str(e))

    def run_pull_forward(self):
        d0, d1 = self._date_range()
        try:
            result = scheduler.pull_forward(d0, d1)
            self.refresh()
            if self.main_window:
                self.main_window.notify(f"Pull forward: {result['moved']} plans moved.")
        except Exception as e:
            QMessageBox.warning(self, "Pull Forward Error", str(e))

    def run_clear_plan(self):
        reply = QMessageBox.question(
            self, "Clear Plan",
            "Delete ALL production plans (SKU + MATERIAL)?\n"
            "This cannot be undone. Run Re-Plan / Auto Plan afterward "
            "to regenerate from scratch.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        n = PlanRepo.delete_all(reason="manual_clear_plan")
        self.refresh()
        if self.main_window:
            self.main_window.notify(f"Cleared {n} plan(s).")
            self.main_window._check_conflicts_silent()

    def _consolidate(self):
        checked = self.canvas.checked_plans()
        if not checked:
            QMessageBox.information(self, "Consolidate",
                "No plans checked. Click the ☑ checkbox on plan blocks first.")
            return
        shifts = ShiftRepo.all()
        ok, msg = ConsolidationEngine.consolidate(checked, shifts)
        QMessageBox.information(self, "Consolidate", msg)
        if ok:
            self.canvas.clear_checks()
            self.refresh()
            if self.main_window:
                self.main_window.notify(msg.split("\n")[0])

    def _clear_checks(self):
        self.canvas.clear_checks()

    def _on_plan_moved(self, plan_id, old_date, old_shift, reason):
        self.refresh()
        if self.main_window:
            self.main_window.notify(f"Plan #{plan_id} moved. Reason: {reason}")
            self.main_window._check_conflicts_silent()

    def _on_plan_selected(self, plan: Dict):
        so  = SORepo.get(plan["so_number"], plan["sku_code"], plan["line_item"])
        due = so["due_date"] if so else "N/A"
        customer = so.get("customer_name", "") if so else ""
        grp = plan.get("consolidation_group") or "-"
        self.detail_label.setText(
            f"Plan #{plan['plan_id']}  |  SO:{plan['so_number']}  "
            f"SKU:{plan['sku_code']}  Line:{plan['line_item']}  "
            f"{'| Customer:'+customer if customer else ''}  |  "
            f"Room:{plan['room_code']}  Process:{plan['process_name']}  |  "
            f"Date:{plan['plan_date']} S{plan['shift_no']}  |  "
            f"Qty:{plan['qty_planned']}  Due:{due}  |  "
            f"{'🔒 LOCKED' if plan['is_locked'] else 'unlocked'}  "
            f"{'⭐FINAL' if plan.get('is_final_seq') else ''}  Grp:{grp}")

    def _on_selection_changed(self, checked_ids: list):
        n = len(checked_ids)
        self.check_label.setText(f"{n}" if n > 0 else "")
        self.btn_consol.setText(f"🔗 Consolidate ({n})")
        self.btn_consol.setEnabled(n >= 2)
        self.btn_clear.setEnabled(n > 0)

    def _export_plan(self):
        from PyQt6.QtWidgets import QFileDialog
        d0, d1 = self._date_range()
        default_name = f"GanttPlan_{d0}_{d1}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Gantt Plan", default_name,
            "Excel Files (*.xlsx)")
        if not path:
            return
        ok, msg = export_gantt_plan(
            self.canvas._plans,
            self.canvas._sos,
            self.canvas._mat_groups,
            path)
        if ok:
            QMessageBox.information(self, "Export", msg)
            if self.main_window:
                self.main_window.notify(msg)
        else:
            QMessageBox.warning(self, "Export Failed", msg)

    def _on_dim_changed(self):
        if not hasattr(self, "canvas"):
            return
        dims = [cb.currentText() for cb in self._dim_combos if cb.currentText() != "—"]
        if not dims:
            dims = ["Room"]
        self.canvas.y_dims = dims
        self._sync_preset_buttons(dims)
        self.refresh()

    def _apply_preset(self, dims: List[str]):
        for i, cb in enumerate(self._dim_combos):
            cb.setCurrentText(dims[i] if i < len(dims) else "—")
        # _on_dim_changed fires automatically via signal

    def _sync_preset_buttons(self, dims: List[str]):
        _PRESET_DIMS = [["Room"], ["Room", "Process"], ["SKU"]]
        for btn, pdims in zip(self._preset_btns, _PRESET_DIMS):
            btn.setChecked(dims == pdims)

    def _on_horizon_changed(self, text: str):
        if not hasattr(self, "canvas"):
            return
        mapping = {"2W": 14, "4W": 28, "6W": 42, "3M": 90}
        self.canvas.horizon_days = mapping.get(text, 28)
        # Sync button group visual state
        if hasattr(self, "_horizon_btns"):
            for lbl, hb in self._horizon_btns.items():
                hb.setChecked(lbl == text)
        self.refresh()

    def _on_shift_toggle(self, checked: bool):
        self.canvas.shift_view = checked
        self.refresh()


# ════════════════════════════════════════════════════════════════════════════
#  Add Plan Dialog
# ════════════════════════════════════════════════════════════════════════════

class AddPlanDialog(QDialog):
    """수동으로 플랜 슬롯에 계획을 추가하는 다이얼로그."""

    def __init__(self, plan_date: str, shift_no: int, room_code: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Plan")
        self.setMinimumWidth(460)
        self._date  = plan_date
        self._shift = shift_no
        self._room  = room_code
        self._build_ui()

    def _build_ui(self):
        from data.repositories import ProcessRoutingRepo
        layout = QFormLayout(self)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        layout.addRow("Date:",  QLabel(self._date))
        layout.addRow("Shift:", QLabel(f"Shift {self._shift}"))
        layout.addRow("Room:",  QLabel(self._room))

        # Process — from room's available processes
        self._proc_combo = QComboBox()
        for rp in RoomRepo.processes_for_room(self._room):
            self._proc_combo.addItem(rp["process_name"], rp)
        layout.addRow("Process:", self._proc_combo)

        # SO + Line Item
        self._so_combo = QComboBox()
        self._so_combo.setMinimumWidth(280)
        self._sos = SORepo.all(status="OPEN")
        for so in self._sos:
            label = (f"{so['so_number']}  {so['sku_code']}  "
                     f"Line {so['line_item']}"
                     + (f"  [{so['customer_name']}]" if so.get("customer_name") else ""))
            self._so_combo.addItem(label, so)
        layout.addRow("SO / SKU / Line:", self._so_combo)

        # Qty
        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(1, 9_999_999)
        self._qty_spin.setValue(100)
        layout.addRow("Qty:", self._qty_spin)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _save(self):
        from data.repositories import ProcessRoutingRepo
        so = self._so_combo.currentData()
        rp = self._proc_combo.currentData()
        if not so or not rp:
            QMessageBox.warning(self, "Add Plan", "SO 또는 공정을 선택하세요.")
            return
        proc_name = rp["process_name"]
        steps = ProcessRoutingRepo.for_entity("SKU", so["sku_code"])
        step = next((s for s in steps if s["process_name"] == proc_name), None)
        seq      = step["process_seq"]   if step else 1
        is_final = (1 if step and step.get("is_final_seq") else 0)

        PlanRepo.insert({
            "entity_type":  "SKU",
            "entity_code":  so["sku_code"],
            "so_number":    so["so_number"],
            "sku_code":     so["sku_code"],
            "line_item":    so["line_item"],
            "process_name": proc_name,
            "process_seq":  seq,
            "is_final_seq": is_final,
            "room_code":    self._room,
            "plan_date":    self._date,
            "shift_no":     self._shift,
            "qty_planned":  self._qty_spin.value(),
            "memo":         "manual",
        })
        self.accept()
