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
    QApplication, QMainWindow, QTabWidget, QStackedWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QStatusBar, QToolBar, QLabel,
    QMessageBox, QTabBar, QMenu, QPushButton, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QFileSystemWatcher, QByteArray, QSize
from PyQt6.QtGui import QAction, QFont, QKeySequence, QShortcut, QColor, QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer

from data.database import init_db
from data.crp_excel import crp_manager

from ui.gantt_tab import GanttTab
from ui.so_tab import SOTab, InternalOrderTab
from ui.master_tab import MasterTab
from ui.crp_tab import CRPTab
from ui.actuals_tab import ActualsTab
from ui.alerts_tab import AlertsTab
from ui.dashboard_tab import DashboardTab
from ui.remaining_tabs import (InventoryTab, ReleaseReportTab, ImpactReportTab,
                               ScenarioTab, LaborUtilizationTab)
from ui.dispatch_tab import DispatchListTab
from ui.help_tab import HelpTab


# ─── SVG icon helpers ────────────────────────────────────────────────────────

def _svg_pixmap(svg_body: str, color: str, size: int = 16) -> QPixmap:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"'
        f' stroke="{color}" fill="none" stroke-width="1.8"'
        f' stroke-linecap="round" stroke-linejoin="round">'
        f'{svg_body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    renderer.render(painter)
    painter.end()
    return px


def _rail_icon(svg_body: str, size: int = 16) -> QIcon:
    """Rail nav icon: dark slate when inactive, blue when checked, darker on hover."""
    icon = QIcon()
    icon.addPixmap(_svg_pixmap(svg_body, "#475569", size),
                   QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_svg_pixmap(svg_body, "#1d4ed8", size),
                   QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(_svg_pixmap(svg_body, "#1e293b", size),
                   QIcon.Mode.Active, QIcon.State.Off)
    return icon


# Feather-icon SVG bodies (viewBox="0 0 24 24")
_IC_GANTT    = ('<rect x="3" y="4" width="18" height="18" rx="2"/>'
                '<line x1="16" y1="2" x2="16" y2="6"/>'
                '<line x1="8" y1="2" x2="8" y2="6"/>'
                '<line x1="3" y1="10" x2="21" y2="10"/>')

_IC_SO       = ('<rect x="5" y="4" width="14" height="18" rx="2"/>'
                '<line x1="8" y1="10" x2="16" y2="10"/>'
                '<line x1="8" y1="14" x2="16" y2="14"/>'
                '<line x1="8" y1="18" x2="13" y2="18"/>')

_IC_RELEASE  = ('<line x1="22" y1="2" x2="11" y2="13"/>'
                '<polygon points="22 2 15 22 11 13 2 9 22 2"/>')

_IC_CRP      = ('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
                '<circle cx="9" cy="7" r="4"/>'
                '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'
                '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>')

_IC_INVENTORY= ('<path d="M21 8l-9-5-9 5 9 5 9-5z"/>'
                '<path d="M3 8v8l9 5 9-5V8"/>'
                '<path d="M12 13v8"/>')

_IC_ACTUALS  = ('<circle cx="12" cy="12" r="10"/>'
                '<polyline points="9 12 12 15 17 9"/>')

_IC_ALERTS   = ('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94'
                'a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
                '<line x1="12" y1="9" x2="12" y2="13"/>'
                '<line x1="12" y1="17" x2="12.01" y2="17"/>')

_IC_DASHBOARD= ('<line x1="6" y1="20" x2="6" y2="10"/>'
                '<line x1="12" y1="20" x2="12" y2="4"/>'
                '<line x1="18" y1="20" x2="18" y2="14"/>')

_IC_HELP     = ('<circle cx="12" cy="12" r="10"/>'
                '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
                '<line x1="12" y1="17" x2="12.01" y2="17"/>')

_IC_LABOR    = ('<rect x="3" y="3" width="18" height="18" rx="2"/>'
                '<line x1="3" y1="9" x2="21" y2="9"/>'
                '<line x1="3" y1="15" x2="21" y2="15"/>'
                '<line x1="9" y1="3" x2="9" y2="21"/>'
                '<line x1="15" y1="3" x2="15" y2="21"/>')

_IC_MASTERS  = ('<line x1="4" y1="21" x2="4" y2="14"/>'
                '<line x1="4" y1="10" x2="4" y2="3"/>'
                '<line x1="12" y1="21" x2="12" y2="12"/>'
                '<line x1="12" y1="8" x2="12" y2="3"/>'
                '<line x1="20" y1="21" x2="20" y2="16"/>'
                '<line x1="20" y1="12" x2="20" y2="3"/>'
                '<line x1="1" y1="14" x2="7" y2="14"/>'
                '<line x1="9" y1="8" x2="15" y2="8"/>'
                '<line x1="17" y1="16" x2="23" y2="16"/>')

# ─── Left navigation sidebar ─────────────────────────────────────────────────

_RAIL_CSS = """
QWidget#rail { background: #eef2fa; }
QPushButton#rail-item {
    background: transparent; color: #334155;
    border: none; border-left: 3px solid transparent;
    text-align: left; padding: 8px 14px 8px 12px;
    font-size: 13px; font-family: "Segoe UI"; font-weight: 500;
}
QPushButton#rail-item:hover {
    background: rgba(37,99,235,0.08); color: #1e293b;
    border-left-color: rgba(37,99,235,0.3);
}
QPushButton#rail-item:checked {
    background: rgba(37,99,235,0.12); color: #1d4ed8;
    border-left-color: #2563eb; font-weight: 700;
}
QLabel#rail-group {
    color: #64748b; font-size: 10px; font-weight: 700;
    background: transparent; padding: 10px 18px 3px 18px;
    font-family: "Segoe UI"; letter-spacing: 0.08em;
}
"""

class NavSidebar(QWidget):
    tabRequested = QTimer  # placeholder; real signal below

    from PyQt6.QtCore import pyqtSignal as _sig
    tabRequested = _sig(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rail")
        self.setFixedWidth(210)
        self.setStyleSheet(_RAIL_CSS)
        self._buttons: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Brand strip — SVG calendar icon + text
        brand_w = QWidget()
        brand_w.setFixedHeight(52)
        brand_w.setStyleSheet(
            "QWidget { background:#16213d; border-bottom:1px solid rgba(255,255,255,0.08); }"
        )
        brand_lay = QHBoxLayout(brand_w)
        brand_lay.setContentsMargins(16, 0, 16, 0)
        brand_lay.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_svg_pixmap(_IC_GANTT, "#7eb8ff", 16))
        icon_lbl.setFixedSize(16, 16)
        icon_lbl.setStyleSheet("background:transparent; border:none;")
        brand_lay.addWidget(icon_lbl)

        text_lbl = QLabel("Production Planner")
        text_lbl.setStyleSheet(
            "color:#ffffff; font-size:13.5px; font-weight:700;"
            " background:transparent; border:none;"
        )
        brand_lay.addWidget(text_lbl)
        brand_lay.addStretch()
        outer.addWidget(brand_w)

        self._item_layout = QVBoxLayout()
        self._item_layout.setContentsMargins(0, 6, 0, 6)
        self._item_layout.setSpacing(0)
        outer.addLayout(self._item_layout)
        outer.addStretch()

    def add_group(self, label: str):
        lbl = QLabel(label.upper())
        lbl.setObjectName("rail-group")
        self._item_layout.addWidget(lbl)

    def add_item(self, svg_body: str, label: str, tab_idx: int) -> QPushButton:
        btn = QPushButton(f"  {label}")
        btn.setObjectName("rail-item")
        btn.setFixedHeight(38)
        btn.setCheckable(True)
        btn.setIcon(_rail_icon(svg_body))
        btn.setIconSize(QSize(16, 16))
        btn.setProperty("tab_idx", tab_idx)
        btn.clicked.connect(lambda _=False, i=tab_idx: self._clicked(i))
        self._buttons.append(btn)
        self._item_layout.addWidget(btn)
        return btn

    def _clicked(self, idx: int):
        self.set_current(idx)
        self.tabRequested.emit(idx)

    def set_current(self, idx: int):
        for btn in self._buttons:
            btn.setChecked(btn.property("tab_idx") == idx)

    def update_badge(self, tab_idx: int, text: str):
        """Update the button text for a given tab (e.g., add conflict count)."""
        for btn in self._buttons:
            if btn.property("tab_idx") == tab_idx:
                base = btn.text().split("  (")[0]
                btn.setText(f"{base}  ({text})" if text else base)
                break


class _TabbedStack:
    """QTabWidget-compatible proxy backed by QStackedWidget."""

    def __init__(self, stack: QStackedWidget):
        self._stack   = stack
        self._widgets: list = []
        self._titles:  list = []
        self.currentChanged = stack.currentChanged   # expose signal directly

    def addTab(self, widget: QWidget, title: str):
        self._widgets.append(widget)
        self._titles.append(title)
        self._stack.addWidget(widget)

    def insertTab(self, idx: int, widget: QWidget, title: str):
        self._widgets.insert(idx, widget)
        self._titles.insert(idx, title)
        self._stack.insertWidget(idx, widget)

    def removeTab(self, idx: int):
        if 0 <= idx < len(self._widgets):
            self._stack.removeWidget(self._widgets[idx])
            del self._widgets[idx]
            del self._titles[idx]

    def currentWidget(self) -> QWidget:
        return self._stack.currentWidget()

    def widget(self, idx: int) -> QWidget:
        return self._widgets[idx] if 0 <= idx < len(self._widgets) else None

    def currentIndex(self) -> int:
        return self._stack.currentIndex()

    def setCurrentWidget(self, w: QWidget):
        self._stack.setCurrentWidget(w)

    def setCurrentIndex(self, idx: int):
        self._stack.setCurrentIndex(idx)

    def count(self) -> int:
        return len(self._widgets)

    def indexOf(self, w: QWidget) -> int:
        try:
            return self._widgets.index(w)
        except ValueError:
            return -1

    # no-ops for backward compatibility
    def setDocumentMode(self, _): pass
    def setTabBar(self, _):       pass
    def setTabText(self, idx: int, text: str): pass


# ─── Detached full window ─────────────────────────────────────────────────────

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
        self.io_tab          = InternalOrderTab(self)
        self.master_tab      = MasterTab(self)
        self.crp_tab         = CRPTab(self)
        self.actuals_tab     = ActualsTab(self)
        self.alerts_tab      = AlertsTab(self)
        self.dashboard_tab   = DashboardTab(self)
        self.inventory_tab   = InventoryTab(self)
        self.release_tab     = ReleaseReportTab(self)
        self.impact_tab      = ImpactReportTab(self)
        self.scenario_tab    = ScenarioTab(self)
        self.dispatch_tab    = DispatchListTab(self)
        self.labor_tab       = LaborUtilizationTab(self)
        self.help_tab        = HelpTab(self)

        _tab_defs = [
            (self.gantt_tab,     "📅  Gantt Planner"),
            (self.so_tab,        "📋  Sales Orders"),
            (self.io_tab,        "🏭  Internal Orders"),
            (self.master_tab,    "🗂  Masters"),
            (self.crp_tab,       "👥  CRP"),
            (self.actuals_tab,   "✅  Actuals"),
            (self.alerts_tab,    "⚠️  Alerts"),
            (self.dashboard_tab, "📊  Dashboard"),
            (self.inventory_tab, "📦  Inventory"),
            (self.release_tab,   "🚀  Release Report"),
            (self.dispatch_tab,  "📋  Dispatch List"),
            (self.impact_tab,    "📈  Impact Report"),
            (self.scenario_tab,  "🎯  Scenario Planner"),
            (self.help_tab,      "❓  Help"),
            (self.labor_tab,     "👷  Labor Utilization"),
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
            except Exception as e:
                import traceback; traceback.print_exc()

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
        self._on_sidebar_nav(5)
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
            import traceback; traceback.print_exc()

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
        # App-wide background
        self.setStyleSheet("QMainWindow { background: #eef0f3; }")

        # ── Tab widgets ───────────────────────────────────────────────────────
        self.gantt_tab       = GanttTab(self)
        self.so_tab          = SOTab(self)
        self.io_tab          = InternalOrderTab(self)
        self.master_tab      = MasterTab(self)
        self.crp_tab         = CRPTab(self)
        self.actuals_tab     = ActualsTab(self)
        self.alerts_tab      = AlertsTab(self)
        self.dashboard_tab   = DashboardTab(self)
        self.inventory_tab   = InventoryTab(self)
        self.release_tab     = ReleaseReportTab(self)
        self.impact_tab      = ImpactReportTab(self)
        self.scenario_tab    = ScenarioTab(self)
        self.dispatch_tab    = DispatchListTab(self)
        self.labor_tab       = LaborUtilizationTab(self)
        self.help_tab        = HelpTab(self)

        # ── Stack widget (content area) ───────────────────────────────────────
        self._stack = QStackedWidget()
        self.tabs = _TabbedStack(self._stack)   # backward-compat proxy

        self._tab_defs = [
            (self.gantt_tab,     "📅  Gantt Planner",       0),
            (self.so_tab,        "📋  Sales Orders",        1),
            (self.io_tab,        "🏭  Internal Orders",     2),
            (self.master_tab,    "🗂  Masters",              3),
            (self.crp_tab,       "👥  CRP",                  4),
            (self.actuals_tab,   "✅  Actuals",              5),
            (self.alerts_tab,    "⚠️  Alerts",               6),
            (self.dashboard_tab, "📊  Dashboard",            7),
            (self.inventory_tab, "📦  Inventory",            8),
            (self.release_tab,   "🚀  Release Report",       9),
            (self.dispatch_tab,  "📋  Dispatch List",       10),
            (self.impact_tab,    "📈  Impact Report",       11),
            (self.scenario_tab,  "🎯  Scenario Planner",    12),
            (self.help_tab,      "❓  Help",                 13),
            (self.labor_tab,     "👷  Labor Utilization",   14),
        ]
        for widget, title, _ in self._tab_defs:
            self.tabs.addTab(widget, title)

        # ── Sidebar ───────────────────────────────────────────────────────────
        self._sidebar = NavSidebar(self)
        self._sidebar.add_group("Plan")
        self._sidebar.add_item(_IC_GANTT,     "Gantt Planner",      0)
        self._sidebar.add_item(_IC_SO,        "Sales Orders",       1)
        self._sidebar.add_item(_IC_SO,        "Internal Orders",    2)
        self._sidebar.add_item(_IC_RELEASE,   "Release Report",     9)
        self._sidebar.add_item(_IC_ALERTS,    "Dispatch List",     10)
        self._sidebar.add_group("Capacity")
        self._sidebar.add_item(_IC_CRP,       "CRP",                4)
        self._sidebar.add_item(_IC_LABOR,     "Labor Utilization", 14)
        self._sidebar.add_item(_IC_INVENTORY, "Inventory",          8)
        self._sidebar.add_item(_IC_DASHBOARD, "Scenario Planner",  12)
        self._sidebar.add_group("Track")
        self._sidebar.add_item(_IC_ACTUALS,   "Actuals",            5)
        self._sidebar.add_item(_IC_ALERTS,    "Alerts",             6)
        self._sidebar.add_item(_IC_DASHBOARD, "Dashboard",          7)
        self._sidebar.add_item(_IC_RELEASE,   "Impact Report",     11)
        self._sidebar.add_group("Setup")
        self._sidebar.add_item(_IC_MASTERS,   "Masters",            3)
        self._sidebar.add_item(_IC_HELP,      "Help",              13)

        self._sidebar.set_current(0)
        self._sidebar.tabRequested.connect(self._on_sidebar_nav)

        # ── Layout: sidebar + stack ───────────────────────────────────────────
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._sidebar)
        body_layout.addWidget(self._stack, stretch=1)
        self.setCentralWidget(body)

        self._stack.currentChanged.connect(self._on_tab_changed)

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setStyleSheet(
            "QToolBar { background:#f7f8fc; border-bottom:1px solid #e2e4ea; spacing:4px; }"
            "QToolButton { padding:4px 10px; font-size:11px; border-radius:4px; }"
            "QToolButton:hover { background:#edeef3; }"
        )
        self.addToolBar(tb)

        act_crp_refresh = QAction("♻  Refresh CRP", self)
        act_crp_refresh.setToolTip("Reload CRP data from Excel")
        act_crp_refresh.triggered.connect(self._refresh_crp)
        tb.addAction(act_crp_refresh)

        act_conflicts = QAction("🔍  Check Conflicts", self)
        act_conflicts.setToolTip("Detect capacity overruns and multi-process room-shift violations")
        act_conflicts.triggered.connect(self._check_conflicts)
        tb.addAction(act_conflicts)

        act_history = QAction("📜  Plan History", self)
        act_history.setToolTip("View full plan change history (moves, locks, deletes)")
        act_history.triggered.connect(self._show_plan_history)
        tb.addAction(act_history)

        tb.addSeparator()

        act_new_win = QAction("🗗  New Window  (Ctrl+N)", self)
        act_new_win.setToolTip("Open current view in a new window (Ctrl+N)")
        act_new_win.triggered.connect(self._detach_current_tab)
        tb.addAction(act_new_win)

    def _build_statusbar(self):
        self.status = QStatusBar()
        self.status.setFixedHeight(22)
        self.status.setStyleSheet(
            "QStatusBar { background:#f0f2f7; border-top:1px solid #e2e4ea;"
            " font-size:10.5px; color:#6b7280; }"
            "QStatusBar::item { border:none; }"
        )
        self.setStatusBar(self.status)
        self.conflict_label = QLabel("  ✅ No conflicts")
        self.conflict_label.setStyleSheet(
            "color:#1d8a4a; font-size:10.5px; font-weight:600;")
        self.status.addPermanentWidget(self.conflict_label)

        # Detached windows count indicator
        self.detached_label = QLabel()
        self.detached_label.setStyleSheet("font-size:10.5px;")
        self.status.addPermanentWidget(self.detached_label)

    def _setup_shortcuts(self):
        sc = QShortcut(QKeySequence("Ctrl+N"), self)
        sc.activated.connect(self._detach_current_tab)

    def _start_conflict_timer(self):
        self._conflict_timer = QTimer(self)
        self._conflict_timer.timeout.connect(self._check_conflicts_silent)
        self._conflict_timer.start(30_000)

    # ── New window / detach ──────────────────────────────────────────────────

    def _on_sidebar_nav(self, idx: int):
        self._stack.setCurrentIndex(idx)
        self._sidebar.set_current(idx)

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
        self._sidebar.set_current(idx)
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
        self._on_sidebar_nav(5)
        self.alerts_tab.refresh()

    def _show_plan_history(self):
        from ui.remaining_tabs import PlanHistoryDialog
        dlg = PlanHistoryDialog(self)
        dlg.exec()

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
                    "color:#c2342f; font-weight:600; font-size:10.5px;")
                self._sidebar.update_badge(5, str(n))
            else:
                self.conflict_label.setText("  ✅ No conflicts")
                self.conflict_label.setStyleSheet(
                    "color:#1d8a4a; font-weight:600; font-size:10.5px;")
                self._sidebar.update_badge(5, "")
        except Exception:
            import traceback; traceback.print_exc()

    def notify(self, msg: str, level: str = "info"):
        self.status.showMessage(msg, 5000)
        for win in list(self._detached_windows):
            try:
                win.notify(msg)
            except Exception:
                import traceback; traceback.print_exc()

    def refresh_all(self):
        """Refresh all tabs (main + secondary windows)."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if hasattr(w, "refresh"):
                try:
                    w.refresh()
                except Exception:
                    import traceback; traceback.print_exc()
        for win in list(self._detached_windows):
            try:
                win._auto_refresh()
            except Exception:
                import traceback; traceback.print_exc()

    def closeEvent(self, event):
        event.accept()
