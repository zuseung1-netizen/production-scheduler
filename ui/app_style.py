"""
Global QSS stylesheet for Production Planner.
Applied at QApplication level so every widget inherits it.
Widget-level setStyleSheet() calls override this where needed.
"""

APP_QSS = """

/* ═══ BASE ═══════════════════════════════════════════════════════════════ */

QWidget {
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 9pt;
    color: #1E293B;
}

QMainWindow {
    background: #ECEEF3;
}

QDialog {
    background: #F4F6FA;
}

QStackedWidget, QScrollArea > QWidget > QWidget {
    background: #ECEEF3;
}


/* ═══ GROUPBOX (card style) ══════════════════════════════════════════════ */

QGroupBox {
    background: #FFFFFF;
    border: 1px solid #DDE3ED;
    border-radius: 7px;
    margin-top: 12px;
    padding: 8px 10px 10px 10px;
    font-weight: 600;
    font-size: 9pt;
    color: #334155;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    top: -1px;
    padding: 0 6px;
    color: #334155;
    background: #FFFFFF;
}


/* ═══ PUSH BUTTONS ═══════════════════════════════════════════════════════ */

QPushButton {
    background: #FFFFFF;
    color: #374151;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    padding: 5px 14px;
    min-height: 22px;
}

QPushButton:hover {
    background: #F0F5FF;
    border-color: #93C5FD;
    color: #1D4ED8;
}

QPushButton:pressed {
    background: #DBEAFE;
    border-color: #3B82F6;
}

QPushButton:disabled {
    background: #F1F5F9;
    color: #94A3B8;
    border-color: #E2E8F0;
}

QPushButton:checkable:checked {
    background: #EFF6FF;
    color: #1D4ED8;
    border-color: #93C5FD;
    font-weight: 600;
}


/* ═══ TABLES ═════════════════════════════════════════════════════════════ */

QTableWidget, QTableView {
    background: #FFFFFF;
    border: 1px solid #DDE3ED;
    border-radius: 5px;
    gridline-color: #EEF1F7;
    selection-background-color: #DBEAFE;
    selection-color: #1E40AF;
    alternate-background-color: #F8FAFC;
}

QTableWidget::item, QTableView::item {
    padding: 3px 8px;
    border: none;
}

QTableWidget::item:selected, QTableView::item:selected {
    background: #DBEAFE;
    color: #1E40AF;
}

QHeaderView {
    background: transparent;
    border: none;
}

QHeaderView::section {
    background: #F1F4FB;
    color: #4B5563;
    border: none;
    border-right: 1px solid #DDE3ED;
    border-bottom: 1.5px solid #CBD5E1;
    padding: 5px 8px;
    font-weight: 600;
    font-size: 8.5pt;
}

QHeaderView::section:first {
    border-left: none;
    border-top-left-radius: 4px;
}

QHeaderView::section:last {
    border-right: none;
}


/* ═══ LINE EDIT / TEXT INPUTS ════════════════════════════════════════════ */

QLineEdit, QPlainTextEdit {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    padding: 4px 8px;
    color: #1E293B;
    selection-background-color: #BFDBFE;
}

QLineEdit:focus, QPlainTextEdit:focus {
    border-color: #3B82F6;
    background: #FAFCFF;
}

QLineEdit:disabled, QPlainTextEdit:disabled {
    background: #F1F5F9;
    color: #94A3B8;
    border-color: #E2E8F0;
}

QTextEdit {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    padding: 4px;
}

QTextEdit:focus {
    border-color: #3B82F6;
}


/* ═══ SPINBOX / DATEEDIT ═════════════════════════════════════════════════ */

QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    padding: 4px 6px;
    color: #1E293B;
}

QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTimeEdit:focus {
    border-color: #3B82F6;
    background: #FAFCFF;
}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QDateEdit::up-button, QDateEdit::down-button {
    background: #F1F5F9;
    border: none;
    width: 18px;
}

QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
QDateEdit::up-button:hover, QDateEdit::down-button:hover {
    background: #DBEAFE;
}


/* ═══ COMBOBOX ═══════════════════════════════════════════════════════════ */

QComboBox {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    padding: 4px 8px;
    color: #1E293B;
    min-width: 80px;
    min-height: 22px;
}

QComboBox:focus {
    border-color: #3B82F6;
    background: #FAFCFF;
}

QComboBox:disabled {
    background: #F1F5F9;
    color: #94A3B8;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
    background: transparent;
}

QComboBox::down-arrow {
    width: 10px;
    height: 10px;
}

QComboBox QAbstractItemView {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    selection-background-color: #DBEAFE;
    selection-color: #1E40AF;
    padding: 2px;
}


/* ═══ CHECKBOX / RADIOBUTTON ═════════════════════════════════════════════ */

QCheckBox, QRadioButton {
    color: #374151;
    spacing: 6px;
    background: transparent;
}

QCheckBox:hover, QRadioButton:hover {
    color: #1D4ED8;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1.5px solid #CBD5E1;
    border-radius: 3px;
    background: #FFFFFF;
}

QCheckBox::indicator:checked {
    background: #2563EB;
    border-color: #2563EB;
}

QCheckBox::indicator:hover {
    border-color: #3B82F6;
}

QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1.5px solid #CBD5E1;
    border-radius: 8px;
    background: #FFFFFF;
}

QRadioButton::indicator:checked {
    background: #2563EB;
    border-color: #2563EB;
}


/* ═══ TAB WIDGET ═════════════════════════════════════════════════════════ */

QTabWidget::pane {
    border: 1px solid #DDE3ED;
    border-top: none;
    background: #FFFFFF;
    border-radius: 0 0 6px 6px;
}

QTabBar::tab {
    background: #EEF1F7;
    color: #64748B;
    border: 1px solid #DDE3ED;
    border-bottom: none;
    padding: 6px 18px;
    margin-right: 2px;
    border-radius: 5px 5px 0 0;
    font-size: 8.5pt;
}

QTabBar::tab:selected {
    background: #FFFFFF;
    color: #1E40AF;
    font-weight: 600;
    border-bottom: 1px solid #FFFFFF;
}

QTabBar::tab:hover:!selected {
    background: #E8EDFF;
    color: #1D4ED8;
}


/* ═══ SCROLLBARS ═════════════════════════════════════════════════════════ */

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #CBD5E1;
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: #94A3B8;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
}

QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background: #CBD5E1;
    border-radius: 4px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background: #94A3B8;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    background: none;
}


/* ═══ SPLITTER ═══════════════════════════════════════════════════════════ */

QSplitter::handle {
    background: #DDE3ED;
}

QSplitter::handle:vertical {
    height: 3px;
    margin: 2px 0;
}

QSplitter::handle:horizontal {
    width: 3px;
    margin: 0 2px;
}


/* ═══ MENU ═══════════════════════════════════════════════════════════════ */

QMenu {
    background: #FFFFFF;
    border: 1px solid #DDE3ED;
    border-radius: 6px;
    padding: 4px 0;
}

QMenu::item {
    padding: 5px 28px 5px 16px;
    color: #374151;
    border-radius: 3px;
}

QMenu::item:selected {
    background: #EBF1FF;
    color: #1D4ED8;
}

QMenu::separator {
    height: 1px;
    background: #E2E8F0;
    margin: 4px 8px;
}


/* ═══ TOOLBAR ════════════════════════════════════════════════════════════ */

QToolBar {
    background: #F7F8FC;
    border-bottom: 1px solid #DDE3ED;
    spacing: 4px;
    padding: 3px 6px;
}

QToolBar QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 10px;
    color: #374151;
}

QToolBar QToolButton:hover {
    background: #EBF1FF;
    border-color: #BFDBFE;
    color: #1D4ED8;
}

QToolBar QToolButton:pressed {
    background: #DBEAFE;
}


/* ═══ STATUS BAR ═════════════════════════════════════════════════════════ */

QStatusBar {
    background: #F0F2F7;
    border-top: 1px solid #DDE3ED;
    color: #64748B;
    font-size: 8.5pt;
}


/* ═══ TOOLTIPS ═══════════════════════════════════════════════════════════ */

QToolTip {
    background: #1E293B;
    color: #F8FAFC;
    border: none;
    border-radius: 4px;
    padding: 5px 9px;
    font-size: 8.5pt;
}


/* ═══ MESSAGEBOX ═════════════════════════════════════════════════════════ */

QMessageBox {
    background: #FFFFFF;
}

QMessageBox QLabel {
    color: #1E293B;
    font-size: 9pt;
}


/* ═══ FORM LAYOUT LABELS ════════════════════════════════════════════════ */

QLabel {
    background: transparent;
    color: #1E293B;
}


/* ═══ SIDEBAR OVERRIDE — keep rail dark, don't touch ════════════════════ */

QWidget#rail {
    background: #16213d;
}

QWidget#rail QLabel {
    color: #aab4d6;
    background: transparent;
}

"""
