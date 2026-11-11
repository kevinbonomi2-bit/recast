import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

# One QApplication for the whole run: the SVG converter would otherwise create a bare
# QGuiApplication first, and widgets (the wheel) can't be built on top of that.
_app = QApplication.instance() or QApplication([])
