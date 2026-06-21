"""
Production Planner — entry point.
Run:  python main.py
Build exe: pyinstaller --onefile --windowed main.py
"""
import sys
import os
import socket
import subprocess

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

_SINGLE_INSTANCE_PORT = 47832
_PID_FILE = os.path.join(_ROOT, ".planner.pid")


def _acquire_instance_lock() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def _read_existing_pid() -> int | None:
    try:
        with open(_PID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _kill_pid(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _write_pid():
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid():
    try:
        os.remove(_PID_FILE)
    except Exception:
        pass


# ── DB must be initialized BEFORE any other import that touches repositories ──
from data.database import init_db
init_db()

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPalette, QColor

from ui.main_window import MainWindow
from ui.app_style import APP_QSS


def main():
    lock_sock = _acquire_instance_lock()

    if lock_sock is None:
        # Another instance is running — ask whether to close it
        app = QApplication(sys.argv)
        app.setStyle("Fusion")

        ret = QMessageBox.warning(
            None,
            "이미 실행 중",
            "Production Planner가 이미 실행 중입니다.\n\n"
            "기존 창을 닫고 새 창을 열겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            sys.exit(0)

        pid = _read_existing_pid()
        if pid:
            _kill_pid(pid)

        # Wait for the port to be released (up to 3 s)
        import time
        for _ in range(15):
            time.sleep(0.2)
            lock_sock = _acquire_instance_lock()
            if lock_sock:
                break

        if lock_sock is None:
            QMessageBox.critical(
                None,
                "오류",
                "기존 창을 종료하지 못했습니다.\n직접 닫은 후 다시 시작해 주세요.",
            )
            sys.exit(1)

    _write_pid()
    try:
        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName("Production Planner")
        app.setOrganizationName("YourCompany")
        app.setStyle("Fusion")
        font = QFont("Segoe UI", 9)
        app.setFont(font)

        # Modern palette (base colors for Fusion renderer)
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window,          QColor("#ECEEF3"))
        pal.setColor(QPalette.ColorRole.WindowText,      QColor("#1E293B"))
        pal.setColor(QPalette.ColorRole.Base,            QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.AlternateBase,   QColor("#F8FAFC"))
        pal.setColor(QPalette.ColorRole.Text,            QColor("#1E293B"))
        pal.setColor(QPalette.ColorRole.Button,          QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.ButtonText,      QColor("#374151"))
        pal.setColor(QPalette.ColorRole.Highlight,       QColor("#2563EB"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#94A3B8"))
        pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#1E293B"))
        pal.setColor(QPalette.ColorRole.ToolTipText,     QColor("#F8FAFC"))
        app.setPalette(pal)

        app.setStyleSheet(APP_QSS)

        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    finally:
        lock_sock.close()
        _remove_pid()


if __name__ == "__main__":
    main()
