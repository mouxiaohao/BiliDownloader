import os
import pathlib
import sys

from PySide6 import QtWidgets, QtGui

import update
from mainwindow import MainWindow
from utils import configUtils


def update_cwd():
    APP_DIR_NAME = ".bili_downloader"

    if getattr(sys, "frozen", False):
        os.chdir(pathlib.Path(sys.executable).parent)
        return

    # change cwd when not meet the requirements
    if os.path.samefile(os.path.split(__file__)[0], os.getcwd()) and sys.platform == "win32":
        os.chdir(os.path.split(os.getcwd())[0])

    if sys.platform == "linux":
        home_path = pathlib.Path(os.getenv("HOME"))
        cwd_path = home_path / APP_DIR_NAME
        if not os.path.exists(cwd_path):
            os.mkdir(cwd_path)
        os.chdir(cwd_path)


def update_style(app: QtWidgets.QApplication):
    style = configUtils.getUserData(configUtils.Configs.QT_STYLE, "default")
    if style != "default":
        app.setStyle(QtWidgets.QStyleFactory.create(style))


def updateHighDpi():
    highdpi = configUtils.getUserData(configUtils.Configs.APPLY_HIGH_DPI, True)
    if not highdpi:
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"


def install_font_for_portable_windows():
    if sys.platform == "win32" and update.NO_UPDATE:
        from utils import check_nuitka as _check_nuitka
        if _check_nuitka.check():
            from utils import install_sysfont_windows as _install_font_win
            if not _install_font_win.check_font("HarmonyOS Sans SC"):
                _install_font_win.install_font("HarmonyOS Sans SC", "font\\HarmonyOS_Sans_SC_Regular.ttf")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        from selfcheck import main as selfcheck_main
        sys.exit(selfcheck_main(sys.argv[1:]))
    updateHighDpi()
    app = QtWidgets.QApplication(sys.argv)
    font_file = pathlib.Path(__file__).resolve().parent / "font" / "HarmonyOS_Sans_SC_Regular.ttf"
    if font_file.is_file():
        QtGui.QFontDatabase.addApplicationFont(str(font_file))
    font = app.font()
    if font_file.is_file():
        font.setFamily("HarmonyOS Sans SC")

    font.setHintingPreference(QtGui.QFont.HintingPreference.PreferNoHinting)
    # font.setStyleStrategy(QtGui.QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    update_cwd()
    update_style(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
