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


def _install_crash_logger():
    import traceback as _tb
    def _hook(exc_type, exc_val, exc_tb):
        with open("drag_crash.txt", "w", encoding="utf-8") as f:
            f.write("UNHANDLED EXCEPTION:\n")
            _tb.print_exception(exc_type, exc_val, exc_tb, file=f)
        _tb.print_exception(exc_type, exc_val, exc_tb)
    sys.excepthook = _hook

def main():
    _install_crash_logger()
    lock_sock = _acquire_instance_lock()

    if lock_sock is None:
        # Another instance is running — kill it automatically and take over
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
            # Last resort: show error only if auto-kill failed
            app = QApplication(sys.argv)
            app.setStyle("Fusion")
            QMessageBox.critical(
                None,
                "Error",
                "Failed to terminate existing instance.\nPlease close it manually and restart.",
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
