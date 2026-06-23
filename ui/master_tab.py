"""
Master Management Tab — SKU, Room/Process, Shift, App Config
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget,
    QTableWidgetItem, QPushButton, QLabel, QComboBox, QLineEdit,
    QSpinBox, QDoubleSpinBox, QFileDialog, QMessageBox,
    QDialog, QDialogButtonBox, QFormLayout, QAbstractItemView,
    QHeaderView, QMenu, QCheckBox, QGroupBox, QTextEdit
)
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtWidgets import QDateEdit
from PyQt6.QtGui import QCursor, QBrush, QColor

from data.repositories import SKURepo, RoomRepo, ShiftRepo, ConfigRepo, SKUProcessRepo, CalendarRepo, CompanyHolidayRepo
from utils.korean_holidays import is_holiday, holiday_name
from utils.excel_io import (
    upload_sku, download_sku_template,
    upload_room, download_room_template,
    upload_sku_process, download_sku_process_template,
    download_item_template, upload_items,
)


class UploadPreviewDialog(QDialog):
    """Generic upload preview — shows parsed Excel rows in a table before confirming."""

    def __init__(self, title: str, headers: list, rows: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(900, 520)
        self._confirmed = False

        layout = QVBoxLayout(self)

        summary = QLabel(f"{len(rows)} row(s) to upload")
        summary.setStyleSheet(
            "font-weight:bold; font-size:12px; padding:6px;")
        layout.addWidget(summary)

        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        table.setAlternatingRowColors(True)
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row):
                table.setItem(ri, ci, QTableWidgetItem(str(val)))
        layout.addWidget(table)

        bbar = QHBoxLayout()
        bbar.addStretch()
        btn_cancel  = QPushButton("Cancel")
        btn_confirm = QPushButton("✅ Confirm Upload")
        btn_confirm.setStyleSheet(
            "background:#2563EB; color:white; font-weight:bold;"
            "border:none; border-radius:5px; padding:6px 18px;")
        btn_confirm.setEnabled(len(rows) > 0)
        btn_cancel.clicked.connect(self.reject)
        btn_confirm.clicked.connect(self._confirm)
        bbar.addWidget(btn_cancel)
        bbar.addWidget(btn_confirm)
        layout.addLayout(bbar)

    def _confirm(self):
        self._confirmed = True
        self.accept()


class MasterTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        tabs = QTabWidget()
        tabs.addTab(ItemMasterWidget(self), "Item Master")
        tabs.addTab(ProcessRoutingWidget(self), "Process Routing")
        tabs.addTab(RoomMasterWidget(self), "Room / Process")
        tabs.addTab(ShiftConfigWidget(self), "Shift Config")
        tabs.addTab(AppConfigWidget(self), "App Config")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

    def refresh(self):
        pass


# ─── Item Master (unified SKU + Material) ────────────────────────────────────

class ItemMasterWidget(QWidget):
    """Single tab for both SKU and Material masters."""

    COLS    = ["Type", "Code", "Name", "UoM", "Post Lead Days", "Allow Grouping", "Note"]
    _SKU_BG  = QColor("#ddeeff")
    _MAT_BG  = QColor("#ddf0dd")
    _MISS_BG = QColor("#ffe0b2")

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # filter bar
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Show:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["ALL", "SKU", "MATERIAL"])
        self.type_combo.currentTextChanged.connect(self._load)
        fbar.addWidget(self.type_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search code / name…")
        self.search.textChanged.connect(self._load)
        fbar.addWidget(self.search)
        fbar.addStretch()
        layout.addLayout(fbar)

        # action buttons
        bbar = QHBoxLayout()
        for label, slot in [
            ("➕ Add SKU",        self._add_sku),
            ("➕ Add Material",   self._add_material),
            ("🗑 Delete",         self._delete),
        ]:
            b = QPushButton(label); b.clicked.connect(slot); bbar.addWidget(b)
        self._btn_save = QPushButton("💾 Save Changes")
        self._btn_save.clicked.connect(self._save_changes)
        bbar.addWidget(self._btn_save)
        for label, slot in [
            ("📤 Upload Items",    self._upload_items),
            ("⬇ Templates",       self._templates),
        ]:
            b = QPushButton(label); b.clicked.connect(slot); bbar.addWidget(b)
        bbar.addStretch()
        layout.addLayout(bbar)

        self.table = _make_table(self.COLS, editable=True)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.itemChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._changed_cells: set = set()
        self._loading = False
        self._load()

    # ── helpers ───────────────────────────────────────────────────────────

    def _missing_materials(self) -> set:
        from data.repositories import ProcessRoutingRepo, MaterialRepo
        defined    = {m["material_code"] for m in MaterialRepo.all()}
        referenced = {r["requires_material_code"]
                      for r in ProcessRoutingRepo.all()
                      if r.get("requires_material_code")}
        return referenced - defined

    def _load(self):
        from data.repositories import SKURepo, MaterialRepo
        self._loading = True
        self._changed_cells.clear()

        t = self.type_combo.currentText()
        f = self.search.text().lower()
        missing = self._missing_materials()

        rows = []
        if t in ("ALL", "SKU"):
            for s in SKURepo.all():
                rows.append({"type": "SKU", "code": s["sku_code"],
                              "name": s["sku_name"], "uom": s["uom"],
                              "lead": s["post_lead_days"],
                              "campaign": int(s.get("campaign_mode", 1)),
                              "note": s["note"] or ""})
        if t in ("ALL", "MATERIAL"):
            for m in MaterialRepo.all():
                rows.append({"type": "MATERIAL", "code": m["material_code"],
                              "name": m["material_name"], "uom": m["uom"],
                              "lead": m["post_lead_days"],
                              "campaign": "",   # N/A for Material
                              "note": m["note"] or ""})
        if f:
            rows = [r for r in rows
                    if f in r["code"].lower() or f in r["name"].lower()]

        self.table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            is_sku = r["type"] == "SKU"
            bg = self._SKU_BG if is_sku else (
                self._MISS_BG if r["code"] in missing else self._MAT_BG)
            for ci, val in enumerate([
                r["type"], r["code"], r["name"],
                str(r["uom"]), str(r["lead"]),
                ("✅" if r.get("campaign") == 1 else ("❌" if r.get("campaign") == 0 else "")),
                r["note"]
            ]):
                item = QTableWidgetItem(str(val))
                item.setBackground(QBrush(bg))
                if ci in (0, 1, 5):  # Type / Code / Allow Grouping are read-only (toggle via dialog)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(ri, ci, item)

        self._loading = False
        self._refresh_status(missing)

    def _refresh_status(self, missing=None):
        if missing is None:
            missing = self._missing_materials()
        if self._changed_cells:
            n = len({r for r, _ in self._changed_cells})
            self._status.setText(f"✏ {n} row(s) modified — unsaved")
            self._status.setStyleSheet(
                "color:#7a5800;background:#fff9c4;padding:4px;border-radius:4px;")
        elif missing:
            self._status.setText(
                f"⚠ {len(missing)} material(s) required by routing but not yet defined: "
                + ", ".join(sorted(missing)))
            self._status.setStyleSheet(
                "color:#b35900;background:#fff3cd;padding:4px;border-radius:4px;")
        else:
            self._status.setText("")
            self._status.setStyleSheet("")

    # ── table signals ──────────────────────────────────────────────────────

    def _on_cell_changed(self, item):
        if self._loading:
            return
        item.setBackground(QBrush(QColor("#fff9c4")))
        self._changed_cells.add((item.row(), item.column()))
        self._refresh_status()

    def _on_double_click(self, index):
        if index.column() not in (0, 1):
            return  # other columns are inline-editable
        row = index.row()
        self._open_edit_dialog(
            self.table.item(row, 0).text(),
            self.table.item(row, 1).text())

    # ── CRUD ───────────────────────────────────────────────────────────────

    def _add_sku(self):
        dlg = SKUEditDialog({}, self)
        if dlg.exec():
            SKURepo.upsert(dlg.result)
            self._load()

    def _add_material(self):
        from data.repositories import MaterialRepo
        dlg = MaterialEditDialog({}, self)
        if dlg.exec():
            MaterialRepo.upsert(dlg.result)
            self._load()

    def _open_edit_dialog(self, item_type: str, code: str):
        from data.repositories import SKURepo, MaterialRepo
        if item_type == "SKU":
            dlg = SKUEditDialog(SKURepo.get(code) or {}, self)
            if dlg.exec():
                SKURepo.upsert(dlg.result); self._load()
        else:
            dlg = MaterialEditDialog(MaterialRepo.get(code) or {}, self)
            if dlg.exec():
                MaterialRepo.upsert(dlg.result); self._load()

    def _delete(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows:
            return
        items = [(self.table.item(r, 0).text(), self.table.item(r, 1).text()) for r in rows]
        n = len(rows)
        label = items[0][1] if n == 1 else f"{n} items"
        if QMessageBox.question(
            self, "Delete", f"Delete {label}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        from data.repositories import SKURepo, MaterialRepo
        for item_type, code in items:
            (SKURepo.delete if item_type == "SKU" else MaterialRepo.delete)(code)
        self._load()

    def _save_changes(self):
        if not self._changed_cells:
            return
        from data.repositories import SKURepo, MaterialRepo
        changed_rows = {r for r, _ in self._changed_cells}
        saved, errors = 0, []
        for ri in sorted(changed_rows):
            try:
                item_type = self.table.item(ri, 0).text()
                code = self.table.item(ri, 1).text()
                name = self.table.item(ri, 2).text().strip()
                uom  = int(self.table.item(ri, 3).text())
                lead = int(self.table.item(ri, 4).text())
                # col 5 = Allow Grouping (read-only display, toggle via edit dialog)
                note = self.table.item(ri, 6).text() or None
                if item_type == "SKU":
                    # preserve existing campaign_mode — inline save doesn't change it
                    existing = SKURepo.get(code) or {}
                    SKURepo.upsert({"sku_code": code, "sku_name": name,
                                    "uom": uom, "post_lead_days": lead,
                                    "campaign_mode": existing.get("campaign_mode", 1),
                                    "note": note})
                else:
                    MaterialRepo.upsert({"material_code": code, "material_name": name,
                                         "uom": uom, "post_lead_days": lead, "note": note})
                bg = self._SKU_BG if item_type == "SKU" else self._MAT_BG
                for ci in range(self.table.columnCount()):
                    it = self.table.item(ri, ci)
                    if it:
                        it.setBackground(QBrush(bg))
                saved += 1
            except Exception as e:
                errors.append(f"Row {ri + 1}: {e}")
        self._changed_cells.clear()
        if errors:
            QMessageBox.warning(self, "Save Errors", "\n".join(errors))
        self._status.setText(f"✅ Saved {saved} row(s)")
        self._status.setStyleSheet("color:green;padding:4px;")

    # ── upload / template ──────────────────────────────────────────────────

    def _upload_items(self):
        from utils.excel_io import parse_item_preview
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload Item Master Excel", "", "Excel (*.xlsx)")
        if not path:
            return
        ok, err, headers, rows = parse_item_preview(path)
        if not ok:
            QMessageBox.warning(self, "Parse Error", err)
            return
        dlg = UploadPreviewDialog("Item Master — Upload Preview", headers, rows, self)
        if not dlg.exec() or not dlg._confirmed:
            return
        ok, msg = upload_items(path)
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Upload Items", msg)
        if ok:
            self._load()

    def _templates(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Item Master Template", "01_Item_Master.xlsx", "Excel (*.xlsx)")
        if path:
            ok, msg = download_item_template(path)
            (QMessageBox.information if ok else QMessageBox.warning)(self, "Template", msg)


class SKUEditDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SKU")
        self.result = dict(data)
        layout = QVBoxLayout(self)
        form   = QFormLayout()

        self.code     = QLineEdit(data.get("sku_code", ""))
        self.name     = QLineEdit(data.get("sku_name", ""))
        self.uom      = QSpinBox(); self.uom.setRange(1, 9999); self.uom.setValue(data.get("uom", 1))
        self.lead     = QSpinBox(); self.lead.setRange(0, 365); self.lead.setValue(data.get("post_lead_days", 0))
        self.campaign = QCheckBox("Allow grouping (batch same-SKU orders)")
        self.campaign.setChecked(bool(int(data.get("campaign_mode", 1))))
        self.note     = QLineEdit(data.get("note") or "")

        form.addRow("SKU Code:",        self.code)
        form.addRow("SKU Name:",        self.name)
        form.addRow("UoM (qty/EA):",    self.uom)
        form.addRow("Post Lead Days:",  self.lead)
        form.addRow("Allow Grouping:",  self.campaign)
        form.addRow("Note:",            self.note)
        layout.addLayout(form)

        note_lbl = QLabel("When enabled, same-SKU orders within max_consolidation_days are merged into one production run.\nDisable for SKUs where batching risks early expiry.")
        note_lbl.setStyleSheet("color:#6b7280;font-size:10px;")
        note_lbl.setWordWrap(True)
        layout.addWidget(note_lbl)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok); btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _ok(self):
        self.result = {
            "sku_code":       self.code.text().strip(),
            "sku_name":       self.name.text().strip(),
            "uom":            self.uom.value(),
            "post_lead_days": self.lead.value(),
            "campaign_mode":  1 if self.campaign.isChecked() else 0,
            "note":           self.note.text() or None,
        }
        if not self.result["sku_code"]:
            QMessageBox.warning(self, "Error", "SKU Code required"); return
        self.accept()


# ─── Room / Process Master ────────────────────────────────────────────────────

class RoomMasterWidget(QWidget):
    COLS = ["Room Code", "Process Name", "Type", "UPPH", "UPH Fixed",
            "HC Min", "HC Max", "HC Fixed", "Changeover (shifts)", "Note"]
    _READONLY_COLS = {0, 1}  # Room Code, Process Name are PK

    def __init__(self, parent=None):
        super().__init__(parent)
        self._master_tab = parent  # MasterTab reference for gantt refresh
        self._edit_mode = False
        self._changed_cells: set = set()
        self._loading = False
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        btn_add  = QPushButton("➕ Add");    btn_add.clicked.connect(self._add)
        btn_del  = QPushButton("🗑 Delete"); btn_del.clicked.connect(self._delete)
        btn_up   = QPushButton("📤 Upload"); btn_up.clicked.connect(self._upload)
        btn_tmpl = QPushButton("⬇ Template"); btn_tmpl.clicked.connect(self._template)
        for b in (btn_add, btn_del, btn_up, btn_tmpl): bar.addWidget(b)
        self._btn_edit = QPushButton("✏ Edit Mode")
        self._btn_edit.clicked.connect(self._toggle_edit_mode)
        self._btn_save = QPushButton("💾 Save Changes")
        self._btn_save.clicked.connect(self._save_changes)
        self._btn_save.setEnabled(False)
        bar.addWidget(self._btn_edit)
        bar.addWidget(self._btn_save)
        bar.addStretch()
        layout.addLayout(bar)

        self.table = _make_table(self.COLS)
        self.table.doubleClicked.connect(self._edit)
        self.table.itemChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table)

        self._status = QLabel()
        layout.addWidget(self._status)
        self._load()

    def _load(self):
        self._loading = True
        self._changed_cells.clear()
        self._btn_save.setEnabled(False)
        rows = RoomRepo.all()
        self.table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            for ci, val in enumerate([
                r["room_code"], r["process_name"], r["process_type"],
                r["upph"] or "", r["uph_fixed"] or "",
                r["hc_min"] or "", r["hc_max"] or "", r["hc_fixed"] or "",
                r.get("changeover_shifts") or 0,
                r["note"] or ""
            ]):
                item = QTableWidgetItem(str(val))
                if ci in self._READONLY_COLS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if ci == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r)
                self.table.setItem(ri, ci, item)
        self._loading = False
        self._status.setText("")

    def _add(self):
        dlg = RoomEditDialog({}, self)
        if dlg.exec(): RoomRepo.upsert(dlg.result); self._load()

    def _edit(self):
        if self._edit_mode:
            return
        row = self.table.currentRow()
        if row < 0: return
        data = RoomRepo.get(self.table.item(row, 0).text(), self.table.item(row, 1).text()) or {}
        dlg  = RoomEditDialog(data, self)
        if dlg.exec(): RoomRepo.upsert(dlg.result); self._load()

    def _delete(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows: return
        items = [(self.table.item(r, 0).text(), self.table.item(r, 1).text()) for r in rows]
        n = len(rows)
        label = f"{items[0][0]}/{items[0][1]}" if n == 1 else f"{n} rows"
        if QMessageBox.question(self, "Delete", f"Delete {label}?") != QMessageBox.StandardButton.Yes:
            return
        for rc, pn in items:
            RoomRepo.delete(rc, pn)
        self._load()

    def _upload(self):
        from utils.excel_io import parse_room_preview
        path, _ = QFileDialog.getOpenFileName(self, "Upload Room Excel", "", "Excel (*.xlsx)")
        if not path:
            return
        ok, err, headers, rows = parse_room_preview(path)
        if not ok:
            QMessageBox.warning(self, "Parse Error", err)
            return
        dlg = UploadPreviewDialog("Room / Process — Upload Preview", headers, rows, self)
        if not dlg.exec() or not dlg._confirmed:
            return
        ok, msg = upload_room(path)
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Upload", msg)
        if ok:
            self._load()

    def _template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Template", "Room_template.xlsx", "Excel (*.xlsx)")
        if path:
            ok, msg = download_room_template(path)
            (QMessageBox.information if ok else QMessageBox.warning)(self, "Template", msg)

    def _toggle_edit_mode(self):
        self._edit_mode = not self._edit_mode
        if self._edit_mode:
            self._btn_edit.setText("🔒 Exit Edit Mode")
            self._btn_edit.setStyleSheet(
                "background:#e65100; color:white; font-weight:bold;")
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked |
                QAbstractItemView.EditTrigger.EditKeyPressed)
            self._status.setText("✏ Edit mode — double-click or press F2 to edit")
            self._status.setStyleSheet(
                "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        else:
            self._btn_edit.setText("✏ Edit Mode")
            self._btn_edit.setStyleSheet("")
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            if not self._changed_cells:
                self._status.setText("")
                self._status.setStyleSheet("")

    def _on_cell_changed(self, item):
        if self._loading:
            return
        item.setBackground(QBrush(QColor("#fff9c4")))
        self._changed_cells.add((item.row(), item.column()))
        n = len({r for r, _ in self._changed_cells})
        self._status.setText(f"✏ {n} row(s) modified — unsaved")
        self._status.setStyleSheet(
            "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        self._btn_save.setEnabled(True)

    def _save_changes(self):
        if not self._changed_cells:
            return
        changed_rows = {r for r, _ in self._changed_cells}
        saved, errors = 0, []
        for ri in sorted(changed_rows):
            try:
                orig = self.table.item(ri, 0).data(Qt.ItemDataRole.UserRole) or {}
                room_code         = self.table.item(ri, 0).text().strip()
                process_name      = self.table.item(ri, 1).text().strip()
                process_type      = self.table.item(ri, 2).text().strip() or "MANUAL"
                upph              = float(self.table.item(ri, 3).text() or 0) or None
                uph_fixed         = float(self.table.item(ri, 4).text() or 0) or None
                hc_min            = int(self.table.item(ri, 5).text() or 0) or None
                hc_max            = int(self.table.item(ri, 6).text() or 0) or None
                hc_fixed          = int(self.table.item(ri, 7).text() or 0) or None
                changeover_shifts = int(self.table.item(ri, 8).text() or 0)
                note              = self.table.item(ri, 9).text() or None
                RoomRepo.upsert({
                    "room_code":          room_code,
                    "process_name":       process_name,
                    "process_type":       process_type,
                    "room_type":          orig.get("room_type", "TYPE-A"),
                    "upph":               upph,
                    "uph_fixed":          uph_fixed,
                    "hc_min":             hc_min,
                    "hc_max":             hc_max,
                    "hc_fixed":           hc_fixed,
                    "changeover_shifts":  changeover_shifts,
                    "note":               note,
                })
                saved += 1
            except Exception as e:
                errors.append(f"Row {ri + 1}: {e}")
        self._changed_cells.clear()
        self._btn_save.setEnabled(False)
        if errors:
            QMessageBox.warning(self, "Save Errors", "\n".join(errors))
        self._status.setText(f"✅ Saved {saved} row(s)")
        self._status.setStyleSheet("color:green; padding:4px;")
        mw = self._get_main_window()
        if mw:
            mw.gantt_tab.refresh()


class RoomEditDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Room / Process")
        self.result = dict(data)
        layout = QVBoxLayout(self)
        form   = QFormLayout()

        self.room = QLineEdit(data.get("room_code", ""))
        self.proc = QLineEdit(data.get("process_name", ""))
        self.ptype = QComboBox(); self.ptype.addItems(["MANUAL", "AUTO"])
        if data.get("process_type") == "AUTO": self.ptype.setCurrentIndex(1)

        self.rtype = QLineEdit(data.get("room_type", ""))
        self.upph       = QDoubleSpinBox(); self.upph.setRange(0, 99999); self.upph.setValue(data.get("upph") or 0)
        self.uph_fix    = QDoubleSpinBox(); self.uph_fix.setRange(0, 99999); self.uph_fix.setValue(data.get("uph_fixed") or 0)
        self.hc_min     = QSpinBox(); self.hc_min.setRange(0, 999); self.hc_min.setValue(data.get("hc_min") or 0)
        self.hc_max     = QSpinBox(); self.hc_max.setRange(0, 999); self.hc_max.setValue(data.get("hc_max") or 0)
        self.hc_fixed   = QSpinBox(); self.hc_fixed.setRange(0, 999); self.hc_fixed.setValue(data.get("hc_fixed") or 0)
        self.changeover = QSpinBox(); self.changeover.setRange(0, 20)
        self.changeover.setSuffix(" shift(s)")
        self.changeover.setValue(int(data.get("changeover_shifts") or 0))
        self.note       = QLineEdit(data.get("note") or "")

        form.addRow("Room Code:",    self.room)
        form.addRow("Process Name:", self.proc)
        form.addRow("Type:",         self.ptype)
        form.addRow("UPPH (manual):",     self.upph)
        form.addRow("Room Type (e.g. TYPE-A):", self.rtype)
        form.addRow("UPH Fixed (auto):",  self.uph_fix)
        form.addRow("HC Min:",  self.hc_min)
        form.addRow("HC Max:",  self.hc_max)
        form.addRow("HC Fixed (auto):", self.hc_fixed)
        form.addRow("Changeover time:", self.changeover)
        form.addRow("Note:",    self.note)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok); btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _ok(self):
        self.result = {
            "room_code":         self.room.text().strip(),
            "process_name":      self.proc.text().strip(),
            "process_type":      self.ptype.currentText(),
            "room_type":         self.rtype.text().strip() or "TYPE-A",
            "upph":              self.upph.value() or None,
            "uph_fixed":         self.uph_fix.value() or None,
            "hc_min":            self.hc_min.value() or None,
            "hc_max":            self.hc_max.value() or None,
            "hc_fixed":          self.hc_fixed.value() or None,
            "changeover_shifts": self.changeover.value(),
            "note":              self.note.text() or None,
        }
        if not self.result["room_code"] or not self.result["process_name"]:
            QMessageBox.warning(self, "Error", "Room Code and Process Name required"); return
        self.accept()


# ─── Shift Config ─────────────────────────────────────────────────────────────

class ShiftConfigWidget(QWidget):
    COLS = ["Shift No", "Name", "Start", "End"]
    _READONLY_COLS = {0}  # Shift No is PK

    def __init__(self, parent=None):
        super().__init__(parent)
        self._master_tab = parent
        self._edit_mode = False
        self._changed_cells: set = set()
        self._loading = False
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        btn_add = QPushButton("➕ Add Shift"); btn_add.clicked.connect(self._add)
        bar.addWidget(btn_add)
        self._btn_edit = QPushButton("✏ Edit Mode")
        self._btn_edit.clicked.connect(self._toggle_edit_mode)
        self._btn_save = QPushButton("💾 Save Changes")
        self._btn_save.clicked.connect(self._save_changes)
        self._btn_save.setEnabled(False)
        bar.addWidget(self._btn_edit)
        bar.addWidget(self._btn_save)
        bar.addStretch()
        layout.addLayout(bar)
        self.table = _make_table(self.COLS)
        self.table.doubleClicked.connect(self._edit)
        self.table.itemChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table)
        layout.addWidget(QLabel("Note: Shift No 1=Day, 2=Night, 3=Third (optional)"))
        self._status = QLabel()
        layout.addWidget(self._status)
        self._load()

    def _load(self):
        self._loading = True
        self._changed_cells.clear()
        self._btn_save.setEnabled(False)
        rows = ShiftRepo.all()
        self.table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            for ci, v in enumerate([r["shift_no"], r["shift_name"], r["start_time"], r["end_time"]]):
                item = QTableWidgetItem(str(v))
                if ci in self._READONLY_COLS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(ri, ci, item)
        self._loading = False
        self._status.setText("")

    def _add(self):
        dlg = ShiftEditDialog({}, self)
        if dlg.exec(): ShiftRepo.upsert(dlg.result); self._load()

    def _edit(self):
        if self._edit_mode:
            return
        row = self.table.currentRow()
        if row < 0: return
        sno = int(self.table.item(row, 0).text())
        data = next((s for s in ShiftRepo.all() if s["shift_no"] == sno), {})
        dlg = ShiftEditDialog(data, self)
        if dlg.exec(): ShiftRepo.upsert(dlg.result); self._load()

    def _toggle_edit_mode(self):
        self._edit_mode = not self._edit_mode
        if self._edit_mode:
            self._btn_edit.setText("🔒 Exit Edit Mode")
            self._btn_edit.setStyleSheet(
                "background:#e65100; color:white; font-weight:bold;")
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked |
                QAbstractItemView.EditTrigger.EditKeyPressed)
            self._status.setText("✏ Edit mode — double-click or press F2 to edit")
            self._status.setStyleSheet(
                "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        else:
            self._btn_edit.setText("✏ Edit Mode")
            self._btn_edit.setStyleSheet("")
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            if not self._changed_cells:
                self._status.setText("")
                self._status.setStyleSheet("")

    def _on_cell_changed(self, item):
        if self._loading:
            return
        item.setBackground(QBrush(QColor("#fff9c4")))
        self._changed_cells.add((item.row(), item.column()))
        n = len({r for r, _ in self._changed_cells})
        self._status.setText(f"✏ {n} row(s) modified — unsaved")
        self._status.setStyleSheet(
            "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        self._btn_save.setEnabled(True)

    def _save_changes(self):
        if not self._changed_cells:
            return
        changed_rows = {r for r, _ in self._changed_cells}
        saved, errors = 0, []
        for ri in sorted(changed_rows):
            try:
                sno   = int(self.table.item(ri, 0).text())
                name  = self.table.item(ri, 1).text().strip()
                start = self.table.item(ri, 2).text().strip()
                end   = self.table.item(ri, 3).text().strip()
                ShiftRepo.upsert({
                    "shift_no":   sno,
                    "shift_name": name,
                    "start_time": start,
                    "end_time":   end,
                })
                saved += 1
            except Exception as e:
                errors.append(f"Row {ri + 1}: {e}")
        self._changed_cells.clear()
        self._btn_save.setEnabled(False)
        if errors:
            QMessageBox.warning(self, "Save Errors", "\n".join(errors))
        self._status.setText(f"✅ Saved {saved} row(s)")
        self._status.setStyleSheet("color:green; padding:4px;")
        mw = self._get_main_window()
        if mw:
            mw.gantt_tab.refresh()


class ShiftEditDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Shift")
        self.result = dict(data)
        layout = QVBoxLayout(self)
        form   = QFormLayout()
        self.sno   = QSpinBox(); self.sno.setRange(1, 3); self.sno.setValue(data.get("shift_no", 1))
        self.name  = QLineEdit(data.get("shift_name", ""))
        self.start = QLineEdit(data.get("start_time", "08:00"))
        self.end   = QLineEdit(data.get("end_time",   "20:00"))
        form.addRow("Shift No:",    self.sno)
        form.addRow("Name:",        self.name)
        form.addRow("Start (HH:MM):", self.start)
        form.addRow("End (HH:MM):",   self.end)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok); btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _ok(self):
        self.result = {
            "shift_no":   self.sno.value(),
            "shift_name": self.name.text(),
            "start_time": self.start.text(),
            "end_time":   self.end.text(),
        }
        self.accept()


# ─── Calendar ─────────────────────────────────────────────────────────────────

class CalendarWidget(QWidget):
    """날짜 × 생산실 × Shift 가동 여부 설정 그리드."""

    BG_OPEN    = QColor("#c8e6c9")
    BG_CLOSED  = QColor("#ffcdd2")
    BG_HOLD    = QColor("#ffe0b2")
    BG_PARTIAL = QColor("#fff9c4")   # yellow tint for partial hold

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dates: list = []
        self._room_shifts: list = []   # [(room_code, shift_no), ...]
        self._loading = False
        self._build_ui()

    def _get_main_window(self):
        w = self.parent()
        while w is not None:
            if hasattr(w, "gantt_tab"):
                return w
            w = w.parent()
        return None

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Controls ──────────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("From:"))
        self.date_from = QDateEdit(QDate.currentDate())
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        ctrl.addWidget(self.date_from)
        ctrl.addWidget(QLabel("To:"))
        self.date_to = QDateEdit(QDate.currentDate().addDays(27))
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        ctrl.addWidget(self.date_to)
        btn_load = QPushButton("Load")
        btn_load.clicked.connect(self._load)
        ctrl.addWidget(btn_load)
        ctrl.addSpacing(16)

        btn_open_wd  = QPushButton("Open Weekdays")
        btn_close_we = QPushButton("Close Weekends")
        btn_open_all = QPushButton("Open All")
        btn_close_all = QPushButton("Close All")
        btn_open_wd.clicked.connect(lambda: self._bulk("open_weekdays"))
        btn_close_we.clicked.connect(lambda: self._bulk("close_weekends"))
        btn_open_all.clicked.connect(lambda: self._bulk("open_all"))
        btn_close_all.clicked.connect(lambda: self._bulk("close_all"))
        for b in (btn_open_wd, btn_close_we, btn_open_all, btn_close_all):
            ctrl.addWidget(b)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        legend = QLabel(
            "● Green = Open   ● Red = Closed   ● Orange = Hold   ● Yellow = Partial (deduct min)  "
            "| Click to toggle Open/Closed | Right-click for Hold / Deduct Minutes")
        legend.setStyleSheet("font-size:10px; color:#555; padding:2px 0;")
        layout.addWidget(legend)

        # ── Grid ──────────────────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.horizontalHeader().setDefaultSectionSize(46)
        layout.addWidget(self.table, stretch=1)

        self._load()

    def refresh(self):
        self._load()

    def _load(self):
        d0 = self.date_from.date().toString("yyyy-MM-dd")
        d1 = self.date_to.date().toString("yyyy-MM-dd")
        cur  = datetime.strptime(d0, "%Y-%m-%d").date()
        end  = datetime.strptime(d1, "%Y-%m-%d").date()

        self._dates = []
        while cur <= end:
            self._dates.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)

        rooms  = RoomRepo.rooms()
        shifts = ShiftRepo.all()
        self._room_shifts = [(r, s["shift_no"]) for r in rooms for s in shifts]

        self._loading = True
        self.table.setRowCount(len(self._room_shifts))
        self.table.setColumnCount(2 + len(self._dates))
        headers = ["Room", "Shift"] + [d[5:] for d in self._dates]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        # Mark holiday columns in header (public + company)
        company_hdays = CompanyHolidayRepo.date_set()
        for di, d in enumerate(self._dates):
            d_obj = datetime.strptime(d, "%Y-%m-%d").date()
            pub_name = holiday_name(d_obj)
            comp_name = ""
            if d in company_hdays:
                row = next((r for r in CompanyHolidayRepo.all() if r["cal_date"] == d), None)
                comp_name = row["name"] if row else "Company Holiday"
            if pub_name or comp_name:
                hdr_item = self.table.horizontalHeaderItem(2 + di)
                if hdr_item:
                    hdr_item.setBackground(QBrush(QColor("#ffcdd2")))
                    hdr_item.setForeground(QBrush(QColor("#c62828")))
                    tip = " / ".join(filter(None, [pub_name, comp_name]))
                    hdr_item.setToolTip(tip)

        for ri, (room, sno) in enumerate(self._room_shifts):
            self.table.setItem(ri, 0, QTableWidgetItem(room))
            self.table.setItem(ri, 1, QTableWidgetItem(f"S{sno}"))
            for di, d in enumerate(self._dates):
                slot = CalendarRepo.get_slot(d, sno, room)
                self._paint_cell(ri, 2 + di, slot)
        self._loading = False

    def _paint_cell(self, row, col, slot):
        deduct = int(slot.get("deduct_minutes", 0)) if slot else 0
        if slot is None:
            text, bg = "✓", self.BG_OPEN
        elif slot["is_hold"]:
            text, bg = "H", self.BG_HOLD
        elif not slot["is_open"]:
            text, bg = "–", self.BG_CLOSED
        elif deduct > 0:
            text, bg = f"⏱{deduct}m", self.BG_PARTIAL
        else:
            text, bg = "✓", self.BG_OPEN
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setBackground(QBrush(bg))
        self.table.setItem(row, col, item)

    def _on_cell_clicked(self, row, col):
        if col < 2 or self._loading:
            return
        room, sno = self._room_shifts[row]
        d = self._dates[col - 2]
        slot = CalendarRepo.get_slot(d, sno, room)
        is_open_now = slot is None or (slot["is_open"] and not slot["is_hold"])
        CalendarRepo.set_slot(d, sno, room, is_open=0 if is_open_now else 1, is_hold=0)
        self._paint_cell(row, col, CalendarRepo.get_slot(d, sno, room))
        mw = self._get_main_window()
        if mw:
            mw.gantt_tab.refresh()

    def _context_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item or item.column() < 2:
            return
        row, col = item.row(), item.column()
        room, sno = self._room_shifts[row]
        d = self._dates[col - 2]
        slot = CalendarRepo.get_slot(d, sno, room)
        is_hold   = bool(slot and slot["is_hold"])
        deduct    = int(slot.get("deduct_minutes", 0)) if slot else 0
        menu = QMenu(self)
        if is_hold:
            menu.addAction("Remove Hold", lambda: self._toggle_hold(row, col, room, sno, d, False))
        else:
            menu.addAction("Set Hold", lambda: self._toggle_hold(row, col, room, sno, d, True))
        menu.addSeparator()
        if deduct > 0:
            menu.addAction(f"⏱ Clear Deduct ({deduct}m)",
                           lambda: self._set_deduct(row, col, room, sno, d, 0))
        menu.addAction("⏱ Set Deduct Minutes…",
                       lambda: self._set_deduct_dialog(row, col, room, sno, d, deduct))
        menu.exec(QCursor.pos())

    def _toggle_hold(self, row, col, room, sno, d, hold: bool):
        slot = CalendarRepo.get_slot(d, sno, room)
        deduct = int(slot.get("deduct_minutes", 0)) if slot else 0
        CalendarRepo.set_slot(d, sno, room, is_open=1, is_hold=1 if hold else 0,
                              deduct_minutes=deduct)
        self._paint_cell(row, col, CalendarRepo.get_slot(d, sno, room))
        mw = self._get_main_window()
        if mw:
            mw.gantt_tab.refresh()

    def _set_deduct(self, row, col, room, sno, d, minutes: int):
        slot = CalendarRepo.get_slot(d, sno, room)
        is_open = 1 if (slot is None or slot["is_open"]) else 0
        is_hold = int(slot["is_hold"]) if slot else 0
        CalendarRepo.set_slot(d, sno, room, is_open=is_open, is_hold=is_hold,
                              deduct_minutes=minutes)
        self._paint_cell(row, col, CalendarRepo.get_slot(d, sno, room))
        mw = self._get_main_window()
        if mw:
            mw.gantt_tab.refresh()

    def _set_deduct_dialog(self, row, col, room, sno, d, current: int):
        from PyQt6.QtWidgets import QDialog, QSpinBox, QFormLayout, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Set Deduct Minutes")
        spin = QSpinBox()
        spin.setRange(0, 720)
        spin.setSuffix(" min")
        spin.setValue(current)
        spin.setToolTip("Minutes unavailable in this shift (e.g. 150 for mandatory training)")
        form = QFormLayout(dlg)
        form.addRow(f"Deduct minutes  ({room} / S{sno} / {d}):", spin)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec():
            self._set_deduct(row, col, room, sno, d, spin.value())

    def _bulk(self, action: str):
        self._loading = True
        for di, d in enumerate(self._dates):
            d_obj = datetime.strptime(d, "%Y-%m-%d").date()
            is_we = d_obj.weekday() >= 5
            col   = 2 + di
            for ri, (room, sno) in enumerate(self._room_shifts):
                if action == "open_all":
                    open_v = 1
                elif action == "close_all":
                    open_v = 0
                elif action == "open_weekdays":
                    open_v = 0 if is_we else 1
                else:  # close_weekends
                    open_v = 0 if is_we else None
                if open_v is not None:
                    CalendarRepo.set_slot(d, sno, room, is_open=open_v, is_hold=0)
                    self._paint_cell(ri, col, {"is_open": open_v, "is_hold": 0})
        self._loading = False
        mw = self._get_main_window()
        if mw:
            mw.gantt_tab.refresh()


# ─── App Config ───────────────────────────────────────────────────────────────

class AppConfigWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Application Settings"))
        form = QFormLayout()

        self.max_pull = QSpinBox(); self.max_pull.setRange(1, 365)
        self.max_pull.setValue(int(ConfigRepo.get("max_pull_days", "45")))
        form.addRow("Max Pull-Forward Days:", self.max_pull)

        self.horizon = QSpinBox(); self.horizon.setRange(1, 12)
        self.horizon.setValue(int(ConfigRepo.get("plan_horizon_weeks", "4")))
        form.addRow("Plan Horizon (weeks):", self.horizon)

        self.merge_days = QSpinBox(); self.merge_days.setRange(1, 365)
        self.merge_days.setValue(int(ConfigRepo.get("material_due_merge_days", "21")))
        form.addRow("Material Due Merge Days:", self.merge_days)

        self.assign_mode = QComboBox()
        self.assign_mode.addItems(["CAPACITY", "UPH"])
        cur_mode = ConfigRepo.get("room_assign_mode", "CAPACITY").upper()
        self.assign_mode.setCurrentIndex(0 if cur_mode == "CAPACITY" else 1)
        form.addRow("Room Assignment Mode:", self.assign_mode)

        self.crp_path = QLineEdit(ConfigRepo.get("crp_excel_path", ""))
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_crp)
        crp_row = QHBoxLayout()
        crp_row.addWidget(self.crp_path)
        crp_row.addWidget(btn_browse)
        crp_widget = QWidget(); crp_widget.setLayout(crp_row)
        form.addRow("CRP Excel Path:", crp_widget)

        layout.addLayout(form)
        btn_save = QPushButton("💾 Save Settings")
        btn_save.clicked.connect(self._save)
        layout.addWidget(btn_save)

        # ── Company Holidays ──────────────────────────────────────────────────
        grp = QGroupBox("Company Holidays")
        grp_lay = QVBoxLayout(grp)

        self._hol_table = QTableWidget()
        self._hol_table.setColumnCount(2)
        self._hol_table.setHorizontalHeaderLabels(["Date", "Name"])
        self._hol_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._hol_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._hol_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._hol_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._hol_table.setMaximumHeight(200)
        grp_lay.addWidget(self._hol_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("＋ Add Holiday")
        btn_add.clicked.connect(self._add_holiday)
        self._btn_del_hol = QPushButton("🗑 Delete")
        self._btn_del_hol.clicked.connect(self._del_holiday)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(self._btn_del_hol)
        btn_row.addStretch()
        grp_lay.addLayout(btn_row)
        layout.addWidget(grp)
        layout.addStretch()

        self._load_holidays()

    def _load_holidays(self):
        rows = CompanyHolidayRepo.all()
        self._hol_table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            self._hol_table.setItem(ri, 0, QTableWidgetItem(r["cal_date"]))
            self._hol_table.setItem(ri, 1, QTableWidgetItem(r["name"]))

    def _add_holiday(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Company Holiday")
        dlg.setMinimumWidth(300)
        form = QFormLayout(dlg)
        date_edit = QDateEdit()
        date_edit.setCalendarPopup(True)
        from PyQt6.QtCore import QDate as _QDate
        date_edit.setDate(_QDate.currentDate())
        name_edit = QLineEdit()
        form.addRow("Date:", date_edit)
        form.addRow("Name:", name_edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = date_edit.date().toString("yyyy-MM-dd")
            n = name_edit.text().strip() or "Company Holiday"
            CompanyHolidayRepo.upsert(d, n)
            self._load_holidays()

    def _del_holiday(self):
        rows = {i.row() for i in self._hol_table.selectedItems()}
        if not rows:
            return
        for ri in sorted(rows, reverse=True):
            d = self._hol_table.item(ri, 0).text()
            CompanyHolidayRepo.delete(d)
        self._load_holidays()

    def _browse_crp(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select CRP Excel", "", "Excel (*.xlsx)")
        if path: self.crp_path.setText(path)

    def _save(self):
        ConfigRepo.set("max_pull_days",      str(self.max_pull.value()))
        ConfigRepo.set("plan_horizon_weeks", str(self.horizon.value()))
        ConfigRepo.set("crp_excel_path",     self.crp_path.text())
        ConfigRepo.set("room_assign_mode",   self.assign_mode.currentText())
        ConfigRepo.set("material_due_merge_days", str(self.merge_days.value()))
        QMessageBox.information(self, "Saved", "Settings saved.")


# ─── shared helper ────────────────────────────────────────────────────────────

def _make_table(cols: list, editable: bool = False) -> QTableWidget:
    t = QTableWidget()
    t.setColumnCount(len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    if editable:
        t.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked |
            QAbstractItemView.EditTrigger.EditKeyPressed)
    else:
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return t


# ─── Routing upload helpers (shared by SKUProcessWidget + ProcessRoutingWidget) ──

def _show_routing_result(parent, title: str, msg: str, warnings: list):
    """Show upload result — scrollable dialog when content is long."""
    body = msg
    if warnings:
        body += "\n\nWarnings acknowledged:\n" + "\n".join(f"  • {w}" for w in warnings)
    if len(body) < 300 and "\n" not in body[50:]:
        QMessageBox.information(parent, title, body)
        return
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(560, 340)
    lay = QVBoxLayout(dlg)
    te = QTextEdit(readOnly=True)
    te.setPlainText(body)
    lay.addWidget(te)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    bb.accepted.connect(dlg.accept)
    lay.addWidget(bb)
    dlg.exec()


class _RoutingWarningsDialog(QDialog):
    """Scrollable dialog asking user to confirm upload despite warnings."""
    def __init__(self, summary: str, warnings: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Routing Upload — Warnings Found")
        self.resize(580, 400)
        lay = QVBoxLayout(self)

        lbl = QLabel(
            f"<b>{summary}</b><br><br>"
            f"<span style='color:#D97706'>⚠ {len(warnings)} warning(s) found.</span><br>"
            "Routing steps have NOT been saved yet. Review the warnings below,<br>"
            "then choose <b>Upload Anyway</b> to proceed or <b>Cancel</b> to abort.")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        te = QTextEdit(readOnly=True)
        te.setPlainText("\n".join(f"• {w}" for w in warnings))
        te.setStyleSheet("background:#FFFDE7; border:1px solid #DDE3ED; border-radius:4px;")
        lay.addWidget(te)

        bb = QDialogButtonBox()
        btn_cancel = bb.addButton("Cancel",        QDialogButtonBox.ButtonRole.RejectRole)
        btn_upload = bb.addButton("Upload Anyway", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_upload.setStyleSheet(
            "background:#D97706; color:white; font-weight:bold; "
            "border:none; border-radius:5px; padding:5px 14px;")
        btn_cancel.setStyleSheet(
            "background:#DC2626; color:white; font-weight:bold; "
            "border:none; border-radius:5px; padding:5px 14px;")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def ask(parent, summary: str, warnings: list) -> bool:
        dlg = _RoutingWarningsDialog(summary, warnings, parent)
        return dlg.exec() == QDialog.DialogCode.Accepted


# ─── SKU Process Routing Widget ───────────────────────────────────────────────

class SKUProcessWidget(QWidget):
    """
    Manage SKU process routing — which processes a SKU requires,
    in what order, which room types are allowed, and which is the final step.
    """
    COLS = ["SKU Code", "Seq", "Process Name", "Allowed Room Types", "Final Step", "Note"]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # Filter by SKU
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Filter SKU:"))
        self.sku_filter = QComboBox()
        self.sku_filter.addItem("ALL")
        for s in SKURepo.all():
            self.sku_filter.addItem(s["sku_code"])
        self.sku_filter.currentTextChanged.connect(self._load)
        fbar.addWidget(self.sku_filter)

        btn_add  = QPushButton("➕ Add Step")
        btn_del  = QPushButton("🗑 Delete Step")
        btn_val  = QPushButton("✔ Validate")
        btn_up   = QPushButton("📤 Upload")
        btn_tmpl = QPushButton("⬇ Template")

        btn_add.clicked.connect(self._add)
        btn_del.clicked.connect(self._delete)
        btn_val.clicked.connect(self._validate)
        btn_up.clicked.connect(self._upload)
        btn_tmpl.clicked.connect(self._template)

        for b in (btn_add, btn_del, btn_val, btn_up, btn_tmpl):
            fbar.addWidget(b)
        fbar.addStretch()
        layout.addLayout(fbar)

        self.table = _make_table(self.COLS)
        self.table.doubleClicked.connect(self._edit)
        layout.addWidget(self.table)

        # Info label
        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self._load()

    def _load(self):
        sku_filter = self.sku_filter.currentText()
        if sku_filter == "ALL":
            rows = SKUProcessRepo.all()
        else:
            rows = SKUProcessRepo.for_sku(sku_filter)

        self.table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            is_final = "✅ YES" if r["is_final_seq"] else ""
            for ci, val in enumerate([
                r["sku_code"], r["process_seq"], r["process_name"],
                r["allowed_room_types"], is_final, r["note"] or ""
            ]):
                item = QTableWidgetItem(str(val))
                if r["is_final_seq"] and ci == 4:
                    item.setBackground(QBrush(QColor("#d4f0c0")))
                self.table.setItem(ri, ci, item)

    def _add(self):
        dlg = SKUProcessEditDialog({}, self)
        if dlg.exec():
            SKUProcessRepo.upsert(dlg.result)
            self._load()

    def _edit(self):
        row = self.table.currentRow()
        if row < 0:
            return
        sku = self.table.item(row, 0).text()
        seq = int(self.table.item(row, 1).text())
        existing = next(
            (r for r in SKUProcessRepo.for_sku(sku) if r["process_seq"] == seq), {}
        )
        dlg = SKUProcessEditDialog(existing, self)
        if dlg.exec():
            SKUProcessRepo.upsert(dlg.result)
            self._load()

    def _delete(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows:
            return
        items = [(self.table.item(r, 0).text(), int(self.table.item(r, 1).text())) for r in rows]
        n = len(rows)
        label = f"seq {items[0][1]} for {items[0][0]}" if n == 1 else f"{n} steps"
        if QMessageBox.question(
            self, "Delete", f"Delete {label}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        for sku, seq in items:
            SKUProcessRepo.delete(sku, seq)
        self._load()

    def _validate(self):
        sku_filter = self.sku_filter.currentText()
        skus = ([sku_filter] if sku_filter != "ALL"
                else [s["sku_code"] for s in SKURepo.all()])
        errors = []
        ok_count = 0
        for sku in skus:
            valid, msg = SKUProcessRepo.validate_routing(sku)
            if valid:
                ok_count += 1
            else:
                errors.append(msg)

        if errors:
            self.info_label.setText(
                f"⚠ {len(errors)} validation error(s):\n" + "\n".join(errors))
            self.info_label.setStyleSheet("color: red;")
        else:
            self.info_label.setText(f"✅ All {ok_count} SKU routing(s) valid.")
            self.info_label.setStyleSheet("color: green;")

    def _upload(self):
        from utils.excel_io import parse_process_routing_preview
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload SKU Process Excel", "", "Excel (*.xlsx)")
        if not path:
            return

        ok, err, headers, rows = parse_process_routing_preview(path)
        if not ok:
            QMessageBox.warning(self, "Parse Error", err)
            return
        dlg = UploadPreviewDialog("SKU Process Routing — Upload Preview", headers, rows, self)
        if not dlg.exec() or not dlg._confirmed:
            return

        ok, summary, warnings = upload_sku_process(path)
        if not ok:
            QMessageBox.warning(self, "Upload Failed", summary)
            return

        if warnings:
            confirmed = _RoutingWarningsDialog.ask(self, summary, warnings)
            if not confirmed:
                return
            ok, msg, _ = upload_sku_process(path, confirmed=True)
        else:
            msg = summary

        _show_routing_result(self, "Upload Complete", msg, warnings)
        self.sku_filter.clear()
        self.sku_filter.addItem("ALL")
        for s in SKURepo.all():
            self.sku_filter.addItem(s["sku_code"])
        self._load()

    def _template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Template", "SKUProcess_template.xlsx", "Excel (*.xlsx)")
        if path:
            ok, msg = download_sku_process_template(path)
            (QMessageBox.information if ok else QMessageBox.warning)(
                self, "Template", msg)


class SKUProcessEditDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SKU Process Step")
        self.result = dict(data)
        layout = QVBoxLayout(self)
        form   = QFormLayout()

        self.sku  = QComboBox()
        for s in SKURepo.all():
            self.sku.addItem(s["sku_code"])
        if data.get("sku_code"):
            idx = self.sku.findText(data["sku_code"])
            if idx >= 0:
                self.sku.setCurrentIndex(idx)

        self.seq   = QSpinBox(); self.seq.setRange(1, 99)
        self.seq.setValue(data.get("process_seq", 1))

        self.proc  = QComboBox()
        # Populate from distinct process names in room_master
        proc_names = sorted({r["process_name"] for r in RoomRepo.all()})
        for p in proc_names:
            self.proc.addItem(p)
        self.proc.setEditable(True)
        if data.get("process_name"):
            idx = self.proc.findText(data["process_name"])
            if idx >= 0:
                self.proc.setCurrentIndex(idx)
            else:
                self.proc.setCurrentText(data["process_name"])

        # Allowed room types — show available types as checkboxes
        self.allowed_edit = QLineEdit(data.get("allowed_room_types", ""))
        self.allowed_edit.setPlaceholderText("e.g. TYPE-A,TYPE-B  (must not be empty)")

        # Helper: show available room types
        available_types = RoomRepo.room_types()
        if available_types:
            hint = QLabel(f"Available room types: {', '.join(available_types)}")
            hint.setStyleSheet("color: #666; font-size: 10px;")

        self.is_final = QCheckBox("This is the final process step (MRP trigger)")
        self.is_final.setChecked(bool(data.get("is_final_seq", 0)))

        self.note  = QLineEdit(data.get("note") or "")

        form.addRow("SKU Code:",          self.sku)
        form.addRow("Process Seq:",       self.seq)
        form.addRow("Process Name:",      self.proc)
        form.addRow("Allowed Room Types:", self.allowed_edit)
        if available_types:
            form.addRow("", hint)
        form.addRow("",                   self.is_final)
        form.addRow("Note:",              self.note)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _ok(self):
        allowed = self.allowed_edit.text().strip()
        if not allowed:
            QMessageBox.warning(
                self, "Error",
                "Allowed Room Types must not be empty.\n"
                "Enter at least one room type (e.g. TYPE-A).")
            return
        self.result = {
            "sku_code":           self.sku.currentText(),
            "process_seq":        self.seq.value(),
            "process_name":       self.proc.currentText().strip(),
            "allowed_room_types": allowed,
            "is_final_seq":       1 if self.is_final.isChecked() else 0,
            "note":               self.note.text() or None,
        }
        self.accept()


class MaterialEditDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Material")
        self.result = dict(data)
        layout = QVBoxLayout(self)
        form   = QFormLayout()
        self.code = QLineEdit(data.get("material_code", ""))
        self.name = QLineEdit(data.get("material_name", ""))
        self.uom  = QSpinBox(); self.uom.setRange(1, 99999)
        self.uom.setValue(data.get("uom", 1))
        self.lead = QSpinBox(); self.lead.setRange(0, 365)
        self.lead.setValue(data.get("post_lead_days", 0))
        self.note = QLineEdit(data.get("note") or "")
        form.addRow("Material Code:",  self.code)
        form.addRow("Material Name:",  self.name)
        form.addRow("UoM:",            self.uom)
        form.addRow("Post Lead Days (QC):", self.lead)
        form.addRow("Note:",           self.note)
        layout.addLayout(form)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok); btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _ok(self):
        if not self.code.text().strip():
            QMessageBox.warning(self, "Error", "Material Code required"); return
        self.result = {
            "material_code":  self.code.text().strip(),
            "material_name":  self.name.text().strip(),
            "uom":            self.uom.value(),
            "post_lead_days": self.lead.value(),
            "note":           self.note.text() or None,
        }
        self.accept()


# ─── Process Routing Widget (unified SKU + MATERIAL) ─────────────────────────

class ProcessRoutingWidget(QWidget):
    COLS = ["Entity Type", "Entity Code", "Seq", "Process Name",
            "Allowed Room Types", "Needs Material", "Final", "Min Gap (shifts)", "Note"]
    _READONLY_COLS = {0, 1, 2}  # Entity Type, Entity Code, Seq are PK

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_mode = False
        self._changed_cells: set = set()
        self._loading = False
        layout = QVBoxLayout(self)

        fbar = QHBoxLayout()
        fbar.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["ALL", "SKU", "MATERIAL"])
        self.type_combo.currentTextChanged.connect(self._load)
        fbar.addWidget(self.type_combo)

        fbar.addWidget(QLabel("Code:"))
        self.code_filter = QLineEdit()
        self.code_filter.setPlaceholderText("filter…")
        self.code_filter.textChanged.connect(self._load)
        fbar.addWidget(self.code_filter)

        btn_add  = QPushButton("➕ Add");    btn_add.clicked.connect(self._add)
        btn_del  = QPushButton("🗑 Delete"); btn_del.clicked.connect(self._delete)
        btn_val  = QPushButton("✔ Validate"); btn_val.clicked.connect(self._validate)
        btn_up   = QPushButton("📤 Upload"); btn_up.clicked.connect(self._upload)
        btn_tmpl = QPushButton("⬇ Template"); btn_tmpl.clicked.connect(self._template)
        for b in (btn_add, btn_del, btn_val, btn_up, btn_tmpl): fbar.addWidget(b)
        self._btn_edit = QPushButton("✏ Edit Mode")
        self._btn_edit.clicked.connect(self._toggle_edit_mode)
        self._btn_save = QPushButton("💾 Save Changes")
        self._btn_save.clicked.connect(self._save_changes)
        self._btn_save.setEnabled(False)
        fbar.addWidget(self._btn_edit)
        fbar.addWidget(self._btn_save)
        fbar.addStretch()
        layout.addLayout(fbar)

        self.table = _make_table(self.COLS)
        self.table.doubleClicked.connect(self._edit)
        self.table.itemChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        self._load()

    def _load(self):
        self._loading = True
        self._changed_cells.clear()
        self._btn_save.setEnabled(False)
        from data.repositories import ProcessRoutingRepo
        rows = ProcessRoutingRepo.all()
        t = self.type_combo.currentText()
        f = self.code_filter.text().lower()
        if t != "ALL":
            rows = [r for r in rows if r["entity_type"] == t]
        if f:
            rows = [r for r in rows if f in r["entity_code"].lower()]

        self.table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            final_txt = "✅" if r["is_final_seq"] else ""
            gap_val   = int(r.get("min_gap_shifts") or 0)
            gap_txt   = str(gap_val)
            for ci, val in enumerate([
                r["entity_type"], r["entity_code"], r["process_seq"],
                r["process_name"], r["allowed_room_types"],
                r.get("requires_material_code") or "",
                final_txt, gap_txt, r["note"] or ""
            ]):
                item = QTableWidgetItem(str(val))
                if ci in self._READONLY_COLS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if ci == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r)
                if r["is_final_seq"] and ci == 6:
                    item.setBackground(QBrush(QColor("#d4f0c0")))
                if r.get("requires_material_code") and ci == 5:
                    item.setBackground(QBrush(QColor("#fff0d0")))
                if gap_val > 0 and ci == 7:
                    item.setBackground(QBrush(QColor("#e8f0ff")))
                self.table.setItem(ri, ci, item)
        self._loading = False

    def _add(self):
        dlg = ProcessRoutingEditDialog({}, self)
        if dlg.exec():
            from data.repositories import ProcessRoutingRepo
            ProcessRoutingRepo.upsert(dlg.result); self._load()

    def _edit(self):
        if self._edit_mode:
            return
        row = self.table.currentRow()
        if row < 0: return
        from data.repositories import ProcessRoutingRepo
        et   = self.table.item(row, 0).text()
        code = self.table.item(row, 1).text()
        seq  = int(self.table.item(row, 2).text())
        existing = next(
            (r for r in ProcessRoutingRepo.for_entity(et, code)
             if r["process_seq"] == seq), {})
        dlg = ProcessRoutingEditDialog(existing, self)
        if dlg.exec():
            ProcessRoutingRepo.upsert(dlg.result); self._load()

    def _delete(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows: return
        from data.repositories import ProcessRoutingRepo
        items = [
            (self.table.item(r, 0).text(),
             self.table.item(r, 1).text(),
             int(self.table.item(r, 2).text()))
            for r in rows
        ]
        n = len(rows)
        label = f"{items[0][0]} {items[0][1]} seq {items[0][2]}" if n == 1 else f"{n} steps"
        if QMessageBox.question(
            self, "Delete", f"Delete {label}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        for et, code, seq in items:
            ProcessRoutingRepo.delete(et, code, seq)
        self._load()

    def _toggle_edit_mode(self):
        self._edit_mode = not self._edit_mode
        if self._edit_mode:
            self._btn_edit.setText("🔒 Exit Edit Mode")
            self._btn_edit.setStyleSheet(
                "background:#e65100; color:white; font-weight:bold;")
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked |
                QAbstractItemView.EditTrigger.EditKeyPressed)
            self.info_label.setText("✏ Edit mode — Final column: '✅' or blank; Min Gap: integer")
            self.info_label.setStyleSheet(
                "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        else:
            self._btn_edit.setText("✏ Edit Mode")
            self._btn_edit.setStyleSheet("")
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            if not self._changed_cells:
                self.info_label.setText("")
                self.info_label.setStyleSheet("")

    def _on_cell_changed(self, item):
        if self._loading:
            return
        item.setBackground(QBrush(QColor("#fff9c4")))
        self._changed_cells.add((item.row(), item.column()))
        n = len({r for r, _ in self._changed_cells})
        self.info_label.setText(f"✏ {n} row(s) modified — unsaved")
        self.info_label.setStyleSheet(
            "color:#7a5800; background:#fff9c4; padding:4px; border-radius:4px;")
        self._btn_save.setEnabled(True)

    def _save_changes(self):
        if not self._changed_cells:
            return
        from data.repositories import ProcessRoutingRepo
        changed_rows = {r for r, _ in self._changed_cells}
        saved, errors = 0, []
        for ri in sorted(changed_rows):
            try:
                orig         = self.table.item(ri, 0).data(Qt.ItemDataRole.UserRole) or {}
                entity_type  = self.table.item(ri, 0).text().strip()
                entity_code  = self.table.item(ri, 1).text().strip()
                seq          = int(self.table.item(ri, 2).text())
                process_name = self.table.item(ri, 3).text().strip()
                allowed      = self.table.item(ri, 4).text().strip()
                req_mat      = self.table.item(ri, 5).text().strip() or None
                final_txt    = self.table.item(ri, 6).text().strip()
                is_final     = 1 if final_txt in ("✅", "1", "yes", "true") else 0
                min_gap      = int(self.table.item(ri, 7).text() or 0)
                note         = self.table.item(ri, 8).text() or None
                if not allowed:
                    errors.append(f"Row {ri+1}: Allowed Room Types cannot be empty")
                    continue
                ProcessRoutingRepo.upsert({
                    "entity_type":            entity_type,
                    "entity_code":            entity_code,
                    "process_seq":            seq,
                    "process_name":           process_name,
                    "allowed_room_types":     allowed,
                    "requires_material_code": req_mat,
                    "is_final_seq":           is_final,
                    "min_gap_shifts":         min_gap,
                    "note":                   note,
                })
                saved += 1
            except Exception as e:
                errors.append(f"Row {ri + 1}: {e}")
        self._changed_cells.clear()
        self._btn_save.setEnabled(False)
        if errors:
            QMessageBox.warning(self, "Save Errors", "\n".join(errors))
        self.info_label.setText(f"✅ Saved {saved} row(s)")
        self.info_label.setStyleSheet("color:green; padding:4px;")

    def _validate(self):
        from data.repositories import ProcessRoutingRepo, SKURepo, MaterialRepo
        errors, ok_count = [], 0
        for s in SKURepo.all():
            v, m = ProcessRoutingRepo.validate("SKU", s["sku_code"])
            if v: ok_count += 1
            else: errors.append(m)
        for m in MaterialRepo.all():
            v, msg = ProcessRoutingRepo.validate("MATERIAL", m["material_code"])
            if v: ok_count += 1
            else: errors.append(msg)
        if errors:
            self.info_label.setText("⚠ " + "\n".join(errors))
            self.info_label.setStyleSheet("color:red;")
        else:
            self.info_label.setText(f"✅ All {ok_count} routings valid.")
            self.info_label.setStyleSheet("color:green;")

    def _upload(self):
        from utils.excel_io import upload_process_routing, parse_process_routing_preview
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload Routing Excel", "", "Excel (*.xlsx)")
        if not path:
            return

        ok, err, headers, rows = parse_process_routing_preview(path)
        if not ok:
            QMessageBox.warning(self, "Parse Error", err)
            return
        dlg = UploadPreviewDialog("Process Routing — Upload Preview", headers, rows, self)
        if not dlg.exec() or not dlg._confirmed:
            return

        ok, summary, warnings = upload_process_routing(path)
        if not ok:
            QMessageBox.warning(self, "Upload Failed", summary)
            return

        if warnings:
            confirmed = _RoutingWarningsDialog.ask(self, summary, warnings)
            if not confirmed:
                return
            ok, msg, _ = upload_process_routing(path, confirmed=True)
        else:
            msg = summary

        _show_routing_result(self, "Upload Complete", msg, warnings)
        self._load()

    def _template(self):
        from utils.excel_io import download_process_routing_template
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Template", "ProcessRouting_template.xlsx",
            "Excel (*.xlsx)")
        if path:
            ok, msg = download_process_routing_template(path)
            (QMessageBox.information if ok else QMessageBox.warning)(
                self, "Template", msg)


class ProcessRoutingEditDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Process Routing Step")
        self.result = dict(data)
        layout = QVBoxLayout(self)
        form   = QFormLayout()

        self.etype = QComboBox()
        self.etype.addItems(["SKU", "MATERIAL"])
        if data.get("entity_type") == "MATERIAL":
            self.etype.setCurrentIndex(1)

        self.ecode = QComboBox()
        self.ecode.setEditable(True)
        self._reload_codes()
        self.etype.currentTextChanged.connect(self._reload_codes)
        if data.get("entity_code"):
            self.ecode.setCurrentText(data["entity_code"])

        self.seq   = QSpinBox(); self.seq.setRange(1, 99)
        self.seq.setValue(data.get("process_seq", 1))

        self.proc = QComboBox(); self.proc.setEditable(True)
        for p in sorted({r["process_name"] for r in RoomRepo.all()}):
            self.proc.addItem(p)
        if data.get("process_name"):
            self.proc.setCurrentText(data["process_name"])

        self.allowed = QLineEdit(data.get("allowed_room_types", ""))
        self.allowed.setPlaceholderText("e.g. TYPE-A,TYPE-B (required)")

        avail = RoomRepo.room_types()
        if avail:
            form.addRow("", QLabel(f"Available types: {', '.join(avail)}"))

        self.req_mat = QComboBox(); self.req_mat.setEditable(True)
        self.req_mat.addItem("")
        from data.repositories import MaterialRepo
        for m in MaterialRepo.all():
            self.req_mat.addItem(m["material_code"])
        if data.get("requires_material_code"):
            self.req_mat.setCurrentText(data["requires_material_code"])

        self.is_final = QCheckBox("Final step (MRP trigger)")
        self.is_final.setChecked(bool(data.get("is_final_seq", 0)))

        self.min_gap = QSpinBox()
        self.min_gap.setRange(0, 30)
        self.min_gap.setSuffix(" shift(s)")
        self.min_gap.setValue(int(data.get("min_gap_shifts") or 0))
        self.min_gap.setToolTip(
            "Empty shifts required between the previous step's end and this step's start.\n"
            "0 = adjacent shift OK  |  1 = 1 shift gap  |  2 ≈ 1 day (2-shift system)")

        self.note = QLineEdit(data.get("note") or "")

        form.addRow("Entity Type:",         self.etype)
        form.addRow("Entity Code:",         self.ecode)
        form.addRow("Sequence:",            self.seq)
        form.addRow("Process Name:",        self.proc)
        form.addRow("Allowed Room Types:",  self.allowed)
        form.addRow("Requires Material:",   self.req_mat)
        form.addRow("",                     self.is_final)
        form.addRow("Min Gap Before Step:", self.min_gap)
        form.addRow("Note:",                self.note)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok); btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _reload_codes(self):
        self.ecode.clear()
        from data.repositories import SKURepo, MaterialRepo
        if self.etype.currentText() == "SKU":
            for s in SKURepo.all(): self.ecode.addItem(s["sku_code"])
        else:
            for m in MaterialRepo.all(): self.ecode.addItem(m["material_code"])

    def _ok(self):
        allowed = self.allowed.text().strip()
        if not allowed:
            QMessageBox.warning(self, "Error",
                "Allowed Room Types must not be empty."); return
        req_mat = self.req_mat.currentText().strip() or None

        # Mandatory: material referenced in routing must exist in material_master
        if req_mat:
            from data.repositories import MaterialRepo
            if not MaterialRepo.get(req_mat):
                reply = QMessageBox.question(
                    self, "Material Not Defined",
                    f"Material '{req_mat}' is not defined in Item Master.\n"
                    f"Define it now? (Required to save this routing step.)",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
                dlg = MaterialEditDialog({"material_code": req_mat}, self)
                if not dlg.exec():
                    return  # user cancelled — block save
                MaterialRepo.upsert(dlg.result)
                self.req_mat.addItem(dlg.result["material_code"])

        self.result = {
            "entity_type":            self.etype.currentText(),
            "entity_code":            self.ecode.currentText().strip(),
            "process_seq":            self.seq.value(),
            "process_name":           self.proc.currentText().strip(),
            "allowed_room_types":     allowed,
            "requires_material_code": req_mat,
            "is_final_seq":           1 if self.is_final.isChecked() else 0,
            "min_gap_shifts":         self.min_gap.value(),
            "note":                   self.note.text() or None,
        }
        self.accept()
