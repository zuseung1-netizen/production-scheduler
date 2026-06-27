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
    QLabel, QPushButton, QToolButton, QComboBox, QToolTip, QInputDialog,
    QMessageBox, QMenu, QDialog, QDialogButtonBox, QTextEdit,
    QApplication, QDateEdit, QFormLayout, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QLineEdit, QButtonGroup, QSizePolicy, QSplitter, QGroupBox,
    QListWidget, QListWidgetItem, QStyle, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, QPointF, pyqtSignal, QTimer, QThread, QByteArray, QSize, QMimeData, QEvent
from PyQt6.QtGui import (
    QPainter, QPainterPath, QColor, QFont, QFontMetrics, QPen, QBrush, QCursor,
    QPixmap, QIcon, QShortcut, QKeySequence, QDrag
)
from PyQt6.QtSvg import QSvgRenderer

import json

from data.repositories import (
    PlanRepo, SORepo, SKURepo, ShiftRepo, RoomRepo,
    CalendarRepo, ConfigRepo, MaterialDemandRepo, CompanyHolidayRepo,
    ProcessRoutingRepo
)
from core.scheduler import scheduler, sku_to_inner, shift_capacity_inner
from utils.excel_io import export_gantt_plan
from utils.korean_holidays import is_holiday, holiday_name


# ─── Icon helpers ─────────────────────────────────────────────────────────────

def _cell_center(widget: "QWidget") -> "QWidget":
    """Wrap a widget with padding margins; widget expands to fill horizontally."""
    from PyQt6.QtWidgets import QWidget as _W, QHBoxLayout as _H, QSizePolicy as _SP
    container = _W()
    container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    lay = _H(container)
    lay.setContentsMargins(4, 3, 4, 3)
    lay.setSpacing(0)
    widget.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Expanding)
    lay.addWidget(widget)
    return container


def _svg_icon(svg_body: str, color: str = "#3730a3", size: int = 16) -> QIcon:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"'
        f' stroke="{color}" fill="none" stroke-width="2"'
        f' stroke-linecap="round" stroke-linejoin="round">'
        f'{svg_body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    renderer.render(p)
    p.end()
    return QIcon(px)


_IC_CALC = (
    '<rect x="4" y="2" width="16" height="20" rx="2"/>'
    '<rect x="7" y="5" width="10" height="4" rx="1"/>'
    '<line x1="8" y1="13" x2="8.01" y2="13" stroke-width="3"/>'
    '<line x1="12" y1="13" x2="12.01" y2="13" stroke-width="3"/>'
    '<line x1="16" y1="13" x2="16.01" y2="13" stroke-width="3"/>'
    '<line x1="8" y1="17" x2="8.01" y2="17" stroke-width="3"/>'
    '<line x1="12" y1="17" x2="12.01" y2="17" stroke-width="3"/>'
    '<line x1="14" y1="17" x2="18" y2="17" stroke-width="2"/>'
    '<line x1="16" y1="15" x2="16" y2="19" stroke-width="2"/>'
)

_IC_APPLY = (
    '<line x1="5" y1="12" x2="19" y2="12"/>'
    '<polyline points="12 5 19 12 12 19"/>'
)


# ─── Visual constants ─────────────────────────────────────────────────────────
PALETTE = [
    "#2E6FD8", "#D4570A", "#27A060", "#C0392B",
    "#6655CC", "#D49010", "#1A9E9E", "#8B5E3C",
    "#3A8FAA", "#8B479B", "#1E7A3C", "#CF4545",
]
CONSOL_BORDER   = QColor(255, 205, 0)
CONFLICT_DOT    = QColor(210, 30,  30)
GRID_LINE       = QColor(218, 220, 230)
GRID_WEEKEND    = QColor(245, 243, 249)
GRID_HOLIDAY    = QColor(255, 237, 237)
TODAY_COL_TINT  = QColor(240, 245, 255)
HEADER_HOLIDAY  = QColor(148, 40,  40)
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
IO_ACCENT       = QColor(80,  40,  160)   # internal order plan accent

ROW_BG_A        = QColor(255, 255, 255)
ROW_BG_B        = QColor(249, 250, 252)

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
CLOSE_TAG_BG    = QColor(217, 119,   6)  # amber — campaign closing shift badge
CHECK_FILL      = QColor(63,  124, 196, 30)

CARD_H     = 72   # row slot height — matches mockup 72px body row
CELL_UTIL_H = 12  # per-cell utilization bar strip at bottom of each row (Room mode)
DAY_W      = 88   # day column width
SHIFT_W    = DAY_W
HEADER_H   = 40   # date-header height (mockup: 40px)
UTIL_ROW_H = 20
UTIL_H     = UTIL_ROW_H * 2   # cap-row height: 2 rows × 20px = 40px
DIM_COL_W  = 124  # Y-label width per dimension (mockup: 124px)
_PANEL_DRAG_MIME = "application/x-gantt-plan-id"
SKU_COL_W  = 72
Y_LABEL_W  = DIM_COL_W

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

        merge_reason  = f"consolidation-merge-{group_id}"
        consol_reason = f"consolidation-{group_id}"

        # ── 1. Re-insert plans that were merged (deleted) during consolidation ─
        deleted_hist = PlanRepo.history_by_reason(merge_reason, "DELETED")
        restored = 0
        for h in deleted_hist:
            old = json.loads(h["old_value"]) if h.get("old_value") else None
            if not old:
                continue
            old.pop("plan_id", None)
            old["is_consolidated"]    = 0
            old["consolidation_group"] = None
            old["is_locked"]          = 0
            PlanRepo.insert(old)
            restored += 1

        # ── 2. Restore anchor plans to their pre-consolidation state ───────────
        modified_hist = PlanRepo.history_by_reason(consol_reason, "MODIFIED")
        anchor_originals: Dict[int, Dict] = {}
        for h in modified_hist:
            pid = h.get("plan_id")
            if pid and pid not in anchor_originals:
                old = json.loads(h["old_value"]) if h.get("old_value") else None
                if old:
                    anchor_originals[pid] = old

        reverted = 0
        for pid, old in anchor_originals.items():
            if PlanRepo.get(pid):
                PlanRepo.update(pid, {
                    "plan_date":           old.get("plan_date"),
                    "shift_no":            old.get("shift_no"),
                    "qty_planned":         old.get("qty_planned"),
                    "is_consolidated":     0,
                    "consolidation_group": None,
                    "is_locked":          0,
                    "memo":                old.get("memo") or "",
                }, reason=f"break-consolidation-{group_id}")
                reverted += 1

        # ── 3. Fallback: clear consolidation flags on any remaining group plans ─
        still_in_group = [p for p in PlanRepo.all()
                          if p.get("consolidation_group") == group_id]
        for p in still_in_group:
            PlanRepo.update(p["plan_id"], {
                "is_consolidated": 0, "consolidation_group": None, "is_locked": 0,
            }, reason=f"break-consolidation-{group_id}")

        return True, (f"Consolidation {group_id} broken.\n"
                      f"{restored} merged block(s) restored, "
                      f"{reverted} anchor block(s) reverted.")


# ─── Frozen date-axis header ──────────────────────────────────────────────────

class GanttHeaderWidget(QWidget):
    """Fixed header (date row + cap row) stays visible during vertical scroll.
    Horizontal scroll is synced with the QScrollArea scrollbar."""

    _TOTAL_H = HEADER_H + UTIL_H   # 40 + 20 = 60px

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self._TOTAL_H)
        self.setMouseTracking(True)
        self.shift_view      : bool = False
        self.horizon_days    : int  = 90
        self.start_date      : date = date.today()
        self._shifts         : list = []
        self._scroll_h       : int  = 0
        self._y_label_w      : int  = DIM_COL_W
        self._cap_map        : dict = {}
        self._company_holidays: set = set()
        self._hc_util_data   : dict = {}
        self._hc_alloc_by_date: dict = {}
        # Cached per-column raw values for tooltip (built during paintEvent)
        self._col_cap_rendered: dict = {}  # col -> (used_inner, cap_inner)

    def _is_holiday(self, d: date) -> bool:
        return is_holiday(d) or d.isoformat() in self._company_holidays

    def sync_from(self, canvas: 'GanttCanvas'):
        self.shift_view          = canvas.shift_view
        self.horizon_days        = canvas.horizon_days
        self.start_date          = canvas.start_date
        self._shifts             = canvas._shifts
        self._y_label_w          = canvas._y_label_w
        self._cap_map            = canvas._cap_map
        self._company_holidays   = canvas._company_holidays
        self._hc_util_data       = canvas._hc_util_by_date
        self._hc_alloc_by_date   = canvas._hc_alloc_by_date
        self.update()

    def set_scroll_h(self, val: int):
        self._scroll_h = val
        self.update()

    def mouseMoveEvent(self, event):
        from PyQt6.QtWidgets import QToolTip
        mx = event.position().x()
        my = event.position().y()
        yw = self._y_label_w
        # Convert widget x to canvas x (accounting for horizontal scroll)
        cx = mx + self._scroll_h
        if cx < yw or self.shift_view:
            QToolTip.hideText()
            return
        col = int((cx - yw) // DAY_W)
        if col < 0 or col >= self.horizon_days:
            QToolTip.hideText()
            return
        ds = (self.start_date + timedelta(days=col)).strftime("%Y-%m-%d")
        tip = ""
        if HEADER_H <= my < HEADER_H + UTIL_ROW_H:
            # CAPACITY row
            used, cap = self._col_cap_rendered.get(col, (0.0, 0.0))
            if cap > 0:
                tip = f"{used:,.0f} / {cap:,.0f}"
        elif HEADER_H + UTIL_ROW_H <= my < HEADER_H + UTIL_H:
            # LABOR row
            pair = self._hc_alloc_by_date.get(ds)
            if pair:
                alloc, total = pair
                tip = f"{alloc:,.0f} / {total:,.0f}"
        if tip:
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        vw = self.width()
        yw = self._y_label_w
        total_col_w = self._col_count() * self._col_w()

        # ── Scrolling date + cap section ──────────────────────────────────────
        p.save()
        p.setClipRect(yw, 0, max(0, vw - yw), self._TOTAL_H)
        p.translate(-self._scroll_h, 0)

        total_w = yw + total_col_w + 200

        # Date header background (#26447f)
        p.fillRect(yw, 0, total_col_w + 200, HEADER_H, HEADER_BG)
        # Cap row background (#1e3a6e)
        cap_bg = QColor(30, 58, 110)
        p.fillRect(yw, HEADER_H, total_col_w + 200, UTIL_H, cap_bg)

        f_dd  = QFont("Segoe UI", 10, QFont.Weight.Bold)
        f_dow = QFont("Segoe UI", 8)
        f_cap = QFont("Segoe UI", 7, QFont.Weight.Bold)

        if not self.shift_view:
            # Aggregate cap data per column
            col_cap: dict = {}
            for (ds, room, proc, sno), (used, cap) in self._cap_map.items():
                col = self._date_to_col(ds, sno)
                if col is not None:
                    cu, cc = col_cap.get(col, (0.0, 0.0))
                    col_cap[col] = (cu + used, cc + cap)
            self._col_cap_rendered = col_cap  # cache for tooltip

            from PyQt6.QtGui import QFontMetrics as _FM
            for col in range(self.horizon_days):
                d  = self.start_date + timedelta(days=col)
                x  = yw + col * DAY_W
                is_weekend = d.weekday() >= 5
                is_today   = d == date.today()
                is_hday    = self._is_holiday(d)

                if is_today:
                    p.fillRect(x, 0, DAY_W, HEADER_H, QColor(30, 74, 142))
                elif is_hday:
                    p.fillRect(x, 0, DAY_W, HEADER_H, HEADER_HOLIDAY)
                elif is_weekend:
                    p.fillRect(x, 0, DAY_W, HEADER_H, HEADER_WEEKEND)

                # Date divider
                p.setPen(QPen(QColor(255, 255, 255, 25)))
                p.drawLine(x, 2, x, HEADER_H - 2)

                # Date number
                p.setPen(QPen(QColor(100, 160, 255) if is_today else HEADER_FG))
                p.setFont(f_dd)
                date_label = d.strftime("%m/%d")
                p.drawText(QRect(x, 2, DAY_W, 22),
                           Qt.AlignmentFlag.AlignCenter,
                           date_label)
                # Day of week
                p.setPen(QPen(QColor(174, 191, 230)))
                p.setFont(f_dow)
                p.drawText(QRect(x, 24, DAY_W, 14),
                           Qt.AlignmentFlag.AlignCenter,
                           d.strftime("%a").upper())

                # Cap bar — Row 1 (8px height, centered in top UTIL_ROW_H)
                used, cap = col_cap.get(col, (0.0, 0.0))
                ratio = (used / cap) if cap > 0 else 0
                bar_h  = 8
                bar_y  = HEADER_H + (UTIL_ROW_H - bar_h) // 2
                cw_    = DAY_W - 4
                fill_w = int(cw_ * min(ratio, 1.0))
                color  = (UTIL_HIGH if ratio > 0.9 else
                          UTIL_MED  if ratio > 0.6 else
                          (UTIL_LOW if ratio > 0 else QColor(214, 218, 227)))
                # Track: faint trough
                p.setBrush(QBrush(QColor(50, 82, 138)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRect(x + 2, bar_y, cw_, bar_h), 2, 2)
                if fill_w > 0:
                    p.setBrush(QBrush(color))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(QRect(x + 2, bar_y, fill_w, bar_h), 2, 2)
                # % badge — always at right edge (100% position)
                if cap > 0:
                    tag_txt   = f"{int(ratio * 100)}%"
                    p.setFont(f_cap)
                    tag_w     = _FM(f_cap).horizontalAdvance(tag_txt) + 6
                    tag_h     = 10
                    tag_x     = x + DAY_W - tag_w - 2
                    tag_y_pos = HEADER_H + (UTIL_ROW_H - tag_h) // 2
                    bg_color  = QColor(194, 52, 47) if ratio > 0.9 else QColor(30, 58, 110, 200)
                    p.setBrush(QBrush(bg_color))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(QRect(tag_x, tag_y_pos, tag_w, tag_h), 2, 2)
                    p.setPen(QPen(Qt.GlobalColor.white))
                    p.drawText(QRect(tag_x, tag_y_pos, tag_w, tag_h),
                               Qt.AlignmentFlag.AlignCenter, tag_txt)

                # HC bar — Row 2 (8px height, centered in bottom UTIL_ROW_H)
                ds_str = (self.start_date + timedelta(days=col)).strftime("%Y-%m-%d")
                hc_pct = self._hc_util_data.get(ds_str, 0.0)
                hc_ratio = hc_pct / 100.0
                hc_bar_y = HEADER_H + UTIL_ROW_H + (UTIL_ROW_H - bar_h) // 2
                hc_fill_w = int(cw_ * min(hc_ratio, 1.0))
                # Track
                p.setBrush(QBrush(QColor(50, 82, 138)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRect(x + 2, hc_bar_y, cw_, bar_h), 2, 2)
                # Filled portion
                hc_color = (UTIL_HIGH if hc_ratio > 0.9 else
                            UTIL_MED  if hc_ratio > 0.6 else
                            (UTIL_LOW if hc_ratio > 0 else QColor(214, 218, 227)))
                if hc_fill_w > 0:
                    p.setBrush(QBrush(hc_color))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(QRect(x + 2, hc_bar_y, hc_fill_w, bar_h), 2, 2)
                # % badge — always at right edge (100% position)
                if ds_str in self._hc_util_data:
                    hc_tag_txt = f"{int(hc_pct)}%"
                    p.setFont(f_cap)
                    hc_tag_w   = _FM(f_cap).horizontalAdvance(hc_tag_txt) + 6
                    hc_tag_h   = 10
                    hc_tag_x   = x + DAY_W - hc_tag_w - 2
                    hc_tag_y   = HEADER_H + UTIL_ROW_H + (UTIL_ROW_H - hc_tag_h) // 2
                    hc_bg      = QColor(194, 52, 47) if hc_ratio > 0.9 else QColor(30, 58, 110, 200)
                    p.setBrush(QBrush(hc_bg))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(QRect(hc_tag_x, hc_tag_y, hc_tag_w, hc_tag_h), 2, 2)
                    p.setPen(QPen(Qt.GlobalColor.white))
                    p.drawText(QRect(hc_tag_x, hc_tag_y, hc_tag_w, hc_tag_h),
                               Qt.AlignmentFlag.AlignCenter, hc_tag_txt)

                # Row separator between CAPACITY and LABOR rows
                p.setPen(QPen(QColor(255, 255, 255, 35)))
                p.drawLine(x, HEADER_H + UTIL_ROW_H, x + DAY_W, HEADER_H + UTIL_ROW_H)

                # Cap divider
                p.setPen(QPen(QColor(255, 255, 255, 15)))
                p.drawLine(x, HEADER_H, x, HEADER_H + UTIL_H)
        else:
            n = len(self._shifts)
            if n:
                for day in range(self.horizon_days):
                    d  = self.start_date + timedelta(days=day)
                    x0 = yw + day * n * SHIFT_W
                    p.setFont(f_dd); p.setPen(QPen(HEADER_FG))
                    p.drawText(QRect(x0, 2, n * SHIFT_W, 22),
                               Qt.AlignmentFlag.AlignCenter,
                               d.strftime("%m/%d"))
                    p.setFont(f_dow)
                    for si, shift in enumerate(self._shifts):
                        sx = x0 + si * SHIFT_W
                        p.setPen(QPen(QColor(255, 255, 255, 25)))
                        p.drawLine(sx, HEADER_H // 2, sx, HEADER_H - 2)
                        p.setPen(QPen(QColor(174, 191, 230)))
                        p.drawText(QRect(sx, 24, SHIFT_W, 14),
                                   Qt.AlignmentFlag.AlignCenter,
                                   f"S{shift['shift_no']}")

        p.restore()

        # ── Fixed Y-label corner ──────────────────────────────────────────────
        p.fillRect(0, 0, yw, HEADER_H, QColor(38, 68, 128))
        p.fillRect(0, HEADER_H, yw, UTIL_H, QColor(30, 58, 110))

        # Accent strips: 3px wide left-edge strip per row for visual identity
        _ACCENT_W = 3
        cap_row_y  = HEADER_H + 2
        labor_row_y = HEADER_H + UTIL_ROW_H + 2
        strip_h    = UTIL_ROW_H - 4
        p.setPen(Qt.PenStyle.NoPen)
        # CAPACITY row — green accent
        p.setBrush(QBrush(UTIL_LOW))
        p.drawRoundedRect(QRect(0, cap_row_y, _ACCENT_W, strip_h), 1, 1)
        # LABOR row — sky-blue accent
        p.setBrush(QBrush(QColor(96, 165, 250)))
        p.drawRoundedRect(QRect(0, labor_row_y, _ACCENT_W, strip_h), 1, 1)

        # Separator line between CAPACITY and LABOR in the corner
        p.setPen(QPen(QColor(255, 255, 255, 35)))
        p.drawLine(0, HEADER_H + UTIL_ROW_H, yw, HEADER_H + UTIL_ROW_H)

        # Row labels — 8px Bold, crisp blue-white
        p.setPen(QPen(QColor(200, 218, 238)))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        lbl_x    = _ACCENT_W + 5   # indent past accent strip
        lbl_w    = yw - lbl_x - 2
        cap_lbl_y  = HEADER_H + (UTIL_ROW_H - 12) // 2
        labor_lbl_y = HEADER_H + UTIL_ROW_H + (UTIL_ROW_H - 12) // 2
        p.drawText(QRect(lbl_x, cap_lbl_y, lbl_w, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "CAPACITY")
        p.drawText(QRect(lbl_x, labor_lbl_y, lbl_w, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "LABOR")
        p.setPen(QPen(QColor(255, 255, 255, 40)))
        p.drawLine(yw, 0, yw, self._TOTAL_H)
        p.end()

    def _col_w(self) -> int:
        return SHIFT_W if self.shift_view else DAY_W

    def _col_count(self) -> int:
        return self.horizon_days * len(self._shifts) if self.shift_view else self.horizon_days

    def _date_to_col(self, d: str, shift_no: int = 1):
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


# ─── Frozen Y-axis label column ───────────────────────────────────────────────

class GanttYLabelWidget(QWidget):
    """Frozen Y-axis sidebar. Lives outside QScrollArea; syncs with vertical scroll."""

    YLBL_BG      = QColor(240, 242, 248)
    YLBL_GRP_BG  = QColor(228, 232, 243)
    YLBL_GRP_FG  = QColor(30,  41,  59)
    YLBL_ROW_A   = QColor(247, 248, 251)
    YLBL_ROW_B   = QColor(239, 241, 246)
    YLBL_SUB_FG  = QColor(100, 116, 139)
    YLBL_SEP_MAJ = QColor(200, 206, 223)
    YLBL_SEP_MIN = QColor(221, 226, 238)
    YLBL_BORDER  = QColor(192, 200, 220)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows         : List[str] = []
        self._row_heights  : List[int] = []
        self._row_y_list   : List[int] = []
        self._total_body_h : int       = 0
        self._y_label_w    : int       = DIM_COL_W
        self._y_dims       : List[str] = ["Room"]
        self._scroll_v     : int       = 0
        self.setFixedWidth(DIM_COL_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def sync_from(self, canvas: 'GanttCanvas'):
        self._canvas       = canvas
        self._rows         = canvas._rows
        self._row_heights  = canvas._row_heights
        self._row_y_list   = canvas._row_y_list
        self._total_body_h = canvas._total_body_h
        self._y_label_w    = canvas._y_label_w
        self._y_dims       = canvas.y_dims[:]
        self.setFixedWidth(self._y_label_w)
        self.update()

    def set_scroll_v(self, val: int):
        self._scroll_v = val
        self.update()

    def _y_sub_label(self, row_key: str) -> str:
        dim = self._y_dims[0] if self._y_dims else ""
        val = row_key.split("|")[0]
        if dim == "Room":
            try:
                from data.repositories import RoomRepo as _RR
                procs = [r["process_name"] for r in _RR.all() if r["room_code"] == val]
                unique = list(dict.fromkeys(procs))
                return " · ".join(unique[:2]) if unique else ""
            except Exception:
                return ""
        return ""

    def paintEvent(self, event):
        # Always pull fresh layout from canvas so card-height toggles stay in sync.
        if hasattr(self, '_canvas'):
            c = self._canvas
            self._rows         = c._rows
            self._row_heights  = c._row_heights
            self._row_y_list   = c._row_y_list
            self._total_body_h = c._total_body_h
            self._y_dims       = c.y_dims[:]
        if not self._rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        yw    = self._y_label_w
        ndims = len(self._y_dims)
        cw    = yw // ndims if ndims else yw
        vh    = self.height()

        p.save()
        p.setClipRect(0, 0, yw - 1, vh)
        p.translate(0, -self._scroll_v)

        full_h = self._total_body_h + self._scroll_v + vh
        p.fillRect(0, 0, yw, max(self._total_body_h, full_h), self.YLBL_BG)

        if ndims == 1:
            f_pri  = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
            f_sub  = QFont("Segoe UI", 8)
            fm_pri = QFontMetrics(f_pri)
            fm_sub = QFontMetrics(f_sub)

            for ri, rk in enumerate(self._rows):
                y  = self._row_y_list[ri]
                rh = self._row_heights[ri]
                p.fillRect(0, y, yw, rh,
                           self.YLBL_ROW_A if ri % 2 == 0 else self.YLBL_ROW_B)
                p.fillRect(0, y, 3, rh, _color_for_key(rk.split("|")[0]))
                p.setPen(QPen(self.YLBL_SEP_MAJ, 1))
                p.drawLine(0, y, yw, y)

                label = _dim_label(self._y_dims[0], rk.split("|")[0])
                sub   = self._y_sub_label(rk)
                mid   = y + rh // 2
                tx, tw = 9, yw - 13

                if sub:
                    p.setPen(QPen(self.YLBL_GRP_FG))
                    p.setFont(f_pri)
                    p.drawText(QRect(tx, mid - 13, tw, 14),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               fm_pri.elidedText(label, Qt.TextElideMode.ElideRight, tw))
                    p.setPen(QPen(self.YLBL_SUB_FG))
                    p.setFont(f_sub)
                    p.drawText(QRect(tx, mid + 2, tw, 12),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               fm_sub.elidedText(sub, Qt.TextElideMode.ElideRight, tw))
                else:
                    p.setPen(QPen(self.YLBL_GRP_FG))
                    p.setFont(f_pri)
                    p.drawText(QRect(tx, y + 2, tw, rh - 4),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               fm_pri.elidedText(label, Qt.TextElideMode.ElideRight, tw))
        else:
            f_grp  = QFont("Segoe UI", 10, QFont.Weight.Bold)
            f_lf   = QFont("Segoe UI", 9)
            fm_grp = QFontMetrics(f_grp)
            fm_lf  = QFontMetrics(f_lf)

            for d in range(ndims):
                x_col   = d * cw
                is_last = (d == ndims - 1)

                groups: List[Tuple] = []
                for ri, rk in enumerate(self._rows):
                    parts  = rk.split("|")
                    prefix = tuple(parts[:d + 1])
                    if not groups or groups[-1][0] != prefix:
                        groups.append((prefix, []))
                    groups[-1][1].append(ri)

                for gi, (prefix, row_idxs) in enumerate(groups):
                    y_top = self._row_y_list[row_idxs[0]]
                    grp_h = sum(self._row_heights[ri] for ri in row_idxs)
                    if is_last:
                        bg = self.YLBL_ROW_A if gi % 2 == 0 else self.YLBL_ROW_B
                    else:
                        bg = self.YLBL_GRP_BG
                    p.fillRect(x_col, y_top, cw, grp_h, bg)
                    if not is_last:
                        p.fillRect(x_col, y_top, 3, grp_h, _color_for_key(prefix[0]))

                    label = _dim_label(self._y_dims[d], prefix[d])
                    fm    = fm_lf if is_last else fm_grp
                    align = (Qt.AlignmentFlag.AlignCenter if not is_last
                             else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    p.setPen(QPen(self.YLBL_GRP_FG))
                    p.setFont(f_grp if not is_last else f_lf)
                    p.drawText(QRect(x_col + 10, y_top + 2, cw - 14, grp_h - 4),
                               align,
                               fm.elidedText(label, Qt.TextElideMode.ElideRight, cw - 14))

                    p.setPen(QPen(self.YLBL_SEP_MAJ, 1))
                    p.drawLine(x_col, y_top, x_col + cw, y_top)

                    if is_last:
                        for ri in row_idxs[1:]:
                            y = self._row_y_list[ri]
                            p.setPen(QPen(self.YLBL_SEP_MIN, 1, Qt.PenStyle.DotLine))
                            p.drawLine(x_col, y, x_col + cw, y)

            p.setPen(QPen(self.YLBL_BORDER, 1))
            for d in range(1, ndims):
                p.drawLine(d * cw, 0, d * cw, self._total_body_h)

        p.restore()
        p.setPen(QPen(self.YLBL_BORDER, 1))
        p.drawLine(yw - 1, 0, yw - 1, vh)


# ─── Gantt Canvas ─────────────────────────────────────────────────────────────

class GanttCanvas(QWidget):
    planMoved           = pyqtSignal(int, str, int, str)
    planSelected        = pyqtSignal(dict)
    selectionChanged    = pyqtSignal(list)
    summaryCardClicked  = pyqtSignal(dict)
    layoutChanged       = pyqtSignal()

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
        self.horizon_days = 90
        self.start_date   : date = date.today()

        self._plans         : List[Dict] = []
        self._expanded_plans: List[Dict] = []   # mat plans split per SO (or == _plans)
        self._pid_to_plan   : Dict[int, Dict] = {}  # plan_id → plan dict (fast lookup)
        self._sos      : Dict       = {}
        self._skus     : Dict       = {}
        self._shifts   : List[Dict] = []
        self._rows     : List[str]  = []
        self._conflicts: List[Dict] = []
        self._search_filter: str    = ""
        self._status_filter: str    = ""   # "on_time" | "at_risk" | "late" | ""

        self._checked  : Set[int] = set()
        self._hover_plan_id : Optional[int]    = None  # card under mouse cursor
        self._drag_plan_id  : Optional[int]    = None
        self._drag_origin   : Optional[QPoint] = None
        self._drag_offset   : QPoint = QPoint(0, 0)
        self._drag_rect     : Optional[QRect]  = None
        self._drag_invalid  : bool             = False
        self._drag_split    : bool             = False   # Ctrl+drag → split mode
        # Stack reorder state (same-cell vertical reorder)
        self._stack_drag      : bool       = False
        self._stack_guide_y   : int        = -1    # guide line Y pixel
        self._stack_guide_idx : int        = 0     # insertion index
        self._stack_orig_col  : int        = -1    # column of origin cell
        self._stack_orig_row  : int        = -1    # row index of origin cell
        self._stack_cell_plans: list       = []    # plans in origin cell, sorted
        # (room_code, process_name) pairs that are valid — populated in load_data
        self._room_proc_set   : set = set()
        self._company_holidays: set = set()   # date ISO strings from DB
        # process_name → minimum process_seq across all SKU routings (for Y-axis ordering)
        self._proc_seq_order  : Dict[str, int] = {}

        # Material first-use date: plan_id → earliest SKU plan_date that consumes it
        self._mat_first_use     : Dict[int, str]   = {}

        # Summarize mode — collapse same (date,shift,room,process,sku) into one card
        self._summarize          : bool            = True
        self._summarized_plans   : List[Dict]      = []   # merged plan dicts
        self._summary_groups     : Dict[int, List[int]] = {}  # rep_id → member_ids

        # Panel drop highlight rect (set during dragMoveEvent from SummaryDetailPanel)
        self._panel_drop_rect    : Optional[QRect] = None
        self.setAcceptDrops(True)

        # Active-filter result: subset of _display_plans after search/status/final_only
        self._filtered_plans     : List[Dict]      = []

        # CLOSE badge visibility (off by default)
        self._show_close_badge   : bool            = False
        self._show_so_num        : bool            = True
        self._show_customer      : bool            = True

        # Final-only filter — hide non-final-seq plans
        self._final_only         : bool            = False

        self._cell_map      : Dict[int, QRect] = {}
        self._check_rects   : Dict[int, QRect] = {}
        self._check_hit_rects: Dict[int, QRect] = {}
        self._cap_map     : Dict[Tuple, Tuple] = {}
        # Per-cell utilization (Room mode): (room_code, date_str) -> (used, cap)
        self._cell_util   : Dict[Tuple, Tuple] = {}
        # headcount util: (date_str, shift_no) -> (alloc, crp_total)
        self._hc_map      : Dict[Tuple, Tuple] = {}
        # HC utilisation by date: date_str -> pct (0-100)
        self._hc_util_by_date: Dict[str, float] = {}
        # Raw LABOR data for tooltip: date_str -> (alloc_hc, total_hc)
        self._hc_alloc_by_date: Dict[str, Tuple[float, float]] = {}
        # plan_id -> (slot_index, total_in_slot) for vertical stacking
        self._plan_layout    : Dict[int, Tuple[int, int]] = {}
        # Pre-built rects (avoid datetime.strptime in paint loop)
        self._prebuilt_rects : Dict[int, QRect]           = {}
        # (col, row_idx) → [plan_ids] for O(1) hover hit-testing
        self._spatial_index  : Dict[Tuple[int,int], List[int]] = {}
        # O(1) row lookup built in _build_rows()
        self._row_index   : Dict[str, int] = {}
        # QPixmap cache — static content rendered once per data change.
        # Only used when canvas fits within MAX_PIXMAP_MP megapixels.
        self._static_pixmap  : Optional["QPixmap"]         = None
        self._pixmap_dirty   : bool                        = True
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

    def _expand_mat_plans(self):
        """When 'SO' is a Y-axis dimension, split each MATERIAL plan into
        per-SO virtual plans so material steps appear in the same SO row
        as the SKU processes that depend on them.

        Virtual plans get negative plan_ids (no conflict with DB rows) and
        a _virtual_mat=True flag to suppress interactive features.
        qty is distributed proportionally by qty_required across members.
        When 'SO' is NOT in y_dims, _expanded_plans == _plans (no copy).
        """
        if "SO" not in self.y_dims:
            self._expanded_plans = self._plans
            return

        expanded: List[Dict] = []
        vid = -1  # virtual plan_id counter (negative, counts down)
        for p in self._plans:
            if p.get("entity_type") != "MATERIAL":
                expanded.append(p)
                continue

            gid     = p.get("material_group_id")
            members = self._mat_groups.get(gid, []) if gid else []
            if not members:
                expanded.append(p)
                continue

            total_req = sum(m.get("qty_required", 0) for m in members) or 1
            orig_qty  = p.get("qty_planned", 0)

            for m in members:
                ratio  = m.get("qty_required", 0) / total_req
                vplan  = dict(p)
                vplan["plan_id"]       = vid
                vplan["so_number"]     = m["so_number"]
                vplan["sku_code"]      = m["sku_code"]
                vplan["line_item"]     = m["line_item"]
                vplan["qty_planned"]   = max(1, round(orig_qty * ratio))
                vplan["_virtual_mat"]  = True
                expanded.append(vplan)
                vid -= 1

        self._expanded_plans = expanded

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
        self._room_proc_set    = {(r["room_code"], r["process_name"]) for r in RoomRepo.all()}
        self._company_holidays = CompanyHolidayRepo.date_set()
        # Build process → min_seq map for Y-axis ordering when "Process" dim is active
        self._proc_seq_order = {}
        for rt in ProcessRoutingRepo.all():
            pname = rt.get("process_name") or ""
            seq   = int(rt.get("process_seq") or 99)
            if pname and (pname not in self._proc_seq_order or seq < self._proc_seq_order[pname]):
                self._proc_seq_order[pname] = seq
        self._expand_mat_plans()
        self._build_mat_first_use(self._expanded_plans)
        self._build_cap_map()
        self._build_summarized_plans()
        self._apply_filters()             # must run after summarize, before rows/layout
        self._build_rows()
        self._build_layout_and_heights()
        self._build_hc_map()
        self._build_closed_map()
        # LABOR bar: 공정배정인원/전체인원 = urgency-distributed HC / total CRP HC
        try:
            d0_str = self.start_date.strftime("%Y-%m-%d")
            d1_str = (self.start_date + timedelta(days=self.horizon_days - 1)).strftime("%Y-%m-%d")
            # Numerator: sum distributed HC per day from _hc_map (shifts with plans only)
            day_alloc: Dict[str, float] = {}
            for (ds, sno), (alloc, _) in self._hc_map.items():
                day_alloc[ds] = day_alloc.get(ds, 0.0) + alloc
            # Denominator: total CRP HC per day across all shifts
            avl = scheduler.get_available_hc_by_date(d0_str, d1_str)
            self._hc_util_by_date = {}
            self._hc_alloc_by_date = {}
            for ds, shifts in avl.items():
                total_hc = sum(shifts.values())
                if total_hc > 0:
                    alloc_hc = day_alloc.get(ds, 0.0)
                    self._hc_util_by_date[ds] = alloc_hc / total_hc * 100
                    self._hc_alloc_by_date[ds] = (alloc_hc, total_hc)
        except Exception:
            self._hc_util_by_date  = {}
            self._hc_alloc_by_date = {}
        self._update_size()
        self._pixmap_dirty = True   # data changed — invalidate static cache
        self.update()

    # ── Summarize mode ───────────────────────────────────────────────────────

    def toggle_close_badge(self, on: bool):
        self._show_close_badge = on
        self._pixmap_dirty = True
        self.update()

    def toggle_so_num(self, on: bool):
        self._show_so_num = on
        self._rebuild_card_height()

    def toggle_customer(self, on: bool):
        self._show_customer = on
        self._rebuild_card_height()

    def _rebuild_card_height(self):
        """Rebuild layout after card height changes (SO#/Customer visibility)."""
        self._build_layout_and_heights()
        self._update_size()
        self._pixmap_dirty = True
        self.update()
        self.layoutChanged.emit()

    @property
    def _card_h(self) -> int:
        """Dynamic card slot height based on visible text lines."""
        extra = int(self._show_so_num) + int(self._show_customer)
        return 72 - (2 - extra) * 10   # 72 / 62 / 52

    def toggle_final_only(self, on: bool):
        self._final_only = on
        self._rebuild_after_filter()

    def toggle_summarize(self, on: bool):
        self._summarize = on
        self._build_summarized_plans()
        self._apply_filters()
        self._build_rows()
        self._build_layout_and_heights()
        self._update_size()
        self._pixmap_dirty = True
        self.update()

    def _build_mat_first_use(self, plans):
        """Build plan_id → first-use date for MATERIAL cards.
        Material plans have empty so_number/sku_code/line_item — they are
        demand-group aggregates.  Use material_group_id → _mat_groups members
        to find which (so, sku, line) combos consume this material, then find
        the earliest SKU plan date >= this material plan's date.
        """
        from collections import defaultdict
        # Index SKU plan dates by (so_number, sku_code, line_item)
        sku_dates: Dict[Tuple, List[str]] = defaultdict(list)
        for p in plans:
            if p.get("entity_type") != "MATERIAL":
                sku_dates[(p["so_number"], p["sku_code"], p["line_item"])].append(
                    p["plan_date"])
        self._mat_first_use: Dict[int, str] = {}
        for p in plans:
            if p.get("entity_type") != "MATERIAL":
                continue
            gid     = p.get("material_group_id")
            members = self._mat_groups.get(gid, []) if gid else []
            mat_dt  = p["plan_date"]
            candidates: List[str] = []
            for m in members:
                key = (m["so_number"], m["sku_code"], m["line_item"])
                candidates.extend(d for d in sku_dates.get(key, []) if d >= mat_dt)
            self._mat_first_use[p["plan_id"]] = min(candidates) if candidates else None

    def _build_summarized_plans(self):
        """Collapse plans sharing the same Y-axis cell into one card.

        Grouping key adapts to the current y_dims:
        - Room in y_dims  → keep room_code  (different rooms = different rows)
        - Process in y_dims → omit process_name (already captured in row_key)
        - Otherwise        → keep process_name so different processes within a
                             row each get their own card.
        """
        if not self._summarize:
            self._summarized_plans = []
            self._summary_groups   = {}
            return

        from collections import defaultdict
        groups: Dict[Tuple, List[Dict]] = defaultdict(list)
        for p in self._expanded_plans:
            # In shift view each shift has its own column → keep shift_no in key.
            # In day view a single column spans all shifts → merge across shifts.
            shift_key = p["shift_no"] if self.shift_view else None
            # room_code: keep only when Room is a Y-dimension (different rooms = different rows)
            room_key = p["room_code"] if "Room" in self.y_dims else None
            # process_name: omit when Process is already a Y-dimension (row_key has it)
            proc_key = p["process_name"] if "Process" not in self.y_dims else None
            key = (p["plan_date"], shift_key,
                   self._plan_row_key(p),
                   room_key, proc_key,
                   p["entity_code"], p["entity_type"])
            groups[key].append(p)

        merged: List[Dict] = []
        summary_groups: Dict[int, List[int]] = {}
        for key, members in groups.items():
            members_sorted = sorted(members, key=lambda x: x["plan_id"])
            rep             = members_sorted[0]
            total_qty       = sum(m["qty_planned"] for m in members_sorted)
            member_ids      = [m["plan_id"] for m in members_sorted]
            rep_id          = rep["plan_id"]

            merged_plan = dict(rep)  # copy all fields from representative
            merged_plan["qty_planned"]  = total_qty
            merged_plan["_merged_ids"]  = member_ids
            merged_plan["_merged_count"]= len(members_sorted)
            # so_number: list of unique SO numbers for tooltip
            merged_plan["_so_list"]     = list(dict.fromkeys(
                m["so_number"] for m in members_sorted if m.get("so_number")))
            # is_locked: True if any member is locked
            merged_plan["is_locked"]    = any(m["is_locked"] for m in members_sorted)
            # Earliest due date across all merged SOs
            _due_dates = [
                so_r["due_date"]
                for m in members_sorted
                for so_r in [self._sos.get((m["so_number"], m["sku_code"], m["line_item"]))]
                if so_r and so_r.get("due_date")
            ]
            merged_plan["_earliest_due"] = min(_due_dates) if _due_dates else None
            merged.append(merged_plan)
            summary_groups[rep_id] = member_ids

        self._summarized_plans = merged
        self._summary_groups   = summary_groups

    @property
    def _display_plans(self) -> List[Dict]:
        return self._summarized_plans if self._summarize else self._expanded_plans

    def _rebuild_after_filter(self):
        self._apply_filters()
        self._build_rows()
        self._build_layout_and_heights()
        self._update_size()
        self._pixmap_dirty = True
        self.update()

    def set_search_filter(self, text: str):
        self._search_filter = text
        self._rebuild_after_filter()

    def set_status_filter(self, status: str):
        """Filter by schedule status: 'on_time', 'at_risk', 'late', or '' to clear."""
        self._status_filter = status
        self._rebuild_after_filter()

    def _is_holiday(self, d: date) -> bool:
        return is_holiday(d) or d.isoformat() in self._company_holidays

    def _plan_row_key(self, plan: Dict) -> str:
        """Return the pipe-joined row key for a plan based on current y_dims."""
        return "|".join(_dim_key(d, plan) for d in self.y_dims)

    def _has_active_filter(self) -> bool:
        return bool(self._search_filter or self._final_only or self._status_filter)

    def _apply_filters(self):
        plans = self._display_plans
        if self._search_filter:
            sf = self._search_filter
            filtered = []
            for p in plans:
                so_rec = self._sos.get((p["so_number"], p["sku_code"], p["line_item"]))
                haystack = " ".join(filter(None, [
                    p.get("so_number", ""), p.get("sku_code", ""),
                    p.get("entity_code", ""), p.get("room_code", ""),
                    p.get("process_name", ""),
                    (so_rec or {}).get("customer_name", ""),
                ])).lower()
                if sf in haystack:
                    filtered.append(p)
            plans = filtered
        if self._final_only:
            plans = [p for p in plans if p.get("is_final_seq")]
        if self._status_filter:
            sf = self._status_filter
            filtered = []
            for p in plans:
                so_rec = self._sos.get((p["so_number"], p["sku_code"], p["line_item"]))
                due_str = (so_rec or {}).get("due_date")
                if due_str:
                    days = (datetime.strptime(due_str, "%Y-%m-%d").date() - date.today()).days
                    so_status = "late" if days < 0 else ("at_risk" if days <= 3 else "on_time")
                else:
                    so_status = "on_time"
                if so_status == sf:
                    filtered.append(p)
            plans = filtered
        self._filtered_plans = plans

    def _build_rows(self):
        source = self._filtered_plans if self._has_active_filter() else self._expanded_plans
        keys = {self._plan_row_key(p) for p in source}
        # Room mode with no active filter: always show all configured rooms
        # so empty rooms are visible for drag-to-room validation
        if self.y_dims == ["Room"] and not self._has_active_filter():
            keys |= set(RoomRepo.rooms())
        # Sort: when Process is a Y dimension, order by process_seq; others alphabetical
        if "Process" in self.y_dims:
            proc_di = self.y_dims.index("Process")
            def _row_sort_key(rk: str):
                parts = rk.split("|")
                key = []
                for di, dim in enumerate(self.y_dims):
                    part = parts[di] if di < len(parts) else ""
                    if dim == "Process":
                        key.append(f"{self._proc_seq_order.get(part, 999):04d}_{part}")
                    else:
                        key.append(part)
                return key
            self._rows = sorted(keys, key=_row_sort_key)
        else:
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

        # Aggregate to (room, date) for per-cell utilization bars
        self._cell_util = {}
        for (ds, room, proc, sno), (used, cap) in self._cap_map.items():
            cu, cc = self._cell_util.get((room, ds), (0.0, 0.0))
            self._cell_util[(room, ds)] = (cu + used, cc + cap)

    def _build_layout_and_heights(self):
        """Assign vertical slot index to each plan (for stacking) and compute
        per-row heights based on the max number of plans in any slot of that row.
        Uses the actual rendered column index as the slot key so that day-view
        (shift_no ignored) and shift-view produce correct non-overlapping stacks.
        Also pre-builds _prebuilt_rects (eliminates datetime.strptime per repaint)
        and _spatial_index (O(1) plan hit-testing for mouse hover).
        """
        from collections import defaultdict

        # ── Pass 1: compute (col, row_key) for every plan ──────────────────────
        # Store col per plan_id so we reuse it for rect pre-build below.
        # Also rebuild pid_to_plan lookup (used by _plan_at fast path).
        _pid_col: Dict[int, Optional[int]] = {}
        _pid_rk : Dict[int, str]           = {}
        slot_plans: Dict[Tuple, List[int]] = defaultdict(list)
        self._pid_to_plan = {}
        for p in self._filtered_plans:
            rk  = self._plan_row_key(p)
            col = self._date_to_col(p["plan_date"], p["shift_no"])
            _pid_col[p["plan_id"]] = col
            _pid_rk [p["plan_id"]] = rk
            self._pid_to_plan[p["plan_id"]] = p
            if col is None:
                continue
            slot_plans[(col, rk)].append(p["plan_id"])

        # ── Pass 2: stack order within each slot ───────────────────────────────
        self._plan_layout = {}
        pid_to_so = {p["plan_id"]: p.get("stack_order", 0) for p in self._filtered_plans}
        for sk, pids in slot_plans.items():
            pids_sorted = sorted(pids, key=lambda pid: (pid_to_so.get(pid, 0), pid))
            for i, pid in enumerate(pids_sorted):
                self._plan_layout[pid] = (i, len(pids_sorted))

        # ── Pass 3: row heights ────────────────────────────────────────────────
        row_max: Dict[str, int] = {}
        for (_, rk), pids in slot_plans.items():
            row_max[rk] = max(row_max.get(rk, 1), len(pids))

        self._row_y_list  = []
        self._row_heights = []
        y = self._body_top()
        _has_room = "Room" in self.y_dims
        for rk in self._rows:
            h = max(1, row_max.get(rk, 1)) * self._card_h
            if _has_room:
                h += CELL_UTIL_H  # reserve bottom strip for per-cell util bar
            self._row_y_list.append(y)
            self._row_heights.append(h)
            y += h
        self._total_body_h = y - self._body_top()

        # ── Pass 4: pre-build QRects ───────────────────────────────────────────
        # Avoids datetime.strptime + dict lookups inside every paintEvent call.
        col_w = self._col_w()
        self._prebuilt_rects: Dict[int, QRect] = {}
        # Spatial index: (col, row_idx) → [plan_id, ...] for O(1) hover detection
        self._spatial_index: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for p in self._filtered_plans:
            pid = p["plan_id"]
            col = _pid_col.get(pid)
            if col is None:
                continue
            rk  = _pid_rk.get(pid, "")
            ri  = self._row_index.get(rk)
            if ri is None or ri >= len(self._row_y_list):
                continue
            slot_idx = self._plan_layout.get(pid, (0, 1))[0]
            x = col * col_w + 1
            w = col_w - 2
            y_pos = self._row_y_list[ri] + slot_idx * self._card_h + 2
            rect = QRect(x, y_pos, w, self._card_h - 4)
            self._prebuilt_rects[pid] = rect
            self._spatial_index[(col, ri)].append(pid)

        # Pre-populate _cell_map from prebuilt rects so hit-testing works
        # even for plans that are outside the current paint clip region.
        self._cell_map = dict(self._prebuilt_rects)

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
        return self._col_count() * self._col_w()

    def _total_h(self):
        return self._body_top() + self._total_body_h + 20

    def _row_at_y(self, y: int) -> int:
        """Return the row index at pixel y, or -1 if outside all rows.
        Uses bisect for O(log N) lookup instead of O(N) linear scan.
        """
        import bisect
        idx = bisect.bisect_right(self._row_y_list, y) - 1
        if idx < 0 or idx >= len(self._row_heights):
            return -1
        if y < self._row_y_list[idx] + self._row_heights[idx]:
            return idx
        return -1

    def _update_size(self):
        self.setMinimumSize(self._total_w(), max(400, self._total_h()))

    def _body_top(self):
        """Canvas body starts at y=0 — header/util are in the frozen widget above."""
        return 0

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
        # Fast path: use pre-built rect (avoids datetime.strptime per call)
        cached = self._prebuilt_rects.get(plan["plan_id"])
        if cached is not None:
            return cached
        # Fallback for plans added after the last _build_layout_and_heights call
        col = self._date_to_col(plan["plan_date"], plan["shift_no"])
        row = self._row_for_plan(plan)
        if col is None or row is None or row >= len(self._row_y_list):
            return None
        slot_idx = self._plan_layout.get(plan["plan_id"], (0, 1))[0]
        col_w = self._col_w()
        x = col * col_w + 1
        w = col_w - 2
        y = self._row_y_list[row] + slot_idx * self._card_h + 2
        return QRect(x, y, w, self._card_h - 4)

    def _checkbox_rect(self, plan_rect: QRect) -> QRect:
        pill_y = plan_rect.y() + PILL_MARGIN
        return QRect(plan_rect.x() + 8,
                     pill_y + (PILL_H - CHECKBOX_S) // 2,
                     CHECKBOX_S, CHECKBOX_S)

    # ── Painting ──────────────────────────────────────────────────────────────

    # Maximum canvas size (in megapixels) to use QPixmap caching.
    # Above this threshold the pixmap would be too large (> ~200 MB).
    _MAX_PIXMAP_MP = 50

    def _use_pixmap_cache(self) -> bool:
        mp = (self._total_w() * self._total_h()) / 1_000_000
        return mp <= self._MAX_PIXMAP_MP

    def _render_to_pixmap(self):
        from PyQt6.QtGui import QPixmap
        sz = self.size()
        if sz.isEmpty():
            return
        dpr = self.devicePixelRatio()
        pw, ph = int(sz.width() * dpr), int(sz.height() * dpr)
        pm = QPixmap(pw, ph)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        try:
            p.scale(dpr, dpr)  # logical coords → physical pixels
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            self._draw_grid(p)
            self._draw_today_line(p)
            self._draw_plans(p)
            self._draw_cell_util_bars(p)
        finally:
            p.end()
        self._static_pixmap = pm
        self._pixmap_dirty  = False

    def _draw_hover_overlay(self, p: QPainter):
        """Draw hover highlight on top of the cached pixmap (dynamic overlay)."""
        # Panel drop target highlight
        if self._panel_drop_rect:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(QPen(QColor(37, 99, 235, 180), 2, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(37, 99, 235, 30)))
            p.drawRect(self._panel_drop_rect)
        # Existing hover card outline
        if not self._hover_plan_id:
            return
        rect = self._prebuilt_rects.get(self._hover_plan_id)
        if rect:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor(37, 99, 235, 200), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), CARD_RADIUS, CARD_RADIUS)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            if self._use_pixmap_cache():
                # ── Pixmap-cached path (most common case) ─────────────────────
                # Re-render the static pixmap only when data/state changed.
                dpr = self.devicePixelRatio()
                sz  = self.size()
                need_render = (
                    self._pixmap_dirty or
                    self._static_pixmap is None or
                    self._static_pixmap.size() != QSize(int(sz.width() * dpr), int(sz.height() * dpr))
                )
                if need_render:
                    self._render_to_pixmap()
                # Blit the cached pixmap scaled to widget logical size → physical 1:1.
                p.drawPixmap(self.rect(), self._static_pixmap)
                # Dynamic overlays drawn on top (never invalidate the pixmap).
                self._draw_hover_overlay(p)
                if self._stack_drag:
                    self._draw_stack_guide(p)
                elif self._drag_rect:
                    self._draw_drag_ghost(p)
            else:
                # ── Direct-render path (large SO-mode canvas) ─────────────────
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_grid(p)
                self._draw_today_line(p)
                self._draw_plans(p)
                self._draw_cell_util_bars(p)
                self._draw_hover_overlay(p)
                if self._stack_drag:
                    self._draw_stack_guide(p)
                elif self._drag_rect:
                    self._draw_drag_ghost(p)
        except Exception:
            import traceback
            with open("drag_crash.txt", "w", encoding="utf-8") as f:
                f.write("paintEvent:\n" + traceback.format_exc())
        finally:
            p.end()

    def _draw_grid(self, p: QPainter):
        w, h = self._total_w(), self._total_h()
        cw_  = self._col_w()
        clip = p.clipBoundingRect().toRect() if p.hasClipping() else QRect(0, 0, w, h)
        cx0, cy0, cx1, cy1 = clip.left(), clip.top(), clip.right(), clip.bottom()

        # Alternating row backgrounds (skip rows outside clip)
        for ri in range(len(self._rows)):
            y  = self._row_y_list[ri]
            rh = self._row_heights[ri]
            if y + rh < cy0: continue
            if y > cy1: break
            bg = ROW_BG_A if ri % 2 == 0 else ROW_BG_B
            p.fillRect(0, y, w, rh, bg)
        # Weekend + today column tints (skip columns outside clip)
        if not self.shift_view:
            today_col_idx = self._date_to_col(date.today().strftime("%Y-%m-%d"))
            col_start = max(0, cx0 // DAY_W)
            col_end   = min(self.horizon_days, cx1 // DAY_W + 1)
            for col in range(col_start, col_end):
                d = self.start_date + timedelta(days=col)
                x = col * DAY_W
                if d.weekday() >= 5:
                    p.fillRect(x, 0, DAY_W, h, GRID_WEEKEND)
                if self._is_holiday(d):
                    p.fillRect(x, 0, DAY_W, h, GRID_HOLIDAY)
                if col == today_col_idx:
                    p.fillRect(x, 0, DAY_W, h, TODAY_COL_TINT)
        # Unavailable cells: closed=gray hatch, hold=orange hatch
        if self._closed_map:
            for (ri, col), status in self._closed_map.items():
                if ri >= len(self._row_y_list):
                    continue
                cy  = self._row_y_list[ri]
                crh = self._row_heights[ri]
                cx  = col * cw_
                # Skip cells entirely outside clip
                if cx + cw_ < cx0 or cx > cx1 or cy + crh < cy0 or cy > cy1:
                    continue
                if status == "hold":
                    base  = QColor(255, 160, 40, 55)
                    hatch = QColor(210, 120, 20, 110)
                else:
                    base  = QColor(160, 160, 165, 60)
                    hatch = QColor(120, 120, 128, 120)
                p.fillRect(cx, cy, cw_, crh, base)
                old_pen = p.pen()
                p.setPen(QPen(hatch, 1))
                step = 8
                x0, y0, x1, y1 = cx, cy, cx + cw_, cy + crh
                for offset in range(-(crh), cw_, step):
                    ax = x0 + offset
                    p.drawLine(max(ax, x0), y0 if ax >= x0 else y0 + (x0 - ax),
                               min(ax + crh, x1), y0 + crh if ax + crh <= x1 else y1 - ((ax + crh) - x1))
                p.setPen(old_pen)
        # Vertical column dividers (skip columns outside clip)
        p.setPen(QPen(GRID_LINE, 1))
        col_start = max(0, cx0 // cw_) if cw_ > 0 else 0
        col_end   = min(self._col_count(), cx1 // cw_ + 1) if cw_ > 0 else self._col_count()
        for col in range(col_start, col_end + 1):
            x = col * cw_
            p.drawLine(x, cy0, x, min(cy1, h))
        # Horizontal row dividers (skip rows outside clip)
        for ri in range(len(self._rows)):
            y = self._row_y_list[ri]
            if y < cy0: continue
            if y > cy1: break
            p.drawLine(0, y, w, y)
        if cy0 <= self._total_body_h <= cy1:
            p.drawLine(0, self._total_body_h, w, self._total_body_h)

    def _draw_y_labels(self, p: QPainter):
        """Y-axis labels — mockup style: #f7f8fb bg, bold name + muted sub-label."""
        yw    = self._y_label_w
        ndims = len(self.y_dims)
        cw    = yw // ndims if ndims else yw

        BG_A   = QColor(247, 248, 251)   # #f7f8fb
        BG_B   = QColor(243, 244, 247)   # slightly darker alt row
        FG_PRI = QColor(22,  33,  61)    # #16213d bold
        FG_SUB = QColor(139, 147, 168)   # #8b93a8 muted
        SEP    = QColor(233, 234, 240)   # #e9eaf0 row divider
        COL_SEP= QColor(200, 203, 215)   # dim column divider

        f_pri = QFont("Segoe UI", 12, QFont.Weight.Bold)
        f_sub = QFont("Segoe UI", 10)

        total_h = self._total_body_h
        p.fillRect(0, 0, yw, total_h, BG_A)

        if ndims == 1:
            for ri, rk in enumerate(self._rows):
                y  = self._row_y_list[ri]
                rh = self._row_heights[ri]
                bg = BG_A if ri % 2 == 0 else BG_B
                p.fillRect(0, y, yw, rh, bg)

                # Row divider
                p.setPen(QPen(SEP, 1))
                p.drawLine(0, y, yw, y)

                label = _dim_label(self.y_dims[0], rk.split("|")[0])
                # Try to get a sub-label
                sub = self._y_sub_label(rk, 0)

                cx_pad, cy_pad = 14, 0
                text_w = yw - cx_pad - 6
                mid = y + rh // 2
                if sub:
                    p.setPen(QPen(FG_PRI))
                    p.setFont(f_pri)
                    p.drawText(QRect(cx_pad, mid - 14, text_w, 16),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               label)
                    p.setPen(QPen(FG_SUB))
                    p.setFont(f_sub)
                    p.drawText(QRect(cx_pad, mid + 2, text_w, 14),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               sub)
                else:
                    p.setPen(QPen(FG_PRI))
                    p.setFont(f_pri)
                    p.drawText(QRect(cx_pad, y + 2, text_w, rh - 4),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               label)

            # Right border
            p.setPen(QPen(COL_SEP, 1))
            p.drawLine(yw - 1, 0, yw - 1, total_h)
            return

        # Multi-depth spanning groups
        f_grp = QFont("Segoe UI", 10, QFont.Weight.Bold)
        f_lf  = QFont("Segoe UI", 9)

        for d in range(ndims):
            x_col   = d * cw
            is_last = (d == ndims - 1)

            groups: List[Tuple[tuple, List[int]]] = []
            for ri, rk in enumerate(self._rows):
                parts  = rk.split("|")
                prefix = tuple(parts[:d + 1])
                if not groups or groups[-1][0] != prefix:
                    groups.append((prefix, []))
                groups[-1][1].append(ri)

            for gi, (prefix, row_idxs) in enumerate(groups):
                bg    = BG_A if gi % 2 == 0 else BG_B
                y_top = self._row_y_list[row_idxs[0]]
                grp_h = sum(self._row_heights[ri] for ri in row_idxs)
                p.fillRect(x_col, y_top, cw, grp_h, bg)

                label = _dim_label(self.y_dims[d], prefix[d])
                p.setPen(QPen(FG_PRI))
                p.setFont(f_grp if not is_last else f_lf)
                align = (Qt.AlignmentFlag.AlignCenter
                         if not is_last
                         else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                p.drawText(QRect(x_col + 10, y_top + 2, cw - 14, grp_h - 4),
                           align, label)

                p.setPen(QPen(SEP, 1))
                p.drawLine(x_col, y_top, x_col + cw, y_top)

                if is_last:
                    for ri in row_idxs[1:]:
                        y = self._row_y_list[ri]
                        p.setPen(QPen(SEP, 1, Qt.PenStyle.DotLine))
                        p.drawLine(x_col, y, x_col + cw, y)

        # Column dividers and right border
        p.setPen(QPen(COL_SEP, 1))
        for d in range(1, ndims):
            x = d * cw
            p.drawLine(x, 0, x, total_h)
        p.drawLine(yw - 1, 0, yw - 1, total_h)

    def _y_sub_label(self, row_key: str, dim_idx: int) -> str:
        """Return a muted sub-label for single-depth Y rows."""
        dim = self.y_dims[dim_idx] if dim_idx < len(self.y_dims) else ""
        val = row_key.split("|")[0] if "|" not in row_key else ""
        if dim == "Room":
            # Show process name(s) assigned to this room
            try:
                from data.repositories import RoomRepo as _RR
                procs = [r["process_name"] for r in _RR.all()
                         if r["room_code"] == val]
                unique_procs = list(dict.fromkeys(procs))
                return " · ".join(unique_procs[:2]) if unique_procs else ""
            except Exception:
                return ""
        return ""

    def _draw_today_line(self, p: QPainter):
        col = self._date_to_col(date.today().strftime("%Y-%m-%d"))
        if col is None:
            return
        x = col * self._col_w()
        p.setOpacity(0.6)
        p.setPen(QPen(TODAY_LINE, 2))
        p.drawLine(x, self._body_top(), x, self._total_h())
        p.setOpacity(1.0)

    def _draw_plans(self, p: QPainter):
        # _cell_map pre-populated from _prebuilt_rects in _build_layout_and_heights;
        # only clear the per-draw hit-test maps (populated for visible plans only).
        self._check_rects.clear()
        self._check_hit_rects.clear()
        conflict_slots = {
            (c["plan_date"], c["room_code"], c["process_name"], c["shift_no"])
            for c in self._conflicts
        }
        consol_groups: Dict[str, List[QRect]] = {}
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Viewport culling: skip plans outside the current dirty/clip rect.
        clip = p.clipBoundingRect().toRect() if p.hasClipping() else self.rect()

        f_l1 = QFont("Segoe UI", 9, QFont.Weight.Bold)
        f_l2 = QFont("Segoe UI", 8)
        f_l2.setWeight(QFont.Weight.Medium)
        f_l3 = QFont("Segoe UI", 7)
        f_tag = QFont("Segoe UI", 7, QFont.Weight.Bold)
        f_lock = QFont("Segoe UI", 7)

        for plan in self._filtered_plans:
            rect = self._plan_rect(plan)
            if not rect:
                continue
            # Skip plans outside the clip region (viewport culling).
            if not clip.intersects(rect):
                continue
            is_merged = len(plan.get("_merged_ids", [])) > 1
            self._cell_map[plan["plan_id"]] = rect
            cb_vis = self._checkbox_rect(rect)
            self._check_rects[plan["plan_id"]] = cb_vis
            cx = cb_vis.x() + cb_vis.width() // 2
            cy = cb_vis.y() + cb_vis.height() // 2
            # Merged cards: register hit rect but block checkbox toggle in mousePressEvent
            self._check_hit_rects[plan["plan_id"]] = QRect(cx - 10, cy - 10, 20, 20)

            so_rec = self._sos.get((plan["so_number"], plan["sku_code"], plan["line_item"]))

            p.setOpacity(1.0)

            is_mat = plan.get("entity_type") == "MATERIAL"
            so = self._sos.get((plan["so_number"], plan["sku_code"], plan["line_item"]))
            is_late = (so and
                       datetime.strptime(so["due_date"], "%Y-%m-%d").date() < date.today()
                       and plan.get("qty_produced", 0) < plan["qty_planned"])
            is_io   = (plan.get("so_number") or "").startswith("IO-")
            accent = (MAT_ACCENT  if is_mat
                      else LATE_ACCENT if is_late
                      else IO_ACCENT   if is_io
                      else _color_for_key(plan["sku_code"]))

            # ── Late glow (soft red halo drawn behind card) ───────────────────
            if is_late:
                p.setPen(Qt.PenStyle.NoPen)
                for spread, alpha in [(9, 10), (7, 18), (5, 30), (3, 48), (1, 68)]:
                    g = QRectF(rect.adjusted(-spread, -spread, spread, spread))
                    p.setBrush(QBrush(QColor(220, 38, 38, alpha)))
                    p.drawRoundedRect(g, CARD_RADIUS + spread, CARD_RADIUS + spread)

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

            # Hover highlight is now drawn as a dynamic overlay in _draw_hover_overlay()
            # to avoid invalidating the static pixmap on every mouse move.

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

            # IO badge (indigo pill, shown for Internal Order plans)
            if is_io:
                _io_bw, _io_bh = 16, PILL_H - 4
                _io_br = QRect(cb.right() + 4, pill_y + 2, _io_bw, _io_bh)
                p.setBrush(QBrush(IO_ACCENT))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(_io_br, 2, 2)
                p.setPen(QPen(Qt.GlobalColor.white))
                p.setFont(QFont("Segoe UI", 5, QFont.Weight.Bold))
                p.drawText(_io_br, Qt.AlignmentFlag.AlignCenter, "IO")

            # Lock icon (pill-row right side)
            lock_right = rect.right() - 3
            if plan["is_locked"]:
                p.setPen(QPen(QColor(160, 168, 190)))
                p.setFont(f_lock)
                lk_r = QRect(rect.right() - 13, pill_y + (PILL_H - 10) // 2, 11, 10)
                p.drawText(lk_r, Qt.AlignmentFlag.AlignCenter, "🔒")
                lock_right = lk_r.x() - 2

            # Campaign CLOSE badge (before CLOSED/HOLD and due-date badges)
            if plan.get("is_closing_shift") and self._show_close_badge and not is_mat:
                _cl_lbl = "CLOSE"
                _cl_bw  = QFontMetrics(f_tag).horizontalAdvance(_cl_lbl) + 6
                _cl_bh  = PILL_H - 4
                _cl_r   = QRect(lock_right - _cl_bw - 2, pill_y + 2, _cl_bw, _cl_bh)
                p.setBrush(QBrush(CLOSE_TAG_BG))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(_cl_r, 2, 2)
                p.setPen(QPen(Qt.GlobalColor.white))
                p.setFont(QFont("Segoe UI", 5, QFont.Weight.Bold))
                p.drawText(_cl_r, Qt.AlignmentFlag.AlignCenter, _cl_lbl)
                lock_right = _cl_r.x() - 2

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

            if is_mat:
                # Material card: show first-use date (when SKU process consumes it)
                _fuse = self._mat_first_use.get(plan["plan_id"])
                if _fuse:
                    _fd   = datetime.strptime(_fuse, "%Y-%m-%d").date()
                    _dlbl = f"{_fd.month}.{_fd.day}"
                    _days = (_fd - date.today()).days
                    _tbg  = QColor(207, 250, 254)   # light cyan
                    _tfg  = QColor(8, 145, 178) if _days >= 0 else QColor(194, 52, 47)
                    _fw   = QFontMetrics(f_tag).horizontalAdvance(_dlbl) + 8
                    _fh   = 12
                    _tx   = lock_right - _fw - (2 if not plan["is_locked"] else 0)
                    _ty   = pill_y + (PILL_H - _fh) // 2
                    _tr   = QRect(_tx, _ty, _fw, _fh)
                    p.setBrush(QBrush(_tbg))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(_tr, 3, 3)
                    p.setPen(QPen(_tfg))
                    p.setFont(f_tag)
                    p.drawText(_tr, Qt.AlignmentFlag.AlignCenter, _dlbl)
            else:
                # SKU card: production deadline = due_date - post_lead_days
                # In summary mode use earliest due across all merged SOs
                if plan.get("_earliest_due"):
                    due = plan["_earliest_due"]
                else:
                    due = so["due_date"] if so else None
                prod_deadline = None
                if due and so:
                    _lead = int((self._skus.get(so["sku_code"]) or {}).get("post_lead_days") or 0)
                    from utils.workdays import sub_workdays as _swdays
                    prod_deadline = _swdays(datetime.strptime(due, "%Y-%m-%d").date(), _lead)

                if prod_deadline:
                    days_to = (prod_deadline - date.today()).days
                    due_str = f"{prod_deadline.month}.{prod_deadline.day}"
                    tag_bg  = DUE_TAG_LATE_BG if days_to < 0 else (
                              DUE_TAG_BG if days_to <= 7 else QColor(220, 230, 245))
                    tag_fg  = DUE_TAG_LATE_FG if days_to < 0 else (
                              DUE_TAG_FG if days_to <= 7 else QColor(80, 100, 140))
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

            if not is_mat:
                _extra_y = ty + 25   # Y for next optional line
                if self._show_so_num:
                    if is_merged:
                        so_list = plan.get("_so_list", [])
                        n_unique = len(so_list)
                        so_str = so_list[0] if n_unique == 1 else f"{n_unique} SOs"
                        p.setPen(QPen(QColor(100, 130, 200) if n_unique > 1 else CARD_TEXT_L3))
                    elif plan.get("so_number"):
                        so_str = plan["so_number"]
                        p.setPen(QPen(CARD_TEXT_L3))
                    else:
                        so_str = None
                    if so_str:
                        p.setFont(f_l3)
                        p.drawText(QRect(tx, _extra_y, tw, 10),
                                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                   QFontMetrics(f_l3).elidedText(so_str, Qt.TextElideMode.ElideRight, tw))
                        _extra_y += 10

                if self._show_customer and not is_merged:
                    customer_name = (so or {}).get("customer_name") or ""
                    if customer_name:
                        p.setPen(QPen(CARD_TEXT_L3))
                        p.setFont(f_l3)
                        p.drawText(QRect(tx, _extra_y, tw, 10),
                                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                   QFontMetrics(f_l3).elidedText(
                                       customer_name, Qt.TextElideMode.ElideRight, tw))

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

    def _draw_cell_util_bars(self, p: QPainter):
        """Draw per-cell capacity utilization bar at the bottom of each row.
        Works whenever 'Room' is in y_dims (not just pure Room mode), day view only."""
        if "Room" not in self.y_dims or self.shift_view:
            return
        from PyQt6.QtGui import QFontMetrics
        f_util = QFont("Segoe UI", 6, QFont.Weight.Bold)
        fm = QFontMetrics(f_util)
        BAR_H = 8
        p.setFont(f_util)
        room_dim_idx = self.y_dims.index("Room")
        for ri, rk in enumerate(self._rows):
            parts = rk.split("|")
            room = parts[room_dim_idx] if room_dim_idx < len(parts) else rk
            row_y = self._row_y_list[ri]
            row_h = self._row_heights[ri]
            # Bar sits in the CELL_UTIL_H strip at the bottom of the row
            bar_y = row_y + row_h - CELL_UTIL_H + (CELL_UTIL_H - BAR_H) // 2
            for col in range(self.horizon_days):
                ds = (self.start_date + timedelta(days=col)).strftime("%Y-%m-%d")
                used, cap = self._cell_util.get((room, ds), (0.0, 0.0))
                if cap <= 0:
                    continue
                ratio = used / cap
                x   = col * DAY_W
                cw  = DAY_W - 2
                fill_w = int(cw * min(ratio, 1.0))
                # Trough
                p.setBrush(QBrush(QColor(50, 82, 138, 100)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRect(x + 1, bar_y, cw, BAR_H), 2, 2)
                # Fill
                color = (UTIL_HIGH if ratio > 0.9 else
                         UTIL_MED  if ratio > 0.6 else
                         UTIL_LOW)
                if fill_w > 0:
                    p.setBrush(QBrush(color))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(QRect(x + 1, bar_y, fill_w, BAR_H), 2, 2)
                # % label right-aligned inside bar
                pct_txt = f"{int(ratio * 100)}%"
                txt_w   = fm.horizontalAdvance(pct_txt)
                p.setPen(QPen(QColor(255, 255, 255, 220)))
                p.drawText(x + cw - txt_w - 1, bar_y + BAR_H - 1, pct_txt)

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

    def _draw_stack_guide(self, p: QPainter):
        """Blue horizontal insertion guide line for same-cell stack reorder."""
        if not self._stack_drag or self._stack_guide_y < 0 or self._stack_orig_col < 0:
            return
        col_w = self._col_w()
        x0 = self._stack_orig_col * col_w + 4
        x1 = (self._stack_orig_col + 1) * col_w - 4
        y  = self._stack_guide_y
        guide_color = QColor(37, 99, 235)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # horizontal line
        p.setPen(QPen(guide_color, 3, Qt.PenStyle.SolidLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(x0 + 8, y, x1, y)
        # circle anchor at left
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(guide_color))
        p.drawEllipse(QPointF(x0 + 4, float(y)), 5.0, 5.0)

    # ── Mouse events ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        try:
            self._mousePressEvent_impl(event)
        except Exception as e:
            import traceback
            with open("drag_crash.txt", "w", encoding="utf-8") as f:
                f.write("mousePressEvent:\n" + traceback.format_exc())
            raise

    def _mousePressEvent_impl(self, event):
        pos = event.pos()
        if event.button() == Qt.MouseButton.LeftButton:
            for pid, hit_rect in self._check_hit_rects.items():
                if hit_rect.contains(pos):
                    # Disable checkbox in summarize mode — merged cards can't be checked
                    if not self._summarize:
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
                # Capture origin cell for potential stack reorder
                orig_col = self._date_to_col(plan["plan_date"], plan["shift_no"])
                orig_row = self._row_for_plan(plan)
                if orig_col is not None and orig_row is not None:
                    self._stack_orig_col = orig_col
                    self._stack_orig_row = orig_row
                    pid_to_so = {p["plan_id"]: p.get("stack_order", 0)
                                 for p in self._plans}
                    self._stack_cell_plans = sorted(
                        [p for p in self._plans
                         if self._date_to_col(p["plan_date"], p["shift_no"]) == orig_col
                         and self._row_for_plan(p) == orig_row],
                        key=lambda p: (pid_to_so.get(p["plan_id"], 0), p["plan_id"]))
                else:
                    self._stack_orig_col = -1
                    self._stack_orig_row = -1
                    self._stack_cell_plans = []
                self.planSelected.emit(plan)

    def mouseMoveEvent(self, event):
        try:
            self._mouseMoveEvent_impl(event)
        except Exception as e:
            import traceback
            with open("drag_crash.txt", "w", encoding="utf-8") as f:
                f.write("mouseMoveEvent:\n" + traceback.format_exc())
            raise

    def _mouseMoveEvent_impl(self, event):
        pos = event.pos()
        if self._drag_plan_id and self._drag_origin:
            if (pos - self._drag_origin).manhattanLength() > 6:
                plan = next((p for p in self._plans
                             if p["plan_id"] == self._drag_plan_id), None)
                if plan:
                    rect = self._cell_map.get(plan["plan_id"])
                    if rect:
                        col_w    = self._col_w()
                        cur_col  = pos.x() // col_w
                        # In summary mode, merged cards = multiple real plans in same cell;
                        # stack reorder doesn't apply — always use regular drag.
                        has_stack = (not self._summarize
                                     and len(self._stack_cell_plans) > 1)

                        if has_stack and cur_col == self._stack_orig_col:
                            # ── STACK REORDER MODE ──────────────────────────
                            self._stack_drag    = True
                            self._drag_rect     = None
                            self._drag_invalid  = False
                            row_y = (self._row_y_list[self._stack_orig_row]
                                     if 0 <= self._stack_orig_row < len(self._row_y_list) else 0)
                            n     = len(self._stack_cell_plans)
                            rel_y = pos.y() - row_y
                            idx   = round(rel_y / self._card_h)
                            self._stack_guide_idx = max(0, min(n, idx))
                            self._stack_guide_y   = row_y + self._stack_guide_idx * self._card_h
                        else:
                            # ── REGULAR DRAG MODE ───────────────────────────
                            self._stack_drag    = False
                            self._stack_guide_y = -1
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
                                    if not self._drag_invalid and target_room:
                                        ghost_cx = self._drag_rect.center().x()
                                        col = ghost_cx // col_w
                                        if 0 <= col < self._col_count():
                                            t_date, t_shift = self._col_to_date_shift(col)
                                            t_date_str = t_date.strftime("%Y-%m-%d") if hasattr(t_date, "strftime") else str(t_date)
                                            if self._slot_closed.get((target_room, t_date_str, t_shift)):
                                                self._drag_invalid = True
                                else:
                                    self._drag_invalid = False
                            else:
                                self._drag_invalid = False
                        self.update()
        plan = self._plan_at(pos)
        new_hover = plan["plan_id"] if plan else None
        if new_hover != self._hover_plan_id:
            # Partial repaint: only the two cards whose hover state changes.
            # This avoids a full-canvas repaint on every mouse move.
            _dirty: List[QRect] = []
            if self._hover_plan_id:
                r = self._prebuilt_rects.get(self._hover_plan_id)
                if r: _dirty.append(r.adjusted(-2, -2, 2, 2))
            self._hover_plan_id = new_hover
            if new_hover:
                r = self._prebuilt_rects.get(new_hover)
                if r: _dirty.append(r.adjusted(-2, -2, 2, 2))
            if _dirty:
                for r in _dirty:
                    self.update(r)
            else:
                self.update()
        # ── Room util bar tooltip (bottom CELL_UTIL_H strip of each row) ─────
        if not plan and "Room" in self.y_dims and not self.shift_view:
            ri = self._row_at_y(pos.y())
            if 0 <= ri < len(self._row_y_list):
                row_y = self._row_y_list[ri]
                row_h = self._row_heights[ri]
                util_top = row_y + row_h - CELL_UTIL_H
                if pos.y() >= util_top:
                    col = pos.x() // DAY_W
                    if 0 <= col < self.horizon_days:
                        rk = self._rows[ri]
                        room_dim_idx = self.y_dims.index("Room")
                        parts = rk.split("|")
                        room = parts[room_dim_idx] if room_dim_idx < len(parts) else rk
                        ds = (self.start_date + timedelta(days=col)).strftime("%Y-%m-%d")
                        used, cap = self._cell_util.get((room, ds), (0.0, 0.0))
                        if cap > 0:
                            pct = used / cap * 100
                            tip = f"{room} | {ds}\n{used:,.0f} / {cap:,.0f}  ({pct:.0f}%)"
                            QToolTip.showText(QCursor.pos(), tip, self)
                            return

        if plan:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
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
                _raw_due = so.get("due_date", "")
                _lead    = int((self._skus.get(plan["sku_code"]) or {}).get("post_lead_days") or 0)
                from utils.workdays import sub_workdays as _swdays
                _prd_dl  = _swdays(datetime.strptime(_raw_due, "%Y-%m-%d").date(), _lead).strftime("%Y-%m-%d") if _raw_due else ""
                tip = (f"SO: {plan['so_number']}  SKU: {plan['sku_code']}  "
                       f"Line: {plan['line_item']}\n"
                       f"Customer: {customer}\n"
                       f"Room: {plan['room_code']}  Process: {plan['process_name']}\n"
                       f"Date: {plan['plan_date']}  Shift: {plan['shift_no']}\n"
                       f"Planned: {plan['qty_planned']}  Produced: {plan['qty_produced']}\n"
                       f"Due: {_raw_due}  Prod deadline: {_prd_dl}\n"
                       f"{'🔒 LOCKED' if plan['is_locked'] else 'unlocked'}\n"
                       f"Consol group: {grp}  "
                       f"{'⭐ FINAL' if plan.get('is_final_seq') else ''}  "
                       f"{'⚠ Closing shift (single-line)' if plan.get('is_closing_shift') else ''}")
            QToolTip.showText(QCursor.pos(), tip, self)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event):
        if self._hover_plan_id is not None:
            self._hover_plan_id = None
            self.update()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def _reset_drag_state(self):
        self._drag_plan_id    = None
        self._drag_rect       = None
        self._drag_invalid    = False
        self._drag_split      = False
        self._stack_drag      = False
        self._stack_guide_y   = -1
        self._stack_guide_idx = 0
        self._stack_cell_plans= []
        self._stack_orig_col  = -1
        self._stack_orig_row  = -1

    def mouseReleaseEvent(self, event):
        try:
            self._mouseReleaseEvent_impl(event)
        except Exception as e:
            import traceback
            with open("drag_crash.txt", "w", encoding="utf-8") as f:
                f.write("mouseReleaseEvent:\n" + traceback.format_exc())
            raise

    def _mouseReleaseEvent_impl(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_plan_id:  # noqa
            # ── STACK REORDER DROP ───────────────────────────────────────────
            if self._stack_drag:
                drag_plan = next(
                    (p for p in self._stack_cell_plans
                     if p["plan_id"] == self._drag_plan_id), None)
                if drag_plan and not drag_plan["is_locked"]:
                    orig_idx = next(
                        (i for i, p in enumerate(self._stack_cell_plans)
                         if p["plan_id"] == self._drag_plan_id), 0)
                    others = [p for p in self._stack_cell_plans
                              if p["plan_id"] != self._drag_plan_id]
                    insert_at = self._stack_guide_idx
                    # Adjust for removed element shift
                    if insert_at > orig_idx:
                        insert_at -= 1
                    insert_at = max(0, min(len(others), insert_at))
                    others.insert(insert_at, drag_plan)
                    if [p["plan_id"] for p in others] != \
                       [p["plan_id"] for p in self._stack_cell_plans]:
                        PlanRepo.update_stack_orders(
                            [(p["plan_id"], i) for i, p in enumerate(others)])
                        if self.parent_tab:
                            self.parent_tab.refresh()
                        else:
                            self.load_data()
                elif drag_plan and drag_plan["is_locked"]:
                    QMessageBox.information(self, "Locked",
                                            "This plan is locked. Unlock it first.")
                self._reset_drag_state()
                self.update()
                return

            if self._drag_rect:
                if self._drag_invalid:
                    QMessageBox.warning(
                        self, "Invalid Move",
                        f"This room does not support the plan's process.\n"
                        f"Cannot move here.")
                else:
                    center = self._drag_rect.center()
                    col    = center.x() // self._col_w()
                    # Use actual cursor position for row (room) determination,
                    # not drag-rect center — avoids room-drift when clicked
                    # near top/bottom edge of a plan card.
                    cursor_y = int(event.pos().y())
                    if 0 <= col < self._col_count():
                        new_date, new_shift = self._col_to_date_shift(col)
                        plan = next((p for p in self._display_plans
                                     if p["plan_id"] == self._drag_plan_id), None)
                        if plan and not plan["is_locked"]:
                            new_room = plan["room_code"]
                            if "Room" in self.y_dims:
                                row = self._row_at_y(cursor_y)
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
                                is_merged = len(plan.get("_merged_ids", [])) > 1
                                n_label   = (f"{plan['_merged_count']} plans"
                                             if is_merged else
                                             f"plan #{self._drag_plan_id}")
                                reason, ok = QInputDialog.getText(
                                    self, "Move Reason",
                                    f"Reason for moving {n_label}?")
                                if ok:
                                    member_ids = (plan["_merged_ids"]
                                                  if is_merged
                                                  else [self._drag_plan_id])
                                    fields: Dict = {
                                        "plan_date": new_date.strftime("%Y-%m-%d"),
                                        "shift_no":  new_shift,
                                    }
                                    if room_changed:
                                        fields["room_code"] = new_room
                                    if self.parent_tab:
                                        if is_merged:
                                            self.parent_tab.push_undo({
                                                "type":    "move_merged",
                                                "label":   f"Move {len(member_ids)} plans",
                                                "members": [
                                                    {"plan_id":   mid,
                                                     "plan_date": plan["plan_date"],
                                                     "shift_no":  plan["shift_no"],
                                                     "room_code": plan["room_code"]}
                                                    for mid in member_ids
                                                ],
                                            })
                                        else:
                                            self.parent_tab.push_undo({
                                                "type":     "move",
                                                "label":    f"Move Plan #{member_ids[0]}",
                                                "plan_id":  member_ids[0],
                                                "plan_date": plan["plan_date"],
                                                "shift_no":  plan["shift_no"],
                                                "room_code": plan["room_code"],
                                            })
                                    for mid in member_ids:
                                        real = next(
                                            (rp for rp in self._plans
                                             if rp["plan_id"] == mid), None)
                                        if real and real["is_locked"]:
                                            continue
                                        PlanRepo.update(mid, fields, reason=reason)
                                    self.planMoved.emit(
                                        self._drag_plan_id,
                                        plan["plan_date"],
                                        plan["shift_no"],
                                        reason)
                        elif plan and plan["is_locked"]:
                            QMessageBox.information(
                                self, "Locked",
                                "This plan is locked. Unlock it first.")
            else:
                # Click without drag on any plan card → open detail panel
                if self._drag_plan_id:
                    plan = self._pid_to_plan.get(self._drag_plan_id)
                    if plan:
                        self.summaryCardClicked.emit(plan)
            self._reset_drag_state()
            self.update()

    # ── Panel → Canvas drag-and-drop ─────────────────────────────────────────

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(_PANEL_DRAG_MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasFormat(_PANEL_DRAG_MIME):
            e.ignore()
            return
        e.acceptProposedAction()
        pos = e.position().toPoint()
        col = pos.x() // self._col_w()
        ri  = self._row_at_y(pos.y())
        cw  = self._col_w()
        if 0 <= col < self._col_count() and 0 <= ri < len(self._row_y_list):
            self._panel_drop_rect = QRect(
                col * cw, self._row_y_list[ri], cw, self._row_heights[ri])
        else:
            self._panel_drop_rect = None
        self.update()

    def dragLeaveEvent(self, e):
        self._panel_drop_rect = None
        self.update()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(_PANEL_DRAG_MIME):
            e.ignore()
            return
        self._panel_drop_rect = None
        self.update()
        try:
            plan_id = int(bytes(e.mimeData().data(_PANEL_DRAG_MIME)).decode())
        except (ValueError, Exception):
            e.ignore()
            return
        plan = next((p for p in self._plans if p["plan_id"] == plan_id), None)
        if not plan:
            e.ignore()
            return
        if plan.get("is_locked"):
            QMessageBox.information(self, "Locked", "This plan is locked. Unlock it first.")
            e.ignore()
            return
        pos = e.position().toPoint()
        col = pos.x() // self._col_w()
        ri  = self._row_at_y(pos.y())
        if not (0 <= col < self._col_count() and 0 <= ri < len(self._rows)):
            e.ignore()
            return
        new_date, new_shift = self._col_to_date_shift(col)
        new_room = plan["room_code"]
        if "Room" in self.y_dims and 0 <= ri < len(self._rows):
            parts = self._rows[ri].split("|")
            room_idx = self.y_dims.index("Room")
            if room_idx < len(parts):
                new_room = parts[room_idx]
        date_changed = (new_date.strftime("%Y-%m-%d") != plan["plan_date"]
                        or new_shift != plan["shift_no"])
        room_changed = (new_room != plan["room_code"])
        if not date_changed and not room_changed:
            e.acceptProposedAction()
            return
        if room_changed and (new_room, plan["process_name"]) not in self._room_proc_set:
            QMessageBox.warning(self, "Invalid Move",
                                f"Room {new_room} does not support {plan['process_name']}.")
            e.ignore()
            return
        reason, ok = QInputDialog.getText(
            self, "Move Reason",
            f"Reason for moving {plan.get('so_number', f'plan #{plan_id}')}?")
        if not ok:
            e.ignore()
            return
        fields: Dict = {"plan_date": new_date.strftime("%Y-%m-%d"), "shift_no": new_shift}
        if room_changed:
            fields["room_code"] = new_room
        if self.parent_tab:
            self.parent_tab.push_undo({
                "type":     "move",
                "label":    f"Move Plan #{plan_id} (panel drag)",
                "plan_id":  plan_id,
                "plan_date": plan["plan_date"],
                "shift_no":  plan["shift_no"],
                "room_code": plan["room_code"],
            })
        PlanRepo.update(plan_id, fields, reason=reason)
        self.planMoved.emit(plan_id, plan["plan_date"], plan["shift_no"], reason)
        e.acceptProposedAction()

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

        # Verify the plan still exists in the DB before making changes
        current = PlanRepo.get(plan["plan_id"])
        if current is None:
            QMessageBox.warning(self, "Split Failed",
                                f"Plan #{plan['plan_id']} no longer exists in the database.\n"
                                "The view may be stale — please refresh.")
            if self.parent_tab:
                self.parent_tab.refresh()
            return

        # Build new plan dict (same slot, same attributes — except for the split fields)
        new_plan = {k: plan[k] for k in plan if k not in
                    ("plan_id", "qty_planned", "qty_produced",
                     "plan_date", "shift_no", "room_code",
                     "created_at", "updated_at",
                     "is_locked", "is_consolidated", "consolidation_group",
                     "memo")}
        new_plan["qty_planned"]       = split_qty
        new_plan["qty_produced"]      = 0
        new_plan["plan_date"]         = new_date.strftime("%Y-%m-%d")
        new_plan["shift_no"]          = new_shift
        new_plan["room_code"]         = new_room
        new_plan["is_locked"]         = 0
        new_plan["is_consolidated"]   = 0
        new_plan["consolidation_group"] = None
        new_plan["memo"]              = f"Split from plan #{plan['plan_id']}"

        try:
            # Reduce original qty (raises ValueError if 0 rows affected)
            PlanRepo.update(plan["plan_id"],
                            {"qty_planned": orig_qty - split_qty},
                            reason=f"split {split_qty} off to {new_date} S{new_shift}")
            # Create new plan at the drop location
            PlanRepo.insert(new_plan)
        except Exception as e:
            QMessageBox.critical(self, "Split Error",
                                 f"Failed to save split:\n{e}\n\n"
                                 "No changes were saved.")
            if self.parent_tab:
                self.parent_tab.refresh()
            return

        if self.parent_tab:
            self.parent_tab.refresh()

    def _toggle_check(self, plan_id: int):
        if plan_id in self._checked:
            self._checked.discard(plan_id)
        else:
            self._checked.add(plan_id)
        self.selectionChanged.emit(list(self._checked))
        self._pixmap_dirty = True
        self.update()

    def _plan_at(self, pos: QPoint) -> Optional[Dict]:
        # Fast path: spatial index → only plans in the cell under cursor.
        col_w = self._col_w()
        if col_w > 0 and self._spatial_index:
            col = pos.x() // col_w
            ri  = self._row_at_y(pos.y())
            if ri >= 0:
                for pid in self._spatial_index.get((col, ri), []):
                    rect = self._prebuilt_rects.get(pid) or self._cell_map.get(pid)
                    if rect and rect.contains(pos):
                        return self._pid_to_plan.get(pid)
        # Fallback: linear scan (before first load_data or after live plan move)
        for plan in self._display_plans:
            rect = self._cell_map.get(plan["plan_id"])
            if rect and rect.contains(pos):
                return plan
        return None

    # ── Context menu ──────────────────────────────────────────────────────────

    def _context_menu(self, pos: QPoint):
        plan = self._plan_at(pos)
        menu = QMenu(self)
        if plan:
            pid       = plan["plan_id"]
            locked    = plan["is_locked"]
            grp       = plan.get("consolidation_group")
            is_merged = len(plan.get("_merged_ids", [])) > 1
            member_ids = plan.get("_merged_ids", [pid])

            lock_lbl = "🔓 Unlock" if locked else "🔒 Lock"
            if is_merged:
                lock_lbl += f" ({plan['_merged_count']} plans)"
            menu.addAction(lock_lbl,
                           lambda: self._toggle_lock_merged(member_ids, locked))
            if not is_merged:
                menu.addAction("✂ Split",     lambda: self._split_plan(plan))
                menu.addAction("⬅ Pull Out", lambda: self._pull_out(plan))
                if grp:
                    menu.addAction(f"🔗 Break Consolidation ({grp})",
                                   lambda: self._break_consol(grp))
                menu.addSeparator()
                menu.addAction("📝 Edit Memo",  lambda: self._edit_memo(plan))
                menu.addAction("🗑 Delete Plan", lambda: self._delete_plan(pid))
            else:
                menu.addSeparator()
                so_list = plan.get("_so_list", [])
                menu.addAction(
                    f"📋 {plan['_merged_count']} plans: "
                    + ", ".join(so_list[:4])
                    + (" …" if len(so_list) > 4 else "")).setEnabled(False)
        else:
            col = pos.x() // self._col_w()
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
            was_locked = bool(plan["is_locked"])
            PlanRepo.lock(plan_id, not was_locked)
            if self.parent_tab:
                action_label = "Unlock" if was_locked else "Lock"
                self.parent_tab.push_undo({
                    "type":       "lock",
                    "label":      f"{action_label} Plan #{plan_id}",
                    "plan_id":    plan_id,
                    "was_locked": was_locked,
                })
                self.parent_tab.refresh()

    def _toggle_lock_merged(self, member_ids: list, currently_locked: bool):
        """Lock or unlock all member plan_ids at once."""
        for mid in member_ids:
            PlanRepo.lock(mid, not currently_locked)
        if self.parent_tab:
            self.parent_tab.refresh()

    def _split_plan(self, plan):
        qty = plan["qty_planned"]
        if qty < 2:
            QMessageBox.information(self, "Split", "Qty too small."); return
        split_qty, ok = QInputDialog.getInt(
            self, "Split", f"First half qty (total {qty}):", qty//2, 1, qty-1)
        if not ok: return

        current = PlanRepo.get(plan["plan_id"])
        if current is None:
            QMessageBox.warning(self, "Split Failed",
                                f"Plan #{plan['plan_id']} no longer exists in the database.\n"
                                "Please refresh the view.")
            if self.parent_tab: self.parent_tab.refresh()
            return

        new_plan = {**plan, "qty_planned": qty - split_qty,
                    "is_locked": 0, "memo": "split-remainder",
                    "is_consolidated": 0, "consolidation_group": None}
        for k in ("plan_id", "created_at", "updated_at"): new_plan.pop(k, None)
        try:
            PlanRepo.update(plan["plan_id"], {"qty_planned": split_qty}, reason="split")
            PlanRepo.insert(new_plan)
        except Exception as e:
            QMessageBox.critical(self, "Split Error", f"Failed to save split:\n{e}")
            if self.parent_tab: self.parent_tab.refresh()
            return
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
            old_memo = plan.get("memo") or ""
            PlanRepo.update(plan["plan_id"], {"memo": text})
            if self.parent_tab:
                self.parent_tab.push_undo({
                    "type":     "memo",
                    "label":    f"Memo on Plan #{plan['plan_id']}",
                    "plan_id":  plan["plan_id"],
                    "old_memo": old_memo,
                })
                self.parent_tab.refresh()

    def _delete_plan(self, plan_id):
        reason, ok = QInputDialog.getText(self, "Delete Plan", "Reason:")
        if ok:
            plan_data = PlanRepo.get(plan_id)
            group_id = plan_data.get("consolidation_group") if plan_data else None
            PlanRepo.delete(plan_id, reason=reason)
            # If the deleted plan was in a consolidation group, check remaining members.
            # A single-member group is an orphan — auto-break to clear the gold border & lock.
            if group_id:
                remaining = [p for p in PlanRepo.all()
                             if p.get("consolidation_group") == group_id]
                if len(remaining) <= 1:
                    ConsolidationEngine.break_group(group_id)
            if plan_data and self.parent_tab:
                self.parent_tab.push_undo({
                    "type":      "delete",
                    "label":     f"Delete Plan #{plan_id} ({plan_data.get('sku_code','')})",
                    "plan_data": plan_data,
                })
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


# ─── Summary Detail Panel ────────────────────────────────────────────────────


class SoPlanRow(QWidget):
    """One draggable row in SummaryDetailPanel."""

    def __init__(self, plan: Dict, so_rec, prod_deadline=None, parent=None):
        super().__init__(parent)
        self._plan = plan
        self._drag_start = None
        self.setFixedHeight(44)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(6)

        handle = QLabel("⠿")
        handle.setFixedWidth(16)
        handle.setStyleSheet("color:#cbd5e1; font-size:14px;")
        lay.addWidget(handle)

        info_col = QVBoxLayout()
        info_col.setSpacing(1)
        so_num = plan.get("so_number", "—")
        status = (so_rec or {}).get("status", "")
        dot_color = "#16a34a" if status == "OPEN" else "#d97706"
        lbl_so = QLabel(f"● {so_num}")
        lbl_so.setStyleSheet(
            f"QLabel {{ font-size:10px; font-weight:700; color:{dot_color}; }}")
        cust = (so_rec or {}).get("customer_name", "") or ""
        lbl_cust = QLabel(cust if cust else "—")
        lbl_cust.setStyleSheet("font-size:8px; color:#94a3b8;")
        info_col.addWidget(lbl_so)
        info_col.addWidget(lbl_cust)
        lay.addLayout(info_col, stretch=1)

        lbl_qty = QLabel(str(plan.get("qty_planned", 0)))
        lbl_qty.setFixedWidth(52)
        lbl_qty.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl_qty.setStyleSheet("font-size:10px; font-weight:600; color:#334155;")
        lay.addWidget(lbl_qty)

        # Production deadline chip (due_date - post_lead_days)
        if prod_deadline:
            days_to = (prod_deadline - date.today()).days
            dl_str = f"{prod_deadline.month}/{prod_deadline.day}"
            if days_to < 0:
                dl_bg, dl_fg = "#fee2e2", "#dc2626"
            elif days_to <= 7:
                dl_bg, dl_fg = "#fef3c7", "#d97706"
            else:
                dl_bg, dl_fg = "#dbeafe", "#2563eb"
            lbl_dl = QLabel(dl_str)
            lbl_dl.setFixedSize(34, 18)
            lbl_dl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_dl.setStyleSheet(
                f"QLabel {{ font-size:9px; font-weight:700; color:{dl_fg};"
                f"background:{dl_bg}; border-radius:3px; padding:0px; }}")
            lbl_dl.setToolTip(f"Production Deadline: {prod_deadline}  ({days_to:+d} days)")
            lay.addWidget(lbl_dl)
        else:
            lbl_line = QLabel(f"L{plan.get('line_item', '?')}")
            lbl_line.setFixedWidth(40)
            lbl_line.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_line.setStyleSheet("font-size:10px; color:#64748b;")
            lay.addWidget(lbl_line)

        badge_w = QWidget()
        badge_w.setFixedWidth(40)
        b_lay = QHBoxLayout(badge_w)
        b_lay.setContentsMargins(0, 0, 0, 0)
        b_lay.setSpacing(2)
        if plan.get("is_locked"):
            bl = QLabel("🔒")
            bl.setFixedSize(18, 18)
            bl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bl.setStyleSheet("background:#f1f5f9; border-radius:3px; font-size:9px;")
            bl.setToolTip("Locked")
            b_lay.addWidget(bl)
        if plan.get("is_final_seq"):
            bf = QLabel("★")
            bf.setFixedSize(18, 18)
            bf.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bf.setStyleSheet("background:#fed7aa; color:#c2410c; border-radius:3px; font-size:9px; font-weight:700;")
            bf.setToolTip("Final process")
            b_lay.addWidget(bf)
        b_lay.addStretch()
        lay.addWidget(badge_w)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.MouseButton.LeftButton) or self._drag_start is None:
            super().mouseMoveEvent(e)
            return
        if (e.pos() - self._drag_start).manhattanLength() < 8:
            super().mouseMoveEvent(e)
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_PANEL_DRAG_MIME, str(self._plan["plan_id"]).encode())
        drag.setMimeData(mime)
        pm = QPixmap(200, 28)
        pm.fill(QColor(239, 246, 255, 220))
        pp = QPainter(pm)
        pp.setRenderHint(QPainter.RenderHint.Antialiasing)
        pp.setPen(QPen(QColor(37, 99, 235), 1.5))
        pp.setBrush(QBrush(QColor(239, 246, 255, 200)))
        pp.drawRoundedRect(pm.rect().adjusted(1, 1, -1, -1), 5, 5)
        pp.setPen(QPen(QColor(37, 99, 235)))
        pp.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        pp.drawText(pm.rect().adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter,
                    f"⠿  {self._plan.get('so_number', '')}  ·  {self._plan.get('qty_planned', 0)}")
        pp.end()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(100, 14))
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start = None

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)


class _DraggableHeader(QWidget):
    """Header strip that lets the user drag the floating panel around."""

    def __init__(self, panel: "FloatingSummaryPanel", parent=None):
        super().__init__(parent)
        self._panel = panel
        self._drag_start: Optional[QPoint] = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint() - self._panel.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self._drag_start is not None:
            self._panel.move(e.globalPosition().toPoint() - self._drag_start)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)


class FloatingSummaryPanel(QWidget):
    """OS-level floating card showing individual plans merged in a summary card."""

    closed = pyqtSignal()

    _PANEL_W = 360
    _PANEL_H = 480

    def __init__(self, canvas: "GanttCanvas", parent=None):
        super().__init__(None)  # top-level — no Qt parent so it floats freely
        self._canvas = canvas
        self._current_plan: Optional[Dict] = None
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self._PANEL_W + 28, self._PANEL_H + 28)  # extra for shadow
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)  # extra room for shadow artifact
        outer.setSpacing(0)

        self._card = QWidget()
        self._card.setObjectName("floatCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card.setStyleSheet(
            "QWidget#floatCard { background:#ffffff; border-radius:10px; }")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 45))
        self._card.setGraphicsEffect(shadow)

        outer.addWidget(self._card)

        lay = QVBoxLayout(self._card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Draggable header
        self._hdr = _DraggableHeader(self)
        self._hdr.setFixedHeight(76)
        self._hdr.setStyleSheet(
            "background:transparent; border-bottom:1px solid #DDE3ED;")

        h_lay = QHBoxLayout(self._hdr)
        h_lay.setContentsMargins(14, 8, 8, 8)
        h_lay.setSpacing(4)

        title_col = QVBoxLayout()
        title_col.setSpacing(3)
        title_col.setContentsMargins(0, 0, 0, 0)

        self._lbl_room = QLabel("")
        self._lbl_room.setStyleSheet(
            "QLabel { color:#64748b; font-size:9px; font-weight:600;"
            " letter-spacing:0.4px; background:transparent; border:none; }")

        self._lbl_sku = QLabel("")
        self._lbl_sku.setStyleSheet(
            "QLabel { color:#1e293b; font-size:15px; font-weight:700;"
            " background:transparent; border:none; }")

        self._lbl_meta = QLabel("")
        self._lbl_meta.setStyleSheet(
            "QLabel { color:#64748b; font-size:9px; font-weight:500;"
            " background:transparent; border:none; }")

        title_col.addWidget(self._lbl_room)
        title_col.addWidget(self._lbl_sku)
        title_col.addWidget(self._lbl_meta)
        h_lay.addLayout(title_col, stretch=1)

        btn_close = QToolButton()
        btn_close.setText("✕")
        btn_close.setFixedSize(26, 26)
        btn_close.setStyleSheet(
            "QToolButton { background:#f1f5f9; border:none; color:#334155;"
            " border-radius:6px; font-size:13px; font-weight:700; padding:0; }"
            "QToolButton:hover { background:#FEE2E2; color:#DC2626; border:none; }"
            "QToolButton:pressed { background:#FECACA; color:#DC2626; border:none; }")
        btn_close.clicked.connect(self.close_panel)
        h_lay.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignVCenter)

        lay.addWidget(self._hdr)

        # Drag hint bar
        hint = QLabel("  ⠿  Drag a row to the Gantt to move that plan")
        hint.setFixedHeight(24)
        hint.setStyleSheet(
            "QLabel { background:#f8fafc; color:#94a3b8; font-size:9px; padding:5px 0;"
            "border-bottom:1px solid #DDE3ED; }")
        lay.addWidget(hint)

        # Column header
        col_h = QWidget()
        col_h.setObjectName("floatColHeader")
        col_h.setFixedHeight(22)
        col_h.setStyleSheet(
            "QWidget#floatColHeader { background:#f1f5f9; border-bottom:1px solid #DDE3ED; }")
        ch_lay = QHBoxLayout(col_h)
        ch_lay.setContentsMargins(14, 0, 14, 0)
        ch_lay.setSpacing(6)
        ch_lay.addSpacing(22)
        for txt, w_fixed in [("SO / Customer", 0), ("Qty", 52), ("Deadline", 40), ("", 40)]:
            lbl = QLabel(txt)
            lbl.setStyleSheet("QLabel { font-size:8px; font-weight:700; color:#94a3b8; }")
            if w_fixed:
                lbl.setFixedWidth(w_fixed)
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                ch_lay.addWidget(lbl)
            else:
                ch_lay.addWidget(lbl, stretch=1)
        lay.addWidget(col_h)

        # Scroll list — NoFrame + NoFocus to kill blue focus border
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll_area.setStyleSheet(
            "QScrollArea { border:none; outline:none; background:transparent; }"
            "QScrollArea > QWidget { background:transparent; }"
            "QScrollArea > QWidget > QWidget { background:#ffffff; }")
        self._list_w = QWidget()
        self._list_w.setObjectName("floatListContent")
        self._list_w.setStyleSheet("QWidget#floatListContent { background:#ffffff; }")
        self._list_lay = QVBoxLayout(self._list_w)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(0)
        self._list_lay.addStretch()
        self._scroll_area.setWidget(self._list_w)
        lay.addWidget(self._scroll_area, stretch=1)

        # Footer
        self._footer = QWidget()
        self._footer.setFixedHeight(46)
        self._footer.setStyleSheet(
            "background:#ffffff; border-top:1px solid #DDE3ED;"
            "border-radius: 0 0 10px 10px;")
        f_lay = QHBoxLayout(self._footer)
        f_lay.setContentsMargins(14, 8, 14, 8)
        f_lay.setSpacing(8)

        self._lbl_footer = QLabel("")
        self._lbl_footer.setStyleSheet("font-size:9px; color:#64748b;")
        f_lay.addWidget(self._lbl_footer, stretch=1)

        btn_unlock = QPushButton("🔓 Unlock All")
        btn_unlock.setStyleSheet(
            "QPushButton{font-size:10px;font-weight:700;padding:4px 8px;"
            "border:1px solid #d4d7e0;border-radius:5px;background:#fff;color:#3a4255;}"
            "QPushButton:hover{background:#f5f6fa;}")
        btn_unlock.clicked.connect(self._unlock_all)
        btn_del = QPushButton("🗑 Delete")
        btn_del.setStyleSheet(
            "QPushButton{font-size:10px;font-weight:700;padding:4px 8px;"
            "border:1px solid #fca5a5;border-radius:5px;background:#fff;color:#dc2626;}"
            "QPushButton:hover{background:#fef2f2;}")
        btn_del.clicked.connect(self._delete_all)
        f_lay.addWidget(btn_unlock)
        f_lay.addWidget(btn_del)
        lay.addWidget(self._footer)

    # ── Public API ────────────────────────────────────────────────────────────

    def show_for(self, merged_plan: Dict, anchor: Optional[QPoint] = None):
        self._current_plan = merged_plan
        self._lbl_room.setText(
            f"{merged_plan.get('room_code', '')}  ·  {merged_plan.get('process_name', '')}".upper())
        sku_code = merged_plan.get("sku_code", "")
        sku_rec = SKURepo.get(sku_code)
        sku_name = (sku_rec or {}).get("sku_name", "") or ""
        if sku_name:
            self._lbl_sku.setText(
                f'<span style="font-size:15px;font-weight:700;color:#1e293b;">{sku_code}</span>'
                f'&nbsp;&nbsp;<span style="font-size:10px;font-weight:400;color:#64748b;">{sku_name}</span>')
        else:
            self._lbl_sku.setText(sku_code)
        n = merged_plan.get("_merged_count", 1)
        qty = merged_plan.get("qty_planned", 0)
        plan_lbl = "plan" if n == 1 else "plans"
        self._lbl_meta.setText(
            f"{merged_plan.get('plan_date', '—')}  ·  Shift {merged_plan.get('shift_no', '—')}  ·  {qty:,} units")
        self._lbl_footer.setText(f"Total {qty:,} units  ·  {n} {plan_lbl}")

        # Clear existing rows (keep trailing stretch)
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        member_ids = set(merged_plan.get("_merged_ids", [merged_plan["plan_id"]]))
        members = sorted(
            [p for p in self._canvas._plans if p["plan_id"] in member_ids],
            key=lambda p: (p.get("so_number", ""), p.get("line_item", 0)))

        for i, plan in enumerate(members):
            so_key = (plan.get("so_number", ""), plan.get("sku_code", ""), plan.get("line_item", 0))
            so_rec = self._canvas._sos.get(so_key)
            _prod_dl = None
            _due_str = (so_rec or {}).get("due_date")
            if _due_str:
                _lead = int((self._canvas._skus.get(plan.get("sku_code", "")) or {}).get("post_lead_days") or 0)
                from utils.workdays import sub_workdays as _swdays
                try:
                    _prod_dl = _swdays(datetime.strptime(_due_str, "%Y-%m-%d").date(), _lead)
                except ValueError:
                    pass
            row = SoPlanRow(plan, so_rec, prod_deadline=_prod_dl, parent=self._list_w)
            if i % 2 == 1:
                row.setStyleSheet("SoPlanRow{background:#f9fafb;}")
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("background:#f1f5f9; border:none; max-height:1px;")
            self._list_lay.insertWidget(self._list_lay.count() - 1, row)
            self._list_lay.insertWidget(self._list_lay.count() - 1, sep)

        self._position_near(anchor or QCursor.pos())
        self.show()
        self.raise_()

    def close_panel(self):
        self.hide()
        self._current_plan = None
        self.closed.emit()

    # ── Smart positioning ─────────────────────────────────────────────────────

    def _position_near(self, anchor: Optional[QPoint] = None):
        # Always appear on the right side of the main window (side-panel feel)
        main_win = self._canvas.window()
        geo = main_win.frameGeometry()
        pw, ph = self.width(), self.height()
        x = geo.right() - pw - 4   # 4px gap from right edge
        y = geo.top() + 80         # below toolbar area
        screen = (QApplication.screenAt(geo.center()) or QApplication.primaryScreen())
        avail = screen.availableGeometry()
        x = max(avail.left(), min(x, avail.right() - pw))
        y = max(avail.top(), min(y, avail.bottom() - ph))
        self.move(x, y)

    # ── Outside-click close via app-level event filter ─────────────────────────

    def showEvent(self, e):
        super().showEvent(e)
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, e):
        super().hideEvent(e)
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if not self.geometry().contains(QCursor.pos()):
                self.close_panel()
        return False

    # ── Actions ───────────────────────────────────────────────────────────────

    def _find_gantt_tab(self):
        for w in QApplication.topLevelWidgets():
            if isinstance(w, GanttTab):
                return w
            # check children recursively
            result = w.findChild(GanttTab)
            if result:
                return result
        return None

    def _unlock_all(self):
        if not self._current_plan:
            return
        for mid in self._current_plan.get("_merged_ids", []):
            plan = next((p for p in self._canvas._plans if p["plan_id"] == mid), None)
            if plan and plan.get("is_locked"):
                PlanRepo.lock(mid, False)
        gt = self._find_gantt_tab()
        if gt:
            gt.refresh()

    def _delete_all(self):
        if not self._current_plan:
            return
        member_ids = self._current_plan.get("_merged_ids", [])
        if QMessageBox.question(
            self, "Delete Plans",
            f"Delete {len(member_ids)} plan(s) in this card?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        for mid in member_ids:
            PlanRepo.delete(mid, reason="bulk-delete from summary panel")
        self.close_panel()
        gt = self._find_gantt_tab()
        if gt:
            gt.refresh()


# ─── Gantt Tab ────────────────────────────────────────────────────────────────

class GanttTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._undo_stack: List[Dict] = []
        self._btn_undo: Optional[QPushButton] = None
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

        # Frozen Y-axis sidebar (stays fixed during horizontal scroll)
        self.gantt_y_label = GanttYLabelWidget(self)
        body.addWidget(self.gantt_y_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self.canvas = GanttCanvas(self)
        self.canvas.parent_tab = self
        self.canvas.planMoved.connect(self._on_plan_moved)
        self.canvas.planSelected.connect(self._on_plan_selected)
        self.canvas.selectionChanged.connect(self._on_selection_changed)
        self.canvas.summaryCardClicked.connect(self._on_summary_card_clicked)
        self.canvas.layoutChanged.connect(self.gantt_y_label.update)
        self.scroll.setWidget(self.canvas)
        self._on_dim_changed()

        self.scroll.horizontalScrollBar().valueChanged.connect(
            self.gantt_header.set_scroll_h)
        self.scroll.verticalScrollBar().valueChanged.connect(
            self.gantt_y_label.set_scroll_v)

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

        # Floating summary panel — OS-level tool window, no parent layout slot
        self.summary_panel = FloatingSummaryPanel(self.canvas)

        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._undo_last)
        QShortcut(QKeySequence("Escape"), self).activated.connect(
            lambda: self.summary_panel.close_panel() if self.summary_panel.isVisible() else None)

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
        self._pill_ok   = self._make_kpi_pill("#e6f4ea", "#1d8a4a", "#2bab5e")
        self._pill_risk = self._make_kpi_pill("#fef3e0", "#b9760a", "#e09a1f")
        self._pill_late = self._make_kpi_pill("#fbe7e7", "#c2342f", "#e0413a")
        self._pill_ok.setToolTip("Filter: On Time only (click to toggle)")
        self._pill_risk.setToolTip("Filter: At Risk only (click to toggle)")
        self._pill_late.setToolTip("Filter: Late only (click to toggle)")
        self._pill_ok.clicked.connect(lambda: self._on_pill_clicked("on_time"))
        self._pill_risk.clicked.connect(lambda: self._on_pill_clicked("at_risk"))
        self._pill_late.clicked.connect(lambda: self._on_pill_clicked("late"))
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

        # Snapshot: manual save + restore
        _SNAP_CSS = (
            "QPushButton { border:1px solid #e2e4ea; border-radius:5px; background:#fff;"
            " font-size:11px; font-weight:600; color:#3a4255; padding:0 8px; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        _style = QApplication.instance().style()
        btn_snap = QPushButton("  Snapshot")
        btn_snap.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        btn_snap.setIconSize(QSize(14, 14))
        btn_snap.setFixedHeight(32)
        btn_snap.setToolTip("Save plan snapshot")
        btn_snap.setStyleSheet(_SNAP_CSS)
        btn_snap.clicked.connect(self._save_snapshot_manual)
        lay.addWidget(btn_snap)

        btn_restore = QPushButton("  Time Machine")
        btn_restore.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        btn_restore.setIconSize(QSize(14, 14))
        btn_restore.setFixedHeight(32)
        btn_restore.setToolTip("Browse and restore plan snapshots")
        btn_restore.setStyleSheet(_SNAP_CSS)
        btn_restore.clicked.connect(self._restore_snapshot_dialog)
        lay.addWidget(btn_restore)

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

        _st = QApplication.instance().style()
        _icon_css = (
            "QPushButton { border:1px solid #e2e4ea; border-radius:5px; background:#fff; }"
            "QPushButton:hover:enabled { background:#f5f6fa; }"
            "QPushButton:disabled { opacity:0.4; }"
        )

        # Icon: Undo
        self._btn_undo = QPushButton()
        self._btn_undo.setIcon(_st.standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self._btn_undo.setIconSize(QSize(16, 16))
        self._btn_undo.setFixedSize(32, 32)
        self._btn_undo.setToolTip("Undo last action (Ctrl+Z)")
        self._btn_undo.setEnabled(False)
        self._btn_undo.setStyleSheet(_icon_css)
        self._btn_undo.clicked.connect(self._undo_last)
        lay.addWidget(self._btn_undo)

        # Icon: Refresh CRP
        btn_crp = QPushButton()
        btn_crp.setIcon(_st.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        btn_crp.setIconSize(QSize(16, 16))
        btn_crp.setFixedSize(32, 32)
        btn_crp.setToolTip("Refresh CRP")
        btn_crp.setStyleSheet(_icon_css)
        btn_crp.clicked.connect(self._on_crp_refresh)
        lay.addWidget(btn_crp)

        # Icon: New Window
        btn_win = QPushButton()
        btn_win.setIcon(_st.standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton))
        btn_win.setIconSize(QSize(16, 16))
        btn_win.setFixedSize(32, 32)
        btn_win.setToolTip("New Window (Ctrl+N)")
        btn_win.setStyleSheet(_icon_css)
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
    def _make_kpi_pill(bg: str, fg: str, dot_clr: str):
        """Returns a checkable QPushButton pill for status filtering."""
        from PyQt6.QtGui import QColor

        def _darken(hex_color: str, factor: float) -> str:
            c = QColor(hex_color)
            h, s, v, a = c.getHsvF()
            c.setHsvF(h, min(s * (1 + factor * 0.3), 1.0), max(v - factor * 0.15, 0.0), a)
            return c.name()

        bg_hover   = _darken(bg, 0.3)
        bg_checked = _darken(bg, 0.55)

        btn = QPushButton(f"● —")
        btn.setCheckable(True)
        btn.setFixedHeight(24)
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background:{bg}; color:{fg}; border-radius:12px; border:none;"
            f"  padding:0 12px; font-size:11px; font-weight:600;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background:{bg_hover};"
            f"}}"
            f"QPushButton:checked {{"
            f"  background:{bg_checked}; font-weight:700;"
            f"}}"
        )
        return btn

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
            self._pill_ok.setText(f"● On time  {on_time}")
            self._pill_risk.setText(f"● At risk  {at_risk}")
            self._pill_late.setText(f"● Late  {late}")
        except Exception:
            pass

    def _on_search_changed(self, text: str):
        if hasattr(self, "canvas"):
            self.canvas.set_search_filter(text.strip().lower())

    def _on_pill_clicked(self, status: str):
        """Toggle status filter. Clicking the active filter again clears it."""
        pills = {"on_time": self._pill_ok, "at_risk": self._pill_risk, "late": self._pill_late}
        active = status if pills[status].isChecked() else ""
        # Uncheck all others
        for key, pill in pills.items():
            pill.setChecked(key == active)
        if hasattr(self, "canvas"):
            self.canvas.set_status_filter(active)

    def _on_crp_refresh(self):
        if self.main_window and hasattr(self.main_window, "_refresh_crp"):
            self.main_window._refresh_crp()

    def _on_defrag(self):
        self._on_weekly_reorganize()

    def _on_weekly_reorganize(self):
        d0 = self.canvas.start_date.strftime("%Y-%m-%d")
        d1 = (self.canvas.start_date + timedelta(
            days=self.canvas.horizon_days - 1)).strftime("%Y-%m-%d")
        from PyQt6.QtWidgets import QApplication as _App
        _App.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = scheduler.weekly_reorganize(d0, d1)
        finally:
            _App.restoreOverrideCursor()
        moved   = result["moved"]
        frozen  = result.get("frozen", 0)
        skipped = result.get("skipped_groups", 0)
        msg = f"Weekly reorganize complete.\n{moved} plan(s) regrouped by SKU within each week."
        if frozen:
            msg += f"\n{frozen} plan(s) frozen in place (deadline or gap constraint)."
        if skipped:
            msg += f"\n{skipped} group(s) skipped (would exceed due date)."
        QMessageBox.information(self, "Weekly Reorganize", msg)
        if moved and self.main_window:
            self.main_window.notify(f"Reorganize: {moved} plans resequenced")
        self.refresh()

    def _on_new_window(self):
        if self.main_window and hasattr(self.main_window, "_detach_current_tab"):
            self.main_window._detach_current_tab()

    # ─── View Bar (Y-axis controls / horizon / export) ────────────────────────

    def _build_viewbar(self) -> QWidget:
        outer = QWidget()
        vl = QVBoxLayout(outer)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        _ROW_CSS = "QWidget { background:#fafbfc; }"
        _PRESET_CSS = (
            "QPushButton { font-size:10px; font-weight:600; padding:3px 9px;"
            " border-radius:4px; border:1px solid #d4d7e0; background:#fff; color:#3a4255; }"
            "QPushButton:checked { background:#dde9ff; border-color:#4f8df0; color:#2451c2; }"
            "QPushButton:hover:!checked { background:#f5f6fa; }"
        )
        _DIM_CSS = (
            "QComboBox { background:#fff; border:1px solid #d4d7e0; border-radius:4px;"
            " padding:3px 5px; font-size:11px; color:#3a4255; }"
            "QComboBox:focus { border-color:#4f8df0; }"
        )

        # ── Row 1: Y-axis presets + dim combos ───────────────────────────────
        row1 = QWidget(); row1.setStyleSheet(_ROW_CSS)
        row1.setFixedHeight(38)
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(14, 0, 12, 0)
        r1.setSpacing(6)

        lbl_y = QLabel("Y-axis")
        lbl_y.setStyleSheet("font-size:11px; color:#6b7280; font-weight:600;")
        r1.addWidget(lbl_y)

        self._preset_btns: List[QPushButton] = []
        for lbl, dims in [("Room", ["Room"]), ("Room›Proc", ["Room", "Process"]), ("SKU", ["SKU"])]:
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setStyleSheet(_PRESET_CSS)
            btn.clicked.connect(lambda _c, d=dims: self._apply_preset(d))
            self._preset_btns.append(btn)
            r1.addWidget(btn)

        r1.addWidget(self._vb_sep())

        self._dim_combos: List[QComboBox] = []
        for i, default_dim in enumerate(["Room", "Process", "—", "—"]):
            if i > 0:
                arr = QLabel("▸"); arr.setStyleSheet("color:#b0b8cc; font-size:10px;")
                r1.addWidget(arr)
            cb = QComboBox()
            cb.addItems(Y_DIM_OPTIONS)
            cb.setCurrentText(default_dim)
            cb.setStyleSheet(_DIM_CSS)
            cb.setFixedWidth(82)
            cb.currentTextChanged.connect(self._on_dim_changed)
            cb.setToolTip(f"Y-axis depth {i+1}")
            self._dim_combos.append(cb)
            r1.addWidget(cb)

        r1.addStretch()

        # Summary toggle
        r1.addWidget(self._vb_sep())
        self._btn_sum = QPushButton("⊞  Summary")
        self._btn_sum.setCheckable(True)
        self._btn_sum.setFixedHeight(26)
        self._btn_sum.setToolTip(
            "Collapse cards: same SKU + Room + Process + Date into one summarized card.\n"
            "Qty is summed. Drag moves all constituent plans together.")
        _sum_css_off = (
            "QPushButton { background:#fff; color:#3a4255; border:1px solid #d4d7e0;"
            " border-radius:4px; padding:3px 9px; font-size:10px; font-weight:600; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        _sum_css_on = (
            "QPushButton { background:#1d4ed8; color:#fff; border:none;"
            " border-radius:4px; padding:3px 9px; font-size:10px; font-weight:600; }"
            "QPushButton:hover { background:#1e40af; }"
        )
        self._btn_sum.setStyleSheet(_sum_css_on)   # default ON
        def _on_sum_toggle(checked):
            self._btn_sum.setStyleSheet(_sum_css_on if checked else _sum_css_off)
            if hasattr(self, 'canvas'):
                self.canvas.toggle_summarize(checked)
                self.gantt_header.sync_from(self.canvas)
                self.gantt_y_label.sync_from(self.canvas)
        self._btn_sum.toggled.connect(_on_sum_toggle)
        self._btn_sum.setChecked(True)             # default ON
        r1.addWidget(self._btn_sum)

        # Final-only filter toggle
        _fo_css_off = (
            "QPushButton { background:#fff; color:#3a4255; border:1px solid #d4d7e0;"
            " border-radius:4px; padding:3px 9px; font-size:10px; font-weight:600; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        _fo_css_on = (
            "QPushButton { background:#d97706; color:#fff; border:none;"
            " border-radius:4px; padding:3px 9px; font-size:10px; font-weight:600; }"
            "QPushButton:hover { background:#b45309; }"
        )
        self._btn_final_only = QPushButton("★  Final only")
        self._btn_final_only.setCheckable(True)
        self._btn_final_only.setFixedHeight(26)
        self._btn_final_only.setToolTip(
            "Highlight only final-process plans (is_final_seq = 1).\n"
            "All other plans are dimmed.")
        self._btn_final_only.setStyleSheet(_fo_css_off)
        def _on_fo_toggle(checked):
            self._btn_final_only.setStyleSheet(_fo_css_on if checked else _fo_css_off)
            self.canvas.toggle_final_only(checked)
        self._btn_final_only.toggled.connect(_on_fo_toggle)
        r1.addWidget(self._btn_final_only)

        # CLOSE badge toggle
        _cb_css_off = (
            "QPushButton { background:#fff; color:#3a4255; border:1px solid #d4d7e0;"
            " border-radius:4px; padding:3px 9px; font-size:10px; font-weight:600; }"
            "QPushButton:hover { background:#f5f6fa; }"
        )
        _cb_css_on = (
            "QPushButton { background:#b45309; color:#fff; border:none;"
            " border-radius:4px; padding:3px 9px; font-size:10px; font-weight:600; }"
            "QPushButton:hover { background:#92400e; }"
        )
        self._btn_close_badge = QPushButton("⬤  CLOSE badge")
        self._btn_close_badge.setCheckable(True)
        self._btn_close_badge.setFixedHeight(26)
        self._btn_close_badge.setToolTip(
            "Show/hide the CLOSE badge on closing-shift plan cards.\n"
            "Off by default — enable to highlight campaign closing shifts.")
        self._btn_close_badge.setStyleSheet(_cb_css_off)
        def _on_cb_toggle(checked):
            self._btn_close_badge.setStyleSheet(_cb_css_on if checked else _cb_css_off)
            self.canvas.toggle_close_badge(checked)
        self._btn_close_badge.toggled.connect(_on_cb_toggle)
        r1.addWidget(self._btn_close_badge)

        # Card label toggles: SO# and Customer
        _lbl_css_on  = ("QPushButton { background:#475569; color:#fff; border:none;"
                        " border-radius:4px; padding:3px 8px; font-size:10px; font-weight:600; }"
                        "QPushButton:hover { background:#334155; }")
        _lbl_css_off = ("QPushButton { background:#fff; color:#3a4255; border:1px solid #d4d7e0;"
                        " border-radius:4px; padding:3px 8px; font-size:10px; font-weight:600; }"
                        "QPushButton:hover { background:#f5f6fa; }")

        self._btn_show_so = QPushButton("SO #")
        self._btn_show_so.setCheckable(True)
        self._btn_show_so.setChecked(True)
        self._btn_show_so.setFixedHeight(26)
        self._btn_show_so.setToolTip("Show/hide SO number line in Gantt cards.\nOff = shorter cards.")
        self._btn_show_so.setStyleSheet(_lbl_css_on)
        def _on_so_toggle(checked):
            self._btn_show_so.setStyleSheet(_lbl_css_on if checked else _lbl_css_off)
            self.canvas.toggle_so_num(checked)
        self._btn_show_so.toggled.connect(_on_so_toggle)
        r1.addWidget(self._btn_show_so)

        self._btn_show_cust = QPushButton("Customer")
        self._btn_show_cust.setCheckable(True)
        self._btn_show_cust.setChecked(True)
        self._btn_show_cust.setFixedHeight(26)
        self._btn_show_cust.setToolTip("Show/hide customer name line in Gantt cards.\nOff = shorter cards.")
        self._btn_show_cust.setStyleSheet(_lbl_css_on)
        def _on_cust_toggle(checked):
            self._btn_show_cust.setStyleSheet(_lbl_css_on if checked else _lbl_css_off)
            self.canvas.toggle_customer(checked)
        self._btn_show_cust.toggled.connect(_on_cust_toggle)
        r1.addWidget(self._btn_show_cust)

        # Weekly reorganize button
        btn_reorg = QPushButton("🔀 Reorganize")
        btn_reorg.setFixedHeight(26)
        btn_reorg.setToolTip(
            "Within each ISO week, group same-SKU plans consecutively per room+process.\n"
            "Reduces changeovers without moving plans across week boundaries.")
        btn_reorg.setStyleSheet(
            "QPushButton { background:#fff; color:#1d4ed8; border:1px solid #93c5fd;"
            " border-radius:4px; padding:3px 9px; font-size:10px; font-weight:600; }"
            "QPushButton:hover { background:#eff6ff; }")
        btn_reorg.clicked.connect(self._on_weekly_reorganize)
        r1.addWidget(btn_reorg)

        vl.addWidget(row1)

        # row separator
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFixedHeight(1); sep1.setStyleSheet("background:#e8eaf0; border:none;")
        vl.addWidget(sep1)

        # ── Row 2: Shift · Horizon · Date · stretch · Actions ────────────────
        row2 = QWidget(); row2.setStyleSheet(_ROW_CSS)
        row2.setFixedHeight(40)
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(14, 0, 12, 0)
        r2.setSpacing(8)

        # Shift toggle
        self.shift_toggle = QPushButton("Shift")
        self.shift_toggle.setCheckable(True)
        self.shift_toggle.setFixedHeight(26)
        self.shift_toggle.setStyleSheet(
            "QPushButton { font-size:10px; font-weight:600; padding:3px 10px;"
            " border-radius:12px; border:1px solid #d4d7e0; color:#6b7280; background:#fff; }"
            "QPushButton:checked { background:#2f5fd6; color:#fff; border-color:#2f5fd6; }"
        )
        self.shift_toggle.toggled.connect(self._on_shift_toggle)
        r2.addWidget(self.shift_toggle)

        r2.addWidget(self._vb_sep())

        # Horizon segmented selector
        hz_outer = QFrame()
        hz_outer.setStyleSheet("QFrame { background:#eef0f4; border-radius:6px; border:none; }")
        hz_outer.setFixedHeight(30)
        hz_lay = QHBoxLayout(hz_outer)
        hz_lay.setContentsMargins(3, 3, 3, 3)
        hz_lay.setSpacing(1)
        _HZ_BTN_CSS = (
            "QPushButton { font-size:10px; font-weight:600; padding:2px 10px;"
            " min-width:30px; border-radius:4px; border:none;"
            " color:#6b7280; background:transparent; }"
            "QPushButton:checked { background:#fff; color:#16213d;"
            " border:1px solid #cbd5e1; }"
        )
        self._horizon_btns: Dict[str, QPushButton] = {}
        hz_grp = QButtonGroup(self)
        hz_grp.setExclusive(True)
        for lbl in ["1M", "3M", "6M"]:
            hb = QPushButton(lbl)
            hb.setCheckable(True)
            hb.setChecked(lbl == "3M")
            hb.setStyleSheet(_HZ_BTN_CSS)
            hb.clicked.connect(lambda _c, t=lbl: self._on_horizon_changed(t))
            hz_lay.addWidget(hb)
            hz_grp.addButton(hb)
            self._horizon_btns[lbl] = hb
        r2.addWidget(hz_outer)

        # Date picker
        from PyQt6.QtCore import QDate
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setFixedHeight(26)
        self.date_edit.setFixedWidth(100)
        self.date_edit.setStyleSheet(
            "QDateEdit { border:1px solid #d4d7e0; border-radius:4px;"
            " padding:2px 6px; font-size:11px; color:#3a4255; }")
        self.date_edit.dateChanged.connect(lambda: self.refresh())
        r2.addWidget(self.date_edit)

        r2.addStretch()

        # Unplanned
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
        r2.addWidget(self.btn_unplanned)

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
        r2.addWidget(btn_export)

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
        r2.addWidget(self.btn_consol)

        # Clear checks
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
        r2.addWidget(self.btn_clear)

        self.check_label = QLabel("")
        self.check_label.setStyleSheet("color:#555; font-size:10px;")
        r2.addWidget(self.check_label)

        vl.addWidget(row2)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1); sep2.setStyleSheet("background:#e2e4ea; border:none;")
        vl.addWidget(sep2)
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
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setStyleSheet(
            "QFrame { background:#F4F6FB; border-left:1px solid #DDE3ED; }"
        )
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        # Header
        hdr = QFrame()
        hdr.setStyleSheet(
            "QFrame { background:#fff; border:none; border-bottom:1px solid #DDE3ED; }"
        )
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(14, 10, 10, 10)
        hdr_lay.setSpacing(6)

        title = QLabel("📦 Unplanned Orders")
        title.setStyleSheet("font-size:13px; font-weight:700; color:#1E293B; border:none;")
        hdr_lay.addWidget(title)

        self.unplanned_count_label = QLabel("0")
        self.unplanned_count_label.setStyleSheet(
            "font-size:11px; font-weight:600; color:#475569;"
            " background:#F1F5F9; border-radius:8px; padding:1px 8px; border:none;"
        )
        hdr_lay.addWidget(self.unplanned_count_label)
        hdr_lay.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(24, 24)
        btn_close.setToolTip("Close")
        btn_close.setStyleSheet(
            "QPushButton { background:transparent; border:none; color:#94A3B8; font-size:13px; }"
            "QPushButton:hover { background:#F1F5F9; border-radius:4px; }"
        )
        btn_close.clicked.connect(self._close_unplanned_panel)
        hdr_lay.addWidget(btn_close)
        pl.addWidget(hdr)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent; border:none;")

        self._unplanned_inner = QWidget()
        self._unplanned_inner.setStyleSheet("background:transparent;")
        self.unplanned_card_layout = QVBoxLayout(self._unplanned_inner)
        self.unplanned_card_layout.setContentsMargins(8, 10, 8, 10)
        self.unplanned_card_layout.setSpacing(8)
        self.unplanned_card_layout.addStretch()
        scroll.setWidget(self._unplanned_inner)
        pl.addWidget(scroll, stretch=1)

        # Footer
        self.unplanned_footer_label = QLabel("0 order(s) · 0 step(s)")
        self.unplanned_footer_label.setStyleSheet(
            "font-size:11px; color:#94A3B8; padding:6px 14px;"
            " border-top:1px solid #EEF1F7; background:#fff;"
        )
        pl.addWidget(self.unplanned_footer_label)

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
        self.unplanned_count_label.setText(str(n_orders))
        self.unplanned_footer_label.setText(
            f"{n_orders} order(s) · {n_steps} step(s)")

    def _make_unplanned_card(self, rep: dict, steps: list, today_str: str) -> QFrame:
        so_number = rep["so_number"]
        sku_code  = rep["sku_code"]
        line_item = rep["line_item"]
        customer  = rep.get("customer_name") or ""
        due_date  = rep.get("due_date") or ""
        priority  = rep.get("priority")
        is_late   = bool(due_date and due_date < today_str)
        n_steps   = len(steps)

        # Determine card state and color tokens
        if is_late:
            strip_color  = "#DC2626"
            border_color = "#FCA5A5"
            btn_bg       = "#DC2626"
            btn_hover    = "#B91C1C"
            dot_color    = "#DC2626"
            name_color   = "#DC2626"
        elif n_steps > 1:
            strip_color  = "#D97706"
            border_color = "#FDE68A"
            btn_bg       = "#2563EB"
            btn_hover    = "#1D4ED8"
            dot_color    = "#D97706"
            name_color   = "#D97706"
        else:
            strip_color  = "#16A34A"
            border_color = "#DDE3ED"
            btn_bg       = "#2563EB"
            btn_hover    = "#1D4ED8"
            dot_color    = "#D97706"
            name_color   = "#D97706"

        card = QFrame()
        card.setFrameShape(QFrame.Shape.NoFrame)
        card.setStyleSheet(
            f"QFrame#unplanned_card {{"
            f" background:#fff;"
            f" border:1px solid {border_color};"
            f" border-radius:8px;"
            f"}}"
        )
        card.setObjectName("unplanned_card")

        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Zone A — Status strip (separate widget to avoid border-top + border-radius QSS conflict)
        strip = QFrame()
        strip.setFixedHeight(4)
        strip.setStyleSheet(
            f"QFrame {{ background:{strip_color}; border:none;"
            f" border-top-left-radius:7px; border-top-right-radius:7px; }}"
        )
        outer.addWidget(strip)

        # ── Body ─────────────────────────────────────────────
        body_widget = QWidget()
        body_widget.setStyleSheet("background:transparent;")
        body = QVBoxLayout(body_widget)
        body.setContentsMargins(12, 9, 12, 6)
        body.setSpacing(4)

        # Zone B — Header row: SO number + SKU/Line
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        lbl_so = QLabel(so_number)
        lbl_so.setStyleSheet(
            "font-size:13px; font-weight:700; color:#1E293B; background:transparent;"
        )
        hdr.addWidget(lbl_so, stretch=1)
        lbl_sku = QLabel(f"{sku_code} · {line_item}")
        lbl_sku.setStyleSheet("font-size:11px; color:#94A3B8; background:transparent;")
        lbl_sku.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hdr.addWidget(lbl_sku)
        body.addLayout(hdr)

        # Badge row (conditional)
        if is_late and due_date:
            try:
                from datetime import datetime as _dt
                overdue_days = (date.today() - _dt.strptime(due_date, "%Y-%m-%d").date()).days
                badge_text = f"⚠ OVERDUE · D+{overdue_days}"
            except Exception:
                badge_text = "⚠ OVERDUE"
            badge = QLabel(badge_text)
            badge.setStyleSheet(
                "font-size:10px; font-weight:700; color:#DC2626;"
                " background:#FEF2F2; border:1px solid #FECACA;"
                " border-radius:3px; padding:2px 7px;"
            )
            badge.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
            body.addWidget(badge)
        elif n_steps > 1:
            badge = QLabel(f"{n_steps} steps unplanned")
            badge.setStyleSheet(
                "font-size:10px; font-weight:700; color:#D97706;"
                " background:#FFFBEB; border:1px solid #FDE68A;"
                " border-radius:3px; padding:2px 7px;"
            )
            badge.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
            body.addWidget(badge)

        # Zone C — Meta row: customer + due date
        meta = QHBoxLayout()
        meta.setSpacing(4)
        cust_text = customer if customer else "—"
        lbl_cust = QLabel(cust_text)
        lbl_cust.setStyleSheet("font-size:11px; color:#64748B; background:transparent;")
        meta.addWidget(lbl_cust, stretch=1)

        if due_date:
            if is_late:
                due_color  = "#DC2626"
                due_weight = "font-weight:600;"
            else:
                try:
                    from datetime import datetime as _dtt
                    days_left = (_dtt.strptime(due_date, "%Y-%m-%d").date() - date.today()).days
                    due_color = "#16A34A" if days_left <= 14 else "#64748B"
                except Exception:
                    due_color = "#64748B"
                due_weight = ""
            due_suffix = " ⚠" if is_late else ""
            lbl_due = QLabel(f"Due {due_date}{due_suffix}")
            lbl_due.setStyleSheet(
                f"font-size:11px; color:{due_color}; {due_weight} background:transparent;"
            )
        else:
            lbl_due = QLabel("Due —")
            lbl_due.setStyleSheet("font-size:11px; color:#94A3B8; background:transparent;")
        lbl_due.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        meta.addWidget(lbl_due)
        body.addLayout(meta)

        # Zone D — Step rows
        for i, s in enumerate(steps):
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setFixedHeight(1)
                sep.setStyleSheet("background:#EEF1F7; border:none;")
                body.addWidget(sep)

            step_row = QHBoxLayout()
            step_row.setSpacing(5)
            step_row.setContentsMargins(0, 0, 0, 0)

            dot = QLabel("●")
            dot.setFixedWidth(10)
            dot.setStyleSheet(f"font-size:7px; color:{dot_color}; background:transparent;")
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step_row.addWidget(dot)

            seq  = s.get("process_seq")
            pname = s.get("process_name") or "(no routing)"
            tag  = f"[{seq}] {pname}" if seq is not None else pname
            lbl_step = QLabel(tag)
            lbl_step.setStyleSheet(
                f"font-size:10px; color:{name_color}; background:transparent;"
            )
            step_row.addWidget(lbl_step, stretch=1)

            rem = s.get("remaining_qty", 0)
            pri_str = f"Pri {priority} · " if (priority and i == 0) else ""
            lbl_rem = QLabel(f"{pri_str}Rem: {rem}")
            lbl_rem.setStyleSheet("font-size:10px; color:#94A3B8; background:transparent;")
            lbl_rem.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            step_row.addWidget(lbl_rem)

            step_container = QWidget()
            step_container.setStyleSheet("background:transparent;")
            step_container.setMinimumHeight(20)
            step_container.setLayout(step_row)
            body.addWidget(step_container)

        outer.addWidget(body_widget)

        # ── Zone E — Action button ────────────────────────────
        action_widget = QWidget()
        action_widget.setStyleSheet("background:transparent;")
        action_lay = QVBoxLayout(action_widget)
        action_lay.setContentsMargins(12, 0, 12, 10)
        action_lay.setSpacing(0)

        btn = QPushButton("▶ Plan Now")
        btn.setFixedHeight(28)
        btn.setStyleSheet(
            f"QPushButton {{ background:{btn_bg}; color:white; border:none;"
            f" border-radius:5px; font-size:11px; font-weight:700; }}"
            f"QPushButton:hover {{ background:{btn_hover}; }}"
            f"QPushButton:pressed {{ background:{btn_hover}; }}"
        )
        btn.clicked.connect(lambda _c, rd=dict(rep): self._force_plan_row(rd))
        action_lay.addWidget(btn)

        outer.addWidget(action_widget)

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
        d0 = self.date_edit.date().toPyDate()
        days = getattr(self.canvas, "horizon_days", 28)
        d1 = d0 + timedelta(days=days)
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

        # Sync frozen header and Y-axis sidebar with canvas data
        self.gantt_header.sync_from(self.canvas)
        self.gantt_y_label.sync_from(self.canvas)

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

    def _save_snapshot_manual(self):
        from data.repositories import PlanSnapshotRepo
        from datetime import datetime as _dt
        label, ok = QInputDialog.getText(
            self, "Save Snapshot", "Snapshot label:",
            text=f"Manual {_dt.now().strftime('%Y-%m-%d %H:%M')}")
        if not ok or not label.strip():
            return
        PlanSnapshotRepo.save(label.strip())
        if self.main_window:
            self.main_window.notify(f"Plan snapshot saved: {label.strip()}")

    def _restore_snapshot_dialog(self):
        dlg = PlanTimeMachineDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            if self.main_window:
                lbl = getattr(dlg, "_restored_label", "snapshot")
                self.main_window.notify(f"Plan restored: \"{lbl}\"")


    def run_auto_plan(self):
        # Prevent concurrent planning runs (e.g. double-click or detached window trigger).
        # Guard with try/except: after worker.deleteLater() the C++ object is gone but
        # self._plan_worker still holds a Python wrapper; isRunning() raises RuntimeError.
        try:
            if getattr(self, '_plan_worker', None) and self._plan_worker.isRunning():
                return
        except RuntimeError:
            self._plan_worker = None

        d0, d1 = self._date_range()
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QCursor

        # Disable button and show busy cursor while planning
        sender = self.sender()
        if sender:
            sender.setEnabled(False)
            sender.setText("⏳  Planning…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        class _Worker(QThread):
            done = pyqtSignal(dict)
            error = pyqtSignal(str)
            def __init__(self, d0, d1):
                super().__init__()
                self._d0, self._d1 = d0, d1
            def run(self):
                try:
                    from data.repositories import PlanSnapshotRepo
                    from datetime import datetime as _dt
                    PlanSnapshotRepo.save(
                        f"Auto: Execute Plan {_dt.now().strftime('%Y-%m-%d %H:%M')}")
                    scheduler._reload_masters()
                    self.done.emit(scheduler.auto_plan(self._d0, self._d1))
                except Exception as exc:
                    self.error.emit(str(exc))

        worker = _Worker(d0, d1)

        def _on_done(report):
            self._plan_worker = None  # clear before deleteLater fires
            QApplication.restoreOverrideCursor()
            if sender:
                sender.setEnabled(True)
                sender.setText("▶  Execute Plan")
            self.refresh()
            msg = (f"Auto-plan: {report['planned']} slots, "
                   f"{len(report['late'])} late, "
                   f"{len(report.get('routing_errors', []))} routing errors.")
            if self.main_window:
                self.main_window.notify(msg)
                self.main_window._check_conflicts_silent()

        def _on_error(msg):
            self._plan_worker = None  # clear before deleteLater fires
            QApplication.restoreOverrideCursor()
            if sender:
                sender.setEnabled(True)
                sender.setText("▶  Execute Plan")
            QMessageBox.warning(self, "Auto Plan Error", msg)

        worker.done.connect(_on_done)
        worker.error.connect(_on_error)
        worker.finished.connect(worker.deleteLater)
        # Keep reference so GC doesn't collect it
        self._plan_worker = worker
        worker.start()

    def run_pull_forward(self):
        dlg = PullForwardDialog(self)
        dlg.exec()
        self.refresh()

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

    # ─── Undo stack ───────────────────────────────────────────────────────────

    def push_undo(self, action: Dict):
        self._undo_stack.append(action)
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        if self._btn_undo:
            self._btn_undo.setEnabled(True)
            n = len(self._undo_stack)
            self._btn_undo.setToolTip(f"Undo: {action.get('label','last action')}  ({n} in stack)  Ctrl+Z")

    def _undo_last(self):
        if not self._undo_stack:
            return
        action = self._undo_stack.pop()
        try:
            if action["type"] == "move":
                PlanRepo.update(action["plan_id"], {
                    "plan_date": action["plan_date"],
                    "shift_no":  action["shift_no"],
                    "room_code": action["room_code"],
                }, reason="undo-move")
            elif action["type"] == "move_merged":
                for m in action["members"]:
                    PlanRepo.update(m["plan_id"], {
                        "plan_date": m["plan_date"],
                        "shift_no":  m["shift_no"],
                        "room_code": m["room_code"],
                    }, reason="undo-move")
            elif action["type"] == "delete":
                pd = {k: v for k, v in action["plan_data"].items()
                      if k not in ("plan_id", "created_at", "updated_at")}
                PlanRepo.insert(pd)
            elif action["type"] == "lock":
                PlanRepo.lock(action["plan_id"], action["was_locked"])
            elif action["type"] == "memo":
                PlanRepo.update(action["plan_id"], {"memo": action["old_memo"]},
                                reason="undo-memo")
        except Exception as e:
            QMessageBox.warning(self, "Undo Failed", str(e))
        self.refresh()
        if self._btn_undo:
            has = bool(self._undo_stack)
            self._btn_undo.setEnabled(has)
            if has:
                top = self._undo_stack[-1]
                self._btn_undo.setToolTip(
                    f"Undo: {top.get('label','last action')}  "
                    f"({len(self._undo_stack)} in stack)  Ctrl+Z")
            else:
                self._btn_undo.setToolTip("Undo last action (Ctrl+Z)")
        if self.main_window:
            self.main_window.notify(f"Undo: {action.get('label', action['type'])}")

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

    def _on_summary_card_clicked(self, plan: Dict):
        """Show the floating summary card for the clicked plan."""
        if self.unplanned_panel.isVisible():
            self.btn_unplanned.setChecked(False)
        self.summary_panel.show_for(plan, anchor=QCursor.pos())

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
        mapping = {"1M": 30, "3M": 90, "6M": 180}
        self.canvas.horizon_days = mapping.get(text, 90)
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
            QMessageBox.warning(self, "Add Plan", "Please select an SO and a process.")
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Pull Forward Dialog (per-line)
# ═══════════════════════════════════════════════════════════════════════════════

class PullForwardDialog(QDialog):
    """
    Lists all planned OPEN SOs. Per row:
      [Calc Earliest] → finds earliest possible completion date (background thread)
      Target Date    → editable date field
      [Apply]        → mode selection → (if push) impact report → confirm → apply
    """

    # Column indices
    C_SO   = 0; C_SKU  = 1; C_LINE = 2; C_CUST = 3
    C_RDUE = 4; C_CDUE = 5; C_COMP = 6; C_EARL = 7
    C_TGT  = 8; C_CALC = 9; C_APPLY = 10

    HEADERS = ["SO", "SKU", "Line", "Customer",
               "Requested Due", "Committed Due", "Current Completion",
               "Earliest Possible", "Target Date", "", ""]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pull Forward — Per-Line Scheduling")
        # Size to 90 % of the available screen width so all columns fit
        screen_w = QApplication.primaryScreen().availableGeometry().width()
        self.resize(int(screen_w * 0.90), 620)
        self._workers: list = []
        self._build_ui()
        self._load()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        # info bar
        info = QLabel("Select a row, set a Target Date, then click  or Pull Forward.")
        info.setStyleSheet("color:#64748b; font-size:11px; padding:4px;")
        lay.addWidget(info)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setMinimumSectionSize(40)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(self.C_CUST,  QHeaderView.ResizeMode.Stretch)
        # Widget columns: Fixed with generous widths that survive DPI scaling
        for col, w in [(self.C_LINE, 55), (self.C_TGT, 135),
                       (self.C_CALC, 52), (self.C_APPLY, 140)]:
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, w)
        lay.addWidget(self.table)

        btns = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._load)
        btns.addWidget(btn_refresh)
        btns.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

    def _load(self):
        from datetime import date as _date_cls
        today = _date_cls.today().strftime("%Y-%m-%d")
        sos = SORepo.all("OPEN")
        rows = []
        for so in sos:
            plans = PlanRepo.for_so(so["so_number"], so["sku_code"], so["line_item"])
            if not plans:
                continue
            final_plans = [p for p in plans if p.get("is_final_seq")]
            cur_comp = (max(p["plan_date"] for p in final_plans)
                        if final_plans else "-")
            rows.append((so, cur_comp))

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        for ri, (so, cur_comp) in enumerate(rows):
            so_no  = so["so_number"]
            sku    = so["sku_code"]
            li     = so["line_item"]
            rdue   = so.get("due_date", "")
            cdue   = so.get("committed_due_date") or ""

            def _ro(txt, row=ri):
                it = QTableWidgetItem(str(txt))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self.table.setItem(ri, self.C_SO,   _ro(so_no))
            self.table.setItem(ri, self.C_SKU,  _ro(sku))
            self.table.setItem(ri, self.C_LINE, _ro(li))
            self.table.setItem(ri, self.C_CUST, _ro(so.get("customer_name") or ""))
            self.table.setItem(ri, self.C_RDUE, _ro(rdue))
            self.table.setItem(ri, self.C_CDUE, _ro(cdue))
            self.table.setItem(ri, self.C_COMP, _ro(cur_comp))
            self.table.setItem(ri, self.C_EARL, _ro("—"))

            # Target date widget — placed directly (no container) to avoid popup clipping
            from PyQt6.QtCore import QDate
            tgt_edit = QDateEdit()
            tgt_edit.setDisplayFormat("yyyy-MM-dd")
            tgt_edit.setCalendarPopup(True)
            # Force the calendar popup to be wide enough; default inherits the widget's
            # narrow column width and clips day columns 4-7 (shows only up to ~9th)
            tgt_edit.calendarWidget().setMinimumWidth(265)
            tgt_ref = cdue if cdue else rdue
            tgt_edit.setDate(QDate.fromString(tgt_ref, "yyyy-MM-dd")
                             if tgt_ref else QDate.currentDate())
            self.table.setCellWidget(ri, self.C_TGT, tgt_edit)

            # Calc button — calculator icon
            btn_calc = QPushButton()
            btn_calc.setIcon(_svg_icon(_IC_CALC, "#3730a3", 18))
            btn_calc.setIconSize(QSize(18, 18))
            btn_calc.setToolTip("Calc Earliest completion date")
            btn_calc.setStyleSheet(
                "QPushButton { background:#e0e7ff; border:1px solid #a5b4fc;"
                " border-radius:4px; }"
                "QPushButton:disabled { background:#f1f5f9; border-color:#cbd5e1; }")
            btn_calc.clicked.connect(lambda _, r=ri, s=so_no, k=sku, l=li:
                                     self._calc_earliest(r, s, k, l))
            self.table.setCellWidget(ri, self.C_CALC, _cell_center(btn_calc))

            # Pull Forward button
            btn_apply = QPushButton("Pull Forward")
            btn_apply.setToolTip("Apply pull-forward to this SO")
            btn_apply.setStyleSheet(
                "QPushButton { background:#2563eb; color:white; border:none;"
                " border-radius:4px; padding:3px 6px;"
                " font-size:9pt; font-weight:600; }"
                "QPushButton:hover { background:#1d4ed8; }"
                "QPushButton:pressed { background:#1e40af; }"
                "QPushButton:disabled { background:#93c5fd; color:#eff6ff; border:none; }")
            btn_apply.clicked.connect(lambda _, r=ri, s=so_no, k=sku, l=li:
                                      self._apply_row(r, s, k, l))
            self.table.setCellWidget(ri, self.C_APPLY, _cell_center(btn_apply))

            # Colour current completion vs committed/requested due
            check = cdue if cdue else rdue
            if cur_comp and cur_comp != "-" and check and cur_comp > check:
                for c in range(self.C_SO, self.C_EARL + 1):
                    it = self.table.item(ri, c)
                    if it:
                        it.setBackground(QBrush(QColor("#ffebee")))

        self.table.setUpdatesEnabled(True)
        self.table.resizeColumnsToContents()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(self.C_CUST, QHeaderView.ResizeMode.Stretch)
        for col, w in [(self.C_LINE, 55), (self.C_TGT, 135),
                       (self.C_CALC, 52), (self.C_APPLY, 140)]:
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, w)

    def _inner_btn(self, row: int, col: int):
        """Return the inner widget from a _cell_center container."""
        container = self.table.cellWidget(row, col)
        if not container:
            return None
        lay = container.layout()
        return lay.itemAt(0).widget() if lay and lay.count() > 0 else None

    def _calc_earliest(self, row: int, so_no: str, sku: str, li: str):
        btn = self._inner_btn(row, self.C_CALC)
        if btn:
            btn.setEnabled(False)
            btn.setIcon(QIcon())   # clear icon while calculating
            btn.setText("…")

        class _Worker(QThread):
            done  = pyqtSignal(str)
            error = pyqtSignal(str)
            def __init__(self, s, k, l):
                super().__init__()
                self._s, self._k, self._l = s, k, l
            def run(self):
                try:
                    result = scheduler._find_earliest_completion(self._s, self._k, self._l)
                    self.done.emit(result or "N/A")
                except Exception as e:
                    self.error.emit(str(e))

        w = _Worker(so_no, sku, li)

        def _on_done(dt, r=row):
            it = self.table.item(r, self.C_EARL)
            if it:
                it.setText(dt)
            b = self._inner_btn(r, self.C_CALC)
            if b:
                b.setEnabled(True)
                b.setText("")
                b.setIcon(_svg_icon(_IC_CALC, "#3730a3", 18))
                b.setIconSize(QSize(18, 18))

        def _on_err(msg, r=row):
            it = self.table.item(r, self.C_EARL)
            if it:
                it.setText("Error")
            b = self._inner_btn(r, self.C_CALC)
            if b:
                b.setEnabled(True)
                b.setText("")
                b.setIcon(_svg_icon(_IC_CALC, "#3730a3", 18))
                b.setIconSize(QSize(18, 18))
            QMessageBox.warning(self, "Calc Error", msg)

        w.done.connect(_on_done)
        w.error.connect(_on_err)
        w.finished.connect(w.deleteLater)
        self._workers.append(w)
        w.start()

    def _apply_row(self, row: int, so_no: str, sku: str, li: str):
        tgt_widget = self.table.cellWidget(row, self.C_TGT)  # QDateEdit placed directly
        if not tgt_widget:
            return
        target_date = tgt_widget.date().toString("yyyy-MM-dd")

        # Step 1: choose mode
        mode_dlg = _PullModeDialog(so_no, sku, li, target_date, self)
        if mode_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        allow_push = mode_dlg.allow_push

        # Step 2: simulate
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            sim = scheduler.simulate_single_pull_forward(
                so_no, sku, li, target_date, allow_push)
        finally:
            QApplication.restoreOverrideCursor()

        if not sim["feasible"]:
            QMessageBox.warning(self, "Not Feasible",
                sim.get("error") or "Cannot schedule to target date.")
            return

        # Step 3: if push mode and displaced SOs exist, show impact report
        if allow_push and sim["displaced"]:
            impact_dlg = _PullImpactDialog(
                so_no, sku, li, target_date,
                sim["final_date"], sim["displaced"], self)
            if impact_dlg.exec() != QDialog.DialogCode.Accepted:
                return

        # Step 4: apply (snapshot first so the user can restore if needed)
        from data.repositories import PlanSnapshotRepo
        from datetime import datetime as _dt
        PlanSnapshotRepo.save(
            f"Auto: Pull Forward {so_no}/{sku} → {target_date} "
            f"{_dt.now().strftime('%H:%M')}")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = scheduler.apply_single_pull_forward(
                so_no, sku, li, target_date, allow_push, sim["displaced"])
        finally:
            QApplication.restoreOverrideCursor()

        if result["success"]:
            replanned = result.get("replanned", 0)
            msg = (f"Pull forward applied.\n"
                   f"Plans created: {result['planned']}\n"
                   f"SOs displaced: {result['displaced_count']}")
            if replanned:
                msg += f"\nDisplaced SOs auto-replanned: {replanned}"
            QMessageBox.information(self, "Applied", msg)
            self._load()
        else:
            QMessageBox.warning(self, "Error", result.get("error", "Unknown error"))


class _PullModeDialog(QDialog):
    """Mode selection: available capacity only vs allow pushing other SOs."""

    def __init__(self, so_no, sku, li, target_date, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pull Forward — Select Mode")
        self.allow_push = False
        from PyQt6.QtWidgets import QRadioButton, QButtonGroup
        lay = QVBoxLayout(self)

        hdr = QLabel(f"<b>{so_no} / {sku} / {li}</b>  →  target: <b>{target_date}</b>")
        hdr.setStyleSheet("font-size:13px; padding:6px 0;")
        lay.addWidget(hdr)

        lay.addWidget(QLabel("How should available capacity be determined?"))

        self._rg = QButtonGroup(self)
        self._r_avail = QRadioButton(
            "Fit into available capacity only\n"
            "(does not move any other plan)")
        self._r_push  = QRadioButton(
            "Allow displacing other unlocked plans\n"
            "(shows impact report before applying)")
        self._r_avail.setChecked(True)
        self._rg.addButton(self._r_avail, 0)
        self._rg.addButton(self._r_push,  1)

        for rb in (self._r_avail, self._r_push):
            rb.setStyleSheet("padding:6px; font-size:12px;")
            lay.addWidget(rb)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _ok(self):
        self.allow_push = self._r_push.isChecked()
        self.accept()


class _PullImpactDialog(QDialog):
    """Shows displaced SOs, their verified new completion dates, and due-date status."""

    def __init__(self, so_no, sku, li, target_date,
                 final_date, displaced, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pull Forward — Impact Report")
        self.resize(980, 440)
        lay = QVBoxLayout(self)

        hdr = QLabel(
            f"Pulling <b>{so_no}/{sku}/{li}</b> to <b>{target_date}</b> "
            f"(estimated completion: <b>{final_date or '?'}</b>)<br>"
            f"The following SOs will be displaced and <b>automatically re-planned</b> "
            f"in the remaining capacity. All new completions have been verified against due dates.")
        hdr.setWordWrap(True)
        hdr.setStyleSheet("padding:6px; font-size:12px;")
        lay.addWidget(hdr)

        tbl = QTableWidget(len(displaced), 9)
        tbl.setHorizontalHeaderLabels([
            "SO", "SKU", "Line", "Customer",
            "Requested Due", "Committed Due",
            "Current Completion", "New Completion", "Status After"])
        tbl.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        tbl.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        STATUS_COLORS = {
            "ON TIME":       QColor("#e8f5e9"),
            "LATE":          QColor("#ffebee"),
            "CANNOT REPLAN": QColor("#ffebee"),
        }
        STATUS_FG = {
            "ON TIME":       QColor("#2e7d32"),
            "LATE":          QColor("#c62828"),
            "CANNOT REPLAN": QColor("#c62828"),
        }

        for ri, d in enumerate(displaced):
            after = d["status_after"]
            vals = [
                d["so_number"], d["sku_code"], d["line_item"],
                d["customer_name"], d["due_date"], d["committed_due_date"],
                d["current_completion"], d["new_completion"], after,
            ]
            for ci, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if ci == 8:
                    it.setBackground(QBrush(STATUS_COLORS.get(after, QColor("white"))))
                    it.setForeground(QBrush(STATUS_FG.get(after, QColor("#1e293b"))))
                    it.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                tbl.setItem(ri, ci, it)

        tbl.resizeColumnsToContents()
        lay.addWidget(tbl)

        info = QLabel(
            f"ℹ  {len(displaced)} SO(s) will be displaced and automatically re-planned "
            f"after this operation. Due-date compliance has been verified for all.")
        info.setStyleSheet(
            "color:#1e3a5f; background:#e8f0fe; border:1px solid #b3c6f7;"
            " border-radius:4px; padding:6px; font-size:11px;")
        lay.addWidget(info)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Confirm & Apply")
        btns.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(
            "background:#2563eb; color:white; font-weight:bold;"
            " border:none; border-radius:4px; padding:5px 14px;")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)


# ═══════════════════════════════════════════════════════════════════════════════
#  Plan Snapshot Restore Dialog
# ═══════════════════════════════════════════════════════════════════════════════

class _SnapshotCardWidget(QWidget):
    """Card widget for a single snapshot row in PlanTimeMachineDialog."""

    def __init__(self, snap: Dict, plan_delta: Optional[int] = None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(2)

        lbl = snap.get("label", "")
        if lbl.startswith("Auto:"):
            dot_color = "#94A3B8"
        elif lbl.startswith("Before restore"):
            dot_color = "#D97706"
        else:
            dot_color = "#059669"

        # Row 1: dot + timestamp + label
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        row1.setContentsMargins(0, 0, 0, 0)

        dot = QLabel("●")
        dot.setStyleSheet(f"color:{dot_color}; font-size:10px;")
        dot.setFixedWidth(12)
        row1.addWidget(dot)

        ts = snap.get("created_at", "")[:16]
        ts_lbl = QLabel(ts)
        ts_lbl.setStyleSheet("color:#64748B; font-size:9pt;")
        row1.addWidget(ts_lbl)

        name_lbl = QLabel(lbl)
        name_lbl.setStyleSheet("font-size:9pt; font-weight:bold; color:#1E293B;")
        row1.addWidget(name_lbl, 1)
        lay.addLayout(row1)

        # Row 2: plan count + delta badge
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.setContentsMargins(16, 0, 0, 0)

        plan_count = snap.get("plan_count", "?")
        count_lbl = QLabel(f"📦 {plan_count} plans")
        count_lbl.setStyleSheet("font-size:8pt; color:#64748B;")
        row2.addWidget(count_lbl)

        if plan_delta is not None:
            if plan_delta > 0:
                b = QLabel(f"+{plan_delta}")
                b.setStyleSheet(
                    "font-size:8pt; background:#D1FAE5; color:#065F46;"
                    " padding:1px 5px; border-radius:3px;")
                row2.addWidget(b)
            elif plan_delta < 0:
                b = QLabel(str(plan_delta))
                b.setStyleSheet(
                    "font-size:8pt; background:#FEE2E2; color:#991B1B;"
                    " padding:1px 5px; border-radius:3px;")
                row2.addWidget(b)
            else:
                b = QLabel("=")
                b.setStyleSheet("font-size:8pt; color:#94A3B8; padding:1px 5px;")
                row2.addWidget(b)
        else:
            b = QLabel("oldest")
            b.setStyleSheet("font-size:8pt; color:#CBD5E1;")
            row2.addWidget(b)

        row2.addStretch()
        lay.addLayout(row2)


class PlanTimeMachineDialog(QDialog):
    """OS Time Machine style snapshot browser: browse, diff, and restore plan states."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⏪ Plan Time Machine")
        self.resize(900, 540)
        self._all_snapshots: List[Dict] = []
        self._filtered: List[Dict] = []
        self._diff_cache: Dict[str, Optional[Dict]] = {}
        self._selected_idx = -1
        self._restored_label: str = ""
        self._build_ui()
        self._load_snapshots()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        banner = QLabel(
            "⚠  Restoring replaces ALL current plans. "
            "Current state is automatically saved as 'Before restore' before any rollback.")
        banner.setStyleSheet(
            "color:#92400e; background:#fffbeb; border:1px solid #fcd34d;"
            " border-radius:4px; padding:6px; font-size:11px;")
        banner.setWordWrap(True)
        lay.addWidget(banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: timeline list ───────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(4)

        filter_row = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All Types", "Auto", "Manual", "Before restore"])
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_combo)
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("🔍 Search label…")
        self._search_box.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search_box, 1)
        ll.addLayout(filter_row)

        self._list = QListWidget()
        self._list.setSpacing(1)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._list_context_menu)
        self._list.currentRowChanged.connect(self._on_selection)
        ll.addWidget(self._list, 1)

        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("Show:"))
        self._limit_combo = QComboBox()
        self._limit_combo.addItems(["10", "20", "50", "All"])
        self._limit_combo.setCurrentText("20")
        self._limit_combo.currentIndexChanged.connect(self._load_snapshots)
        limit_row.addWidget(self._limit_combo)
        limit_row.addStretch()
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color:#64748B; font-size:10px;")
        limit_row.addWidget(self._count_label)
        ll.addLayout(limit_row)

        splitter.addWidget(left)

        # ── Right: detail panel ───────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.setSpacing(0)

        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._detail_content = QWidget()
        self._detail_inner = QVBoxLayout(self._detail_content)
        self._detail_inner.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._detail_inner.setSpacing(8)

        ph = QLabel("Select a snapshot from the list to view details.")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet("color:#94A3B8; font-size:13px; margin:40px;")
        self._detail_inner.addWidget(ph)

        self._detail_scroll.setWidget(self._detail_content)
        rl.addWidget(self._detail_scroll)
        splitter.addWidget(right)

        splitter.setSizes([360, 540])
        lay.addWidget(splitter, 1)

        # ── Bottom buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._btn_delete = QPushButton("🗑 Delete Selected")
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(self._btn_delete)
        btn_row.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        self._btn_restore = QPushButton("⏪ Restore to This Point")
        self._btn_restore.setEnabled(False)
        self._btn_restore.setStyleSheet(
            "background:#d97706; color:white; font-weight:bold;"
            " border:none; border-radius:5px; padding:6px 18px;")
        self._btn_restore.clicked.connect(self._restore)
        btn_row.addWidget(self._btn_restore)
        lay.addLayout(btn_row)

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_snapshots(self):
        from data.repositories import PlanSnapshotRepo
        lv = self._limit_combo.currentText()
        limit = 0 if lv == "All" else int(lv)
        self._all_snapshots = PlanSnapshotRepo.list_snapshots(limit=limit)
        self._diff_cache.clear()
        self._apply_filter()

    def _apply_filter(self):
        ftype = self._filter_combo.currentText()
        search = self._search_box.text().lower().strip()
        result = []
        for s in self._all_snapshots:
            lbl = s.get("label", "")
            if ftype == "Auto" and not lbl.startswith("Auto:"):
                continue
            if ftype == "Manual" and (
                    lbl.startswith("Auto:") or lbl.startswith("Before restore")):
                continue
            if ftype == "Before restore" and not lbl.startswith("Before restore"):
                continue
            if search and search not in lbl.lower():
                continue
            result.append(s)
        self._filtered = result
        self._rebuild_list()

    def _rebuild_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        for i, snap in enumerate(self._filtered):
            all_idx = next((j for j, s in enumerate(self._all_snapshots)
                            if s["batch_id"] == snap["batch_id"]), -1)
            prev_count = None
            if all_idx >= 0 and all_idx + 1 < len(self._all_snapshots):
                prev_count = self._all_snapshots[all_idx + 1].get("plan_count") or 0
            curr_count = snap.get("plan_count") or 0
            delta = (curr_count - prev_count) if prev_count is not None else None

            card = _SnapshotCardWidget(snap, delta)
            item = QListWidgetItem()
            item.setSizeHint(card.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, card)

        self._count_label.setText(f"{len(self._filtered)} snapshot(s)")
        self._list.blockSignals(False)

        if self._filtered:
            self._list.setCurrentRow(0)
        else:
            self._clear_detail()
            self._btn_restore.setEnabled(False)
            self._btn_delete.setEnabled(False)

    # ── Selection → detail panel ──────────────────────────────────────────────

    def _on_selection(self, row):
        if row < 0 or row >= len(self._filtered):
            return
        self._selected_idx = row
        snap = self._filtered[row]
        self._btn_restore.setEnabled(True)
        self._btn_delete.setEnabled(True)
        self._build_detail(snap)

    def _build_detail(self, snap: Dict):
        while self._detail_inner.count():
            item = self._detail_inner.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        lbl = snap.get("label", "")
        ts = snap.get("created_at", "")[:19].replace("T", " ")

        hdr = QLabel(lbl)
        hdr.setStyleSheet("font-size:12pt; font-weight:bold; color:#1E293B;")
        hdr.setWordWrap(True)
        self._detail_inner.addWidget(hdr)

        ts_lbl = QLabel(f"⏱  {ts}")
        ts_lbl.setStyleSheet("color:#64748B; font-size:10pt;")
        self._detail_inner.addWidget(ts_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        self._detail_inner.addWidget(sep)

        # Summary group
        summary_grp = QGroupBox("Snapshot Summary")
        sg = QFormLayout(summary_grp)
        sg.addRow("Total plans:", QLabel(f"<b>{snap.get('plan_count', '?')}</b>"))
        self._detail_inner.addWidget(summary_grp)

        # Diff group
        diff_grp = QGroupBox("Changes vs. Previous Snapshot")
        dg = QVBoxLayout(diff_grp)

        all_idx = next((j for j, s in enumerate(self._all_snapshots)
                        if s["batch_id"] == snap["batch_id"]), -1)
        has_prev = all_idx >= 0 and all_idx + 1 < len(self._all_snapshots)

        if not has_prev:
            no_prev = QLabel("No previous snapshot — this is the oldest saved state.")
            no_prev.setStyleSheet("color:#94A3B8; font-size:10px;")
            dg.addWidget(no_prev)
        else:
            prev_snap = self._all_snapshots[all_idx + 1]
            prev_ts = prev_snap.get("created_at", "")[:16]
            prev_lbl = prev_snap.get("label", "")
            vs_lbl = QLabel(f"vs. {prev_ts}  —  {prev_lbl}")
            vs_lbl.setStyleSheet("color:#64748B; font-size:9px;")
            dg.addWidget(vs_lbl)

            cache_key = snap["batch_id"]
            if cache_key not in self._diff_cache:
                self._diff_cache[cache_key] = self._compute_diff(
                    snap["batch_id"], prev_snap["batch_id"])
            diff = self._diff_cache[cache_key]

            diff_tbl = QTableWidget(4, 2)
            diff_tbl.setHorizontalHeaderLabels(["Type", "Count"])
            diff_tbl.verticalHeader().setVisible(False)
            diff_tbl.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch)
            diff_tbl.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Interactive)
            diff_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            diff_tbl.setFixedHeight(120)
            diff_tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

            for ri, (row_lbl, count, bg, fg) in enumerate([
                ("➕ Added",   diff["added"],   "#D1FAE5", "#065F46"),
                ("➖ Removed", diff["removed"],  "#FEE2E2", "#991B1B"),
                ("✏  Changed", diff["changed"],  "#DBEAFE", "#1E40AF"),
                ("=  Same",   diff["same"],    "#FFFFFF", "#64748B"),
            ]):
                for ci, val in enumerate([row_lbl, str(count)]):
                    it = QTableWidgetItem(val)
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    it.setBackground(QBrush(QColor(bg)))
                    it.setForeground(QBrush(QColor(fg)))
                    diff_tbl.setItem(ri, ci, it)
            dg.addWidget(diff_tbl)

        self._detail_inner.addWidget(diff_grp)

        warn = QLabel(
            "⚠  Restoring will replace all current plans with this snapshot.\n"
            "Current state is automatically saved before restore.")
        warn.setStyleSheet(
            "color:#92400e; background:#fffbeb; border:1px solid #fcd34d;"
            " border-radius:4px; padding:6px; font-size:10px;")
        warn.setWordWrap(True)
        self._detail_inner.addWidget(warn)
        self._detail_inner.addStretch()

    def _clear_detail(self):
        while self._detail_inner.count():
            item = self._detail_inner.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        ph = QLabel("Select a snapshot from the list to view details.")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet("color:#94A3B8; font-size:13px; margin:40px;")
        self._detail_inner.addWidget(ph)

    # ── Diff computation ──────────────────────────────────────────────────────

    def _compute_diff(self, curr_id: str, prev_id: str) -> Dict:
        from data.repositories import PlanSnapshotRepo

        def _key(p: Dict) -> str:
            pid = p.get("plan_id")
            if pid:
                return str(pid)
            return (f"{p.get('so_number')}|{p.get('entity_code')}"
                    f"|{p.get('process_seq')}|{p.get('plan_date')}|{p.get('shift_no')}")

        curr_map = {_key(p): p for p in PlanSnapshotRepo.get_snapshot_data(curr_id)}
        prev_map = {_key(p): p for p in PlanSnapshotRepo.get_snapshot_data(prev_id)}

        added   = len(set(curr_map) - set(prev_map))
        removed = len(set(prev_map) - set(curr_map))
        common  = set(curr_map) & set(prev_map)
        changed = sum(1 for k in common if curr_map[k] != prev_map[k])
        same    = len(common) - changed
        return {"added": added, "removed": removed, "changed": changed, "same": same}

    # ── Actions ───────────────────────────────────────────────────────────────

    def _restore(self):
        if self._selected_idx < 0 or self._selected_idx >= len(self._filtered):
            return
        snap = self._filtered[self._selected_idx]
        label = snap.get("label", "")
        ts = snap.get("created_at", "")[:16]

        confirm = QMessageBox.question(
            self, "Restore Plan",
            f"Restore to:\n\"{label}\"\n({ts})\n\n"
            "Current plans will be saved as 'Before restore' first, then replaced.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from data.repositories import PlanSnapshotRepo
            from datetime import datetime as _dt
            PlanSnapshotRepo.save(
                f"Before restore — {_dt.now().strftime('%Y-%m-%d %H:%M')}")
            PlanSnapshotRepo.rollback(snap["batch_id"])
            self._restored_label = label
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Restore Error", str(e))
            return
        QApplication.restoreOverrideCursor()
        self.accept()

    def _delete_selected(self):
        if self._selected_idx < 0 or self._selected_idx >= len(self._filtered):
            return
        snap = self._filtered[self._selected_idx]
        confirm = QMessageBox.question(
            self, "Delete Snapshot",
            f"Delete snapshot \"{snap.get('label', '')}\"?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        from data.repositories import PlanSnapshotRepo
        PlanSnapshotRepo.delete(snap["batch_id"])
        self._load_snapshots()

    def _list_context_menu(self, pos):
        if self._list.currentRow() < 0:
            return
        menu = QMenu(self)
        act_del = menu.addAction("🗑 Delete This Snapshot")
        act = menu.exec(self._list.mapToGlobal(pos))
        if act == act_del:
            self._delete_selected()
