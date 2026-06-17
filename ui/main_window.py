"""
Main application window.
Tab layout:
  0 - Gantt Planner
  1 - SO Management
  2 - Master Management (SKU / Room / Shift / Config)
  3 - CRP
  4 - Actuals Entry
  5 - Alerts / Conflicts
  6 - Dashboard
  7 - Inventory

New Window:
  - Right-click tab header → "Open in New Window"
  - Ctrl+N → open current active tab in new window
  - Detached window shares the same widget instance (same DB, same signals)
  - Closing detached window re-docks the tab back to the main window
  - A refresh timer keeps all open views in sync (every 10 s)
"""
import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget,
    QVBoxLayout, QStatusBar, QToolBar, QLabel, QMessageBox,
    QTabBar, QMenu
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QFileSystemWatcher
from PyQt6.QtGui import QAction, QFont, QKeySequence, QShortcut

from data.database import init_db
from data.crp_excel import crp_manager

from ui.gantt_tab import GanttTab
from ui.so_tab import SOTab
from ui.master_tab import MasterTab
from ui.crp_tab import CRPTab
from ui.actuals_tab import ActualsTab
from ui.alerts_tab import AlertsTab
from ui.dashboard_tab import DashboardTab
from ui.remaining_tabs import InventoryTab, ReleaseReportTab


# ─── Detached full window ────────────────────────────────────────────────────

class DetachedWindow(QMainWindow):
    """
    A secondary window with the full tab structure, backed by the same DB.
    Opens independently — main window keeps all its tabs intact.
    Auto-refreshes the active tab every 10 s.
    """

    def __init__(self, start_tab_index: int, main_window: "MainWindow"):
        super().__init__()
        self.setWindowTitle("Production Planner — Secondary Window")
        self.setMinimumSize(1400, 800)
        self._main_window = main_window
        self._build_ui(start_tab_index)
        self._build_toolbar()
        self._build_statusbar()
        self._start_refresh_timer()

    def _build_ui(self, start_tab_index: int):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.gantt_tab       = GanttTab(self)
        self.so_tab          = SOTab(self)
        self.master_tab      = MasterTab(self)
        self.crp_tab         = CRPTab(self)
        self.actuals_tab     = ActualsTab(self)
        self.alerts_tab      = AlertsTab(self)
        self.dashboard_tab   = DashboardTab(self)
        self.inventory_tab   = InventoryTab(self)
        self.release_tab     = ReleaseReportTab(self)

        _tab_defs = [
            (self.gantt_tab,     "📅  Gantt Planner"),
            (self.so_tab,        "📋  Sales Orders"),
            (self.master_tab,    "🗂  Masters"),
            (self.crp_tab,       "👥  CRP"),
            (self.actuals_tab,   "✅  Actuals"),
            (self.alerts_tab,    "⚠️  Alerts"),
            (self.dashboard_tab, "📊  Dashboard"),
            (self.inventory_tab, "📦  Inventory"),
            (self.release_tab,   "🚀  Release Report"),
        ]
        for widget, title in _tab_defs:
            self.tabs.addTab(widget, title)

        self.tabs.setCurrentIndex(
            max(0, min(start_tab_index, self.tabs.count() - 1)))
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

    def _build_toolbar(self):
        tb = self.addToolBar("Toolbar")
        tb.setMovable(False)

        act_replan = QAction("🔄  Re-Plan", self)
        act_replan.triggered.connect(self.gantt_tab.run_auto_plan)
        tb.addAction(act_replan)

        act_pull = QAction("⬅  Pull Forward", self)
        act_pull.triggered.connect(self.gantt_tab.run_pull_forward)
        tb.addAction(act_pull)

        act_clear = QAction("🗑  Clear Plan", self)
        act_clear.triggered.connect(self.gantt_tab.run_clear_plan)
        tb.addAction(act_clear)

        tb.addSeparator()

        act_crp = QAction("♻  Refresh CRP", self)
        act_crp.triggered.connect(self._refresh_crp)
        tb.addAction(act_crp)

        tb.addSeparator()

        act_conflicts = QAction("🔍  Check Conflicts", self)
        act_conflicts.triggered.connect(self._check_conflicts)
        tb.addAction(act_conflicts)

    def _build_statusbar(self):
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self.conflict_label = QLabel("  ✅ No conflicts")
        self.conflict_label.setStyleSheet("color: green;")
        self._status.addPermanentWidget(self.conflict_label)

    def _start_refresh_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)
        self._timer.start(10_000)

    def _auto_refresh(self):
        tab = self.tabs.currentWidget()
        if hasattr(tab, "refresh"):
            try:
                tab.refresh()
            except Exception:
                pass

    def _on_tab_changed(self, idx: int):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._status.showMessage("Loading…")
        QApplication.processEvents()
        try:
            tab = self.tabs.widget(idx)
            if hasattr(tab, "refresh"):
                tab.refresh()
        finally:
            QApplication.restoreOverrideCursor()
            self._status.clearMessage()

    def _refresh_crp(self):
        from data.crp_excel import crp_manager
        ok, msg = crp_manager.refresh()
        if ok:
            self._status.showMessage(msg, 5000)
            self.gantt_tab.refresh()
        else:
            QMessageBox.warning(self, "CRP Refresh", msg)

    def _check_conflicts(self):
        self._check_conflicts_silent()
        self.tabs.setCurrentWidget(self.alerts_tab)
        self.alerts_tab.refresh()

    def _check_conflicts_silent(self):
        from core.scheduler import scheduler
        from datetime import date, timedelta
        today = date.today()
        d0 = today.strftime("%Y-%m-%d")
        d1 = (today + timedelta(weeks=4)).strftime("%Y-%m-%d")
        try:
            conflicts = scheduler.detect_conflicts(d0, d1)
            if conflicts:
                n = len(conflicts)
                self.conflict_label.setText(f"  ⚠  {n} conflict{'s' if n>1 else ''}")
                self.conflict_label.setStyleSheet("color: red; font-weight: bold;")
            else:
                self.conflict_label.setText("  ✅ No conflicts")
                self.conflict_label.setStyleSheet("color: green;")
        except Exception:
            pass

    def notify(self, msg: str, level: str = "info"):
        self._status.showMessage(msg, 5000)

    def closeEvent(self, event):
        self._timer.stop()
        self._main_window._detached_windows.discard(self)
        self._main_window._update_detached_label()
        event.accept()


# ─── Custom tab bar with right-click menu ────────────────────────────────────

class RightClickTabBar(QTabBar):
    def __init__(self, main_window: "MainWindow", parent=None):
        super().__init__(parent)
        self._main_window = main_window

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            idx = self.tabAt(event.pos())
            if idx >= 0:
                self._show_context_menu(idx, event.globalPosition().toPoint())
                return
        super().mousePressEvent(event)

    def _show_context_menu(self, tab_index: int, pos: QPoint):
        menu = QMenu(self)
        act_new = menu.addAction("🗗  Open in New Window")
        act_new.triggered.connect(
            lambda: self._main_window._detach_tab(tab_index))
        menu.exec(pos)


# ─── Main window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Production Planner")
        self.setMinimumSize(1400, 800)
        self._detached_windows: set = set()
        self._init_db()
        self._build_ui()
        self._build_toolbar()
        self._build_statusbar()
        self._start_conflict_timer()
        self._setup_shortcuts()
        self._setup_crp_watcher()

    def _init_db(self):
        try:
            init_db()
        except Exception as e:
            QMessageBox.critical(self, "DB Error", str(e))

    def _build_ui(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # Replace default tab bar with right-click-aware one
        custom_bar = RightClickTabBar(self, self.tabs)
        self.tabs.setTabBar(custom_bar)

        self.gantt_tab       = GanttTab(self)
        self.so_tab          = SOTab(self)
        self.master_tab      = MasterTab(self)
        self.crp_tab         = CRPTab(self)
        self.actuals_tab     = ActualsTab(self)
        self.alerts_tab      = AlertsTab(self)
        self.dashboard_tab   = DashboardTab(self)
        self.inventory_tab   = InventoryTab(self)
        self.release_tab     = ReleaseReportTab(self)

        self._tab_defs = [
            (self.gantt_tab,     "📅  Gantt Planner"),
            (self.so_tab,        "📋  Sales Orders"),
            (self.master_tab,    "🗂  Masters"),
            (self.crp_tab,       "👥  CRP"),
            (self.actuals_tab,   "✅  Actuals"),
            (self.alerts_tab,    "⚠️  Alerts"),
            (self.dashboard_tab, "📊  Dashboard"),
            (self.inventory_tab, "📦  Inventory"),
            (self.release_tab,   "🚀  Release Report"),
        ]
        for widget, title in self._tab_defs:
            self.tabs.addTab(widget, title)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)

        act_replan = QAction("🔄  Re-Plan", self)
        act_replan.setToolTip("Run auto-planning for the current horizon")
        act_replan.triggered.connect(self.gantt_tab.run_auto_plan)
        tb.addAction(act_replan)

        act_pull = QAction("⬅  Pull Forward", self)
        act_pull.setToolTip("Pull unscheduled capacity forward")
        act_pull.triggered.connect(self.gantt_tab.run_pull_forward)
        tb.addAction(act_pull)

        act_clear_plan = QAction("🗑  Clear Plan", self)
        act_clear_plan.setToolTip(
            "Delete ALL production plans (SKU + MATERIAL). Asks to confirm first.")
        act_clear_plan.triggered.connect(self.gantt_tab.run_clear_plan)
        tb.addAction(act_clear_plan)

        tb.addSeparator()

        act_crp_refresh = QAction("♻  Refresh CRP", self)
        act_crp_refresh.setToolTip("Reload CRP data from Excel")
        act_crp_refresh.triggered.connect(self._refresh_crp)
        tb.addAction(act_crp_refresh)

        tb.addSeparator()

        act_conflicts = QAction("🔍  Check Conflicts", self)
        act_conflicts.triggered.connect(self._check_conflicts)
        tb.addAction(act_conflicts)

        tb.addSeparator()

        act_new_win = QAction("🗗  New Window  (Ctrl+N)", self)
        act_new_win.setToolTip(
            "Open current tab in a new window\n"
            "Or right-click any tab header")
        act_new_win.triggered.connect(self._detach_current_tab)
        tb.addAction(act_new_win)

    def _build_statusbar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.conflict_label = QLabel("  ✅ No conflicts")
        self.conflict_label.setStyleSheet("color: green;")
        self.status.addPermanentWidget(self.conflict_label)

        # Detached windows count indicator
        self.detached_label = QLabel()
        self.status.addPermanentWidget(self.detached_label)

    def _setup_shortcuts(self):
        sc = QShortcut(QKeySequence("Ctrl+N"), self)
        sc.activated.connect(self._detach_current_tab)

    def _start_conflict_timer(self):
        self._conflict_timer = QTimer(self)
        self._conflict_timer.timeout.connect(self._check_conflicts_silent)
        self._conflict_timer.start(30_000)

    # ── New window / detach ──────────────────────────────────────────────────

    def _detach_current_tab(self):
        self._open_new_window(self.tabs.currentIndex())

    def _detach_tab(self, tab_index: int):
        self._open_new_window(tab_index)

    def _open_new_window(self, start_tab_index: int):
        win = DetachedWindow(start_tab_index, self)
        self._detached_windows.add(win)
        win.show()
        self._update_detached_label()
        self.notify("New window opened.")

    def _update_detached_label(self):
        n = len(self._detached_windows)
        if n:
            self.detached_label.setText(f"  🗗 {n} detached")
            self.detached_label.setStyleSheet("color: #4472C4; font-size:11px;")
        else:
            self.detached_label.setText("")

    # ── Tab / refresh ────────────────────────────────────────────────────────

    def _on_tab_changed(self, idx: int):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.status.showMessage("Loading…")
        QApplication.processEvents()
        try:
            tab = self.tabs.widget(idx)
            if hasattr(tab, "refresh"):
                tab.refresh()
        finally:
            QApplication.restoreOverrideCursor()
            self.status.clearMessage()

    def _setup_crp_watcher(self):
        from data.repositories import ConfigRepo as _CR
        self._crp_watcher = QFileSystemWatcher(self)
        self._crp_watcher.fileChanged.connect(self._on_crp_file_changed)
        self._watch_crp_path(_CR.get("crp_excel_path", ""))

    def _watch_crp_path(self, path: str):
        if self._crp_watcher.files():
            self._crp_watcher.removePaths(self._crp_watcher.files())
        import os
        if path and os.path.exists(path):
            self._crp_watcher.addPath(path)

    def _on_crp_file_changed(self, path: str):
        # Re-add path: some editors replace the file (inode changes) on save
        import os
        if os.path.exists(path):
            self._crp_watcher.addPath(path)
        self._do_crp_refresh(silent=True)

    def _do_crp_refresh(self, silent: bool = False):
        from core.scheduler import scheduler as _sched
        ok, msg = crp_manager.refresh()
        _sched._reload_masters()
        if ok:
            self.gantt_tab.refresh()
            self.crp_tab.refresh()
            if not silent:
                self.status.showMessage(msg, 5000)
            else:
                self.status.showMessage("CRP auto-refreshed.", 3000)
        elif not silent:
            QMessageBox.warning(self, "CRP Refresh", msg)

    def _refresh_crp(self):
        self._do_crp_refresh(silent=False)
        self._watch_crp_path(
            __import__("data.repositories", fromlist=["ConfigRepo"])
            .ConfigRepo.get("crp_excel_path", "")
        )

    def _check_conflicts(self):
        self._check_conflicts_silent()
        # alerts_tab may be detached — try to find it
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) is self.alerts_tab:
                self.tabs.setCurrentIndex(i)
                self.alerts_tab.refresh()
                return
        # If detached, just refresh it in its window
        self.alerts_tab.refresh()

    def _check_conflicts_silent(self):
        from core.scheduler import scheduler
        from datetime import date, timedelta
        today = date.today()
        d0 = today.strftime("%Y-%m-%d")
        d1 = (today + timedelta(weeks=4)).strftime("%Y-%m-%d")
        try:
            conflicts = scheduler.detect_conflicts(d0, d1)
            if conflicts:
                n = len(conflicts)
                self.conflict_label.setText(
                    f"  ⚠  {n} conflict{'s' if n>1 else ''}")
                self.conflict_label.setStyleSheet(
                    "color: red; font-weight: bold;")
                # Update alerts tab text if still docked
                for i in range(self.tabs.count()):
                    if self.tabs.widget(i) is self.alerts_tab:
                        self.tabs.setTabText(i, f"⚠️  Alerts ({n})")
            else:
                self.conflict_label.setText("  ✅ No conflicts")
                self.conflict_label.setStyleSheet("color: green;")
                for i in range(self.tabs.count()):
                    if self.tabs.widget(i) is self.alerts_tab:
                        self.tabs.setTabText(i, "⚠️  Alerts")
        except Exception:
            pass

    def notify(self, msg: str, level: str = "info"):
        self.status.showMessage(msg, 5000)
        for win in list(self._detached_windows):
            try:
                win.notify(msg)
            except Exception:
                pass

    def refresh_all(self):
        """Refresh all tabs (main + secondary windows)."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if hasattr(w, "refresh"):
                try:
                    w.refresh()
                except Exception:
                    pass
        for win in list(self._detached_windows):
            try:
                win._auto_refresh()
            except Exception:
                pass

    def closeEvent(self, event):
        event.accept()
