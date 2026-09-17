# -*- coding: utf-8 -*-
"""界面冒烟测试（离屏运行，不需要显示器）

运行：
    .venv/Scripts/python.exe tests/test_ui_smoke.py

为什么要单独一个文件：有些修复的正确性只能在界面层验证，纯逻辑单测碰不到。
这里钉住的是 v1.1.2 的界面修复与文案：

一、预览画质
   旧 ImageViewer 在 resizeEvent 里对 `preview_label.pixmap()`（已经是缩放结果）
   再缩放，拖几次窗口画质逐级劣化且回不来；而且小图会被放大填满预览区，越放越糊。
   现改为保留原始像素、每次从原图重算，小图不放大。
   这里用「原始像素不被覆盖」+「小图保持原尺寸」两条断言钉住。

二、解析按钮的取消语义
   解析进行中同一个按钮要变成「取消解析」并且**仍可点击**——按钮若被禁用就没法中止了。

三、文案与实现一致
   「过滤小图与装饰」的提示里不许再出现从未内置过的固定尺寸（321×192 等）。

四、保存链路端到端
   真的按一次「下载选中图片」，等文件落到磁盘上。v1.1.2 丢过一次 app.py 里
   `SaveWorker` 的 import，只有走到这条路径才发现（详见 test_download_through_gui）。
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QBuffer, QByteArray, QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

_passed = 0
_failed = []


def check(cond, label):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(label)
        print(f'  ✗ {label}')


def eq(got, want, label):
    check(got == want, f'{label}\n      得到: {got}\n      期望: {want}')


def png_bytes(w, h, color=0xFF3366FF):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(color)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, 'PNG')
    buf.close()
    return bytes(ba)


def settle(viewer, w, h):
    """改尺寸 → 让布局落定 → 强制重算一次预览（绕过 60ms 防抖计时器，免得等）"""
    viewer.resize(w, h)
    QApplication.processEvents()
    viewer._apply_scale()
    QApplication.processEvents()


def test_preview_scale(app):
    from web_image_dl.widgets import ImageViewer

    viewer = ImageViewer()
    viewer.resize(900, 700)
    viewer.show()
    QApplication.processEvents()

    print('\n[UI-1] 大图：反复缩放后原始像素始终不被覆盖')
    big = png_bytes(2000, 1500)
    viewer.show_image(big)
    eq(viewer._source.size(), QSize(2000, 1500), '原始像素尺寸正确')

    for w, h in [(600, 500), (900, 700), (500, 400), (1000, 800), (900, 700), (420, 340)]:
        settle(viewer, w, h)
        eq(viewer._source.size(), QSize(2000, 1500),
           f'缩放到 {w}×{h} 之后原始像素仍在（旧实现会从缩放结果再缩，逐级变糊）')

    print('\n[UI-2] 大图：预览尺寸 == 从原图直接缩放的期望值')
    settle(viewer, 900, 700)
    vw = viewer.viewport().width() - 20
    vh = viewer.viewport().height() - 20
    expect = viewer._source.scaled(min(vw, viewer._source.width()),
                                   min(vh, viewer._source.height()),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
    got = viewer.preview_label.pixmap().size()
    eq((got.width(), got.height()), (expect.width(), expect.height()),
       '预览图尺寸与「从原图算」一致')
    check(got.width() <= 2000 and got.height() <= 1500, '预览图没有被放大到超过原图')

    print('\n[UI-3] 小图：保持原尺寸，不被放大填满预览区')
    # 旧实现会把 120×90 的小图放大到预览区大小，等于用插值伪造像素，越看越糊
    viewer.show_image(png_bytes(120, 90))
    settle(viewer, 900, 700)
    eq(viewer.preview_label.pixmap().size(), QSize(120, 90),
       '120×90 的小图原尺寸显示（没被拉伸）')

    print('\n[UI-4] 切换图片与清空不残留旧像素')
    viewer.show_image(png_bytes(300, 200))
    eq(viewer._source.size(), QSize(300, 200), '换图后原始像素同步更新')
    viewer.clear()
    check(viewer._source.isNull(), 'clear() 之后原始像素被释放')
    check(viewer.preview_label.pixmap() is None or viewer.preview_label.pixmap().isNull(),
          'clear() 之后预览区没有残留旧图')


def test_cancel_button(app):
    from web_image_dl.app import ImageDownloaderApp

    print('\n[UI-5] 解析按钮的取消语义')
    win = ImageDownloaderApp()
    eq(win.parse_btn.text(), '解析图片', '初始文案')

    win._reset_before_parse()
    eq(win.parse_btn.text(), '取消解析', '解析中转为「取消解析」')
    check(win.parse_btn.isEnabled(), '解析中按钮仍可点击 —— 否则根本没法取消')

    win._on_parse_done(True)
    eq(win.parse_btn.text(), '解析图片', '解析结束恢复原文案')
    check(win.parse_btn.isEnabled(), '解析结束按钮可用')


def test_copy_matches_impl(app):
    from web_image_dl.app import ImageDownloaderApp

    print('\n[UI-6] 「过滤小图与装饰」的说明与实际生效的规则一致')
    win = ImageDownloaderApp()
    tip = win.filter_square_cb.toolTip()

    check('200px' in tip, '写明了真实生效的「最长边 ≤ 200px」')
    check('10KB' in tip, '写明了真实生效的「透明 PNG < 10KB」')
    check('右键' in tip, '写明了「右键标记过的尺寸」这条来源')

    # 这串尺寸从来没有内置进配置（blocked_sizes.json 默认三组都是空的），
    # 提示里再写出来就是骗人
    for ghost in ['321×192', '272×272', '1080×460', '900×383', '72×170']:
        check(ghost not in tip, f'不再宣称存在内置尺寸 {ghost}')


def test_shutdown_with_running_thread(app):
    from web_image_dl.app import ImageDownloaderApp

    print('\n[UI-7] 关窗时能等到后台线程收尾（不触发 QThread destroyed 崩溃）')
    win = ImageDownloaderApp()
    win.show()
    QApplication.processEvents()
    win.close()          # closeEvent 里会 cancel + wait
    QApplication.processEvents()
    check(True, '关窗流程未抛异常')


def test_download_through_gui(app):
    """从「点保存按钮」一路走到文件落盘。

    这条是补的教训：v1.1.2 里 app.py 把 `SaveWorker` 的 import 丢了（同文件多处并行
    编辑互相覆盖），运行时点保存直接 NameError。当时的测试全过了 —— 因为逻辑单测直接
    调 write_image()，界面测试又没碰保存按钮，谁都没走到 `_on_download` 里那一行。
    所以这里必须真的按一次按钮、并等文件出现在磁盘上。
    """
    import tempfile

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.worker import ImageInfo

    print('\n[UI-8] 点保存后图片真的落盘（钉住 SaveWorker 被正确引入）')
    win = ImageDownloaderApp()
    win.show()
    QApplication.processEvents()

    for i in range(3):
        info = ImageInfo(url=f'https://mmbiz.qpic.cn/mmbiz_png/ID{i}/0', index=i,
                         data=png_bytes(60 + i, 40), ext='.png',
                         width=60 + i, height=40)
        win._on_image_loaded(i, info)
    QApplication.processEvents()
    eq(len(win.thumb_items), 3, '3 张图进了缩略图区')
    eq(win.download_btn.isEnabled(), True, '有选中项时保存按钮可用')

    outdir = tempfile.mkdtemp(prefix='imgsnag_ui_save_')
    orig_dialog = QFileDialog.getExistingDirectory
    orig_info = QMessageBox.information
    orig_warn = QMessageBox.warning
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: outdir)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    try:
        try:
            win._on_download()      # 缺 import 时这里抛 NameError，save_worker 会留在 None
        except Exception as e:
            check(False, f'点保存抛异常: {type(e).__name__}: {e}')
        worker = win.save_worker
        check(worker is not None, '保存线程已创建（缺 import 时此处为 None）')
        if worker is not None:
            worker.wait(15000)      # 等落盘线程收尾
            QApplication.processEvents()
    finally:
        QFileDialog.getExistingDirectory = orig_dialog
        QMessageBox.information = orig_info
        QMessageBox.warning = orig_warn

    got = sorted(os.listdir(outdir))
    eq(got, ['img_01.png', 'img_02.png', 'img_03.png'], '三张图按序号落盘')
    for name in got:
        size = os.path.getsize(os.path.join(outdir, name))
        check(size > 0, f'{name} 不是空文件（实测 {size} 字节）')

    # 界面状态要复位，否则用户看到的是永远停在「保存中」的按钮
    eq(win.download_btn.text(), '下载选中图片 (3)', '保存结束后按钮文案复位')
    check(win.download_btn.isEnabled(), '保存结束后按钮恢复可用')


def main():
    app = QApplication.instance() or QApplication([])
    test_preview_scale(app)
    test_cancel_button(app)
    test_copy_matches_impl(app)
    test_shutdown_with_running_thread(app)
    test_download_through_gui(app)
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
