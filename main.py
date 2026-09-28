#!/usr/bin/env python3
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from version import VERSION
from web_image_dl.app import ImageDownloaderApp


def resource_path(name):
    """打包后资源在 _internal（sys._MEIPASS），源码模式在脚本同目录"""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main():
    app = QApplication(sys.argv)
    # 主窗口可以被收进右下角托盘（只是 hide()）—— 此时「最后一个窗口关闭」并不代表要退出，
    # 否则一收托盘程序当场就没了。真退出统一走 ImageDownloaderApp._quit_app() /
    # closeEvent 的 quit 分支 → QApplication.quit()。
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("ImgSnag 微信公众号版")
    app.setApplicationVersion(VERSION)
    icon_path = resource_path("ImgSnag.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    window = ImageDownloaderApp()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
