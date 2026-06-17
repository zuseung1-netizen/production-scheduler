"""
Production Planner — entry point.
Run:  python main.py
Build exe: pyinstaller --onefile --windowed main.py
"""
import sys
import os
import socket

# Ensure imports always resolve from the project root,
# regardless of the CWD the user launches from.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)   # make CWD = project root so relative file ops are predictable

_SINGLE_INSTANCE_PORT = 47832   # arbitrary local port for instance lock


def _acquire_instance_lock() -> socket.socket | None:
    """Try to bind the lock port. Returns the socket on success, None if already running."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


# ── DB must be initialized BEFORE any other import that touches repositories ──
from data.database import init_db
init_db()

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ui.main_window import MainWindow


def main():
    lock_sock = _acquire_instance_lock()
    if lock_sock is None:
        # Another instance is already running — show warning and exit
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        QMessageBox.warning(
            None,
            "Already Running",
            "Production Planner is already running.\n\n"
            "Please use the existing window instead of opening a new instance.\n"
            "If you need a second view, use Ctrl+N (New Window) inside the app."
        )
        sys.exit(0)

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Production Planner")
        app.setOrganizationName("YourCompany")

        # Modern look
        app.setStyle("Fusion")
        font = QFont("Segoe UI", 9)
        app.setFont(font)

        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    finally:
        lock_sock.close()


if __name__ == "__main__":
    main()
