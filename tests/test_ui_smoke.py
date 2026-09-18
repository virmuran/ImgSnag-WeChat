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

五、v1.2.0 的界面改进
   预览缩放（能看 100% 像素）、缩略图卡片信息行与「重复/小图」角标、
   被过滤图片的可点开入口、全选按钮文案切换、历史页搜索、空状态引导、
   输入框回车解析（eventFilter 若没有实现就是死代码，专门钉一条）。

六、v1.3.0 的缩放锚点与拖动
   Ctrl+滚轮要以鼠标位置为锚点（旧实现永远从左上角放大，"想看某个细节"就得滚半天），
   放大到超出视口后按空格可以拖动。
   空间关系类的修复只能靠几何断言钉住：断言鼠标下的那个原图像素缩放前后仍停在原地，
   以及拖动 N 像素后滚动条恰好走了 N 像素。

七、v1.3.1 「有时候拖得动、有时候拖不动」
   两条真因，都钉在用例里：
   a) 焦点在文本框里就整段不给拖（用户在链接框点一下就废了，而且毫无反馈）。
      现改为**指针位置说了算**：指针压在预览区上就归预览区，在别处才放行空格。
      指针不在预览区时打字的空格必须照常进输入框。
   b) 图没超出视口时「没得拖」是几何决定的，但旧版一声不响 —— 现在会给出
      「先 Ctrl+滚轮放大再拖」的提示。另外补了不按空格直接左键拖动（带起步门槛，
      免得单击/双击把画面带歪）和中键拖动。

八、v1.4.0 「直接左键拖」升为主路径
   用户实测后认定：直接按住左键拖比「按住空格再拖」好用得多。于是空格降为备选，
   「能拖」这件事改由光标自己常驻表达（图超出视口即张开的手），几何提示也不再挂在
   空格上 —— 否则用户永远发现不了左键能拖。

九、v1.5.0 屏蔽尺寸变成双向门 + 界面记事（UI-20 / UI-21）
   旧的 add_blocked 是单向门：右键误屏蔽一个尺寸，那类图从此静默消失，既看不到
   屏蔽了哪些尺寸、也没有撤销入口，普通用户只能认为图丢了。现在历史页底部有
   「已屏蔽的尺寸」面板：列得出分类/尺寸/命中次数，能逐条恢复也能全部恢复，撤销后
   当前页面里被它拦下的图当场放回来并勾上。
   另一半是持久化：全项目此前零 QSettings，排序/保存格式/两个开关/窗口大小位置/
   上次保存目录每次启动都回到默认，现由 web_image_dl/settings.py 统一记住。
   注意：这两块测试都会读写配置，所以走 _isolated_config 把配置指到临时目录，
   绝不碰用户真实的 blocked_sizes.json 与注册表。
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


class _isolated_config:
    """把用户配置临时改到临时目录。

    blocked_config 与 QSettings 都是模块级单例，测试如果直接用，会**读写用户真实的**
    %APPDATA%/ImgSnagWeChat/blocked_sizes.json 和注册表 —— 跑一遍测试就把用户攒下的
    屏蔽规则清掉、把窗口尺寸改掉，是最难察觉的那种副作用。这里用临时文件顶替，
    退出时原样切回去（config_path 是实例属性，所以 app/worker 里引用的那个单例
    也会跟着走临时文件）。
    """

    def __enter__(self):
        import tempfile

        from web_image_dl.blocked_config import blocked_config
        from web_image_dl.settings import settings

        self.dir = tempfile.mkdtemp(prefix='imgsnag_iso_')
        self._blocked = blocked_config
        self._settings = settings
        self._saved_path = blocked_config.config_path
        blocked_config.config_path = os.path.join(self.dir, 'blocked_sizes.json')
        blocked_config.reload()
        settings.use_ini_file(os.path.join(self.dir, 'settings.ini'))
        return self

    def __exit__(self, *exc):
        self._blocked.config_path = self._saved_path
        self._blocked.reload()
        self._settings.use_default_store()
        return False


def _fake_infos(count=3):
    """造几张带真字节的图，字段按真实文章填"""
    from web_image_dl.worker import ImageInfo
    sizes = [(1200, 800), (1080, 1440), (800, 600), (1080, 608), (1600, 900)]
    out = []
    for i in range(count):
        w, h = sizes[i % len(sizes)]
        out.append(ImageInfo(
            url=f'https://mmbiz.qpic.cn/mmbiz_png/FAKE{i}/0',
            index=i, data=png_bytes(w, h), width=w, height=h, ext='.png',
        ))
    return out


def test_preview_zoom(app):
    """预览缩放：这是判断「下载的到底是不是原图」的唯一手段"""
    from web_image_dl.widgets import ImageViewer

    print('\n[UI-9] 预览缩放：能放到 100% 看真实像素')
    viewer = ImageViewer()
    viewer.resize(900, 700)
    viewer.show()
    QApplication.processEvents()

    viewer.show_image(png_bytes(2000, 1500))
    QApplication.processEvents()
    check(viewer._fit is True, '默认是「适应窗口」模式')
    fit_w = viewer.preview_label.pixmap().width()
    check(fit_w < 2000, f'适应窗口下先缩小以看清全图（{fit_w} < 2000）')
    check('适应窗口' in viewer.zoom_label.text(),
          f'角落指示器说明当前模式与比例：{viewer.zoom_label.text()}')

    viewer.zoom_100()
    QApplication.processEvents()
    got = viewer.preview_label.pixmap().size()
    eq((got.width(), got.height()), (2000, 1500), '100% 时按原始像素 1:1 显示')
    eq(viewer.zoom_label.text(), '100%', '指示器显示 100%')
    eq(viewer._source.size(), QSize(2000, 1500), '放大到 100% 不污染原始像素')

    viewer.zoom_in()
    check(viewer.preview_label.pixmap().width() > 2000, '能继续放大到 100% 以上')
    viewer.zoom_out()
    viewer.zoom_out()
    check(viewer.preview_label.pixmap().width() < 2000, '能缩回去')

    viewer.toggle_fit()
    check(viewer._fit is True, '双击（toggle_fit）从 100% 切回适应窗口')
    viewer.zoom_100()
    viewer.toggle_fit()
    check(viewer._fit is True, '再双击又回到适应窗口')

    print('\n[UI-9b] 缩放上限受像素预算保护（8 倍套大图会把内存撑爆）')
    viewer.show_image(png_bytes(100, 80))          # 小图，避免测试自己吃掉大内存
    viewer.MAX_RENDER_PIXELS = 100 * 80 * 4        # 临时把预算压到 4 倍
    eq(round(viewer.max_zoom(), 2), 2.0, '按图反推出安全倍率上限 2.0')
    viewer.set_zoom(8.0)
    eq(round(viewer._zoom, 2), 2.0, '请求 8 倍被夹到 2.0')
    check(viewer.max_zoom() >= 1.0, '无论图多大，100% 永远保留')
    viewer.MAX_RENDER_PIXELS = ImageViewer.MAX_RENDER_PIXELS


def test_thumbnail_card(app):
    from web_image_dl.widgets import ThumbnailItem

    print('\n[UI-10] 缩略图卡片：信息合成一行 + 角标')
    infos = _fake_infos(1)
    item = ThumbnailItem(infos[0])
    check('1200×800' in item.info_label.text(), f'信息行含分辨率：{item.info_label.text()}')
    check('KB' in item.info_label.text() or 'MB' in item.info_label.text(),
          f'信息行含体积：{item.info_label.text()}')
    check(item.badge.isHidden(), '普通图不显示角标')
    check('1200×800' in item.toolTip(), 'tooltip 里有分辨率')
    check('来源' in item.toolTip(), 'tooltip 标明原图档/回退压缩版')

    dup = _fake_infos(1)[0]
    dup.is_duplicate, dup.dup_of = True, 0
    dup_item = ThumbnailItem(dup)
    eq(dup_item.badge.text(), '重复', '重复图带「重复」角标（原来只有几乎看不见的淡黄边框）')
    check(not dup_item.badge.isHidden(), '「重复」角标可见')

    mini = _fake_infos(1)[0]
    mini.is_mini_square = True
    eq(ThumbnailItem(mini).badge.text(), '小图', '被过滤的小图带「小图」角标')


def test_hidden_hint(app):
    from web_image_dl.app import ImageDownloaderApp

    print('\n[UI-11] 被过滤的小图有可点开的入口（不再凭空消失）')
    win = ImageDownloaderApp()
    win.show()
    QApplication.processEvents()

    infos = _fake_infos(3)
    infos[2].is_mini_square = True
    for i, info in enumerate(infos):
        win._on_image_loaded(i, info)
    QApplication.processEvents()

    check(win.hidden_btn.isVisible(), '出现「已隐藏」提示按钮')
    check('已隐藏 1' in win.hidden_btn.text(), f'提示写明数量：{win.hidden_btn.text()!r}')
    check(win.thumb_items[2].filtered_out, '小的那张确实被隐藏了')
    check(not win.thumb_items[2].isVisible(), '隐藏是真的不显示')

    win._on_toggle_hidden()
    QApplication.processEvents()
    check(not win.filter_square_cb.isChecked(), '点提示按钮 = 取消「过滤小图与装饰」')
    check(all(not it.filtered_out for it in win.thumb_items), '小图重新显示出来')
    check('正在显示 1' in win.hidden_btn.text(), f'提示改为「正在显示」：{win.hidden_btn.text()!r}')

    win._on_toggle_hidden()
    QApplication.processEvents()
    check(win.thumb_items[2].filtered_out, '再点一次又隐藏回去')


def test_select_all_button(app):
    from web_image_dl.app import ImageDownloaderApp

    print('\n[UI-12] 全选/取消全选合成一个按钮，文案随状态切换')
    win = ImageDownloaderApp()
    win.show()
    QApplication.processEvents()

    infos = _fake_infos(3)
    infos[1].is_duplicate = True
    for i, info in enumerate(infos):
        win._on_image_loaded(i, info)
    QApplication.processEvents()

    # 解析完的图默认就是全勾选的（重复图除外），所以按钮一进来就是「取消全选」
    eq(win.select_all_btn.text(), '取消全选', '默认全勾选时按钮显示「取消全选」')
    win._on_select_all_clicked()
    QApplication.processEvents()
    eq(win.select_all_btn.text(), '全选', '取消全选后文案翻转')
    check(not any(it.checkbox.isChecked() for it in win.thumb_items), '全部取消勾选')

    win._on_select_all_clicked()
    QApplication.processEvents()
    eq(win.select_all_btn.text(), '取消全选', '全选后文案再翻回来')
    check(win.thumb_items[0].checkbox.isChecked() and win.thumb_items[2].checkbox.isChecked(),
          '非重复图被勾上')
    check(not win.thumb_items[1].checkbox.isChecked(), '重复图不参与批量选择')


def test_history_search(app):
    from datetime import datetime, timedelta

    import web_image_dl.app as app_mod
    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.history_manager import HistoryEntry

    print('\n[UI-13] 历史页：搜索过滤 + URL 只显示尾部 ID')
    now = datetime.now()
    rows = [
        HistoryEntry(id=1, source_url='https://mp.weixin.qq.com/s/AAA111bbb', total_images=8,
                     success_images=8, save_path=r'D:\图\公众号', status='done',
                     parsed_at=now.isoformat(timespec='seconds')),
        HistoryEntry(id=2, source_url='https://mp.weixin.qq.com/s/CCC222ddd', total_images=3,
                     success_images=0, save_path='', status='failed',
                     parsed_at=(now - timedelta(hours=5)).isoformat(timespec='seconds')),
    ]
    real = app_mod.history_manager.get_all
    app_mod.history_manager.get_all = lambda limit=200: list(rows)
    try:
        win = ImageDownloaderApp()
        win._switch_page(1)
        QApplication.processEvents()
        eq(win.history_table.rowCount(), 2, '两条记录都显示')

        cell = win.history_table.item(0, 0).text()
        check(cell == 'AAA111bbb', f'URL 列只显示尾部 ID（实际：{cell!r}）')
        check('https://' not in cell, 'URL 列不再被 mp.weixin.qq.com/s/ 前缀占满')
        check(win.history_table.item(0, 0).toolTip().startswith('https://'), 'tooltip 保留完整链接')

        win.history_search.setText('failed')
        QApplication.processEvents()
        eq(win.history_table.rowCount(), 1, '按原始状态值（failed）搜索能过滤出失败那条')
        eq(win.history_table.item(0, 0).text(), 'CCC222ddd', '过滤后剩下的正是 CCC222ddd')

        win.history_search.setText('失败')
        QApplication.processEvents()
        eq(win.history_table.rowCount(), 1, '按界面显示的中文状态（失败）搜索同样命中')

        win.history_search.setText('保存路径里没有的关键词')
        QApplication.processEvents()
        eq(win.history_table.rowCount(), 1, '无匹配时保留一行提示')
        check('没有匹配' in win.history_table.item(0, 0).text(), '提示文案是「没有匹配的记录」')

        win.history_search.setText('D:\\图')
        QApplication.processEvents()
        eq(win.history_table.rowCount(), 1, '按保存路径搜索也能命中')
    finally:
        app_mod.history_manager.get_all = real


def test_empty_guide_and_enter(app):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QMessageBox

    from web_image_dl.app import ImageDownloaderApp

    print('\n[UI-14] 空状态有引导 + 输入框回车即解析')
    win = ImageDownloaderApp()
    win.show()
    QApplication.processEvents()

    guide = win.preview.preview_label.text()
    check('粘贴公众号文章链接' in guide, f'空状态给出三步引导：{guide!r}')
    check('解析' in guide and '下载' in guide, '引导里写明了后续两步')

    # eventFilter：装过滤器而没实现时，Qt 会静默退回默认实现，等于白装
    warned = []
    orig_warn = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: (warned.append(a), QMessageBox.Ok)[1])
    try:
        win.url_input.setPlainText('https://example.com/not-wechat')
        app.sendEvent(win.url_input, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        QApplication.processEvents()
        eq(len(warned), 1, '回车触发了「解析」流程（非公众号链接被拦下并提示）')

        app.sendEvent(win.url_input, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.ShiftModifier))
        QApplication.processEvents()
        eq(len(warned), 1, 'Shift+Enter 不触发解析（留给多行内容）')

        guide_after = win.preview.preview_label.text()
        check('粘贴公众号文章链接' in guide_after or '加载中' in guide_after,
              '解析前预览区不会变成空白')
    finally:
        QMessageBox.warning = orig_warn


def wheel_at(viewer, vp_point, delta):
    """造一个真实滚轮事件。

    全局坐标由视口坐标反算 —— 和事件处理器里 `viewport().mapFromGlobal()` 的还原方式
    一致，这样测的才是真实那条路径（而不是直接调内部方法绕过坐标换算）。
    """
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    g = viewer.viewport().mapToGlobal(vp_point)
    return QWheelEvent(QPointF(vp_point), QPointF(g), QPoint(), QPoint(0, delta),
                       Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)


def test_zoom_anchor(app):
    """Ctrl+滚轮必须放大「鼠标指着的地方」，而不是永远从左上角长出来"""
    from PySide6.QtCore import QPoint

    from web_image_dl.widgets import ImageViewer

    print('\n[UI-15] Ctrl+滚轮以鼠标位置为锚点')
    viewer = ImageViewer()
    viewer.resize(900, 700)
    viewer.show()
    QApplication.processEvents()
    viewer.show_image(png_bytes(2000, 1500))
    QApplication.processEvents()

    check(viewer._fit is True, '默认适应窗口')
    eq(viewer.horizontalScrollBar().value(), 0, '适应窗口时横向没有滚动余量（此时必然贴左上角）')

    # 锚点取显示图的中间偏右偏下：这里离开左上角足够远，"左上角锚定"和"鼠标锚定"的差别很明显
    disp = viewer.preview_label.size()
    origin = viewer.preview_label.mapTo(viewer.viewport(), QPoint(0, 0))
    anchor = QPoint(origin.x() + int(disp.width() * 0.75),
                    origin.y() + int(disp.height() * 0.55))
    before = viewer._source_point_at(anchor)
    check(before is not None, '能反算出鼠标指着的原图像素')
    if before is None:
        return

    app.sendEvent(viewer.preview_label, wheel_at(viewer, anchor, 120))
    QApplication.processEvents()
    check(viewer._fit is False and viewer.preview_label.width() > disp.width(),
          f'滚轮确实放大了（{disp.width()} → {viewer.preview_label.width()}）')

    after = viewer._source_point_at(anchor)
    check(after is not None, '放大后仍能反算锚点处的原图像素')
    if after is not None:
        dx, dy = abs(after[0] - before[0]), abs(after[1] - before[1])
        check(dx <= 4 and dy <= 4,
              f'鼠标下的像素缩放前后停在原地（偏移 {dx:.1f}, {dy:.1f} 原图像素）'
              f' 前 {before[0]:.0f},{before[1]:.0f} 后 {after[0]:.0f},{after[1]:.0f}')
    check(viewer.horizontalScrollBar().value() > 0,
          '横向滚动条被推开了 —— 旧实现（左上角锚定）这里永远是 0')

    # 再滚一次，锚点要持续成立（不是只对第一下有效）
    before2 = viewer._source_point_at(anchor)
    app.sendEvent(viewer.preview_label, wheel_at(viewer, anchor, 120))
    QApplication.processEvents()
    after2 = viewer._source_point_at(anchor)
    if before2 and after2:
        dx, dy = abs(after2[0] - before2[0]), abs(after2[1] - before2[1])
        check(dx <= 4 and dy <= 4, f'连续缩放锚点依旧稳定（偏移 {dx:.1f}, {dy:.1f}）')

    # 缩回比视口还小的时候，图会被居中，锚点自然失效——不该崩、也不该留下怪状态
    for _ in range(12):
        app.sendEvent(viewer.preview_label, wheel_at(viewer, anchor, -120))
        QApplication.processEvents()
    check(viewer.preview_label.width() < viewer.viewport().width(),
          '能一路缩到比视口还小')
    eq(viewer._source.size(), QSize(2000, 1500), '反复缩放不污染原始像素')


def test_space_pan(app):
    """放大超出视口后，按住空格 + 拖动 = 平移"""
    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QKeyEvent, QMouseEvent

    from web_image_dl.widgets import ImageViewer

    print('\n[UI-16] 按住空格拖动平移')
    viewer = ImageViewer()
    viewer.resize(900, 700)
    viewer.show()
    QApplication.processEvents()
    viewer.show_image(png_bytes(2000, 1500))
    QApplication.processEvents()

    # 空格能不能拦，取决于鼠标是否悬在预览区上；离屏环境光标位置不可控，直接钉住这一条
    viewer.under_mouse = lambda: True

    space_press = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier)
    space_release = QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.NoModifier)

    eq(viewer._pan_available(), False, '图还在窗口里装得下 → 没什么可拖')
    # 指针压在预览区上但确实没得拖：吃掉空格（免得飘进输入框），并且把原因说出来。
    # 旧版这里悄悄放行、界面上毫无反应，用户只会以为拖动功能坏了。
    check(viewer._handle_space_key(space_press) is True, '指针在预览区上 → 空格归预览区处理')
    check(viewer._space_held is True, '进入待命（此时按下不会动，但会给提示）')
    check('放大' in viewer.zoom_label.text() and viewer.zoom_label.isVisible(),
          f'右下角提示「先放大再拖」：{viewer.zoom_label.text()!r}')

    # 指针不在预览区上（在别处、或正在打字）→ 空格必须原样放行
    viewer._space_held = False
    viewer._update_pan_cursor()
    viewer.under_mouse = lambda: False
    check(viewer._handle_space_key(space_press) is False, '指针不在预览区 → 不抢空格')
    viewer.under_mouse = lambda: True

    viewer.set_zoom(2.0)
    QApplication.processEvents()
    eq(viewer._pan_available(), True, '放大到超出视口后可以拖')
    check(viewer._handle_space_key(space_press) is True, '空格被拦住（备选的抓手姿势）')
    check(viewer._space_held is True, '已进入拖动待命状态')
    eq(viewer.viewport().cursor().shape(), Qt.OpenHandCursor, '光标变成张开的手，提示可以拖')

    viewer._set_scroll(300, 200)        # 先挪到中间，双向都留出余量
    QApplication.processEvents()
    h0 = viewer.horizontalScrollBar().value()
    v0 = viewer.verticalScrollBar().value()
    check(h0 > 0 and v0 > 0, f'拖动前滚动条在中间（h={h0} v={v0}）')

    start = QPoint(400, 350)
    g0 = viewer.viewport().mapToGlobal(start)
    app.sendEvent(viewer.preview_label, QMouseEvent(
        QEvent.MouseButtonPress, QPointF(start), QPointF(g0),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    QApplication.processEvents()
    # v1.4.0 起按住空格也不再「按下即拖」：空格只是让光标一直保持张开的手，
    # 起步仍统一走 PAN_START_SLOP 门槛，免得单击/双击被带歪。
    check(viewer._pending_pan is True, '按下左键进入待起步（空格不再改变门槛）')
    move = QPoint(start.x() - 120, start.y() - 80)
    gm = viewer.viewport().mapToGlobal(move)
    app.sendEvent(viewer.preview_label, QMouseEvent(
        QEvent.MouseMove, QPointF(move), QPointF(gm),
        Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    QApplication.processEvents()
    check(viewer._panning is True, '越过门槛后进入拖动中')
    eq(viewer.horizontalScrollBar().value(), h0 + 120, '向左拖 → 内容跟手（横向 +120）')
    eq(viewer.verticalScrollBar().value(), v0 + 80, '向上拖 → 内容跟手（纵向 +80）')

    app.sendEvent(viewer.preview_label, QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(move), QPointF(gm),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    QApplication.processEvents()
    check(viewer._panning is False, '松开左键结束拖动')
    check(viewer._handle_space_key(space_release) is True, '松开空格退出待命状态')
    check(viewer._space_held is False, '空格状态已复位')
    # 空格松了、图仍超出视口 → 光标回到「张开的手」。v1.3.x 这里退回箭头，
    # 等于把「能拖」的信号收走了；v1.4.0 直接左键就能拖，光标必须常驻。
    eq(viewer.viewport().cursor().shape(), Qt.OpenHandCursor,
       '松开空格后仍是张开的手（图还超出视口，直接拖随时成立）')

    # 不按空格、直接用左键拖 —— v1.4.0 起这是**主路径**（用户实测定论：比按空格好用），
    # 空格降级为备选。但得走够门槛才算拖：轻轻一抖当点击，免得单击/双击把画面带歪。
    h1 = viewer.horizontalScrollBar().value()
    app.sendEvent(viewer.preview_label, QMouseEvent(
        QEvent.MouseButtonPress, QPointF(start), QPointF(g0),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    tiny = QPoint(start.x() - 2, start.y())
    app.sendEvent(viewer.preview_label, QMouseEvent(
        QEvent.MouseMove, QPointF(tiny), QPointF(viewer.viewport().mapToGlobal(tiny)),
        Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    QApplication.processEvents()
    eq(viewer.horizontalScrollBar().value(), h1, '只动 2px（门槛内）算点击，画面不动')
    check(viewer._panning is False, '门槛内还没进入拖动状态')

    app.sendEvent(viewer.preview_label, QMouseEvent(
        QEvent.MouseMove, QPointF(move), QPointF(gm),
        Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    QApplication.processEvents()
    check(viewer._panning is True, '超过门槛后正式进入拖动')
    eq(viewer.horizontalScrollBar().value(), h1 + 120, '不按空格、左键直接拖也能平移（+120）')

    # 松开事件落到别的控件上也必须认账，否则状态卡在拖动中、光标一直握拳
    app.sendEvent(viewer.zoom_label, QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(move), QPointF(gm),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    QApplication.processEvents()
    check(viewer._panning is False, '松开事件落在别处也能正常结束拖动')
    eq(viewer.viewport().cursor().shape(), Qt.OpenHandCursor, '光标回到张开的手（仍有得拖）')


def test_direct_drag_is_primary(app):
    """v1.4.0：「直接左键拖」是主路径，空格降为备选。

    用户实测结论是**不按空格更好用**。所以「能拖」这件事不能再靠按空格才显现 ——
    否则用户根本没理由去试左键。这里钉三件事：
      a) 图超出视口时，一个键都不按，光标就是张开的手；
      b) 图装得下时按下左键会给出「先放大再拖」的提示，而不是静默无反应；
      c) 反复按左键不会卡状态、不抛异常（过滤器里手动转发事件会递归爆栈，不能这么干）。
    """
    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QMouseEvent

    from web_image_dl.widgets import ImageViewer

    print('\n[UI-19] 直接左键拖 = 主路径（空格降为备选）')
    viewer = ImageViewer()
    viewer.resize(900, 700)
    viewer.show()
    QApplication.processEvents()
    viewer.under_mouse = lambda: True          # 离屏环境光标位置没法摆

    # a) 还没放图：没得拖 → 箭头
    eq(viewer.viewport().cursor().shape(), Qt.ArrowCursor, '空状态不给手形（没得拖）')

    viewer.show_image(png_bytes(2000, 1500))
    QApplication.processEvents()
    eq(viewer._has_overflow(), False, '适应窗口下整图都在视口内')
    eq(viewer.viewport().cursor().shape(), Qt.ArrowCursor, '装得下就不给手形 —— 光标不许骗人')

    viewer.set_zoom(2.0)
    QApplication.processEvents()
    eq(viewer._has_overflow(), True, '放大后图超出视口')
    eq(viewer._space_held, False, '前置条件：一个键都没按')
    eq(viewer.viewport().cursor().shape(), Qt.OpenHandCursor,
       '不按任何键，光标就是张开的手 —— 「能直接拖」唯一的可见信号')
    eq(viewer.preview_label.cursor().shape(), Qt.OpenHandCursor, 'label 上也是手形')

    # b) 图装得下时按左键 → 提示「先放大再拖」。v1.3.x 只在按空格时提示，
    #    而用户根本不会去按空格，于是「点了没反应」。
    viewer.fit_to_window()
    QApplication.processEvents()
    viewer._update_zoom_label()
    QApplication.processEvents()
    eq(viewer._has_overflow(), False, '回到适应窗口')

    o = QPoint(400, 350)
    go = viewer.viewport().mapToGlobal(o)
    app.sendEvent(viewer.preview_label, QMouseEvent(
        QEvent.MouseButtonPress, QPointF(o), QPointF(go),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    QApplication.processEvents()
    check(viewer._pending_pan is False and viewer._panning is False,
          '没得拖时不进入任何拖动状态')
    check('放大' in viewer.zoom_label.text(),
          f'左键点下去要说清「先放大再拖」：{viewer.zoom_label.text()!r}')
    check(viewer.zoom_label.isVisible(), '提示真的显示出来了')

    # c) 过滤器绝不能把事件「手动转发」给同一个控件 —— 过滤器装在 viewport 和
    #    label 两层上，在过滤器里 sendEvent 会立刻重新进这个过滤器并递归爆栈
    #    （试过：26MB 的 RecursionError 堆栈 + 测试挂死）。这条用「跑完不炸」钉住。
    viewer.set_zoom(2.0)
    QApplication.processEvents()
    app.sendEvent(viewer.viewport(), QMouseEvent(
        QEvent.MouseButtonPress, QPointF(o), QPointF(go),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    QApplication.processEvents()
    check(viewer._pending_pan is True, '有得拖时进入待起步状态')
    app.sendEvent(viewer.viewport(), QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(o), QPointF(go),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    QApplication.processEvents()
    check(viewer._pending_pan is False, '松开后待起步状态复位（没卡在拖动里）')

    # 没得拖时连按几次：不许抛异常、不许卡状态
    viewer.fit_to_window()
    QApplication.processEvents()
    for _ in range(3):
        app.sendEvent(viewer.viewport(), QMouseEvent(
            QEvent.MouseButtonPress, QPointF(o), QPointF(go),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        app.sendEvent(viewer.viewport(), QMouseEvent(
            QEvent.MouseButtonRelease, QPointF(o), QPointF(go),
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        QApplication.processEvents()
    check(viewer._pending_pan is False and viewer._panning is False,
          '没得拖时反复按左键不会卡进拖动状态，也没有异常（防递归）')


def test_pan_guards(app):
    """空格归谁：看指针压在哪，不看键盘焦点在哪"""
    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QKeyEvent, QMouseEvent

    from web_image_dl.app import ImageDownloaderApp

    print('\n[UI-17] 空格归谁：指针位置说了算')
    win = ImageDownloaderApp()
    win.show()
    QApplication.processEvents()

    viewer = win.preview
    viewer.under_mouse = lambda: True          # 离屏环境光标不可控
    viewer.show_image(png_bytes(3000, 2400))
    QApplication.processEvents()
    viewer.set_zoom(1.5)
    QApplication.processEvents()

    eq(viewer._pan_available(), True, '大图 + 指针在预览区 → 可以拖')

    # 焦点在文本框里、但指针压在预览区上 → 用户是在拖图，空格归预览区。
    # 旧版把「焦点不在文本框里」当成能拖的前提，结果用户在链接框里点一下之后
    # 整段拖动就废了（手形光标不出来、按下去没反应），而且界面上毫无提示。
    viewer._text_input_focused = lambda: True
    try:
        eq(viewer._pan_available(), True,
           '焦点在文本框里，指针在预览区上 → 照样能拖（旧版返回 False，整段废掉）')
        check(viewer._handle_space_key(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier)) is True,
            '指针在预览区上 → 空格归预览区（否则会飘进输入框且什么都不发生）')
        check(viewer._space_held is True, '进入拖动待命')
        QApplication.processEvents()
        eq(viewer.viewport().cursor().shape(), Qt.OpenHandCursor,
           '手形光标必须出来 —— 「能拖」的可见信号（v1.4.0 起不按空格也是手形）')

        # 焦点在文本框里也真的能拖起来
        viewer._set_scroll(200, 200)
        QApplication.processEvents()
        h0 = viewer.horizontalScrollBar().value()
        start = QPoint(300, 300)
        g0 = viewer.viewport().mapToGlobal(start)
        app.sendEvent(viewer.preview_label, QMouseEvent(
            QEvent.MouseButtonPress, QPointF(start), QPointF(g0),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        mv = QPoint(start.x() - 100, start.y())
        app.sendEvent(viewer.preview_label, QMouseEvent(
            QEvent.MouseMove, QPointF(mv), QPointF(viewer.viewport().mapToGlobal(mv)),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QApplication.processEvents()
        eq(viewer.horizontalScrollBar().value(), h0 + 100,
           '焦点在文本框里，按住空格拖也能平移')
        app.sendEvent(viewer.preview_label, QMouseEvent(
            QEvent.MouseButtonRelease, QPointF(mv), QPointF(mv),
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        QApplication.processEvents()
    finally:
        del viewer._text_input_focused
    viewer._space_held = False
    viewer._update_pan_cursor()

    # 指针不在预览区：那才是真的在打字，空格必须原样进输入框
    viewer.under_mouse = lambda: False
    win.url_input.setPlainText('')
    win.url_input.setFocus()
    QApplication.processEvents()
    check(viewer._handle_space_key(
        QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier)) is False,
        '指针不在预览区 → 空格放行')
    viewer._space_held = False
    if QApplication.focusWidget() is win.url_input:
        # 注意 text 参数不能省：QTextEdit 插入的是 event.text()，不带上就什么都不会进去
        app.sendEvent(win.url_input,
                      QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, ' '))
        QApplication.processEvents()
        eq(win.url_input.toPlainText(), ' ',
           '链接框里的空格真的敲进去了（指针不在预览区时不会被吞）')
    else:
        print('   （离屏环境拿不到输入焦点，这条跳过；上面的放行断言已覆盖同一逻辑）')

    viewer.under_mouse = lambda: True
    win.parse_btn.setFocus()
    QApplication.processEvents()
    eq(viewer._pan_available(), True, '指针回到预览区 → 又能拖了')


def test_pan_real_path(app):
    """拖动要经得起真实事件分发（窗口命中测试 + 「按住的控件」记账）。

    UI-16/17 是把事件直接塞给 label 的，那条路绕开了 Qt 的命中测试：
    真机上事件可能落在别处（比例指示器、滚动条、窗口外），坐标系也不同。
    这里用 QTest 从窗口系统走一遍，确认拖动照样成立。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from web_image_dl.widgets import ImageViewer

    print('\n[UI-18] 拖动走真实事件路径（QTest → 窗口系统）')
    viewer = ImageViewer()
    viewer.resize(900, 700)
    viewer.show()
    QApplication.processEvents()
    viewer.show_image(png_bytes(2000, 1500))
    QApplication.processEvents()
    viewer.under_mouse = lambda: True          # 离屏环境光标位置没法摆
    viewer.set_zoom(2.0)
    QApplication.processEvents()
    viewer._set_scroll(400, 400)
    QApplication.processEvents()

    h0 = viewer.horizontalScrollBar().value()
    v0 = viewer.verticalScrollBar().value()
    QTest.mousePress(viewer.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(400, 350))
    QApplication.processEvents()
    check(viewer._pending_pan is True, '真实路径下左键按下被接住（还没过门槛）')

    QTest.mouseMove(viewer.viewport(), QPoint(360, 330), 5)
    QApplication.processEvents()
    check(viewer._panning is True, '越过门槛后进入拖动')
    eq(viewer.horizontalScrollBar().value(), h0 + 40, '真实路径下横向跟手 +40')
    eq(viewer.verticalScrollBar().value(), v0 + 20, '真实路径下纵向跟手 +20')

    QTest.mouseRelease(viewer.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(360, 330))
    QApplication.processEvents()
    check(viewer._panning is False, '真实路径下松开结束拖动')
    eq(viewer.viewport().cursor().shape(), Qt.OpenHandCursor,
       '真实路径下光标回到张开的手（图仍超出视口）')


def test_blocked_panel(app):
    """P0：屏蔽尺寸曾经是「单向门」—— 误屏蔽一个尺寸，那类图从此静默消失，
    面板上既看不到是哪些尺寸、也没有撤销入口，只能去手改 JSON 文件。
    这里从右键回调一路走到面板上的「恢复」按钮，钉住「可逆」这件事。"""
    from PySide6.QtWidgets import QMessageBox, QPushButton

    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.blocked_config import blocked_config

    print('\n[UI-20] 「已屏蔽的尺寸」面板：看得见、撤得掉')
    with _isolated_config():
        win = ImageDownloaderApp()
        win.show()
        QApplication.processEvents()

        eq(blocked_config.count(), 0, '起手屏蔽表为空（用的是临时配置，不碰真实用户数据）')
        # 用 isHidden 而不是 isVisible：历史页此刻不是 QStackedWidget 的当前页，
        # 整页都算「不可见」，isVisible 恒为 False，只有显式隐藏才是我们要断言的
        check(not win.blocked_empty.isHidden(), '空的时候说明去哪儿屏蔽')
        check(win.blocked_scroll.isHidden(), '空的时候不占地方')
        check(win.blocked_clear_btn.isHidden(), '空的时候没有「全部恢复」')
        eq(win.blocked_title.text(), '已屏蔽的尺寸', '标题不显示多余的 (0)')

        infos = _fake_infos(3)
        for i, info in enumerate(infos):
            win._on_image_loaded(i, info)
        QApplication.processEvents()
        eq(len(win.thumb_items), 3, '3 张图进缩略图区')

        # 右键菜单最终调的就是这个回调
        win._on_block_size(infos[0], 'ui', (infos[0].width, infos[0].height))
        QApplication.processEvents()

        rows = blocked_config.list_blocked()
        eq(blocked_config.count(), 1, '屏蔽表里多了一条')
        eq((rows[0]['category'], rows[0]['width'], rows[0]['height']),
           ('ui', 1200, 800), '记下的正是这张图的尺寸与分类')
        eq(rows[0]['hits'], 1, '当场被过滤掉的那张也计入「已过滤 N 张」')
        check(win.thumb_items[0].filtered_out, '被屏蔽的那张立刻隐藏')
        eq(win.thumb_items[0].badge.text(), '已屏蔽',
           '角标写明「已屏蔽」（与笼统的「小图」区分开，用户才知道能去历史页撤）')
        check('屏蔽' in win.thumb_items[0].toolTip(), 'tooltip 说清是被哪条规则拦下的')
        eq(win.blocked_title.text(), '已屏蔽的尺寸 (1)', '面板标题给出条数')
        check(not win.blocked_scroll.isHidden(), '有内容时列表显示出来')
        check(not win.blocked_clear_btn.isHidden(), '有内容时出现「全部恢复」')
        check(win.blocked_empty.isHidden(), '有内容时空状态提示让位')

        row_w = win.blocked_rows.itemAt(0).widget()
        check(row_w is not None, '面板里出现了那一行')
        undo = [b for b in row_w.findChildren(QPushButton) if b.text() == '恢复']
        eq(len(undo), 1, '那一行带一个「恢复」按钮')
        undo[0].click()          # 走真实点击 → 槽函数
        QApplication.processEvents()

        eq(blocked_config.count(), 0, '恢复后屏蔽表清空')
        check(not win.thumb_items[0].filtered_out, '被它拦下的图当场放回来')
        check(win.thumb_items[0].isVisible(), '放回来是真的显示出来了')
        check(win.thumb_items[0].checkbox.isChecked(),
              '放回来的图直接勾上 —— 撤销屏蔽的本意就是「这些我要下」')
        check(not win.blocked_empty.isHidden(), '面板回到空状态')

        # 「全部恢复」
        win._on_block_size(infos[1], 'avatar', (infos[1].width, infos[1].height))
        win._on_block_size(infos[2], 'cover', (infos[2].width, infos[2].height))
        QApplication.processEvents()
        eq(blocked_config.count(), 2, '又屏蔽两条')
        eq([r['category'] for r in blocked_config.list_blocked()], ['avatar', 'cover'],
           '两条分别归到各自分类')

        orig_q = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            win.blocked_clear_btn.click()
            QApplication.processEvents()
        finally:
            QMessageBox.question = orig_q

        eq(blocked_config.count(), 0, '「全部恢复」清空了屏蔽表')
        eq(blocked_config.list_blocked(), [], '列表也空了')
        check(not win.thumb_items[1].filtered_out and not win.thumb_items[2].filtered_out,
              '两条屏蔽拦下的图都放回来了')
        check(win.thumb_items[1].checkbox.isChecked() and win.thumb_items[2].checkbox.isChecked(),
              '放回来的两张都勾上了')
        check(not win.blocked_empty.isHidden() and win.blocked_scroll.isHidden(),
              '面板回到空状态')

        # 用户点「取消」时不许清空
        win._on_block_size(infos[0], 'ui', (infos[0].width, infos[0].height))
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        try:
            win.blocked_clear_btn.click()
            QApplication.processEvents()
        finally:
            QMessageBox.question = orig_q
        eq(blocked_config.count(), 1, '确认框选「否」时屏蔽表原样保留')

        win.close()
        QApplication.processEvents()


def test_settings_persist(app):
    """P1：旧版全项目零 QSettings —— 排序方式、保存格式、两个开关、窗口大小位置
    每次启动都回到默认，连保存目录也每次都从「文档」开始。这里钉住「记事了」。"""
    from PySide6.QtWidgets import QFileDialog

    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.settings import K_FORMAT, K_LAST_SAVE_DIR, settings

    print('\n[UI-21] 界面状态与保存目录会被记住')
    with _isolated_config():
        win = ImageDownloaderApp()
        win.show()
        QApplication.processEvents()
        # 尺寸刻意选在离屏平台那块 800×800 的屏幕之内：Qt 的 restoreGeometry 会把
        # 窗口裁进可用屏幕，用 1100×750 以上的尺寸会被裁到 798×774，看起来像「没记住」，
        # 其实记忆是好的（同一个 QByteArray 在 1920×1080 的机器上原样还原）
        win.resize(720, 640)
        QApplication.processEvents()
        win.sort_cb.setCurrentIndex(2)
        win.fmt_cb.setCurrentIndex(1)                 # JPG
        win.filter_square_cb.setChecked(False)
        win.original_cb.setChecked(False)
        settings.set(K_LAST_SAVE_DIR, r'C:\Windows')
        win._save_settings()
        QApplication.processEvents()

        win2 = ImageDownloaderApp()
        win2.show()
        QApplication.processEvents()
        eq(win2.sort_cb.currentIndex(), 2, '排序方式被记住')
        eq(win2.fmt_cb.currentText(), 'JPG', '保存格式被记住')
        eq(win2.filter_square_cb.isChecked(), False, '「过滤小图与装饰」开关被记住')
        eq(win2.original_cb.isChecked(), False, '「原图画质」开关被记住')
        check(abs(win2.width() - 720) <= 4, f'窗口宽度被记住（实测 {win2.width()}）')
        check(abs(win2.height() - 640) <= 4, f'窗口高度被记住（实测 {win2.height()}）')
        check((win2.width(), win2.height()) != (1100, 750),
              '确实来自上次的几何信息，而不是 __init__ 里的默认 resize')
        eq(win2._last_save_dir(), r'C:\Windows', '上次保存的目录被记住')

        # 保存对话框要真的从那个目录打开
        seen = {}
        orig_dialog = QFileDialog.getExistingDirectory
        QFileDialog.getExistingDirectory = staticmethod(
            lambda parent, title, start='': (seen.setdefault('start', start), '')[1]
        )
        try:
            win2._on_download()
        finally:
            QFileDialog.getExistingDirectory = orig_dialog
        eq(seen.get('start'), r'C:\Windows', '保存对话框从上次的目录开始，不用重新导航')

        # 目录被删了 → 回落到系统默认，而不是弹一个不存在的路径
        settings.set(K_LAST_SAVE_DIR, r'C:\__imgsnag_not_exist__')
        eq(win2._last_save_dir(), '', '上次的目录已不存在时回落到系统默认')

        # 存坏的下拉值不能让启动崩掉
        settings.set(K_FORMAT, 'TIFF（不是个选项）')
        win3 = ImageDownloaderApp()
        QApplication.processEvents()
        eq(win3.fmt_cb.currentText(), '原格式', '存坏的下拉值回落默认值，不崩')

        win2.close()
        win3.close()
        win.close()
        QApplication.processEvents()


def main():
    app = QApplication.instance() or QApplication([])
    test_preview_scale(app)
    test_cancel_button(app)
    test_copy_matches_impl(app)
    test_shutdown_with_running_thread(app)
    test_download_through_gui(app)
    test_preview_zoom(app)
    test_thumbnail_card(app)
    test_hidden_hint(app)
    test_select_all_button(app)
    test_history_search(app)
    test_empty_guide_and_enter(app)
    test_zoom_anchor(app)
    test_space_pan(app)
    test_pan_guards(app)
    test_pan_real_path(app)
    test_direct_drag_is_primary(app)
    test_blocked_panel(app)
    test_settings_persist(app)
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
