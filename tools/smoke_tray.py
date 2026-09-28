# -*- coding: utf-8 -*-
"""真机烟测：右下角托盘图标能否真在 Windows 上创建（离屏恒判「无托盘」）。

窗口会闪现约 2 秒后自动退出。想手动看一眼托盘长什么样时，直接跑这个脚本即可：

    .venv\\Scripts\\python.exe tools\\smoke_tray.py

它验证的是离屏测试**测不到**的一步：真实的 QSystemTrayIcon 能不能建出来、能不能显示、
收进托盘后图标是否还在。窗口尺寸偏好走临时 ini，**不碰你真实的注册表设置**。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from PySide6.QtCore import QTimer                                  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon        # noqa: E402

from web_image_dl.settings import settings                         # noqa: E402

# 隔离偏好：烟测不该改掉你真实的窗口大小/关闭行为
settings.use_ini_file(os.path.join(tempfile.mkdtemp(prefix='imgsnag_smoke_'), 'settings.ini'))

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)      # 与 main.py 一致

from web_image_dl.app import ImageDownloaderApp                    # noqa: E402

print('平台                :', app.platformName())
print('系统托盘可用        :', QSystemTrayIcon.isSystemTrayAvailable())

win = ImageDownloaderApp()
win.show()

checks = {
    '托盘对象已创建': win._tray is not None,
    '托盘图标可见': bool(win._tray and win._tray.isVisible()),
    '右键菜单已挂上': bool(win._tray and win._tray.contextMenu() is not None),
    '托盘提示含版本号': bool(win._tray and 'ImgSnag' in win._tray.toolTip()),
    '关闭行为偏好': win._resolve_close_action(),
}

steps = []


def step_hide():
    ok = win._minimize_to_tray()
    steps.append(('收进托盘后窗口隐藏', ok and win.isHidden()))
    steps.append(('收进托盘后进程仍在（未真退出）', win._really_quit is False))
    steps.append(('收进托盘后图标仍可见', bool(win._tray and win._tray.isVisible())))


def step_restore():
    win._restore_from_tray()
    steps.append(('唤回后窗口可见', not win.isHidden()))


QTimer.singleShot(600, step_hide)
QTimer.singleShot(1200, step_restore)
QTimer.singleShot(1800, win._quit_app)          # 等同托盘菜单「退出」

rc = app.exec()

print()
for k, v in checks.items():
    print(f'  {k}: {v}')
for k, v in steps:
    print(f'  {k}: {v}')
print('退出后托盘已摘      :', win._tray is None or not win._tray.isVisible())

ok = all(checks[k] for k in ('托盘对象已创建', '托盘图标可见', '右键菜单已挂上')) \
    and all(v for _, v in steps)
print('SMOKE', 'OK' if ok else 'FAIL')
sys.exit(0 if ok else 1)
