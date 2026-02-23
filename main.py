from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

# Embedded Python can run in isolated mode and miss this script folder on sys.path.
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Lama Cleaner Video GUI")
    window = MainWindow(repo_root=BASE_DIR)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
