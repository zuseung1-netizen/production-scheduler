"""
CRP Tab, Actuals Entry Tab, Alerts Tab, Dashboard Tab
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Dict, List

import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QFileDialog, QMessageBox, QScrollArea, QGroupBox, QComboBox,
    QSpinBox, QLineEdit, QDateEdit, QFormLayout, QDialog,
    QDialogButtonBox, QTextEdit, QSplitter, QTabWidget, QCheckBox
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor, QBrush, QFont

from data.repositories import (
    PlanRepo, SORepo, SKURepo, ShiftRepo, RoomRepo, ActualRepo, ConfigRepo,
    ProcessRoutingRepo
)
from data.crp_excel import crp_manager
from core.scheduler import scheduler


# ════════════════════════════════════════════════════════════════════════════
#  CRP TAB
# ════════════════════════════════════════════════════════════════════════════

class CRPTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self._crp_edit_mode    = False
        self._crp_changed_cells: set = set()
        self._crp_loading      = False
        self._crp_dates:  list = []
        self._crp_shifts: list = []

        bar = QHBoxLayout()
        bar.addWidget(QLabel("CRP Excel:"))
        self.path_label = QLabel(ConfigRepo.get("crp_excel_path", "(not set)"))
        bar.addWidget(self.path_label)

        btn_refresh  = QPushButton("♻ Refresh from Excel")
        btn_template = QPushButton("⬇ Download CRP Template")
        btn_hc       = QPushButton("📊 HC Recommendation")
        btn_refresh.clicked.connect(self._refresh)
        btn_template.clicked.connect(self._template)
        btn_hc.clicked.connect(self._auto_fill_hc)
        bar.addWidget(btn_refresh)
        bar.addWidget(btn_template)
        bar.addWidget(btn_hc)

        self._btn_crp_edit = QPushButton("✏ Edit Mode")
        self._btn_crp_edit.setCheckable(True)
        self._btn_crp_edit.clicked.connect(self._toggle_crp_edit_mode)
        bar.addWidget(self._btn_crp_edit)

        self._btn_crp_save = QPushButton("💾 Save to Excel")
        self._btn_crp_save.setVisible(False)
        self._btn_crp_save.clicked.connect(self._save_crp_changes)
        bar.addWidget(self._btn_crp_save)

        bar.addStretch()
        layout.addLayout(bar)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Guidance banner when CRP path not configured
        self.crp_guide = QLabel(
            "⚠  CRP Excel path is not configured. "
            "Go to Masters > App Config > CRP Excel Path and set the path, then click Refresh.")
        self.crp_guide.setStyleSheet(
            "background:#fff3cd; color:#856404; padding:6px 10px; "
            "font-size:12px; border:1px solid #ffc107;")
        self.crp_guide.setWordWrap(True)
        self.crp_guide.setVisible(False)
        layout.addWidget(self.crp_guide)

        # Headcount summary table
        layout.addWidget(QLabel("Total Headcount per Shift (from CRP Excel):"))
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._on_crp_cell_changed)
        layout.addWidget(self.table, stretch=1)

        self.refresh()

    def refresh(self):
        if self._crp_edit_mode:
            return  # preserve in-progress edits
        path = ConfigRepo.get("crp_excel_path", "")
        self.path_label.setText(path or "(not set)")
        no_path = not path or not __import__("os").path.exists(path)
        self.crp_guide.setVisible(no_path)
        self._load_table()

    def _refresh(self):
        ok, msg = crp_manager.refresh()
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: green;" if ok else "color: red;")
        self._load_table()

    def _load_table(self):
        self._crp_loading = True
        self._crp_changed_cells.clear()
        data = crp_manager.get_all()  # {(date_str, shift_no): total_hc}
        if not data:
            self.table.setRowCount(0)
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(["Shift", "HC"])
            self._crp_loading = False
            return

        dates  = sorted({k[0] for k in data.keys()})[:28]
        shifts = sorted({k[1] for k in data.keys()})
        self._crp_dates  = dates
        self._crp_shifts = shifts

        headers = ["Shift"] + dates
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(shifts))

        editable = (QTableWidgetItem.ItemType.Type,)  # just to call flags below
        for ri, shift in enumerate(shifts):
            lbl = QTableWidgetItem(f"Shift {shift}")
            lbl.setFlags(lbl.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(ri, 0, lbl)
            for di, d in enumerate(dates):
                hc   = data.get((d, shift), 0)
                item = QTableWidgetItem(str(hc))
                if hc == 0:
                    item.setBackground(QBrush(QColor("#ffe0e0")))
                if not self._crp_edit_mode:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(ri, 1 + di, item)

        self._crp_loading = False

    def _toggle_crp_edit_mode(self, checked: bool):
        self._crp_edit_mode = checked
        self._btn_crp_edit.setText("✏ Editing..." if checked else "✏ Edit Mode")
        self._btn_crp_edit.setStyleSheet(
            "background:#fef9c3; font-weight:bold;" if checked else "")
        self._btn_crp_save.setVisible(checked)
        if checked:
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked |
                QAbstractItemView.EditTrigger.EditKeyPressed)
            # unlock all data cells
            for ri in range(self.table.rowCount()):
                for ci in range(1, self.table.columnCount()):
                    item = self.table.item(ri, ci)
                    if item:
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        else:
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self._load_table()

    def _on_crp_cell_changed(self, item):
        if self._crp_loading or not self._crp_edit_mode:
            return
        if item.column() == 0:
            return
        item.setBackground(QBrush(QColor("#fef08a")))
        self._crp_changed_cells.add((item.row(), item.column()))
        n = len(self._crp_changed_cells)
        self.status_label.setText(f"✏ Edit mode — {n} cell(s) modified")
        self.status_label.setStyleSheet("color:#92400e;")

    def _save_crp_changes(self):
        if not self._crp_changed_cells:
            self._toggle_crp_edit_mode(False)
            self._btn_crp_edit.setChecked(False)
            return

        updates: dict = {}
        errors  = []
        for ri, ci in self._crp_changed_cells:
            item = self.table.item(ri, ci)
            if not item:
                continue
            try:
                hc = int(item.text())
            except ValueError:
                errors.append(f"Row {ri+1}, Col {ci}: '{item.text()}' is not a valid integer")
                continue
            if ci - 1 >= len(self._crp_dates) or ri >= len(self._crp_shifts):
                continue
            date_str = self._crp_dates[ci - 1]
            shift_no = self._crp_shifts[ri]
            updates[(date_str, shift_no)] = hc

        if errors:
            QMessageBox.warning(self, "Invalid Values", "\n".join(errors))
            return

        ok, msg = crp_manager.write_total_hc(updates)
        if ok:
            self.status_label.setText(f"✅ Saved {len(updates)} cell(s) to CRP Excel")
            self.status_label.setStyleSheet("color:green;")
            self._crp_edit_mode = False
            self._btn_crp_edit.setChecked(False)
            self._btn_crp_edit.setText("✏ Edit Mode")
            self._btn_crp_edit.setStyleSheet("")
            self._btn_crp_save.setVisible(False)
            self._load_table()
        else:
            QMessageBox.warning(self, "Save Failed", msg)

    def _template(self):
        from datetime import date as dt
        path, _ = QFileDialog.getSaveFileName(self, "Save CRP Template", "CRP_template.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        shifts = [s["shift_no"] for s in ShiftRepo.all()]
        today = dt.today()
        d0 = today.strftime("%Y-%m-%d")
        d1 = (today + timedelta(days=56)).strftime("%Y-%m-%d")
        ok, msg = crp_manager.create_template(path, shifts, d0, d1)
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Template", msg)

    def _auto_fill_hc(self):
        dlg = HCDemandDialog(self)
        if dlg.exec() and dlg.to_apply:
            ok, msg = crp_manager.write_total_hc(dlg.to_apply)
            (QMessageBox.information if ok else QMessageBox.warning)(self, "HC Recommendation", msg)
            if ok:
                self._load_table()


# ════════════════════════════════════════════════════════════════════════════
#  ACTUALS TAB
# ════════════════════════════════════════════════════════════════════════════

class ActualsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Filter: select date + shift to see planned SO-LineItems
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Date:"))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        fbar.addWidget(self.date_edit)

        fbar.addWidget(QLabel("Shift:"))
        self.shift_combo = QComboBox()
        self._reload_shifts()
        fbar.addWidget(self.shift_combo)

        btn_load = QPushButton("🔍 Load Planned Items")
        btn_load.clicked.connect(self._load_planned)
        fbar.addWidget(btn_load)

        btn_sample = QPushButton("🧪 LOT Sample Qty")
        btn_sample.clicked.connect(self._open_sample_entry)
        fbar.addWidget(btn_sample)

        btn_replan = QPushButton("🔄 Replan after Actuals")
        btn_replan.clicked.connect(self._replan)
        fbar.addWidget(btn_replan)
        fbar.addStretch()
        layout.addLayout(fbar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Planned items for the shift
        grp1 = QGroupBox("Planned Items")
        g1l  = QVBoxLayout(grp1)
        self.plan_table = QTableWidget()
        self.plan_table.setColumnCount(8)
        self.plan_table.setHorizontalHeaderLabels([
            "Plan ID", "SO", "SKU", "Line", "Room", "Process", "Qty Planned", "Qty Actual (enter)"
        ])
        self.plan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        g1l.addWidget(self.plan_table)

        btn_save = QPushButton("💾 Save Actuals")
        btn_save.clicked.connect(self._save_actuals)
        g1l.addWidget(btn_save)
        splitter.addWidget(grp1)

        # Unplanned production entry
        grp2 = QGroupBox("Unplanned Production Entry")
        g2l  = QFormLayout(grp2)
        self.up_so    = QLineEdit(); g2l.addRow("SO Number:", self.up_so)
        self.up_sku   = QLineEdit(); g2l.addRow("SKU Code:",  self.up_sku)
        self.up_li    = QLineEdit(); g2l.addRow("Line Item:", self.up_li)
        self.up_lot   = QLineEdit(); g2l.addRow("LOT Number:",self.up_lot)
        self.up_room  = QLineEdit(); g2l.addRow("Room:",      self.up_room)
        self.up_proc  = QLineEdit(); g2l.addRow("Process:",   self.up_proc)
        self.up_qty   = QSpinBox(); self.up_qty.setRange(1, 999999); g2l.addRow("Qty:", self.up_qty)
        self.up_note  = QLineEdit(); g2l.addRow("Note:",      self.up_note)
        btn_add_up = QPushButton("➕ Add Unplanned Actual")
        btn_add_up.clicked.connect(self._add_unplanned)
        g2l.addRow("", btn_add_up)
        splitter.addWidget(grp2)

        splitter.setSizes([500, 200])
        layout.addWidget(splitter, stretch=1)

    def _reload_shifts(self):
        self.shift_combo.clear()
        for s in ShiftRepo.all():
            self.shift_combo.addItem(f"{s['shift_no']} - {s['shift_name']}", s["shift_no"])

    def refresh(self):
        self._reload_shifts()

    def _load_planned(self):
        d = self.date_edit.date().toString("yyyy-MM-dd")
        sno = self.shift_combo.currentData()
        plans = [p for p in PlanRepo.all(d, d) if p["shift_no"] == sno]
        self.plan_table.setRowCount(len(plans))
        for ri, p in enumerate(plans):
            actual = ActualRepo.actual_qty(p["so_number"], p["sku_code"], p["line_item"])
            for ci, val in enumerate([
                p["plan_id"], p["so_number"], p["sku_code"], p["line_item"],
                p["room_code"], p["process_name"], p["qty_planned"], actual
            ]):
                item = QTableWidgetItem(str(val))
                if ci == 7:  # actual qty — editable
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.plan_table.setItem(ri, ci, item)

    def _save_actuals(self):
        d = self.date_edit.date().toString("yyyy-MM-dd")
        sno = self.shift_combo.currentData()
        saved = 0
        for ri in range(self.plan_table.rowCount()):
            plan_id = int(self.plan_table.item(ri, 0).text())
            try:
                qty = int(self.plan_table.item(ri, 7).text())
            except (ValueError, AttributeError):
                continue
            if qty <= 0:
                continue
            plan = PlanRepo.get(plan_id)
            if not plan:
                continue
            lot, ok = "", True
            from PyQt6.QtWidgets import QInputDialog
            lot, ok = QInputDialog.getText(
                self, "LOT Number",
                f"LOT for SO {plan['so_number']} SKU {plan['sku_code']} Qty {qty}:"
            )
            ActualRepo.insert({
                "plan_id":      plan_id,
                "so_number":    plan["so_number"],
                "sku_code":     plan["sku_code"],
                "line_item":    plan["line_item"],
                "lot_number":   lot,
                "room_code":    plan["room_code"],
                "process_name": plan["process_name"],
                "actual_date":  d,
                "shift_no":     sno,
                "qty_actual":   qty,
                "note":         "",
            })
            saved += 1
        QMessageBox.information(self, "Actuals", f"Saved {saved} actual entries.")

    def _add_unplanned(self):
        d = self.date_edit.date().toString("yyyy-MM-dd")
        sno = self.shift_combo.currentData()
        ActualRepo.insert({
            "plan_id":      None,
            "so_number":    self.up_so.text(),
            "sku_code":     self.up_sku.text(),
            "line_item":    self.up_li.text(),
            "lot_number":   self.up_lot.text(),
            "room_code":    self.up_room.text(),
            "process_name": self.up_proc.text(),
            "actual_date":  d,
            "shift_no":     sno,
            "qty_actual":   self.up_qty.value(),
            "note":         self.up_note.text(),
        })
        QMessageBox.information(self, "Saved", "Unplanned actual saved.")

    def _open_sample_entry(self):
        """Open LOT sample quantity entry dialog for recent actuals."""
        dlg = LotSampleDialog(self)
        dlg.exec()

    def _replan(self):
        d0 = date.today().strftime("%Y-%m-%d")
        d1 = (date.today() + timedelta(weeks=8)).strftime("%Y-%m-%d")
        result = scheduler.replan_after_actuals(d0, d1)
        dlg = ReplanReportDialog(result, self)
        dlg.exec()
        if self.main_window:
            self.main_window.gantt_tab.refresh()
            self.main_window.notify(
                f"Replan: {len(result['deleted'])} deleted, "
                f"{len(result['replanned'])} re-planned, "
                f"{len(result['errors'])} errors."
            )


class ReplanReportDialog(QDialog):
    """리플래닝 실행 결과를 삭제/재계획/오류로 나눠 보여주는 다이얼로그."""

    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Replan from Actuals — Report")
        self.setMinimumSize(740, 480)
        self._result = result
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        deleted   = self._result.get("deleted", [])
        replanned = self._result.get("replanned", [])
        errors    = self._result.get("errors", [])

        summary = QLabel(
            f"Deleted: {len(deleted)} plan(s)   |   "
            f"Re-planned: {len(replanned)} SO line(s)   |   "
            f"Errors: {len(errors)}"
        )
        summary.setStyleSheet(
            "font-weight:bold; font-size:12px; padding:8px; "
            "background:#e8f0fe; border-radius:4px;")
        layout.addWidget(summary)

        tabs = QTabWidget()

        # ── Deleted ──
        t_del = QTableWidget(len(deleted), 4)
        t_del.setHorizontalHeaderLabels(["Plan ID", "SO", "SKU / Line", "Reason"])
        t_del.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t_del.horizontalHeader().setStretchLastSection(True)
        t_del.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for ri, d in enumerate(deleted):
            t_del.setItem(ri, 0, QTableWidgetItem(str(d["plan_id"])))
            t_del.setItem(ri, 1, QTableWidgetItem(d["so_number"]))
            t_del.setItem(ri, 2, QTableWidgetItem(f"{d['sku_code']} / {d['line_item']}"))
            t_del.setItem(ri, 3, QTableWidgetItem(d["reason"]))
            for ci in range(4):
                t_del.item(ri, ci).setBackground(QBrush(QColor("#fff9c4")))
        tabs.addTab(t_del, f"Deleted ({len(deleted)})")

        # ── Re-planned ──
        t_rep = QTableWidget(len(replanned), 5)
        t_rep.setHorizontalHeaderLabels(["SO", "SKU", "Line", "Actual Qty", "Remaining Qty"])
        t_rep.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t_rep.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for ri, r in enumerate(replanned):
            t_rep.setItem(ri, 0, QTableWidgetItem(r["so_number"]))
            t_rep.setItem(ri, 1, QTableWidgetItem(r["sku_code"]))
            t_rep.setItem(ri, 2, QTableWidgetItem(r["line_item"]))
            t_rep.setItem(ri, 3, QTableWidgetItem(str(r["actual_qty"])))
            t_rep.setItem(ri, 4, QTableWidgetItem(str(r["remaining_qty"])))
            for ci in range(5):
                t_rep.item(ri, ci).setBackground(QBrush(QColor("#c8e6c9")))
        tabs.addTab(t_rep, f"Re-planned ({len(replanned)})")

        # ── Errors ──
        t_err = QTableWidget(len(errors), 2)
        t_err.setHorizontalHeaderLabels(["SO", "Reason"])
        t_err.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t_err.horizontalHeader().setStretchLastSection(True)
        t_err.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for ri, e in enumerate(errors):
            t_err.setItem(ri, 0, QTableWidgetItem(str(e.get("so", ""))))
            t_err.setItem(ri, 1, QTableWidgetItem(str(e.get("reason", ""))))
            for ci in range(2):
                t_err.item(ri, ci).setBackground(QBrush(QColor("#ffcdd2")))
        tabs.addTab(t_err, f"Errors ({len(errors)})")

        layout.addWidget(tabs, stretch=1)

        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)


# ════════════════════════════════════════════════════════════════════════════
#  ALERTS TAB
# ════════════════════════════════════════════════════════════════════════════

class AlertsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        btn_refresh = QPushButton("🔍 Refresh Alerts")
        btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(btn_refresh); bar.addStretch()
        layout.addLayout(bar)

        self.conflict_grp = QGroupBox("⚠ Capacity Conflicts")
        cg_l = QVBoxLayout(self.conflict_grp)
        self.conflict_table = _alerts_table(
            ["Date", "Room", "Process", "Shift", "Planned", "Capacity", "Overrun"]
        )
        cg_l.addWidget(self.conflict_table)
        layout.addWidget(self.conflict_grp)

        self.late_grp = QGroupBox("🔴 Late / At-Risk SOs")
        lg_l = QVBoxLayout(self.late_grp)
        self.late_table = _alerts_table(
            ["SO", "SKU", "Line", "Due Date", "Planned Complete", "Remaining Qty", "Reason"]
        )
        lg_l.addWidget(self.late_table)
        layout.addWidget(self.late_grp)

        self.refresh()

    def refresh(self):
        self._load_conflicts()
        self._load_late()

    def _load_conflicts(self):
        d0 = date.today().strftime("%Y-%m-%d")
        d1 = (date.today() + timedelta(weeks=4)).strftime("%Y-%m-%d")
        conflicts = scheduler.detect_conflicts(d0, d1)
        self.conflict_table.setRowCount(len(conflicts))
        for ri, c in enumerate(conflicts):
            for ci, val in enumerate([
                c["plan_date"], c["room_code"], c["process_name"], c["shift_no"],
                c["planned_inner"], c["capacity_inner"], c["overrun_inner"]
            ]):
                item = QTableWidgetItem(str(val))
                item.setBackground(QBrush(QColor("#ffe0e0")))
                self.conflict_table.setItem(ri, ci, item)

    def _load_late(self):
        today = date.today()
        sos = SORepo.all("OPEN")
        late_rows = []
        for so in sos:
            due = datetime.strptime(so["due_date"], "%Y-%m-%d").date()
            actual = ActualRepo.actual_qty(
                so["so_number"], so["sku_code"], so["line_item"])
            remaining = so["qty"] - actual
            if remaining <= 0:
                continue
            plans = PlanRepo.for_so(
                so["so_number"], so["sku_code"], so["line_item"])
            if plans:
                last = max(plans, key=lambda p: (p["plan_date"], p["shift_no"]))
                plan_complete = f"{last['plan_date']} S{last['shift_no']}"
                plan_complete_date = datetime.strptime(
                    last["plan_date"], "%Y-%m-%d").date()
                is_late = plan_complete_date > due
            else:
                plan_complete = "Not planned"
                is_late = True

            if is_late or due < today:
                late_rows.append({
                    "so": so["so_number"], "sku": so["sku_code"],
                    "li": so["line_item"], "due": so["due_date"],
                    "complete": plan_complete, "remaining": remaining,
                    "reason": "Late" if due < today else "Plan exceeds due date"
                })

        self.late_table.setRowCount(len(late_rows))
        for ri, r in enumerate(late_rows):
            for ci, val in enumerate([
                r["so"], r["sku"], r["li"], r["due"],
                r["complete"], r["remaining"], r["reason"]
            ]):
                item = QTableWidgetItem(str(val))
                item.setBackground(QBrush(QColor("#ffcccc")))
                self.late_table.setItem(ri, ci, item)

        # QC shortfall section — SOs where net qty < SO qty after sample+reject
        self._load_qc_shortfall()

    def _load_qc_shortfall(self):
        """Show SOs where actual - sample - reject < SO qty."""
        from data.repositories import LotSampleRepo
        sos = SORepo.all("OPEN")
        shortfall_rows = []
        for so in sos:
            actual_total = ActualRepo.actual_qty(
                so["so_number"], so["sku_code"], so["line_item"])
            if actual_total == 0:
                continue   # nothing produced yet — skip
            sample_total = LotSampleRepo.total_sample_qty(
                so["so_number"], so["sku_code"], so["line_item"])
            reject_total = LotSampleRepo.total_reject_qty(
                so["so_number"], so["sku_code"], so["line_item"])
            net = actual_total - sample_total - reject_total
            if net < so["qty"]:
                shortfall_rows.append({
                    "so":     so["so_number"],
                    "sku":    so["sku_code"],
                    "li":     so["line_item"],
                    "due":    so["due_date"],
                    "actual": actual_total,
                    "sample": sample_total,
                    "reject": reject_total,
                    "net":    net,
                    "needed": so["qty"],
                    "short":  so["qty"] - net,
                })

        # Re-use or create QC shortfall table
        if not hasattr(self, "qc_table"):
            self.qc_grp = QGroupBox("🔴 QC Shortfall (net qty < SO qty)")
            qg_l = QVBoxLayout(self.qc_grp)
            self.qc_table = _alerts_table([
                "SO", "SKU", "Line", "Due Date",
                "Actual", "Sample", "Reject", "Net Qty",
                "SO Qty", "Short By"
            ])
            qg_l.addWidget(self.qc_table)
            # Insert after late_grp in parent layout
            parent_layout = self.layout()
            parent_layout.addWidget(self.qc_grp)

        self.qc_table.setRowCount(len(shortfall_rows))
        for ri, r in enumerate(shortfall_rows):
            for ci, val in enumerate([
                r["so"], r["sku"], r["li"], r["due"],
                r["actual"], r["sample"], r["reject"],
                r["net"], r["needed"], r["short"]
            ]):
                item = QTableWidgetItem(str(val))
                if ci in (6, 9):   # reject col, short-by col — strong red
                    item.setBackground(QBrush(QColor("#ffcccc")))
                elif ci == 5:      # sample col — yellow
                    item.setBackground(QBrush(QColor("#fffbe6")))
                elif ci == 7:      # net qty — orange
                    item.setBackground(QBrush(QColor("#fde8d8")))
                self.qc_table.setItem(ri, ci, item)


def _alerts_table(cols: list) -> QTableWidget:
    t = QTableWidget()
    t.setColumnCount(len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return t


# ════════════════════════════════════════════════════════════════════════════
#  DASHBOARD TAB
# ════════════════════════════════════════════════════════════════════════════

class DashboardTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("📊 Production Dashboard"))

        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh)
        layout.addWidget(btn_refresh)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-size:13px; padding:8px;")
        layout.addWidget(self.summary_label)

        # SO status table by due week
        self.week_table = QTableWidget()
        self.week_table.setColumnCount(6)
        self.week_table.setHorizontalHeaderLabels(
            ["Week", "Total SOs", "Fully Planned", "Partially Planned",
             "Not Planned", "Actual Started"])
        self.week_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.week_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.week_table.setAlternatingRowColors(True)
        layout.addWidget(self.week_table, stretch=1)

        self.refresh()

    def refresh(self):
        self._compute_summary()

    def _compute_summary(self):
        sos = SORepo.all("OPEN")
        total = len(sos)
        fully, partial, none_ = 0, 0, 0
        for so in sos:
            planned = PlanRepo.planned_qty(so["so_number"], so["sku_code"], so["line_item"])
            actual  = ActualRepo.actual_qty(so["so_number"], so["sku_code"], so["line_item"])
            remaining = so["qty"] - actual
            if planned >= remaining:
                fully += 1
            elif planned > 0:
                partial += 1
            else:
                none_ += 1

        self.summary_label.setText(
            f"Total Open SOs: <b>{total}</b>  |  "
            f"Fully Planned: <b style='color:green'>{fully}</b>  |  "
            f"Partially Planned: <b style='color:orange'>{partial}</b>  |  "
            f"Not Planned: <b style='color:red'>{none_}</b>"
        )

        # Weekly breakdown
        from collections import defaultdict
        weeks: Dict[str, Dict] = defaultdict(lambda: {"total": 0, "full": 0, "part": 0, "none": 0, "started": 0})
        for so in sos:
            try:
                due = datetime.strptime(so["due_date"], "%Y-%m-%d").date()
                # ISO week
                week_key = due.strftime("%Y-W%V")
            except Exception:
                week_key = "Unknown"
            weeks[week_key]["total"] += 1
            planned = PlanRepo.planned_qty(so["so_number"], so["sku_code"], so["line_item"])
            actual  = ActualRepo.actual_qty(so["so_number"], so["sku_code"], so["line_item"])
            remaining = so["qty"] - actual
            if actual > 0:
                weeks[week_key]["started"] += 1
            if planned >= remaining:
                weeks[week_key]["full"] += 1
            elif planned > 0:
                weeks[week_key]["part"] += 1
            else:
                weeks[week_key]["none"] += 1

        week_keys = sorted(weeks.keys())
        self.week_table.setRowCount(len(week_keys))
        for ri, wk in enumerate(week_keys):
            w = weeks[wk]
            for ci, val in enumerate([wk, w["total"], w["full"], w["part"], w["none"], w["started"]]):
                item = QTableWidgetItem(str(val))
                if ci == 4 and val > 0:
                    item.setBackground(QBrush(QColor("#ffcccc")))
                elif ci == 1:
                    item.setBackground(QBrush(QColor("#e8f0fe")))
                self.week_table.setItem(ri, ci, item)


# ════════════════════════════════════════════════════════════════════════════
#  LOT SAMPLE DIALOG
# ════════════════════════════════════════════════════════════════════════════

class LotSampleDialog(QDialog):
    """
    Planner enters QC sample and reject quantities per LOT after production.
    net_qty = actual_qty - sample_qty - reject_qty (deliverable qty).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LOT QC Quantity Entry")
        self.resize(1050, 600)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Filter bar
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Filter SO/SKU:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("SO number or SKU code…")
        self.search.textChanged.connect(self._load)
        fbar.addWidget(self.search)
        fbar.addStretch()
        layout.addLayout(fbar)

        # Summary label
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("padding:4px; font-weight:bold;")
        layout.addWidget(self.summary_label)

        # Legend
        legend = QLabel(
            "  🟡 Sample Qty — QC 샘플로 소모된 수량   "
            "  🔴 Reject Qty — QC 부적합 판정 수량   "
            "  Net Qty = Actual − Sample − Reject")
        legend.setStyleSheet(
            "font-size:11px; color:var(--secondary); "
            "padding:4px 8px; background:#f8f8f8;")
        layout.addWidget(legend)

        # Table columns:
        # 0:actual_id | 1:type | 2:code | 3:SO/Line | 4:LOT
        # 5:actual_qty | 6:sample_qty✏ | 7:reject_qty✏ | 8:net_qty | 9:note✏
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "Actual ID", "Type", "Code", "SO / Line",
            "LOT Number", "Actual Qty",
            "Sample Qty ✏", "Reject Qty ✏", "Net Qty", "Note ✏"
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, stretch=1)

        # Buttons
        bbar = QHBoxLayout()
        btn_save = QPushButton("💾 Save QC Entries")
        btn_save.clicked.connect(self._save)
        bbar.addWidget(btn_save)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        bbar.addWidget(btn_close)
        bbar.addStretch()
        layout.addLayout(bbar)

        self._load()

    def _load(self):
        from data.repositories import ActualRepo, LotSampleRepo
        actuals = ActualRepo.recent(300)
        f = self.search.text().lower()
        if f:
            actuals = [a for a in actuals
                       if f in (a.get("so_number", "") +
                                a.get("entity_code", "")).lower()]

        self.table.setRowCount(len(actuals))
        self._actual_rows = actuals

        SAMPLE_BG = QColor("#fffbe6")   # yellow tint — sample
        REJECT_BG = QColor("#fde8e8")   # red tint   — reject
        NET_OK    = QColor("#d4f0c0")   # green      — sufficient
        NET_SHORT = QColor("#ffcccc")   # red        — short

        short_count = 0
        for ri, a in enumerate(actuals):
            samples     = LotSampleRepo.for_actual(a["actual_id"])
            sample_qty  = sum(s["sample_qty"] for s in samples)
            reject_qty  = sum(s.get("reject_qty", 0) for s in samples)
            net_qty     = a["qty_actual"] - sample_qty - reject_qty

            so_line = (f"{a.get('so_number','')}/{a.get('line_item','')}"
                       if a.get("so_number") else "—")
            so_needed = 0
            if a.get("so_number") and a.get("sku_code"):
                so = SORepo.get(
                    a["so_number"], a["sku_code"],
                    a.get("line_item", ""))
                if so:
                    so_needed = so["qty"]

            row_vals = [
                a["actual_id"],
                a["entity_type"],
                a["entity_code"],
                so_line,
                a.get("lot_number") or "—",
                a["qty_actual"],
                sample_qty,    # col 6 — editable
                reject_qty,    # col 7 — editable
                net_qty,       # col 8 — computed
                ""             # col 9 — note editable
            ]

            for ci, val in enumerate(row_vals):
                item = QTableWidgetItem(str(val))

                if ci == 6:    # sample qty — editable, yellow
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    item.setBackground(QBrush(SAMPLE_BG))
                elif ci == 7:  # reject qty — editable, red tint
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    item.setBackground(QBrush(REJECT_BG))
                elif ci == 8:  # net qty — colour coded
                    is_short = (so_needed > 0 and net_qty < so_needed)
                    item.setBackground(QBrush(NET_SHORT if is_short else NET_OK))
                    if is_short:
                        short_count += 1
                        item.setText(
                            f"{net_qty}  ⚠ short {so_needed - net_qty}")
                        item.setFlags(
                            item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    else:
                        item.setFlags(
                            item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                elif ci == 9:  # note — editable
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(
                        item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                self.table.setItem(ri, ci, item)

        if short_count:
            self.summary_label.setText(
                f"⚠  {short_count} SO-LOT(s) short after sample/reject deduction")
            self.summary_label.setStyleSheet(
                "color:red; font-weight:bold; padding:4px;")
        else:
            self.summary_label.setText(
                f"✅  {len(actuals)} actuals — all SOs appear sufficient")
            self.summary_label.setStyleSheet(
                "color:green; font-weight:bold; padding:4px;")

    def _save(self):
        from data.repositories import ActualRepo, LotSampleRepo
        saved = 0
        for ri in range(self.table.rowCount()):
            actual_id = int(self.table.item(ri, 0).text())
            try:
                sample_qty = int(self.table.item(ri, 6).text())
                reject_qty = int(self.table.item(ri, 7).text())
            except (ValueError, AttributeError):
                continue
            if sample_qty < 0 or reject_qty < 0:
                continue

            actual = next((a for a in self._actual_rows
                           if a["actual_id"] == actual_id), None)
            if not actual:
                continue

            note_item = self.table.item(ri, 9)
            note = note_item.text() if note_item else None

            # Delete existing, re-insert with latest values
            for s in LotSampleRepo.for_actual(actual_id):
                LotSampleRepo.delete(s["sample_id"])

            if sample_qty > 0 or reject_qty > 0:
                LotSampleRepo.insert({
                    "actual_id":   actual_id,
                    "entity_type": actual["entity_type"],
                    "entity_code": actual["entity_code"],
                    "lot_number":  actual.get("lot_number") or "",
                    "so_number":   actual.get("so_number") or "",
                    "sku_code":    actual.get("sku_code") or "",
                    "line_item":   actual.get("line_item") or "",
                    "sample_qty":  sample_qty,
                    "reject_qty":  reject_qty,
                    "note":        note or None,
                })
                saved += 1

        QMessageBox.information(
            self, "Saved",
            f"QC quantities saved for {saved} lot(s).")
        self._load()


# ════════════════════════════════════════════════════════════════════════════
#  INVENTORY TAB
# ════════════════════════════════════════════════════════════════════════════

class InventoryTab(QWidget):
    """
    재고 관리 탭.
    - 재고 Excel 업로드 / 템플릿 다운로드
    - 재고 목록 (FEFO 정렬, 잔여 수량 표시)
    - SO별 재고 배정: FEFO 자동 제안 → 플래너 컨펌 or 수동 변경
    """
    _EDITABLE_INV_COLS = {3, 6, 7, 8}   # Qty Available, Prod Date, Expiry Date, Status
    _READONLY_INV_COLS = {0, 1, 2, 4, 5}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._inv_edit_mode = False
        self._inv_changed_cells: set = set()
        self._inv_loading = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Toolbar ──
        bar = QHBoxLayout()
        btn_upload = QPushButton("📤 Upload Inventory")
        btn_tmpl   = QPushButton("⬇ Template")
        btn_alloc  = QPushButton("🔗 Allocate to SO")
        btn_upload.clicked.connect(self._upload)
        btn_tmpl.clicked.connect(self._template)
        btn_alloc.clicked.connect(self._open_allocate)
        for b in (btn_upload, btn_tmpl, btn_alloc):
            bar.addWidget(b)

        bar.addWidget(QLabel("  Filter SKU:"))
        self.sku_filter = QLineEdit()
        self.sku_filter.setPlaceholderText("SKU code…")
        self.sku_filter.textChanged.connect(self.refresh)
        bar.addWidget(self.sku_filter)

        bar.addWidget(QLabel("Status:"))
        self.status_filter = QComboBox()
        self.status_filter.addItems(["ALL", "AVAILABLE", "ALLOCATED", "CONSUMED"])
        self.status_filter.currentTextChanged.connect(self.refresh)
        bar.addWidget(self.status_filter)

        self._btn_inv_edit = QPushButton("✏ Edit Mode")
        self._btn_inv_edit.clicked.connect(self._toggle_inv_edit_mode)
        self._btn_inv_save = QPushButton("💾 Save Changes")
        self._btn_inv_save.clicked.connect(self._save_inv_changes)
        self._btn_inv_save.setEnabled(False)
        bar.addWidget(self._btn_inv_edit)
        bar.addWidget(self._btn_inv_save)
        bar.addStretch()
        layout.addLayout(bar)

        # ── Summary ──
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("padding:4px; font-size:12px;")
        layout.addWidget(self.summary_label)

        self._inv_status = QLabel()
        self._inv_status.setWordWrap(True)
        layout.addWidget(self._inv_status)

        # ── Inventory table ──
        inv_grp = QGroupBox("Inventory Lots (FEFO order)")
        ig_l = QVBoxLayout(inv_grp)
        self.inv_table = QTableWidget()
        self.inv_table.setColumnCount(9)
        self.inv_table.setHorizontalHeaderLabels([
            "Inv ID", "SKU", "LOT Number", "Qty Available",
            "Qty Allocated", "Qty Remaining",
            "Production Date", "Expiry Date", "Status"
        ])
        self.inv_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.inv_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.inv_table.setAlternatingRowColors(True)
        self.inv_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.inv_table.itemChanged.connect(self._on_inv_cell_changed)
        ig_l.addWidget(self.inv_table)
        layout.addWidget(inv_grp, stretch=1)

        # ── Allocation history table ──
        alloc_grp = QGroupBox("SO Allocation History")
        ag_l = QVBoxLayout(alloc_grp)
        self.alloc_table = QTableWidget()
        self.alloc_table.setColumnCount(8)
        self.alloc_table.setHorizontalHeaderLabels([
            "Alloc ID", "SO", "SKU", "Line",
            "LOT", "Qty Allocated", "Allocated At", "Note"
        ])
        self.alloc_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.alloc_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.alloc_table.setAlternatingRowColors(True)

        btn_dealloc = QPushButton("🗑 Remove Selected Allocation")
        btn_dealloc.clicked.connect(self._deallocate)
        ag_l.addWidget(self.alloc_table)
        ag_l.addWidget(btn_dealloc)
        layout.addWidget(alloc_grp, stretch=1)

        self.refresh()

    def refresh(self):
        self._load_inventory()
        self._load_allocations()

    def _load_inventory(self):
        self._inv_loading = True
        self._inv_changed_cells.clear()
        self._btn_inv_save.setEnabled(False)
        from data.repositories import InventoryRepo
        sku_f    = self.sku_filter.text().strip()
        status_f = self.status_filter.currentText()
        rows = InventoryRepo.all(
            sku_code=sku_f if sku_f else None,
            status=status_f if status_f != "ALL" else None)

        total_avail = sum(r["qty_available"] for r in rows)
        total_rem   = sum(r["qty_remaining"]  for r in rows)
        self.summary_label.setText(
            f"Total lots: {len(rows)}  |  "
            f"Total available: {total_avail:,}  |  "
            f"Total remaining: {total_rem:,}")

        self.inv_table.setRowCount(len(rows))
        STATUS_COLORS = {
            "AVAILABLE": QColor("#d4f0c0"),
            "ALLOCATED": QColor("#ffe0a0"),
            "CONSUMED":  QColor("#d0d0d0"),
            "EXPIRED":   QColor("#ffcccc"),
        }
        for ri, r in enumerate(rows):
            vals = [
                r["inv_id"], r["sku_code"], r["lot_number"],
                r["qty_available"], r["qty_allocated"], r["qty_remaining"],
                r.get("production_date") or "—",
                r.get("expiry_date") or "—",
                r["status"],
            ]
            bg = STATUS_COLORS.get(r["status"], QColor("white"))
            for ci, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if ci in self._READONLY_INV_COLS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if ci == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r)
                if ci == 5 and r["qty_remaining"] == 0:
                    item.setBackground(QBrush(QColor("#d0d0d0")))
                elif ci == 8:
                    item.setBackground(QBrush(bg))
                self.inv_table.setItem(ri, ci, item)
        self._inv_loading = False
        self._inv_status.setText("")

    def _toggle_inv_edit_mode(self):
        self._inv_edit_mode = not self._inv_edit_mode
        if self._inv_edit_mode:
            self._btn_inv_edit.setText("🔒 Exit Edit Mode")
            self._btn_inv_edit.setStyleSheet(
                "background:#e65100; color:white; font-weight:bold;")
            self.inv_table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked |
                QAbstractItemView.EditTrigger.EditKeyPressed)
            self._inv_status.setText(
                "✏ 편집 모드 — Qty Available, Production Date, Expiry Date, Status 편집 가능")
            self._inv_status.setStyleSheet(
                "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        else:
            self._btn_inv_edit.setText("✏ Edit Mode")
            self._btn_inv_edit.setStyleSheet("")
            self.inv_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            if not self._inv_changed_cells:
                self._inv_status.setText("")
                self._inv_status.setStyleSheet("")

    def _on_inv_cell_changed(self, item):
        if self._inv_loading:
            return
        if item.column() not in self._EDITABLE_INV_COLS:
            return
        item.setBackground(QBrush(QColor("#fff9c4")))
        self._inv_changed_cells.add((item.row(), item.column()))
        n = len({r for r, _ in self._inv_changed_cells})
        self._inv_status.setText(f"✏ {n} row(s) modified — unsaved")
        self._inv_status.setStyleSheet(
            "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        self._btn_inv_save.setEnabled(True)

    def _save_inv_changes(self):
        if not self._inv_changed_cells:
            return
        from data.repositories import InventoryRepo
        changed_rows = {r for r, _ in self._inv_changed_cells}
        saved, errors = 0, []
        for ri in sorted(changed_rows):
            try:
                orig = self.inv_table.item(ri, 0).data(Qt.ItemDataRole.UserRole)
                if not orig:
                    continue
                qty_text     = self.inv_table.item(ri, 3).text().strip()
                prod_date    = self.inv_table.item(ri, 6).text().strip()
                expiry_date  = self.inv_table.item(ri, 7).text().strip()
                status       = self.inv_table.item(ri, 8).text().strip().upper()

                qty = int(qty_text) if qty_text else orig["qty_available"]
                if qty < 0:
                    errors.append(f"Row {ri+1}: Qty Available must be >= 0")
                    continue
                valid_statuses = ("AVAILABLE", "ALLOCATED", "CONSUMED", "EXPIRED")
                if status not in valid_statuses:
                    errors.append(f"Row {ri+1}: Status must be one of {valid_statuses}")
                    continue

                prod_date   = None if prod_date in ("—", "") else prod_date
                expiry_date = None if expiry_date in ("—", "") else expiry_date

                updated = dict(orig)
                updated.update({
                    "qty_available":   qty,
                    "production_date": prod_date,
                    "expiry_date":     expiry_date,
                    "status":          status,
                })
                InventoryRepo.upsert(updated)
                saved += 1
            except Exception as e:
                errors.append(f"Row {ri + 1}: {e}")
        self._inv_changed_cells.clear()
        self._btn_inv_save.setEnabled(False)
        if errors:
            QMessageBox.warning(self, "Save Errors", "\n".join(errors))
        self._inv_status.setText(f"✅ Saved {saved} row(s)")
        self._inv_status.setStyleSheet("color:green; padding:4px;")
        self._load_inventory()

    def _load_allocations(self):
        from data.repositories import AllocationRepo
        rows = AllocationRepo.all_allocations()
        self.alloc_table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            for ci, v in enumerate([
                r["alloc_id"], r["so_number"], r["sku_code"],
                r["line_item"], r["lot_number"], r["qty_allocated"],
                r["allocated_at"][:16] if r.get("allocated_at") else "—",
                r.get("note") or ""
            ]):
                self.alloc_table.setItem(ri, ci, QTableWidgetItem(str(v)))

    def _upload(self):
        from utils.excel_io import upload_inventory
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload Inventory Excel", "", "Excel (*.xlsx)")
        if path:
            ok, msg = upload_inventory(path)
            (QMessageBox.information if ok else QMessageBox.warning)(
                self, "Upload", msg)
            if ok:
                self.refresh()

    def _template(self):
        from utils.excel_io import download_inventory_template
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Template", "Inventory_template.xlsx", "Excel (*.xlsx)")
        if path:
            ok, msg = download_inventory_template(path)
            (QMessageBox.information if ok else QMessageBox.warning)(
                self, "Template", msg)

    def _open_allocate(self):
        dlg = InventoryAllocationDialog(self)
        if dlg.exec():
            self.refresh()
            if self.main_window:
                self.main_window.notify("Inventory allocation saved.")

    def _deallocate(self):
        row = self.alloc_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Deallocate",
                                    "Select an allocation row first.")
            return
        alloc_id = int(self.alloc_table.item(row, 0).text())
        if QMessageBox.question(
            self, "Remove Allocation",
            f"Remove allocation #{alloc_id}?"
        ) == QMessageBox.StandardButton.Yes:
            from data.repositories import AllocationRepo
            AllocationRepo.deallocate(alloc_id)
            self.refresh()


# ─── Inventory Allocation Dialog ──────────────────────────────────────────────

class InventoryAllocationDialog(QDialog):
    """
    FEFO 자동 제안 후 플래너 컨펌 / 수동 변경 다이얼로그.
    1. SO 선택 → 필요 수량 표시
    2. FEFO 제안 자동 생성
    3. 플래너가 수량 조정 가능
    4. 확인 → AllocationRepo.confirm_fefo_suggestion()
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Allocate Inventory to SO")
        self.resize(820, 580)
        self._suggestion: list = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── SO 선택 ──
        so_grp = QGroupBox("1. Select SO-LineItem")
        sg_l   = QFormLayout(so_grp)

        self.so_combo = QComboBox()
        self._reload_sos()
        sg_l.addRow("SO / SKU / Line:", self.so_combo)

        self.so_info = QLabel()
        self.so_info.setStyleSheet("color:gray; font-size:11px;")
        sg_l.addRow("", self.so_info)

        btn_suggest = QPushButton("🔍 Generate FEFO Suggestion")
        btn_suggest.clicked.connect(self._suggest)
        sg_l.addRow("", btn_suggest)
        layout.addWidget(so_grp)

        # ── FEFO 제안 테이블 ──
        sug_grp = QGroupBox("2. FEFO Suggestion (editable)")
        sug_l   = QVBoxLayout(sug_grp)

        self.sug_table = QTableWidget()
        self.sug_table.setColumnCount(6)
        self.sug_table.setHorizontalHeaderLabels([
            "Inv ID", "LOT Number", "Expiry Date",
            "Lot Remaining", "Allocate Qty ✏", "Note ✏"
        ])
        self.sug_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.sug_table.setAlternatingRowColors(True)
        sug_l.addWidget(self.sug_table)

        self.sug_total_label = QLabel()
        self.sug_total_label.setStyleSheet("font-size:12px; padding:4px;")
        sug_l.addWidget(self.sug_total_label)

        # Manual override — add any lot
        add_bar = QHBoxLayout()
        add_bar.addWidget(QLabel("Add lot manually:"))
        self.manual_inv_combo = QComboBox()
        self.manual_inv_combo.setMinimumWidth(200)
        add_bar.addWidget(self.manual_inv_combo)
        self.manual_qty = QSpinBox()
        self.manual_qty.setRange(1, 999999)
        add_bar.addWidget(self.manual_qty)
        btn_add_manual = QPushButton("➕ Add")
        btn_add_manual.clicked.connect(self._add_manual_lot)
        add_bar.addWidget(btn_add_manual)
        add_bar.addStretch()
        sug_l.addLayout(add_bar)
        layout.addWidget(sug_grp, stretch=1)

        # ── Buttons ──
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText(
            "✅ Confirm Allocation")
        btns.accepted.connect(self._confirm)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _reload_sos(self):
        from data.repositories import SORepo
        self.so_combo.clear()
        sos = SORepo.all("OPEN")
        for so in sos:
            label = (f"{so['so_number']} / {so['sku_code']} / "
                     f"{so['line_item']}  (due: {so['due_date']})")
            self.so_combo.addItem(
                label,
                (so["so_number"], so["sku_code"], so["line_item"]))

    def _current_so(self):
        return self.so_combo.currentData()

    def _suggest(self):
        so_key = self._current_so()
        if not so_key:
            return
        so_no, sku, li = so_key
        from data.repositories import AllocationRepo, InventoryRepo, SORepo
        so = SORepo.get(so_no, sku, li)
        if not so:
            return

        prod_needed = AllocationRepo.production_needed(so_no, sku, li)
        already_alloc = AllocationRepo.total_allocated(so_no, sku, li)
        total_inv     = InventoryRepo.total_available(sku)

        self.so_info.setText(
            f"SO qty: {so['qty']}  |  Already allocated: {already_alloc}  |  "
            f"Prod needed: {prod_needed}  |  Inventory available: {total_inv}")

        # How much can we cover from inventory?
        cover = min(prod_needed, total_inv)
        if cover <= 0:
            QMessageBox.information(
                self, "No Inventory",
                f"No available inventory for {sku}.")
            return

        suggestion = InventoryRepo.fefo_suggestion(sku, cover)
        self._suggestion = suggestion
        self._load_suggestion_table(suggestion)

        # Populate manual override combo
        self.manual_inv_combo.clear()
        for lot in InventoryRepo.available_for_sku(sku):
            self.manual_inv_combo.addItem(
                f"{lot['lot_number']}  (rem: {lot['qty_remaining']}, "
                f"exp: {lot.get('expiry_date') or '—'})",
                lot)

    def _load_suggestion_table(self, suggestion):
        self.sug_table.setRowCount(len(suggestion))
        for ri, s in enumerate(suggestion):
            for ci, v in enumerate([
                s["inv_id"], s["lot_number"],
                s.get("expiry_date") or "—",
                s["qty_remaining"],
                s["qty_to_allocate"],  # editable
                ""                     # note editable
            ]):
                item = QTableWidgetItem(str(v))
                if ci == 4:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    item.setBackground(QBrush(QColor("#fffbe6")))
                elif ci == 5:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.sug_table.setItem(ri, ci, item)
        self._update_total_label()

    def _update_total_label(self):
        total = 0
        for ri in range(self.sug_table.rowCount()):
            try:
                total += int(self.sug_table.item(ri, 4).text())
            except (ValueError, AttributeError):
                pass
        so_key = self._current_so()
        if so_key:
            from data.repositories import AllocationRepo
            needed = AllocationRepo.production_needed(*so_key)
            color = "green" if total >= needed else "orange"
            self.sug_total_label.setText(
                f"Total to allocate: <b style='color:{color}'>{total}</b>  "
                f"/ Prod needed: {needed}")
        else:
            self.sug_total_label.setText(f"Total to allocate: {total}")

    def _add_manual_lot(self):
        lot_data = self.manual_inv_combo.currentData()
        if not lot_data:
            return
        qty = self.manual_qty.value()
        # Check if already in table
        for ri in range(self.sug_table.rowCount()):
            if self.sug_table.item(ri, 0).text() == str(lot_data["inv_id"]):
                # Update existing row qty
                existing = int(self.sug_table.item(ri, 4).text() or 0)
                self.sug_table.item(ri, 4).setText(str(existing + qty))
                self._update_total_label()
                return
        # Add new row
        ri = self.sug_table.rowCount()
        self.sug_table.setRowCount(ri + 1)
        for ci, v in enumerate([
            lot_data["inv_id"], lot_data["lot_number"],
            lot_data.get("expiry_date") or "—",
            lot_data["qty_remaining"], qty, ""
        ]):
            item = QTableWidgetItem(str(v))
            if ci in (4, 5):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                if ci == 4:
                    item.setBackground(QBrush(QColor("#fffbe6")))
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sug_table.setItem(ri, ci, item)
        self._update_total_label()

    def _confirm(self):
        so_key = self._current_so()
        if not so_key:
            QMessageBox.warning(self, "Error", "Select an SO first.")
            return
        so_no, sku, li = so_key

        allocations = []
        for ri in range(self.sug_table.rowCount()):
            try:
                inv_id = int(self.sug_table.item(ri, 0).text())
                lot    = self.sug_table.item(ri, 1).text()
                qty    = int(self.sug_table.item(ri, 4).text())
                note_i = self.sug_table.item(ri, 5)
                note   = note_i.text() if note_i else None
            except (ValueError, AttributeError):
                continue
            if qty <= 0:
                continue
            # Validate against lot remaining
            from data.repositories import InventoryRepo
            lot_data = InventoryRepo.get(inv_id)
            if not lot_data:
                continue
            if qty > lot_data["qty_remaining"]:
                QMessageBox.warning(
                    self, "Qty Error",
                    f"LOT {lot}: allocate qty {qty} exceeds "
                    f"remaining {lot_data['qty_remaining']}.")
                return
            allocations.append({
                "inv_id":          inv_id,
                "lot_number":      lot,
                "qty_to_allocate": qty,
                "note":            note,
            })

        if not allocations:
            QMessageBox.warning(self, "Error",
                                "No valid allocation rows.")
            return

        from data.repositories import AllocationRepo
        AllocationRepo.confirm_fefo_suggestion(so_no, sku, li, allocations)
        QMessageBox.information(
            self, "Allocated",
            f"Allocated {len(allocations)} lot(s) to "
            f"{so_no}/{sku}/{li}.")
        self.accept()


# ════════════════════════════════════════════════════════════════════════════
#  RELEASE REPORT TAB
# ════════════════════════════════════════════════════════════════════════════

class ReleaseReportTab(QWidget):
    """
    SO / SKU / LineItem별 예상 릴리즈 일정 리포트.

    계산 로직:
      - 생산계획의 마지막 공정(is_final_seq=1) 완료 예정 Shift 기준
      - Release Date = 마지막 공정 완료일 + SKU.post_lead_days
      - Due Date와 비교해서 ON TIME / AT RISK / LATE 상태 표시

    컬럼:
      SO | SKU | Line | SO Qty | Prod Needed | Planned Qty |
      Last Process Date | Last Shift | Release Date | Due Date |
      Days to Due | Status | Note
    """

    STATUS_ON_TIME = "ON TIME"
    STATUS_AT_RISK = "AT RISK"   # release within 3 days of due
    STATUS_LATE    = "LATE"
    STATUS_NO_PLAN = "NOT PLANNED"

    AT_RISK_DAYS = 3             # configurable per instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._rows: list = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Toolbar ──
        bar = QHBoxLayout()

        bar.addWidget(QLabel("Filter:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("SO / SKU / line item…")
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search)

        bar.addWidget(QLabel("Status:"))
        self.status_filter = QComboBox()
        self.status_filter.addItems(
            ["ALL", "LATE", "AT RISK", "ON TIME", "NOT PLANNED"])
        self.status_filter.currentTextChanged.connect(self._apply_filter)
        bar.addWidget(self.status_filter)

        bar.addWidget(QLabel("Due within (days):"))
        self.due_days_spin = QSpinBox()
        self.due_days_spin.setRange(0, 365)
        self.due_days_spin.setValue(0)
        self.due_days_spin.setSpecialValueText("All")
        self.due_days_spin.valueChanged.connect(self._apply_filter)
        bar.addWidget(self.due_days_spin)

        bar.addStretch()

        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(btn_refresh)

        btn_export = QPushButton("📥 Export")
        btn_export.clicked.connect(self._export)
        bar.addWidget(btn_export)

        layout.addLayout(bar)

        # ── Summary strip ──
        self.summary = QLabel()
        self.summary.setStyleSheet(
            "padding:5px 8px; font-size:12px; "
            "background:var(--surface); border-bottom:1px solid #ddd;")
        layout.addWidget(self.summary)

        # ── Table ──
        self.table = QTableWidget()
        self.table.setColumnCount(13)
        self.table.setHorizontalHeaderLabels([
            "SO Number", "SKU Code", "Line Item",
            "SO Qty", "Prod Needed", "Planned Qty",
            "Last Process Date", "Shift",
            "Release Date", "Due Date",
            "Days to Due", "Status", "Note"
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, stretch=1)

        self.refresh()

    # ── Data computation ─────────────────────────────────────────────────────

    def refresh(self):
        self._rows = self._compute_rows()
        self._apply_filter()

    def _compute_rows(self) -> list:
        from datetime import date as dt, datetime as dtt, timedelta
        from data.repositories import (
            SORepo, PlanRepo, SKURepo, ActualRepo, AllocationRepo
        )

        today = dt.today()
        sos   = SORepo.all()
        skus  = {s["sku_code"]: s for s in SKURepo.all()}
        rows  = []

        for so in sos:
            so_no = so["so_number"]
            sku_c = so["sku_code"]
            li    = so["line_item"]
            sku   = skus.get(sku_c, {})
            post_lead = int(sku.get("post_lead_days") or 0)

            # Quantities
            prod_needed = AllocationRepo.production_needed(so_no, sku_c, li)
            planned_qty = PlanRepo.planned_qty(so_no, sku_c, li)

            # Find last FINAL plan for this SO-LineItem
            plans = PlanRepo.for_so(so_no, sku_c, li)
            final_plans = [p for p in plans if p.get("is_final_seq")]
            if not final_plans:
                # Fall back to any plan if no final flagged
                final_plans = plans

            if final_plans:
                last_plan = max(
                    final_plans,
                    key=lambda p: (p["plan_date"], p["shift_no"]))
                last_date  = last_plan["plan_date"]
                last_shift = last_plan["shift_no"]
                last_dt    = dtt.strptime(last_date, "%Y-%m-%d").date()
                release_dt = last_dt + timedelta(days=post_lead)
                release_str = release_dt.strftime("%Y-%m-%d")
                last_date_str = last_date
            else:
                last_date_str = "—"
                last_shift    = "—"
                release_dt    = None
                release_str   = "—"

            due_dt  = dtt.strptime(so["due_date"], "%Y-%m-%d").date()

            # Status
            if release_dt is None:
                status     = self.STATUS_NO_PLAN
                days_to_due = ""
            else:
                days_to_due = (due_dt - release_dt).days
                if release_dt > due_dt:
                    status = self.STATUS_LATE
                elif days_to_due <= self.AT_RISK_DAYS:
                    status = self.STATUS_AT_RISK
                else:
                    status = self.STATUS_ON_TIME

            # Note: flag closed/hold
            note = ""
            if so["status"] == "HOLD":
                note = "⏸ HOLD"
            elif so["status"] == "CLOSED":
                note = "✔ CLOSED"
            elif prod_needed == 0 and planned_qty == 0:
                note = "Covered by inventory"

            rows.append({
                "so_number":    so_no,
                "sku_code":     sku_c,
                "line_item":    li,
                "so_qty":       so["qty"],
                "prod_needed":  prod_needed,
                "planned_qty":  planned_qty,
                "last_date":    last_date_str,
                "last_shift":   str(last_shift),
                "release_date": release_str,
                "due_date":     so["due_date"],
                "days_to_due":  days_to_due,
                "status":       status,
                "note":         note,
                "so_status":    so["status"],
                "release_dt":   release_dt,
                "due_dt":       due_dt,
            })

        return rows

    # ── Filter & render ──────────────────────────────────────────────────────

    def _apply_filter(self):
        from datetime import date as dt, timedelta
        search     = self.search.text().lower()
        status_f   = self.status_filter.currentText()
        due_days   = self.due_days_spin.value()
        today      = dt.today()

        filtered = self._rows
        if search:
            filtered = [r for r in filtered if search in (
                r["so_number"] + r["sku_code"] + r["line_item"]).lower()]
        if status_f != "ALL":
            filtered = [r for r in filtered if r["status"] == status_f]
        if due_days > 0:
            cutoff = today + timedelta(days=due_days)
            filtered = [r for r in filtered
                        if r["due_dt"] <= cutoff]

        self._render(filtered)
        self._update_summary(filtered)

    def _render(self, rows: list):
        # Disable sorting while filling to avoid index issues
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))

        STATUS_BG = {
            self.STATUS_LATE:     QColor("#ffcccc"),
            self.STATUS_AT_RISK:  QColor("#fff0cc"),
            self.STATUS_ON_TIME:  QColor("#d4f0c0"),
            self.STATUS_NO_PLAN:  QColor("#e8e8e8"),
        }
        STATUS_FG = {
            self.STATUS_LATE:     QColor("#cc0000"),
            self.STATUS_AT_RISK:  QColor("#996600"),
            self.STATUS_ON_TIME:  QColor("#2d7a2d"),
            self.STATUS_NO_PLAN:  QColor("#666666"),
        }

        for ri, r in enumerate(rows):
            bg = STATUS_BG.get(r["status"], QColor("white"))
            fg = STATUS_FG.get(r["status"], QColor("black"))

            vals = [
                r["so_number"], r["sku_code"], r["line_item"],
                r["so_qty"], r["prod_needed"], r["planned_qty"],
                r["last_date"], r["last_shift"],
                r["release_date"], r["due_date"],
                str(r["days_to_due"]) if r["days_to_due"] != "" else "—",
                r["status"], r["note"],
            ]
            for ci, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                # Status column — coloured text + bg
                if ci == 11:
                    item.setBackground(QBrush(bg))
                    item.setForeground(QBrush(fg))
                    font = QFont(); font.setBold(True)
                    item.setFont(font)
                # Days-to-due — colour by value
                elif ci == 10 and r["days_to_due"] != "":
                    try:
                        d = int(r["days_to_due"])
                        if d < 0:
                            item.setBackground(QBrush(QColor("#ffcccc")))
                        elif d <= self.AT_RISK_DAYS:
                            item.setBackground(QBrush(QColor("#fff0cc")))
                    except ValueError:
                        pass
                # Release date — bold if late
                elif ci == 8 and r["status"] == self.STATUS_LATE:
                    item.setBackground(QBrush(QColor("#ffcccc")))
                    font = QFont(); font.setBold(True)
                    item.setFont(font)
                # Dim closed/hold rows
                if r["so_status"] in ("CLOSED", "HOLD") and ci != 11:
                    item.setForeground(QBrush(QColor("#999999")))

                self.table.setItem(ri, ci, item)

        self.table.setSortingEnabled(True)

    def _update_summary(self, rows: list):
        total    = len(rows)
        late     = sum(1 for r in rows if r["status"] == self.STATUS_LATE)
        at_risk  = sum(1 for r in rows if r["status"] == self.STATUS_AT_RISK)
        on_time  = sum(1 for r in rows if r["status"] == self.STATUS_ON_TIME)
        no_plan  = sum(1 for r in rows if r["status"] == self.STATUS_NO_PLAN)

        parts = [f"Total: <b>{total}</b>"]
        if late:
            parts.append(
                f"<span style='color:#cc0000'>🔴 LATE: <b>{late}</b></span>")
        if at_risk:
            parts.append(
                f"<span style='color:#996600'>🟡 AT RISK: <b>{at_risk}</b></span>")
        if on_time:
            parts.append(
                f"<span style='color:#2d7a2d'>🟢 ON TIME: <b>{on_time}</b></span>")
        if no_plan:
            parts.append(
                f"<span style='color:#666'>⚪ NOT PLANNED: <b>{no_plan}</b></span>")

        self.summary.setText("  |  ".join(parts))

    # ── Export ───────────────────────────────────────────────────────────────

    def _export(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
            from PyQt6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Release Report",
                "Release_Report.xlsx", "Excel (*.xlsx)")
            if not path:
                return

            wb = Workbook(); ws = wb.active
            ws.title = "Release Report"

            HDR_FILL = PatternFill("solid", fgColor="4472C4")
            HDR_FONT = Font(color="FFFFFF", bold=True)
            STATUS_FILLS = {
                "LATE":        PatternFill("solid", fgColor="FFCCCC"),
                "AT RISK":     PatternFill("solid", fgColor="FFF0CC"),
                "ON TIME":     PatternFill("solid", fgColor="D4F0C0"),
                "NOT PLANNED": PatternFill("solid", fgColor="E8E8E8"),
            }

            headers = [
                "SO Number", "SKU Code", "Line Item",
                "SO Qty", "Prod Needed", "Planned Qty",
                "Last Process Date", "Shift",
                "Release Date", "Due Date",
                "Days to Due", "Status", "Note"
            ]
            for ci, h in enumerate(headers, 1):
                c = ws.cell(row=1, column=ci, value=h)
                c.fill = HDR_FILL; c.font = HDR_FONT
                c.alignment = Alignment(horizontal="center")

            for ri, r in enumerate(self._rows, 2):
                vals = [
                    r["so_number"], r["sku_code"], r["line_item"],
                    r["so_qty"], r["prod_needed"], r["planned_qty"],
                    r["last_date"], r["last_shift"],
                    r["release_date"], r["due_date"],
                    r["days_to_due"] if r["days_to_due"] != "" else None,
                    r["status"], r["note"],
                ]
                for ci, v in enumerate(vals, 1):
                    cell = ws.cell(row=ri, column=ci, value=v)
                    if r["status"] in STATUS_FILLS:
                        cell.fill = STATUS_FILLS[r["status"]]

            # Auto column width
            for col in ws.columns:
                max_len = max(
                    (len(str(c.value)) for c in col if c.value), default=8)
                ws.column_dimensions[col[0].column_letter].width = \
                    min(max_len + 2, 30)

            wb.save(path)
            QMessageBox.information(
                self, "Export", f"Saved to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))


# ════════════════════════════════════════════════════════════════════════════
#  HC DEMAND DIALOG (생산실별 인력 자동배정)
# ════════════════════════════════════════════════════════════════════════════

class HCDemandDialog(QDialog):
    """
    현재 생산계획을 기반으로 Shift별 필요 총 HC를 계산하고
    CRP Excel에 반영할 수 있는 다이얼로그.

    - 각 생산실/공정의 필요 HC를 계산 → Shift별로 합산
    - 현재 CRP 총 HC와 비교, 차이가 있는 행 강조
    - 체크 후 Apply → CRP Excel의 ShiftNo별 총 HC 업데이트
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HC Recommendation — Shift Total")
        self.setMinimumSize(780, 500)
        self.to_apply: dict = {}
        self._build_ui()

    def _build_ui(self):
        from core.scheduler import scheduler, _shift_hours
        layout = QVBoxLayout(self)

        info = QLabel(
            "현재 생산계획 기반으로 Shift별 필요 총 인원을 계산합니다. "
            "CRP Excel에는 생산실/공정별이 아닌 Shift 총원만 입력하면 "
            "시스템이 자동으로 배분합니다.\n"
            "적용할 행을 체크한 뒤 Apply를 누르면 CRP Excel에 반영됩니다.")
        info.setWordWrap(True)
        info.setStyleSheet("padding:6px; background:#e8f0fe; border-radius:4px;")
        layout.addWidget(info)

        # ── Compute recommended total HC per (date, shift) ────────────────────
        plans = PlanRepo.all()
        shifts = {s["shift_no"]: s for s in ShiftRepo.all()}
        room_proc_map = {
            (rp["room_code"], rp["process_name"]): rp
            for rp in RoomRepo.all()
        }

        # Aggregate inner qty per (date, room, proc, shift)
        slot_qty: dict = {}
        for p in plans:
            key = (p["plan_date"], p["room_code"], p["process_name"], p["shift_no"])
            uom = 1
            if p.get("sku_code"):
                sku = SKURepo.get(p["sku_code"])
                if sku:
                    uom = int(sku.get("uom") or 1)
            slot_qty[key] = slot_qty.get(key, 0) + p["qty_planned"] * uom

        # Required HC per room/process/shift → sum to total per (date, shift)
        shift_req: dict = {}   # (date, shift) -> total required HC
        for (d, room, proc, sno), inner_qty in slot_qty.items():
            rp = room_proc_map.get((room, proc))
            sh = shifts.get(sno)
            if not rp or not sh:
                continue
            sh_h = _shift_hours(sh)
            if rp["process_type"] == "AUTO":
                req_hc = int(rp.get("hc_fixed") or 1)
            else:
                upph = float(rp.get("upph") or 0)
                if upph <= 0 or sh_h <= 0:
                    req_hc = int(rp.get("hc_min") or 1)
                else:
                    req_hc = math.ceil(inner_qty / (upph * sh_h))
                    req_hc = max(int(rp.get("hc_min") or 1),
                                 min(int(rp.get("hc_max") or 999), req_hc))
            shift_req[(d, sno)] = shift_req.get((d, sno), 0) + req_hc

        rows = []
        for (d, sno), req_total in sorted(shift_req.items()):
            cur_total = crp_manager.get_total_hc(d, sno)
            rows.append({
                "date": d, "shift": sno,
                "cur_hc": cur_total, "req_hc": req_total,
                "diff": req_total - cur_total,
            })

        # ── Table ─────────────────────────────────────────────────────────────
        self.table = QTableWidget(len(rows), 6)
        self.table.setHorizontalHeaderLabels([
            "Apply", "Date", "Shift",
            "Current CRP Total HC", "Recommended Total HC", "Diff"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        self._rows_data = rows
        self._checks: list = []
        for ri, r in enumerate(rows):
            cb = QCheckBox()
            cb.setChecked(r["diff"] != 0)
            self._checks.append(cb)
            self.table.setCellWidget(ri, 0, cb)
            self.table.setItem(ri, 1, QTableWidgetItem(r["date"]))
            self.table.setItem(ri, 2, QTableWidgetItem(f"Shift {r['shift']}"))
            self.table.setItem(ri, 3, QTableWidgetItem(str(r["cur_hc"])))
            self.table.setItem(ri, 4, QTableWidgetItem(str(r["req_hc"])))
            diff_item = QTableWidgetItem(f"{r['diff']:+d}")
            if r["diff"] > 0:
                diff_item.setForeground(QColor("#c62828"))
            elif r["diff"] < 0:
                diff_item.setForeground(QColor("#2e7d32"))
            self.table.setItem(ri, 5, diff_item)

            row_bg = QColor("#fff9c4") if r["diff"] != 0 else QColor("#f5f5f5")
            for ci in range(1, 6):
                if self.table.item(ri, ci):
                    self.table.item(ri, ci).setBackground(QBrush(row_bg))

        layout.addWidget(self.table, stretch=1)

        # ── Buttons ───────────────────────────────────────────────────────────
        bbar = QHBoxLayout()
        btn_all  = QPushButton("Check All")
        btn_none = QPushButton("Uncheck All")
        btn_all.clicked.connect(lambda: [cb.setChecked(True) for cb in self._checks])
        btn_none.clicked.connect(lambda: [cb.setChecked(False) for cb in self._checks])
        bbar.addWidget(btn_all)
        bbar.addWidget(btn_none)
        bbar.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_apply  = QPushButton("💾 Apply to CRP Excel")
        btn_apply.setStyleSheet(
            "background:#2e6fd8; color:white; font-weight:bold; padding:6px 16px;")
        btn_cancel.clicked.connect(self.reject)
        btn_apply.clicked.connect(self._apply)
        bbar.addWidget(btn_cancel)
        bbar.addWidget(btn_apply)
        layout.addLayout(bbar)

        if not rows:
            info.setText("현재 생산계획이 없습니다. Auto-Plan을 먼저 실행하세요.")

    def _apply(self):
        self.to_apply = {}
        for r, cb in zip(self._rows_data, self._checks):
            if cb.isChecked():
                self.to_apply[(r["date"], r["shift"])] = r["req_hc"]
        if not self.to_apply:
            QMessageBox.information(self, "Apply", "체크된 항목이 없습니다.")
            return
        self.accept()
