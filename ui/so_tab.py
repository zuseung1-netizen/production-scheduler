"""
Sales Order management tab.
- SO list table with filtering
- Upload / Download / Export
- History & Rollback
- Priority editing, Hold toggle, Start-no-earlier input
- Planned completion time & Release date columns
"""
from __future__ import annotations

import os
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QComboBox, QLineEdit, QFileDialog,
    QMessageBox, QDialog, QDialogButtonBox, QTextEdit, QSpinBox,
    QDateEdit, QCheckBox, QGroupBox, QSplitter, QAbstractItemView,
    QHeaderView, QTabWidget, QMenu
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor, QBrush, QCursor

from data.repositories import SORepo, PlanRepo, ActualRepo, SKURepo, InventoryRepo, AllocationRepo
from utils.excel_io import upload_so, preview_so_upload, download_so_template, export_all


STATUS_COLORS = {
    "OPEN":   QColor("#d4f0c0"),
    "HOLD":   QColor("#ffe0a0"),
    "CLOSED": QColor("#d0d0d0"),
}

LATE_COLOR = QColor("#ffcccc")


class SOTab(QWidget):
    # 0=SO, 1=SKU, 2=Line, 3=Customer, 4=Qty, 5=PlannedQty, 6=ActualQty,
    # 7=Inventory(RO), 8=Due(Req), 9=CommittedDue, 10=Priority, 11=Status,
    # 12=ProdCompletion, 13=Release, 14=ReceivedAt, 15=Note, 16=StartNoEarlier
    _EDITABLE_COLS  = {3, 4, 8, 9, 10, 11, 15, 16}
    _READONLY_COLS  = {0, 1, 2, 5, 6, 7, 12, 13, 14}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._edit_mode = False
        self._changed_cells: set = set()
        self._loading = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── filter bar ──
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Status:"))
        self.filter_status = QComboBox()
        self.filter_status.addItems(["ALL", "OPEN", "HOLD", "CLOSED"])
        self.filter_status.currentTextChanged.connect(self.refresh)
        fbar.addWidget(self.filter_status)

        fbar.addWidget(QLabel("Search:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("SO / SKU / Line…")
        self.search_box.textChanged.connect(self.refresh)
        fbar.addWidget(self.search_box)
        fbar.addStretch()

        btn_upload   = QPushButton("📤 Upload SO")
        btn_template = QPushButton("⬇ Download Template")
        btn_export   = QPushButton("📥 Export All")
        btn_rollback = QPushButton("↩ Rollback")

        btn_upload.clicked.connect(self._upload)
        btn_template.clicked.connect(self._download_template)
        btn_export.clicked.connect(self._export)
        btn_rollback.clicked.connect(self._rollback)

        for b in (btn_upload, btn_template, btn_export, btn_rollback):
            fbar.addWidget(b)

        btn_bulk_alloc = QPushButton("📦 Bulk Allocate")
        btn_bulk_alloc.setStyleSheet(
            "background:#1976d2; color:white; font-weight:bold; padding:5px 12px;")
        btn_bulk_alloc.clicked.connect(self._open_bulk_allocation)
        fbar.addWidget(btn_bulk_alloc)

        self._btn_edit = QPushButton("✏ Edit Mode")
        self._btn_edit.clicked.connect(self._toggle_edit_mode)
        self._btn_save = QPushButton("💾 Save Changes")
        self._btn_save.clicked.connect(self._save_changes)
        self._btn_save.setEnabled(False)
        fbar.addWidget(self._btn_edit)
        fbar.addWidget(self._btn_save)

        layout.addLayout(fbar)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # ── splitter: table + history ──
        splitter = QSplitter(Qt.Orientation.Vertical)

        # SO table
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.doubleClicked.connect(self._edit_row)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemChanged.connect(self._on_cell_changed)

        cols = ["SO Number", "SKU Code", "Line", "Customer", "Qty", "Planned Qty",
                "Actual Qty", "📦 Inventory", "Due Date (Req)", "Committed Due", "Priority", "Status",
                "Prod. Completion", "Release Date", "Received At", "Note", "Start No Earlier"]
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.table)

        # History table
        hist_grp = QGroupBox("SO Change History")
        hist_layout = QVBoxLayout(hist_grp)
        self.hist_table = QTableWidget()
        self.hist_table.setColumnCount(7)
        self.hist_table.setHorizontalHeaderLabels(
            ["Batch", "SO", "SKU", "Line", "Change", "Old", "New"])
        self.hist_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.hist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hist_layout.addWidget(self.hist_table)
        splitter.addWidget(hist_grp)
        splitter.setSizes([600, 200])
        layout.addWidget(splitter, stretch=1)

        self.refresh()

    def refresh(self):
        self._load_table()
        self._load_history()

    def _load_table(self):
        self._loading = True
        self._changed_cells.clear()
        self._btn_save.setEnabled(False)
        status_filter = self.filter_status.currentText()
        search = self.search_box.text().lower()
        sos = SORepo.all(None if status_filter == "ALL" else status_filter)
        if search:
            sos = [s for s in sos if search in (s["so_number"] + s["sku_code"] + s["line_item"]).lower()]

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(sos))
        today = date.today()

        # Batch allocation query — avoids N+1
        alloc_map = AllocationRepo.allocation_summary_for_open_sos()

        for ri, so in enumerate(sos):
            planned = PlanRepo.planned_qty(so["so_number"], so["sku_code"], so["line_item"])
            actual  = ActualRepo.actual_qty(so["so_number"], so["sku_code"], so["line_item"])

            # Compute planned completion (last plan date)
            plans = PlanRepo.for_so(so["so_number"], so["sku_code"], so["line_item"])
            if plans:
                last_plan = max(plans, key=lambda p: (p["plan_date"], p["shift_no"]))
                prod_complete = f"{last_plan['plan_date']} S{last_plan['shift_no']}"
            else:
                prod_complete = "-"

            # Release date = prod_complete + post_lead_days
            sku_data = SKURepo.get(so["sku_code"])
            post_lead = int(sku_data["post_lead_days"]) if sku_data else 0
            if plans and post_lead:
                rel_date = (datetime.strptime(last_plan["plan_date"], "%Y-%m-%d").date()
                            + timedelta(days=post_lead)).strftime("%Y-%m-%d")
            else:
                rel_date = "-"

            # Inventory allocation summary (col 7)
            key = (so["so_number"], so["sku_code"], so["line_item"])
            allocated = alloc_map.get(key, 0)
            so_qty    = so["qty"]
            if allocated == 0:
                inv_text = "—"
                inv_bg   = None
            elif allocated >= so_qty:
                inv_text = "✅ FULL"
                inv_bg   = QColor("#e8f5e9")
            else:
                inv_text = f"{allocated}/{so_qty}"
                inv_bg   = QColor("#fff3e0")

            values = [
                so["so_number"], so["sku_code"], so["line_item"],
                so.get("customer_name") or "",
                so["qty"], planned, actual,
                inv_text,                                          # col 7 Inventory
                so["due_date"],
                so.get("committed_due_date") or "",
                so["priority"] if so["priority"] is not None else "",
                so["status"],
                prod_complete, rel_date,
                so["received_at"][:10] if so["received_at"] else "",
                so["note"] or "",
                so.get("start_no_earlier") or "",
            ]
            for ci, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                if ci in self._READONLY_COLS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if ci == 0:
                    item.setData(Qt.ItemDataRole.UserRole, so)
                self.table.setItem(ri, ci, item)

            # Row colouring — use committed_due_date if set, else requested
            check_due = so.get("committed_due_date") or so["due_date"]
            is_late = (so["status"] == "OPEN" and
                       datetime.strptime(check_due, "%Y-%m-%d").date() < today and
                       actual < so["qty"])
            bg = LATE_COLOR if is_late else STATUS_COLORS.get(so["status"], QColor("white"))
            for ci in range(self.table.columnCount()):
                self.table.item(ri, ci).setBackground(QBrush(bg))
            # Inventory cell gets its own colour on top of row colour
            if inv_bg and not is_late:
                self.table.item(ri, 7).setBackground(QBrush(inv_bg))

        self.table.setSortingEnabled(True)
        self._loading = False

    def _load_history(self):
        rows = SORepo.history()
        self.hist_table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            for ci, val in enumerate([
                r["upload_batch"], r["so_number"], r["sku_code"],
                r["line_item"], r["change_type"], r["old_value"], r["new_value"]
            ]):
                self.hist_table.setItem(ri, ci, QTableWidgetItem(str(val or "")))

    def _context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        so_no  = self.table.item(row, 0).text()
        sku    = self.table.item(row, 1).text()
        li     = self.table.item(row, 2).text()
        status = self.table.item(row, 11).text()  # col 11 = Status

        menu = QMenu(self)
        menu.addAction("✏ Edit",    lambda: self._edit_row())
        if status != "HOLD":
            menu.addAction("⏸ Hold",  lambda: self._set_hold(so_no, sku, li, True))
        else:
            menu.addAction("▶ Unhold", lambda: self._set_hold(so_no, sku, li, False))
        if status in ("OPEN", "HOLD"):
            menu.addAction("✂ Split", lambda: self._split_so(so_no, sku, li))
        menu.addAction("🚫 Close",  lambda: self._close_so(so_no, sku, li))
        menu.addAction("⭐ Set Priority", lambda: self._set_priority(so_no, sku, li))
        menu.addAction("📅 Set Start-No-Earlier", lambda: self._set_start_date(so_no, sku, li))
        menu.addSeparator()
        menu.addAction("📦 Allocate Inventory", lambda: self._open_inv_allocation(so_no, sku, li))
        menu.exec(QCursor.pos())

    def _edit_row(self):
        if self._edit_mode:
            return
        row = self.table.currentRow()
        if row < 0:
            return
        so_no = self.table.item(row, 0).text()
        sku   = self.table.item(row, 1).text()
        li    = self.table.item(row, 2).text()
        so    = SORepo.get(so_no, sku, li)
        if not so:
            return
        dlg = SOEditDialog(so, self)
        if dlg.exec():
            SORepo.upsert(dlg.result)
            self.refresh()

    def _toggle_edit_mode(self):
        self._edit_mode = not self._edit_mode
        if self._edit_mode:
            self._btn_edit.setText("🔒 Exit Edit Mode")
            self._btn_edit.setStyleSheet(
                "background:#e65100; color:white; font-weight:bold;")
            self.table.setSortingEnabled(False)
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked |
                QAbstractItemView.EditTrigger.EditKeyPressed)
            self._status_label.setText(
                "✏ Edit mode — Customer, Qty, Due Date, Priority, Status, Note are editable")
            self._status_label.setStyleSheet(
                "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        else:
            self._btn_edit.setText("✏ Edit Mode")
            self._btn_edit.setStyleSheet("")
            self.table.setSortingEnabled(True)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            if not self._changed_cells:
                self._status_label.setText("")
                self._status_label.setStyleSheet("")

    def _on_cell_changed(self, item):
        if self._loading:
            return
        if item.column() not in self._EDITABLE_COLS:
            return
        item.setBackground(QBrush(QColor("#fff9c4")))
        self._changed_cells.add((item.row(), item.column()))
        n = len({r for r, _ in self._changed_cells})
        self._status_label.setText(f"✏ {n} row(s) modified — unsaved")
        self._status_label.setStyleSheet(
            "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        self._btn_save.setEnabled(True)

    def _save_changes(self):
        if not self._changed_cells:
            return
        changed_rows = {r for r, _ in self._changed_cells}
        saved, errors = 0, []
        for ri in sorted(changed_rows):
            try:
                orig = self.table.item(ri, 0).data(Qt.ItemDataRole.UserRole)
                if not orig:
                    continue
                customer          = self.table.item(ri, 3).text().strip() or None
                qty_text          = self.table.item(ri, 4).text().strip()
                due_date          = self.table.item(ri, 8).text().strip()
                committed_due     = self.table.item(ri, 9).text().strip() or None
                pri_text          = self.table.item(ri, 10).text().strip()
                status            = self.table.item(ri, 11).text().strip().upper()
                note              = self.table.item(ri, 15).text().strip() or None
                start_no_earlier  = self.table.item(ri, 16).text().strip() or None

                qty = int(qty_text) if qty_text else orig["qty"]
                if qty <= 0:
                    errors.append(f"Row {ri+1}: Qty must be > 0")
                    continue
                if status not in ("OPEN", "HOLD", "CLOSED"):
                    errors.append(f"Row {ri+1}: Status must be OPEN/HOLD/CLOSED")
                    continue
                try:
                    datetime.strptime(due_date, "%Y-%m-%d")
                except ValueError:
                    errors.append(f"Row {ri+1}: Due Date format must be YYYY-MM-DD")
                    continue
                if committed_due:
                    try:
                        datetime.strptime(committed_due, "%Y-%m-%d")
                    except ValueError:
                        errors.append(f"Row {ri+1}: Committed Due format must be YYYY-MM-DD")
                        continue
                if start_no_earlier:
                    try:
                        datetime.strptime(start_no_earlier, "%Y-%m-%d")
                    except ValueError:
                        errors.append(f"Row {ri+1}: Start No Earlier format must be YYYY-MM-DD")
                        continue
                try:
                    pri = int(pri_text) if pri_text else None
                    if pri is not None and pri <= 0:
                        pri = None
                except ValueError:
                    pri = None

                updated = dict(orig)
                updated.update({
                    "customer_name":      customer,
                    "qty":                qty,
                    "due_date":           due_date,
                    "committed_due_date": committed_due,
                    "priority":           pri,
                    "status":             status,
                    "note":               note,
                    "start_no_earlier":   start_no_earlier,
                })
                SORepo.upsert(updated)
                saved += 1
            except Exception as e:
                errors.append(f"Row {ri + 1}: {e}")
        self._changed_cells.clear()
        self._btn_save.setEnabled(False)
        if errors:
            QMessageBox.warning(self, "Save Errors", "\n".join(errors))
        self._status_label.setText(f"✅ Saved {saved} row(s)")
        self._status_label.setStyleSheet("color:green; padding:4px;")
        self._load_table()
        if self.main_window and hasattr(self.main_window, "gantt_tab"):
            self.main_window.gantt_tab.refresh()

    def _set_hold(self, so_no, sku, li, hold: bool):
        SORepo.hold(so_no, sku, li, hold)
        self.refresh()

    def _close_so(self, so_no, sku, li):
        if QMessageBox.question(self, "Close SO", f"Close {so_no}/{sku}/{li}?") == QMessageBox.StandardButton.Yes:
            SORepo.close(so_no, sku, li)
            self.refresh()

    def _set_priority(self, so_no, sku, li):
        so = SORepo.get(so_no, sku, li)
        cur = so["priority"] if so and so["priority"] is not None else 0
        val, ok = QSpinBox().value, False
        from PyQt6.QtWidgets import QInputDialog
        val, ok = QInputDialog.getInt(self, "Priority", "Priority (lower = higher):", cur, 0, 9999)
        if ok:
            SORepo.set_priority(so_no, sku, li, val if val > 0 else None)
            self.refresh()

    def _set_start_date(self, so_no, sku, li):
        from PyQt6.QtWidgets import QInputDialog
        so = SORepo.get(so_no, sku, li)
        cur = so.get("start_no_earlier", "") or ""
        val, ok = QInputDialog.getText(self, "Start No Earlier", "YYYY-MM-DD:", text=cur)
        if ok:
            SORepo.upsert({**so, "start_no_earlier": val or None})
            self.refresh()

    def _open_inv_allocation(self, so_no, sku, li):
        dlg = SOInventoryAllocationDialog(so_no, sku, li, self)
        if dlg.exec():
            self.refresh()
            if self.main_window:
                self.main_window.notify(f"Inventory allocated: {so_no}/{sku}/{li}")

    def _open_bulk_allocation(self):
        dlg = BulkAllocationDialog(self)
        dlg.exec()
        self.refresh()

    def _upload(self):
        path, _ = QFileDialog.getOpenFileName(self, "Upload SO Excel", "", "Excel (*.xlsx *.xls)")
        if not path:
            return

        # Step 1: preview diff without writing to DB
        ok, err_msg, preview = preview_so_upload(path)
        if not ok:
            QMessageBox.warning(self, "Preview Failed", err_msg)
            return

        s = preview["summary"]
        if s["new"] + s["modified"] + s["closed"] + s["unchanged"] == 0:
            QMessageBox.information(self, "Upload", "No SO rows found in file.")
            return

        # Step 2: show diff dialog
        dlg = SOUploadPreviewDialog(preview, self)
        if not dlg.exec() or not dlg._confirmed:
            return

        # Step 3: confirmed — execute actual upload
        ok, msg, summary = upload_so(path)
        if ok:
            QMessageBox.information(self, "Upload Result", msg)
            self.refresh()
            if self.main_window:
                self.main_window.notify(msg)
        else:
            QMessageBox.warning(self, "Upload Failed", msg)

    def _download_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save SO Template", "SO_template.xlsx", "Excel (*.xlsx)")
        if path:
            ok, msg = download_so_template(path)
            if ok:
                QMessageBox.information(self, "Template", f"Saved: {msg}")
            else:
                QMessageBox.warning(self, "Error", msg)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export All Data", "planner_export.xlsx", "Excel (*.xlsx)")
        if path:
            ok, msg = export_all(path)
            if ok:
                QMessageBox.information(self, "Export", msg)
            else:
                QMessageBox.warning(self, "Export Failed", msg)

    def _rollback(self):
        snapshots = SORepo.list_snapshots()
        if not snapshots:
            QMessageBox.information(self, "Rollback", "No snapshots available.")
            return
        items = [f"{s['batch_id']}  ({s['created_at']})" for s in snapshots]
        from PyQt6.QtWidgets import QInputDialog
        item, ok = QInputDialog.getItem(self, "Rollback", "Select snapshot to restore:", items, 0, False)
        if ok and item:
            batch_id = snapshots[items.index(item)]["batch_id"]
            if QMessageBox.warning(self, "Confirm Rollback",
                f"Restore SO data to snapshot {batch_id}?\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                SORepo.rollback(batch_id)
                self.refresh()
                QMessageBox.information(self, "Rollback", "Rollback complete.")


class SOUploadPreviewDialog(QDialog):
    """Shows a diff of what will change before committing an SO upload."""

    _BG = {
        "NEW":       QColor("#d4f0c0"),   # green
        "MODIFIED":  QColor("#fff9c4"),   # yellow
        "CLOSED":    QColor("#ffcccc"),   # red
        "UNCHANGED": QColor("#f0f0f0"),   # grey
    }
    _CHANGED_CELL_BG = QColor("#ffe066")  # darker yellow for modified cells

    COLS = ["Change", "SO#", "SKU", "Line", "Customer",
            "Qty", "Due Date", "Priority", "Status", "Note"]
    # maps col index → field name for MODIFIED highlighting
    _COL_FIELD = {4: "customer_name", 5: "qty", 6: "due_date",
                  7: "priority", 8: "status", 9: "note"}

    def __init__(self, preview: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SO Upload Preview — Review Changes")
        self.setMinimumSize(1000, 560)
        self._preview = preview
        self._confirmed = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── summary bar ──────────────────────────────────────────────────
        s = self._preview["summary"]
        parts = []
        if s["new"]:       parts.append(f"🟢 {s['new']} new")
        if s["modified"]:  parts.append(f"🟡 {s['modified']} modified")
        if s["closed"]:    parts.append(f"🔴 {s['closed']} will close")
        if s["unchanged"]: parts.append(f"⚪ {s['unchanged']} unchanged")
        if self._preview.get("errors"):
            parts.append(f"⚠ {len(self._preview['errors'])} parse errors")
        summary_label = QLabel("  |  ".join(parts) if parts else "No changes detected")
        summary_label.setStyleSheet("font-weight:bold; padding:6px; font-size:12px;")
        layout.addWidget(summary_label)

        if self._preview.get("errors"):
            err_label = QLabel("Parse errors: " + "; ".join(self._preview["errors"][:3]))
            err_label.setStyleSheet("color:red; font-size:10px; padding:2px 6px;")
            layout.addWidget(err_label)

        # ── filter bar ───────────────────────────────────────────────────
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Show:"))
        self._filter = QComboBox()
        self._filter.addItems(["ALL", "New", "Modified", "Closed", "Unchanged"])
        self._filter.currentTextChanged.connect(self._load_table)
        fbar.addWidget(self._filter)
        fbar.addStretch()
        layout.addLayout(fbar)

        # ── table ─────────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLS))
        self._table.setHorizontalHeaderLabels(self.COLS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setAlternatingRowColors(False)
        layout.addWidget(self._table)

        # ── buttons ───────────────────────────────────────────────────────
        bbar = QHBoxLayout()
        bbar.addStretch()
        btn_cancel  = QPushButton("Cancel")
        btn_confirm = QPushButton("✅ Confirm Upload")
        btn_confirm.setStyleSheet("background:#4caf50;color:white;font-weight:bold;padding:6px 16px;")
        btn_cancel.clicked.connect(self.reject)
        btn_confirm.clicked.connect(self._confirm)
        # disable confirm if nothing to apply
        total_changes = s["new"] + s["modified"] + s["closed"]
        btn_confirm.setEnabled(total_changes > 0)
        bbar.addWidget(btn_cancel)
        bbar.addWidget(btn_confirm)
        layout.addLayout(bbar)

        self._load_table()

    def _load_table(self):
        f = self._filter.currentText().upper()
        rows = [r for r in self._preview["rows"]
                if f == "ALL" or r["change_type"] == f]

        self._table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            ct  = r["change_type"]
            nd  = r["new"]
            old = r["old"] or {}
            bg  = self._BG[ct]
            changed = set(r["changed_fields"])

            n_splits = r.get("split_children", 0)
            change_label = ct if not n_splits else f"{ct} ⚠ ({n_splits} splits)"
            vals = [
                change_label,
                nd.get("so_number", ""),
                nd.get("sku_code", ""),
                nd.get("line_item", ""),
                nd.get("customer_name") or "",
                str(nd.get("qty", "")),
                nd.get("due_date", "") or "",
                str(nd.get("priority", "") or ""),
                nd.get("status", ""),
                nd.get("note", "") or "",
            ]

            for ci, val in enumerate(vals):
                field = self._COL_FIELD.get(ci)
                if ct == "MODIFIED" and field and field in changed:
                    old_val = str(old.get(field, "") or "")
                    cell_text = f"{old_val} → {val}" if old_val != val else val
                    item = QTableWidgetItem(cell_text)
                    item.setBackground(QBrush(self._CHANGED_CELL_BG))
                else:
                    item = QTableWidgetItem(val)
                    item.setBackground(QBrush(bg))
                if ci == 0 and n_splits:
                    item.setToolTip(
                        f"This SO has {n_splits} split child(ren).\n"
                        "Split children are NOT auto-closed by upload.\n"
                        "Review and adjust split quantities manually if needed.")
                self._table.setItem(ri, ci, item)

    def _confirm(self):
        self._confirmed = True
        self.accept()


class SOEditDialog(QDialog):
    def __init__(self, so: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit SO — {so['so_number']}/{so['sku_code']}/{so['line_item']}")
        self.so = so
        self.result = dict(so)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        from PyQt6.QtWidgets import QFormLayout
        form = QFormLayout()

        self.customer_edit = QLineEdit(self.so.get("customer_name") or "")
        form.addRow("Customer:", self.customer_edit)

        self.qty_edit = QSpinBox(); self.qty_edit.setRange(1, 9999999)
        self.qty_edit.setValue(self.so.get("qty", 1))
        form.addRow("Qty:", self.qty_edit)

        self.due_edit = QDateEdit()
        self.due_edit.setDisplayFormat("yyyy-MM-dd")
        if self.so.get("due_date"):
            from PyQt6.QtCore import QDate
            self.due_edit.setDate(QDate.fromString(self.so["due_date"], "yyyy-MM-dd"))
        form.addRow("Requested Due Date:", self.due_edit)

        self.committed_due_edit = QDateEdit()
        self.committed_due_edit.setDisplayFormat("yyyy-MM-dd")
        self.committed_due_edit.setSpecialValueText("(not set)")
        self.committed_due_edit.setMinimumDate(QDate(2000, 1, 1))
        cdd = self.so.get("committed_due_date")
        if cdd:
            from PyQt6.QtCore import QDate
            self.committed_due_edit.setDate(QDate.fromString(cdd, "yyyy-MM-dd"))
        else:
            self.committed_due_edit.setDate(QDate(2000, 1, 1))
        self._committed_due_set = bool(cdd)
        chk = QCheckBox("Set Committed Due Date")
        chk.setChecked(bool(cdd))
        self.committed_due_edit.setEnabled(bool(cdd))
        chk.toggled.connect(lambda v: (self.committed_due_edit.setEnabled(v),
                                       setattr(self, "_committed_due_set", v)))
        self._committed_due_chk = chk
        form.addRow(chk, self.committed_due_edit)

        self.priority_edit = QSpinBox(); self.priority_edit.setRange(0, 9999)
        self.priority_edit.setSpecialValueText("(none)")
        self.priority_edit.setValue(self.so.get("priority") or 0)
        form.addRow("Priority:", self.priority_edit)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["OPEN", "HOLD", "CLOSED"])
        idx = self.status_combo.findText(self.so.get("status", "OPEN"))
        if idx >= 0:
            self.status_combo.setCurrentIndex(idx)
        form.addRow("Status:", self.status_combo)

        self.note_edit = QLineEdit(self.so.get("note") or "")
        form.addRow("Note:", self.note_edit)

        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        self.result["customer_name"] = self.customer_edit.text().strip() or None
        self.result["qty"]      = self.qty_edit.value()
        self.result["due_date"] = self.due_edit.date().toString("yyyy-MM-dd")
        if self._committed_due_set:
            self.result["committed_due_date"] = self.committed_due_edit.date().toString("yyyy-MM-dd")
        else:
            self.result["committed_due_date"] = None
        pri = self.priority_edit.value()
        self.result["priority"] = pri if pri > 0 else None
        self.result["status"]   = self.status_combo.currentText()
        self.result["note"]     = self.note_edit.text() or None
        self.accept()


# ── SO Split ──────────────────────────────────────────────────────────────────

class _SplitSOTab(QWidget):
    """SO 탭에 _split_so() 메서드 믹스인용 — SOTab에 직접 추가."""


def _so_tab_split_so(self, so_no: str, sku: str, li: str):
    so = SORepo.get(so_no, sku, li)
    if not so:
        return
    dlg = SplitSODialog(so, self)
    if dlg.exec():
        for row_data in dlg.split_results:
            SORepo.upsert(row_data)
        self.refresh()
        if self.main_window:
            self.main_window.notify(
                f"Split {so_no}/{sku}/{li} → {len(dlg.split_results)} line(s).")


# Attach as method on SOTab
SOTab._split_so = _so_tab_split_so


class SplitSODialog(QDialog):
    """SO 수량을 여러 LineItem으로 분할하는 다이얼로그."""

    def __init__(self, so: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Split SO — {so['so_number']} / {so['sku_code']} / {so['line_item']}")
        self.setMinimumSize(1060, 380)
        self.so = so
        self.split_results: List[Dict] = []
        self._loading = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── header info ──
        customer = self.so.get("customer_name") or "-"
        info = QLabel(
            f"SO: {self.so['so_number']}  |  SKU: {self.so['sku_code']}  |  "
            f"Line: {self.so['line_item']}  |  Customer: {customer}  |  "
            f"Total Qty: {self.so['qty']}"
        )
        info.setStyleSheet(
            "font-weight:bold; padding:8px 10px; background:#e8f0fe; "
            "border-radius:4px; font-size:12px;")
        layout.addWidget(info)

        # ── split table ──
        # cols: 0=Line Item, 1=Qty, 2=Due Date, 3=Committed Due, 4=Start No Earlier, 5=Priority, 6=Note
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Line Item", "Qty", "Due Date", "Committed Due", "Start No Earlier", "Priority", "Note"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_changed)
        layout.addWidget(self.table)

        # populate: original row + one empty split
        self._add_row(
            self.so["line_item"],
            self.so["qty"],
            self.so["due_date"],
            self.so.get("committed_due_date") or "",
            self.so.get("start_no_earlier") or "",
            self.so.get("priority") or "",
            self.so.get("note") or "",
            original=True,
        )
        self._add_row(
            self._next_line_item(0),
            0,
            self.so["due_date"],
            "",
            "",
            self.so.get("priority") or "",
            "",
            original=False,
        )

        # ── bottom bar ──
        bbar = QHBoxLayout()
        btn_add = QPushButton("+ Add Row")
        btn_add.clicked.connect(self._add_empty_row)
        bbar.addWidget(btn_add)

        self.remaining_lbl = QLabel()
        self.remaining_lbl.setMinimumWidth(160)
        bbar.addWidget(self.remaining_lbl)
        bbar.addStretch()

        btn_cancel = QPushButton("Cancel")
        self.btn_split = QPushButton("✂ Split")
        self.btn_split.setStyleSheet(
            "background:#2e6fd8; color:white; font-weight:bold; padding:6px 18px;")
        btn_cancel.clicked.connect(self.reject)
        self.btn_split.clicked.connect(self._confirm)
        bbar.addWidget(btn_cancel)
        bbar.addWidget(self.btn_split)
        layout.addLayout(bbar)

        self._refresh_remaining()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _next_line_item(self, extra_count: int) -> str:
        base = self.so["line_item"]
        return f"{base}-{2 + extra_count}"

    def _add_row(self, line_item, qty, due_date, committed_due, start_no_earlier, priority, note, original=False):
        self._loading = True
        ri = self.table.rowCount()
        self.table.insertRow(ri)

        li_item = QTableWidgetItem(str(line_item))
        if original:
            li_item.setFlags(li_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            li_item.setBackground(QBrush(QColor("#dce8fb")))
        self.table.setItem(ri, 0, li_item)
        self.table.setItem(ri, 1, QTableWidgetItem(str(qty)))
        self.table.setItem(ri, 2, QTableWidgetItem(str(due_date)))
        self.table.setItem(ri, 3, QTableWidgetItem(str(committed_due)))
        self.table.setItem(ri, 4, QTableWidgetItem(str(start_no_earlier)))
        self.table.setItem(ri, 5, QTableWidgetItem(str(priority)))
        self.table.setItem(ri, 6, QTableWidgetItem(str(note)))
        self._loading = False

    def _add_empty_row(self):
        extras = self.table.rowCount() - 1
        self._add_row(
            self._next_line_item(extras), 0,
            self.so["due_date"], "", "", self.so.get("priority") or "", "", original=False)
        self._refresh_remaining()

    def _on_changed(self, _item):
        if not self._loading:
            self._refresh_remaining()

    def _refresh_remaining(self):
        total = self.so["qty"]
        assigned = 0
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 1)
            try:
                assigned += int(cell.text()) if cell else 0
            except ValueError:
                pass
        remaining = total - assigned
        if remaining == 0:
            self.remaining_lbl.setText(f"Remaining: 0  ✓")
            self.remaining_lbl.setStyleSheet("font-weight:bold; color:#2e7d32;")
            self.btn_split.setEnabled(True)
        elif remaining < 0:
            self.remaining_lbl.setText(f"Over by {-remaining}")
            self.remaining_lbl.setStyleSheet("font-weight:bold; color:#c62828;")
            self.btn_split.setEnabled(False)
        else:
            self.remaining_lbl.setText(f"Remaining: {remaining}")
            self.remaining_lbl.setStyleSheet("font-weight:bold; color:#e65100;")
            self.btn_split.setEnabled(False)

    # ── confirm ───────────────────────────────────────────────────────────────

    def _confirm(self):
        rows = []
        line_items_seen = set()
        for ri in range(self.table.rowCount()):
            li               = (self.table.item(ri, 0).text() or "").strip()
            due              = (self.table.item(ri, 2).text() or "").strip()
            committed_due    = (self.table.item(ri, 3).text() or "").strip() or None
            start_no_earlier = (self.table.item(ri, 4).text() or "").strip() or None
            pri_text         = (self.table.item(ri, 5).text() or "").strip()
            note             = (self.table.item(ri, 6).text() or "").strip()
            try:
                qty = int(self.table.item(ri, 1).text() or 0)
            except ValueError:
                qty = 0
            try:
                pri = int(pri_text) if pri_text else None
            except ValueError:
                pri = None

            if qty <= 0:
                continue
            if not li:
                QMessageBox.warning(self, "Validation", f"Row {ri+1}: Line Item cannot be empty.")
                return
            if li in line_items_seen:
                QMessageBox.warning(self, "Validation", f"Duplicate Line Item: {li}")
                return
            for field_name, field_val in [("Committed Due", committed_due),
                                           ("Start No Earlier", start_no_earlier)]:
                if field_val:
                    try:
                        datetime.strptime(field_val, "%Y-%m-%d")
                    except ValueError:
                        QMessageBox.warning(
                            self, "Validation",
                            f"Row {ri+1}: {field_name} format must be YYYY-MM-DD.")
                        return
            line_items_seen.add(li)
            is_original = (li == self.so["line_item"])
            rows.append({
                "so_number":          self.so["so_number"],
                "sku_code":           self.so["sku_code"],
                "line_item":          li,
                "customer_name":      self.so.get("customer_name"),
                "qty":                qty,
                "due_date":           due,
                "committed_due_date": committed_due,
                "priority":           pri,
                "status":             self.so.get("status", "OPEN"),
                "start_no_earlier":   start_no_earlier,
                "note":               note or None,
                "received_at":        self.so.get("received_at"),
                "split_from":         None if is_original else self.so["line_item"],
            })

        if len(rows) < 2:
            QMessageBox.warning(self, "Validation", "Need at least 2 rows with Qty > 0 to split.")
            return

        self.split_results = rows
        self.accept()


# ── Single-SO Inventory Allocation Dialog (Version B: inline) ─────────────────

class SOInventoryAllocationDialog(QDialog):
    """Inline FEFO allocation for a single SO line item."""

    def __init__(self, so_number: str, sku_code: str, line_item: str, parent=None):
        super().__init__(parent)
        self.so_number = so_number
        self.sku_code  = sku_code
        self.line_item = line_item
        so = SORepo.get(so_number, sku_code, line_item)
        self.so_qty     = so["qty"] if so else 0
        self.due_date   = so["due_date"] if so else ""
        self.setWindowTitle(f"📦 Allocate Inventory — {so_number} / {sku_code} / {line_item}")
        self.setMinimumSize(780, 480)
        self._spinboxes: list = []   # (inv_id, lot_number, QSpinBox, qty_remaining)
        self._build_ui()
        self._load_lots()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # SO info bar
        so    = SORepo.get(self.so_number, self.sku_code, self.line_item)
        already = AllocationRepo.total_allocated(self.so_number, self.sku_code, self.line_item)
        needed  = max(0, self.so_qty - already)
        info = QLabel(
            f"SO: {self.so_number}  |  SKU: {self.sku_code}  |  Line: {self.line_item}  |  "
            f"SO Qty: {self.so_qty}  |  Already Allocated: {already}  |  "
            f"Production Needed: {AllocationRepo.production_needed(self.so_number, self.sku_code, self.line_item)}"
        )
        info.setStyleSheet(
            "font-weight:bold; padding:8px 10px; background:#e8f0fe; "
            "border-radius:4px; font-size:12px;")
        layout.addWidget(info)

        # Progress bar row
        prog_row = QHBoxLayout()
        prog_row.addWidget(QLabel("Allocation:"))
        self._prog_bar = QLabel()
        self._prog_bar.setFixedHeight(18)
        self._prog_bar.setMinimumWidth(300)
        self._prog_bar.setStyleSheet(
            "background:#e3e9f5; border-radius:9px; padding:1px 6px; font-size:11px;")
        prog_row.addWidget(self._prog_bar)
        self._prog_label = QLabel()
        self._prog_label.setMinimumWidth(160)
        prog_row.addWidget(self._prog_label)
        prog_row.addStretch()
        layout.addLayout(prog_row)

        # Lot table — cols: LOT | Expiry | Lot Remaining | Allocate Qty
        hdr_row = QHBoxLayout()
        hdr_row.addWidget(QLabel("Available Lots (FEFO sorted):"))
        hdr_row.addStretch()
        btn_fefo = QPushButton("🤖 Auto FEFO")
        btn_fefo.clicked.connect(self._apply_fefo)
        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self._clear_all)
        hdr_row.addWidget(btn_fefo)
        hdr_row.addWidget(btn_clear)
        layout.addLayout(hdr_row)

        self._lot_table = QTableWidget()
        self._lot_table.setColumnCount(4)
        self._lot_table.setHorizontalHeaderLabels(
            ["LOT Number", "Expiry Date", "Lot Remaining", "Allocate Qty"])
        hdr = self._lot_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._lot_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._lot_table, stretch=1)

        # Summary + expiry warning
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("font-size:12px; padding:4px;")
        layout.addWidget(self._summary_label)

        self._warn_label = QLabel()
        self._warn_label.setStyleSheet(
            "background:#fff3e0; color:#b45309; border:1px solid #ffcc80; "
            "border-radius:4px; padding:6px 10px; font-size:11px;")
        self._warn_label.setWordWrap(True)
        self._warn_label.hide()
        layout.addWidget(self._warn_label)

        # Footer buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("✅ Confirm Allocation")
        btns.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(
            "background:#2e7d32; color:white; font-weight:bold; padding:6px 18px;")
        btns.accepted.connect(self._confirm)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ── Data ─────────────────────────────────────────────────────────────────

    def _load_lots(self):
        lots = InventoryRepo.available_for_sku(self.sku_code)
        self._spinboxes.clear()
        self._lot_table.setRowCount(len(lots))
        for ri, lot in enumerate(lots):
            exp    = lot.get("expiry_date") or "—"
            rem    = lot["qty_remaining"]
            warn   = (exp != "—" and self.due_date and exp < self.due_date)

            lot_item = QTableWidgetItem(lot["lot_number"])
            exp_item = QTableWidgetItem(exp)
            rem_item = QTableWidgetItem(str(rem))
            for item in (lot_item, exp_item, rem_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if warn:
                exp_item.setForeground(QBrush(QColor("#e65100")))
                exp_item.setText(f"{exp} ⚠")

            spin = QSpinBox()
            spin.setRange(0, rem)
            spin.setValue(0)
            spin.valueChanged.connect(self._update_summary)

            self._lot_table.setItem(ri, 0, lot_item)
            self._lot_table.setItem(ri, 1, exp_item)
            self._lot_table.setItem(ri, 2, rem_item)
            self._lot_table.setCellWidget(ri, 3, spin)
            self._spinboxes.append((lot["inv_id"], lot["lot_number"], spin, rem, warn))

        self._update_summary()

    def _apply_fefo(self):
        needed = self.so_qty
        for inv_id, lot_no, spin, rem, warn in self._spinboxes:
            if needed <= 0:
                spin.setValue(0)
            else:
                take = min(rem, needed)
                spin.setValue(take)
                needed -= take
        self._update_summary()

    def _clear_all(self):
        for _, _, spin, _, _ in self._spinboxes:
            spin.setValue(0)
        self._update_summary()

    def _update_summary(self):
        total = sum(spin.value() for _, _, spin, _, _ in self._spinboxes)
        pct   = int(total / self.so_qty * 100) if self.so_qty else 0
        remaining = self.so_qty - total

        self._prog_label.setText(
            f"{total} / {self.so_qty} ({pct}%)  —  still to produce: {max(0, remaining)}")
        color = "#2e7d32" if total >= self.so_qty else ("#e65100" if total > 0 else "#64748b")
        self._prog_label.setStyleSheet(f"font-weight:bold; color:{color};")

        # Expiry warnings
        warnings = []
        for _, lot_no, spin, _, warn in self._spinboxes:
            if spin.value() > 0 and warn:
                warnings.append(f"⚠ {lot_no} expires before due date")
        if warnings:
            self._warn_label.setText("  ".join(warnings))
            self._warn_label.show()
        else:
            self._warn_label.hide()

        self._summary_label.setText(
            f"Total to allocate: {total} / {self.so_qty}  "
            + ("✅ Fully covered" if total >= self.so_qty
               else f"⚠ {remaining} units still need production"))

    # ── Confirm ──────────────────────────────────────────────────────────────

    def _confirm(self):
        entries = [(inv_id, lot_no, spin.value())
                   for inv_id, lot_no, spin, _, _ in self._spinboxes
                   if spin.value() > 0]
        if not entries:
            QMessageBox.warning(self, "No Allocation", "Set at least one lot quantity.")
            return
        # Clear existing allocations then write new ones
        AllocationRepo.deallocate_all_for_so(self.so_number, self.sku_code, self.line_item)
        suggestion = [{"inv_id": inv_id, "lot_number": lot_no, "qty_to_allocate": qty}
                      for inv_id, lot_no, qty in entries]
        AllocationRepo.confirm_fefo_suggestion(
            self.so_number, self.sku_code, self.line_item, suggestion)
        self.accept()


# ── Bulk Allocation Dialog (Version C) ────────────────────────────────────────

class BulkAllocationDialog(QDialog):
    """FEFO × Priority auto-allocation for all OPEN SOs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📦 Bulk Inventory Allocation — All Open SOs")
        self.setMinimumSize(900, 560)
        self._result: dict = {}
        self._build_ui()
        self._load_preview()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # KPI bar
        kpi_bar = QHBoxLayout()
        self._kpi_open    = self._make_kpi("Open SOs",     "blue")
        self._kpi_full    = self._make_kpi("✅ Full",       "green")
        self._kpi_partial = self._make_kpi("⚠ Partial",    "orange")
        self._kpi_none    = self._make_kpi("❌ No Inv.",    "red")
        self._kpi_inv     = self._make_kpi("Inv. Available","gray")
        for w in (self._kpi_open, self._kpi_full, self._kpi_partial,
                  self._kpi_none, self._kpi_inv):
            kpi_bar.addWidget(w)
        kpi_bar.addStretch()

        btn_auto = QPushButton("🚀 Auto-Allocate All  (FEFO × Priority)")
        btn_auto.setStyleSheet(
            "background:#e65100; color:white; font-weight:bold; "
            "padding:9px 20px; font-size:13px;")
        btn_auto.clicked.connect(self._auto_allocate)
        btn_reset = QPushButton("↺ Reset All")
        btn_reset.clicked.connect(self._reset)
        kpi_bar.addWidget(btn_auto)
        kpi_bar.addWidget(btn_reset)
        layout.addLayout(kpi_bar)

        # SO table
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["Pri", "SO Number", "SKU / Line", "Customer", "SO Qty", "Allocated", "Status"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table, stretch=1)

        # Result banner (hidden until auto-allocate runs)
        self._result_label = QLabel()
        self._result_label.setWordWrap(True)
        self._result_label.setStyleSheet(
            "background:#e8f5e9; color:#1b5e20; border:1px solid #a5d6a7; "
            "border-radius:4px; padding:8px 12px; font-size:12px;")
        self._result_label.hide()
        layout.addWidget(self._result_label)

        # Footer
        btns = QHBoxLayout()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(btn_close)
        layout.addLayout(btns)

    def _make_kpi(self, label: str, color: str) -> QGroupBox:
        colors = {"blue": "#1976d2", "green": "#2e7d32",
                  "orange": "#e65100", "red": "#c62828", "gray": "#64748b"}
        grp = QGroupBox()
        grp.setFixedWidth(110)
        v = QVBoxLayout(grp)
        v.setContentsMargins(6, 4, 6, 4)
        val_lbl = QLabel("—")
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val_lbl.setStyleSheet(
            f"font-size:22px; font-weight:800; color:{colors.get(color,'#333')};")
        sub_lbl = QLabel(label)
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setStyleSheet("font-size:10px; color:#64748b;")
        v.addWidget(val_lbl)
        v.addWidget(sub_lbl)
        grp.val_lbl = val_lbl
        return grp

    # ── Data ─────────────────────────────────────────────────────────────────

    def _load_preview(self):
        sos = SORepo.all("OPEN")
        sos.sort(key=lambda s: (
            s["priority"] if s["priority"] is not None else 9999,
            s["due_date"] or ""))
        alloc_map = AllocationRepo.allocation_summary_for_open_sos()

        n_full = n_partial = n_none = 0
        inv_total = sum(
            InventoryRepo.total_available(s["sku_code"]) for s in sos) if sos else 0

        self._table.setRowCount(len(sos))
        for ri, so in enumerate(sos):
            key       = (so["so_number"], so["sku_code"], so["line_item"])
            allocated = alloc_map.get(key, 0)
            so_qty    = so["qty"]

            if allocated >= so_qty:
                status_txt = "✅ Full"; bg = QColor("#e8f5e9"); n_full += 1
            elif allocated > 0:
                status_txt = "⚠ Partial"; bg = QColor("#fff8e1"); n_partial += 1
            else:
                avail = InventoryRepo.total_available(so["sku_code"])
                status_txt = "❌ No Inventory" if avail == 0 else "— Not Allocated"
                bg = QColor("#ffebee") if avail == 0 else QColor("#ffffff")
                n_none += 1

            row_vals = [
                str(so["priority"] or "—"),
                so["so_number"],
                f"{so['sku_code']} / {so['line_item']}",
                so.get("customer_name") or "",
                str(so_qty),
                str(allocated),
                status_txt,
            ]
            for ci, val in enumerate(row_vals):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setBackground(QBrush(bg))
                self._table.setItem(ri, ci, item)

        # KPI update
        self._kpi_open.val_lbl.setText(str(len(sos)))
        self._kpi_full.val_lbl.setText(str(n_full))
        self._kpi_partial.val_lbl.setText(str(n_partial))
        self._kpi_none.val_lbl.setText(str(n_none))
        self._kpi_inv.val_lbl.setText(str(inv_total))

    def _auto_allocate(self):
        reply = QMessageBox.question(
            self, "Auto-Allocate All",
            "Run FEFO × Priority auto-allocation for all OPEN SOs?\n\n"
            "Existing unconfirmed allocations will be overwritten.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        result = AllocationRepo.bulk_auto_allocate()
        self._result = result

        n_alloc  = len(result["allocated"])
        n_skip   = len(result["skipped"])
        qty_total = sum(r["qty_covered"] for r in result["allocated"])

        msg = f"✅ Auto-allocation complete: {n_alloc} SOs covered ({qty_total} units)."
        if n_skip:
            skipped_sos = ", ".join(r["so_number"] for r in result["skipped"][:5])
            msg += f"\n⚠ {n_skip} SO(s) skipped (no inventory): {skipped_sos}"
            if n_skip > 5:
                msg += " …"
        self._result_label.setText(msg)
        self._result_label.show()

        self._load_preview()

    def _reset(self):
        if QMessageBox.question(
                self, "Reset All Allocations",
                "Remove ALL inventory allocations for OPEN SOs?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        sos = SORepo.all("OPEN")
        for so in sos:
            AllocationRepo.deallocate_all_for_so(
                so["so_number"], so["sku_code"], so["line_item"])
        self._result_label.hide()
        self._load_preview()
