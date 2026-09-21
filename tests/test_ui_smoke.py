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

十、v1.6.0 关于与检查更新（UI-22）
   两个新入口：侧边栏底部的版本号打开「关于」，发现新版本时侧边栏亮提示、点开是
   更新对话框。检查更新会真的起线程跑一遍（网络替换成假数据，测试不联网）。
   这里钉住的不是「对话框能弹出来」，而是几件容易做错的事：
   a) 静默检查（启动时自动查）**一律不弹窗**，只在侧边栏亮提示 —— 用户没点过，
      弹窗就是打扰；连不上 GitHub 时更不许每次启动都弹。
   b) 失败必须说话（手动检查时），且用 warning 而不是 information，别把失败说成成功。
   c) 6 小时节流：GitHub 未认证请求按 IP 限流 60 次/小时，每次启动都查迟早被 403。
   d) 关于里要写明「唯一的对外请求是检查更新」，不能一边说纯本地一边偷偷联网。

十一、v1.7.0 下载落进专属图库文件夹（UI-23 / UI-24）
  旧版每下必选目录，且图是平铺命名直接扔进所选目录：一直存「下载」，第二篇的 img_01
  就变成 img_01_1，越用越乱。现在默认直接存进「图库／日期_时分秒_标题」，不再弹框，
  「下载」目录里也不会再堆图集（那是最容易顺手清空的地方）。这里钉的不是「有没有弹框」，
  而是几件容易做错的事：默认路径不许还弹框、标题取不到要有兜底名、同名要另起 _2 不许覆盖、
  「另存到…」同样建子文件夹、图库不可用要**回退**到手动选择而不是让下载白费；
  以及历史页：本地文件夹没了要标出来并把「打开」置灰，别给个点了没反应的按钮。

十二、v1.7.1 两处交互调整（UI-25）
  a) 「打开图库 / 另存到… / 下载」从左到右排进动作栏 —— 放在状态栏里用户注意不到。
  b) 历史页「解析」点一下直接开解析；已有解析在跑时只填链接不触发
     （_on_parse 的同按钮语义是「取消」，直接调会把正在跑的解析打断）。

十三、v1.8.0 历史批次在软件里打开（UI-26）
  「历史里只要没删本地图片就能打开」以前只能跳资源管理器。现在点「查看」（或双击行）
  直接在软件里翻看。钉住：真的从磁盘读回像素（不是空壳）、**只读**不许重复下载
  （图已在本地，再下只会在图库里堆同名副本）、按磁盘真实文件名与自然序显示、
  坏文件跳过并计数、文件夹没了置灰说原因。
"""
import os
import re
import sys
import tempfile
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# 必须自带字体目录：PySide6 不再内置字体，缺了它 Qt 会退回缺字体度量，
# 控件的建议宽度整体漂移 —— 几何用例会随机挂（曾出现最小宽度 800→828 的漂移）
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

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

    v1.7.0 起保存不再弹文件夹选择框，而是自动建「图库／日期_时分秒_标题」子文件夹，
    所以断言也跟着改成「文件落在自动建出来的那个子文件夹里」。
    """
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.settings import K_LIBRARY_DIR, settings
    from web_image_dl.worker import ImageInfo

    print('\n[UI-8] 点保存后图片真的落盘（钉住 SaveWorker 被正确引入）')
    with _isolated_config():
        root = tempfile.mkdtemp(prefix='imgsnag_ui_lib_')
        settings.set(K_LIBRARY_DIR, root)

        win = ImageDownloaderApp()
        win.show()
        QApplication.processEvents()
        win._article_title = '秋天的第一杯奶茶'

        for i in range(3):
            info = ImageInfo(url=f'https://mmbiz.qpic.cn/mmbiz_png/ID{i}/0', index=i,
                             data=png_bytes(60 + i, 40), ext='.png',
                             width=60 + i, height=40)
            win._on_image_loaded(i, info)
        QApplication.processEvents()
        eq(len(win.thumb_items), 3, '3 张图进了缩略图区')
        eq(win.download_btn.isEnabled(), True, '有选中项时保存按钮可用')

        # 新流程不该再弹选择框 —— 真弹了就让测试直接失败，而不是傻等对话框
        orig_dialog = QFileDialog.getExistingDirectory
        orig_info = QMessageBox.information
        orig_warn = QMessageBox.warning
        QFileDialog.getExistingDirectory = staticmethod(
            lambda *a, **k: (_ for _ in ()).throw(AssertionError('v1.7.0 起下载不该再弹选择框')))
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        try:
            try:
                win._on_download()   # 缺 import 时这里抛 NameError，save_worker 会留在 None
            except AssertionError as e:
                check(False, str(e))
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

        subs = sorted(os.listdir(root))
        eq(len(subs), 1, '图库下自动建了一个专属子文件夹')
        sub = os.path.join(root, subs[0]) if subs else root
        check(bool(re.match(r'^\d{4}-\d{2}-\d{2}_\d{6}_', subs[0] if subs else '')),
              f'子文件夹名以「日期_时分秒_」开头（实测 {subs[0] if subs else "无"}）')
        check((subs[0] if subs else '').endswith('秋天的第一杯奶茶'),
              '文章标题进了文件夹名，一眼看出是哪一篇')

        got = sorted(os.listdir(sub))
        eq(got, ['img_01.png', 'img_02.png', 'img_03.png'], '三张图按序号落进子文件夹')
        for name in got:
            size = os.path.getsize(os.path.join(sub, name))
            check(size > 0, f'{name} 不是空文件（实测 {size} 字节）')

        # 界面状态要复位，否则用户看到的是永远停在「保存中」的按钮
        eq(win.download_btn.text(), '下载选中图片 (3)', '保存结束后按钮文案复位')
        check(win.download_btn.isEnabled(), '保存结束后按钮恢复可用')

        win.close()
        QApplication.processEvents()


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
        import time

        from web_image_dl.blocked_config import blocked_config
        from web_image_dl.settings import K_LAST_UPDATE_CHECK, settings

        self.dir = tempfile.mkdtemp(prefix='imgsnag_iso_')
        self._blocked = blocked_config
        self._settings = settings
        self._saved_path = blocked_config.config_path
        blocked_config.config_path = os.path.join(self.dir, 'blocked_sizes.json')
        blocked_config.reload()
        settings.use_ini_file(os.path.join(self.dir, 'settings.ini'))
        # 别让测试真的联外：窗口构造 2 秒后会自动查一次新版本，把「上次检查时间」
        # 设成现在，6 小时节流就会挡住它。UI-22 里自己覆盖这个值来测节流本身。
        settings.set(K_LAST_UPDATE_CHECK, int(time.time()))
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
        # 其实记忆是好的（同一个 QByteArray 在 1920×1080 的机器上原样还原）。
        win.resize(760, 640)
        QApplication.processEvents()
        # 记下**实际**达成的尺寸而不是写死 760/640：最小宽度是布局算出来的，
        # 写死会在动作栏变化时把「被顶回来几像素」误报成「没记住」
        saved_w, saved_h = win.width(), win.height()
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
        # 宽度不做 ±4 精确断言：离屏平台的 restoreGeometry 在贴近屏幕右缘时会
        # 加上窗框边距再钳制（实测存 780/790 都恢复成 792），宽度的最后几像素
        # 不可靠。高度不受影响，精确往返；宽度只断言「确实来自存档」：
        #   • 不是 __init__ 的默认 1100
        #   • 落在 [最小宽度, 屏宽] 这个合法区间内
        # （真实机器 1920×1080 上同一份存档原样还原，宽度精确性在那里才验得了）
        check(abs(win2.height() - saved_h) <= 4, f'窗口高度被记住（{win2.height()} vs 存下的 {saved_h}）')
        check(win2.width() != 1100,
              f'宽度来自存档而不是默认 1100（实测 {win2.width()}）')
        check(win.minimumSizeHint().width() - 4 <= win2.width() <= 800,
              f'宽度落在 [最小宽度, 屏宽] 内（实测 {win2.width()}，最小 {win.minimumSizeHint().width()}）')
        check((win2.width(), win2.height()) != (1100, 750),
              '确实来自上次的几何信息，而不是 __init__ 里的默认 resize')
        eq(win2._last_save_dir(), r'C:\Windows', '上次保存的目录被记住')

        # 「另存到…」对话框要真的从那个目录开始（下载本身已经不弹框了）
        seen = {}
        orig_dialog = QFileDialog.getExistingDirectory
        QFileDialog.getExistingDirectory = staticmethod(
            lambda parent, title, start='': (seen.setdefault('start', start), '')[1]
        )
        try:
            win2._on_download_as()
        finally:
            QFileDialog.getExistingDirectory = orig_dialog
        eq(seen.get('start'), r'C:\Windows', '「另存到…」从上次的目录开始，不用重新导航')

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


def _grab_and_close_dialog(title, grab, tries=20, delay=50):
    """在对话框自己的事件循环里把它抓下来再关掉。

    QDialog.exec() 是阻塞的，测试没法在它返回之后再取内容 —— 只能在 exec 的循环
    里动手。所以先排一个定时器，回调时按标题找到那个对话框：抓内容 → reject。
    找不到就重试（GUI 首次布局可能慢），重试完仍找不到时**兜底关掉所有可见对话框** ——
    否则 exec 永远不返回，整个测试套件会挂死在这里（这种挂死最难查）。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    state = {'n': 0}

    def _tick():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible() and w.windowTitle() == title:
                grab(w)
                w.reject()
                return
        state['n'] += 1
        if state['n'] < tries:
            QTimer.singleShot(delay, _tick)
            return
        for w in QApplication.topLevelWidgets():      # 兜底：别让 exec 挂死
            if isinstance(w, QDialog) and w.isVisible():
                grab(w)
                w.reject()

    QTimer.singleShot(delay, _tick)


def test_about_and_update(app):
    """v1.6.0：侧边栏版本号 → 关于对话框；检查更新 → 侧边栏提示 + 更新对话框。

    两个都是新入口，其中一个还把用户直接带向下载页，所以必须走**真实路径**：
    关于是 Layout 里那个标签的点击行为、检查更新是真的起线程跑一遍（网络整体
    替换成假数据，本用例不联网）。同时钉住「静默检查一律不弹窗」—— 启动时自动
    查一次如果每次都弹，用户会以为软件疯了。
    """
    import time

    from PySide6.QtWidgets import (
        QCheckBox, QDialog, QLabel, QMessageBox, QPushButton, QTextBrowser, QTextEdit,
    )

    from web_image_dl import app as app_mod
    from web_image_dl import updater as U
    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.settings import (
        K_AUTO_CHECK_UPDATE, K_LAST_UPDATE_CHECK, settings,
    )

    print('\n[UI-22] 关于对话框与检查更新')
    with _isolated_config():
        win = ImageDownloaderApp()
        win.show()
        QApplication.processEvents()

        # ---- a) 入口：侧边栏版本号就是「关于」，提示默认不出现 ----
        check(win.version_label.text().startswith('v'),
              f'侧边栏底部显示版本号（{win.version_label.text()}）')
        check('关于' in win.version_label.toolTip(),
              '这个标签的 tooltip 说明点它是「关于」（否则没人知道能点）')
        eq(win.update_hint.isHidden(), True,
           '默认不显示「有新版」提示（还没查过就不许瞎提示）')
        eq(settings.get(K_AUTO_CHECK_UPDATE), True, '新装默认开启启动时自动检查')

        # ---- b) 真的打开关于对话框 ----
        seen = {}

        def grab_about(w):
            seen['opened'] = True
            seen['labels'] = [c.text() for c in w.findChildren(QLabel)]
            seen['html'] = ''.join(c.toHtml() for c in w.findChildren(QTextBrowser))
            seen['buttons'] = [c.text() for c in w.findChildren(QPushButton)]
            checks = w.findChildren(QCheckBox)
            seen['check_texts'] = [c.text() for c in checks]
            seen['auto_checked'] = checks[0].isChecked() if checks else None
            if checks:
                checks[0].setChecked(False)       # 真实链路：toggled → 写设置

        _grab_and_close_dialog('关于 ImgSnag', grab_about)
        win._show_about()                          # 点标签走的就是这个方法
        QApplication.processEvents()

        check(seen.get('opened'), '关于对话框能打开')
        joined_labels = ' '.join(seen.get('labels', []))
        check(win.version_label.text() in joined_labels,
              '对话框里带版本号（报 bug 时用户能直接念出来）')
        html = seen.get('html', '')
        check('virmuran/ImgSnag-WeChat' in html, '给出源码地址')
        check('history.db' in html, '写明下载历史存在哪（用户要找回自己的文件）')
        check('PySide6' in html, '列明第三方依赖')
        check('不收集任何信息' in html, '写清隐私态度')
        check('检查更新' in html, '说明唯一会联网的地方就是检查更新（说实话）')
        check('检查更新' in seen.get('buttons', []), '对话框里有「检查更新」按钮')
        check('关闭' in seen.get('buttons', []), '对话框里有「关闭」按钮')
        check(any('自动检查' in t for t in seen.get('check_texts', [])),
              '有「启动后自动检查新版本」开关')
        eq(seen.get('auto_checked'), True, '开关默认是勾上的')
        eq(settings.get(K_AUTO_CHECK_UPDATE), False,
           '在对话框里取消勾选会真的写进设置（不是个摆设）')
        settings.set(K_AUTO_CHECK_UPDATE, True)

        # ---- c) 6 小时节流与开关（用 spy 替掉真检查，避免起线程） ----
        calls = []
        orig_check = win._check_updates
        win._check_updates = lambda silent=False: calls.append(silent)

        settings.set(K_LAST_UPDATE_CHECK, int(time.time()))
        win._maybe_silent_update_check()
        eq(calls, [], '刚查过 → 不再发请求（GitHub 未认证限流 60 次/小时）')

        settings.set(K_LAST_UPDATE_CHECK, int(time.time()) - U.CHECK_INTERVAL - 10)
        win._maybe_silent_update_check()
        eq(calls, [True], '超过 6 小时 → 发起一次静默检查')

        settings.set(K_AUTO_CHECK_UPDATE, False)
        settings.set(K_LAST_UPDATE_CHECK, 0)
        win._maybe_silent_update_check()
        eq(len(calls), 1, '关掉自动检查后不再请求（但仍可手动点）')

        settings.set(K_AUTO_CHECK_UPDATE, True)
        settings.set(K_LAST_UPDATE_CHECK, 1730000000)   # 2024 年的时间戳，肯定过期
        win._check_updates = orig_check

        # ---- d) 真实线程路径：发现新版本 ----
        fake = U.UpdateInfo(
            ok=True, current='1.5.0', latest='1.6.0', tag='v1.6.0', has_update=True,
            notes='## v1.6.0\n- 新增关于与检查更新',
            page_url='https://example.com/release',
            assets=[
                U.AssetInfo('ImgSnagWeChat_1.6.0_setup.exe', 'installer',
                            30_500_000, 'https://example.com/a.exe', '1.6.0'),
                U.AssetInfo('ImgSnagWeChat_1.6.0_portable.zip', 'portable',
                            31_800_000, 'https://example.com/b.zip', '1.6.0'),
            ],
        )
        orig_fetch = app_mod.check_for_updates
        app_mod.check_for_updates = lambda *a, **k: fake
        try:
            win._check_updates(silent=True)         # 走真实线程 + 信号回主线程
            check(win._update_thread is not None, '检查线程被创建')
            win._update_thread.wait(8000)
            QApplication.processEvents()
        finally:
            app_mod.check_for_updates = orig_fetch

        eq(win.update_hint.isHidden(), False, '发现新版本 → 侧边栏亮出提示')
        eq(win.update_hint.text(), '⬆ v1.6.0', '提示上写着版本号')
        check('1.6.0' in win.update_hint.toolTip(), 'tooltip 说明点它能看更新内容')
        eq(getattr(win._last_update_info, 'latest', None), '1.6.0', '检查结果被留存下来（对话框要用）')
        check(settings.get(K_LAST_UPDATE_CHECK) > 1, '记下本次检查时间（供 6 小时节流）')

        # ---- e) 更新对话框（点提示走的就是这里） ----
        dlg = {}

        def grab_update(w):
            dlg['opened'] = True
            dlg['labels'] = [c.text() for c in w.findChildren(QLabel)]
            dlg['notes'] = [c.toPlainText() for c in w.findChildren(QTextEdit)]
            dlg['buttons'] = [c.text() for c in w.findChildren(QPushButton)]

        _grab_and_close_dialog('发现新版本', grab_update)
        win._show_update_dialog()
        QApplication.processEvents()

        check(dlg.get('opened'), '更新对话框能打开')
        joined = ' '.join(dlg.get('labels', []))
        check('1.6.0' in joined, '对话框里写明新版本号')
        check('1.5.0' in joined, '对话框里写明当前版本（用户要能确认自己在哪一版）')
        check('安装包' in joined and '便携包' in joined,
              '告知这次发布有哪些文件（安装包/便携包由用户自己选）')
        eq(dlg.get('notes'), ['## v1.6.0\n- 新增关于与检查更新'], '更新说明原文显示出来')
        check('前往下载' in dlg.get('buttons', []), '有「前往下载」按钮')
        check('稍后' in dlg.get('buttons', []), '有「稍后」按钮（不强迫用户现在升级）')

        # ---- f) 已是最新 / 失败，以及静默时一律安静 ----
        box_msgs = []
        orig_info = QMessageBox.information
        orig_warn = QMessageBox.warning
        QMessageBox.information = staticmethod(
            lambda parent, title, text, *a, **k: box_msgs.append(('info', text)) or QMessageBox.Ok)
        QMessageBox.warning = staticmethod(
            lambda parent, title, text, *a, **k: box_msgs.append(('warn', text)) or QMessageBox.Ok)
        try:
            current = U.UpdateInfo(ok=True, current='1.5.0', latest='1.5.0', tag='v1.5.0')
            win._on_update_checked(current, silent=False)
            eq(len(box_msgs), 1, '手动检查且已是最新 → 给一次回应（否则用户以为按钮坏了）')
            check('最新' in box_msgs[0][1], '提示里说清已是最新')
            check('v1.5.0' in box_msgs[0][1], '顺带回显 GitHub 上的最新 tag（便于发现发版失误）')
            eq(win.update_hint.isHidden(), True, '已是最新 → 侧边栏提示被清掉')
            eq(win.update_hint.text(), '', '提示文案一起清干净（不留半截）')

            box_msgs.clear()
            win._on_update_checked(current, silent=True)
            eq(box_msgs, [], '静默检查且无更新 → 一声不吭')

            box_msgs.clear()
            broken = U.UpdateInfo(ok=False, current='1.5.0',
                                  error='无法连接 GitHub，请检查网络（公司网络可能限制访问）')
            win._on_update_checked(broken, silent=False)
            eq(len(box_msgs), 1, '手动检查失败 → 给出提示（不能静默失败）')
            eq(box_msgs[0][0], 'warn', '失败用 warning，不冒充成功')
            check('网络' in box_msgs[0][1], '把失败原因照实转达（含可操作建议）')

            box_msgs.clear()
            win._on_update_checked(broken, silent=True)
            eq(box_msgs, [], '静默检查失败 → 安静（连不上 GitHub 是常态，不能每次启动都弹）')

            box_msgs.clear()
            mismatched = U.UpdateInfo(ok=True, current='1.5.0', latest='1.6.0', tag='v1.6.0',
                                      has_update=False, version_mismatch=True)
            win._on_update_checked(mismatched, silent=False)
            eq(len(box_msgs), 1, '没有更新但发现发版配置不一致 → 手动检查时提示')
            check('tag' in box_msgs[0][1], '把「tag 与文件名对不上」这件事说清楚')
        finally:
            QMessageBox.information = orig_info
            QMessageBox.warning = orig_warn

        win.close()
        QApplication.processEvents()


def test_auto_library_folder(app):
    """v1.7.0：下载自动落进「图库／专属子文件夹」，不再每次选目录（UI-23）。

    旧版每次点下载都要选一次目录，而且图是平铺命名（img_01、img_02…）直接扔进所选
    目录：一直存「下载」的话，第二篇文章的 img_01 就变成 img_01_1，越用越乱，也看
    不出哪张属于哪一篇。这里钉住新行为的几个「容易做错」的点：

    a) 默认路径**不再弹选择框**（还弹就等于没改）
    b) 文件夹名 = 日期_时分秒_标题；标题取不到时用兜底名
    c) 同名撞车要另起一个（_2），绝不覆盖上一次下的图
    d) 「另存到…」这次放到别处，但同样建专属子文件夹
    e) 「打开图库」优先打开最近一次真正落盘的目录
    f) 图库目录不可用时必须**回退**到手动选择，而不是直接报错让用户下不成
    """
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.settings import K_LIBRARY_DIR, settings
    from web_image_dl.worker import ImageInfo

    print('\n[UI-23] 下载自动建专属文件夹（不再每次选目录）')

    def add_images(win, n=2):
        for i in range(n):
            win._on_image_loaded(i, ImageInfo(
                url=f'https://mmbiz.qpic.cn/mmbiz_png/ID{i}/0', index=i,
                data=png_bytes(50 + i, 30), ext='.png', width=50 + i, height=30))

    def run(win):
        win._on_download()
        w = win.save_worker
        if w is not None:
            w.wait(15000)
            QApplication.processEvents()
        return w

    with _isolated_config():
        lib = tempfile.mkdtemp(prefix='imgsnag_lib_')
        elsewhere = tempfile.mkdtemp(prefix='imgsnag_as_')
        settings.set(K_LIBRARY_DIR, lib)
        orig_dialog = QFileDialog.getExistingDirectory
        orig_info, orig_warn = QMessageBox.information, QMessageBox.warning
        orig_startfile = getattr(os, 'startfile', None)
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        try:
            # ---- (a)(b) 不弹框 + 名字里带标题 ----
            win = ImageDownloaderApp()
            win.show()
            QApplication.processEvents()
            eq(win._library_root(), lib, '图库根目录取设置里的值')
            win._article_title = '秋天的第一杯奶茶'
            add_images(win, 2)

            calls = []
            QFileDialog.getExistingDirectory = staticmethod(
                lambda *a, **k: calls.append(1) or '')
            run(win)
            eq(calls, [], '默认下载完全不弹文件夹选择框')
            subs = os.listdir(lib)
            eq(len(subs), 1, '自动建了一个专属子文件夹')
            name = subs[0] if subs else ''
            check(bool(re.match(r'^\d{4}-\d{2}-\d{2}_\d{6}_', name)),
                  f'名字以「日期_时分秒_」开头（实测 {name}）')
            check(name.endswith('秋天的第一杯奶茶'), '文章标题进了文件夹名')
            eq(sorted(os.listdir(os.path.join(lib, name))), ['img_01.png', 'img_02.png'],
               '两张图落进这个子文件夹')
            eq(win._last_save_folder, os.path.join(lib, name), '记住了最近落盘的目录')
            win.close()
            QApplication.processEvents()

            # ---- (b2) 抓不到标题 → 兜底名 ----
            win2 = ImageDownloaderApp()
            win2.show()
            QApplication.processEvents()
            win2._article_title = ''
            add_images(win2, 1)
            run(win2)
            fallback_dirs = [n for n in os.listdir(lib) if '微信图片' in n]
            eq(len(fallback_dirs), 1, '取不到标题时文件夹名用兜底「微信图片」')
            eq(sorted(os.listdir(os.path.join(lib, fallback_dirs[0]))), ['img_01.png'],
               '兜底命名的那批也正常落盘')
            win2.close()
            QApplication.processEvents()

            # ---- (c) 同名撞车不覆盖 ----
            with mock.patch('web_image_dl.app.folder_name_for',
                            lambda parent, title, **k: '固定名'):
                os.makedirs(os.path.join(lib, '固定名'))
                win3 = ImageDownloaderApp()
                win3.show()
                QApplication.processEvents()
                win3._article_title = '标题'
                add_images(win3, 1)
                run(win3)
                check(os.path.isdir(os.path.join(lib, '固定名_2')),
                      '同名时另起 _2，不覆盖上一次')
                eq(os.listdir(os.path.join(lib, '固定名')), [],
                   '原来那个同名目录内容没被碰过')
                eq(sorted(os.listdir(os.path.join(lib, '固定名_2'))), ['img_01.png'],
                   '新的一批落进 _2')
                win3.close()
                QApplication.processEvents()

            # ---- (d) 另存到… ----
            QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: elsewhere)
            win4 = ImageDownloaderApp()
            win4.show()
            QApplication.processEvents()
            win4._article_title = '另存的标题'
            add_images(win4, 1)
            win4._on_download_as()
            w4 = win4.save_worker
            if w4 is not None:
                w4.wait(15000)
                QApplication.processEvents()
            subs4 = os.listdir(elsewhere)
            eq(len(subs4), 1, '「另存到…」也在其中建了一个子文件夹')
            check(bool(subs4) and subs4[0].endswith('另存的标题'),
                  '「另存到…」同样按 日期_时分秒_标题 命名')
            eq(sorted(os.listdir(os.path.join(elsewhere, subs4[0]))), ['img_01.png'],
               '图落进另存位置的那个子文件夹')

            # ---- (e) 打开图库 ----
            opened = []
            os.startfile = staticmethod(lambda p: opened.append(p))
            win4._on_open_library()
            eq(opened, [os.path.join(elsewhere, subs4[0])],
               '「打开图库」优先打开最近落盘的那个子文件夹')
            win4.close()
            QApplication.processEvents()

            # ---- (f) 图库不可用 → 回退手动选择 ----
            blocker = os.path.join(lib, 'blocker_file')
            open(blocker, 'w').close()
            settings.set(K_LIBRARY_DIR, os.path.join(blocker, 'sub'))   # 路径中间夹着文件
            manual = tempfile.mkdtemp(prefix='imgsnag_manual_')
            QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: manual)
            win5 = ImageDownloaderApp()
            win5.show()
            QApplication.processEvents()
            add_images(win5, 1)
            run(win5)
            eq(sorted(os.listdir(manual)), ['img_01.png'],
               '图库不可用时回退到手动选择的目录（不让这次下载白费）')
            win5.close()
            QApplication.processEvents()
        finally:
            QFileDialog.getExistingDirectory = orig_dialog
            QMessageBox.information, QMessageBox.warning = orig_info, orig_warn
            if orig_startfile is not None:
                os.startfile = orig_startfile


def test_history_missing_marker(app):
    """v1.7.0：历史里本地文件夹没了要能一眼看出来（UI-24）。

    「只要没删本地图片就能打开」的前提是「删了也得看得出来」—— 否则用户点「打开」
    毫无反应，只会以为是程序坏了。所以不存在的目录要标注 + 把按钮置灰 + 说明原因。
    """
    import web_image_dl.app as app_mod
    from PySide6.QtWidgets import QPushButton

    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.history_manager import HistoryEntry

    print('\n[UI-24] 历史页标记「本地已删除」并置灰「打开」')

    alive = tempfile.mkdtemp(prefix='imgsnag_alive_')
    gone = os.path.join(tempfile.gettempdir(), '__imgsnag_never_existed__')
    with _isolated_config():
        win = ImageDownloaderApp()
        win.show()
        QApplication.processEvents()
        rows = [
            HistoryEntry(id=1, source_url='https://mp.weixin.qq.com/s/AAA',
                         parsed_at='2026-09-18T16:00:00', total_images=8,
                         success_images=8, save_path=alive, status='done'),
            HistoryEntry(id=2, source_url='https://mp.weixin.qq.com/s/BBB',
                         parsed_at='2026-09-18T15:00:00', total_images=5,
                         success_images=5, save_path=gone, status='done'),
            HistoryEntry(id=3, source_url='https://mp.weixin.qq.com/s/CCC',
                         parsed_at='2026-09-18T14:00:00', total_images=0,
                         success_images=0, save_path='', status='scanned'),
        ]
        real_get_all = app_mod.history_manager.get_all
        app_mod.history_manager.get_all = lambda limit=200: list(rows)
        try:
            win._refresh_history()
            QApplication.processEvents()
        finally:
            app_mod.history_manager.get_all = real_get_all

        t = win.history_table
        eq(t.rowCount(), 3, '三条记录都渲染了')
        eq(t.item(0, 3).text(), os.path.basename(alive),
           '路径列只显示那一次的子文件夹名（不占满整列）')
        check('本地已删除' in t.item(1, 3).text(),
              f'不存在的目录被标出来（实测 {t.item(1, 3).text()!r}）')
        check('本地文件夹已不存在' in t.item(1, 3).toolTip(),
              'tooltip 给出完整路径，便于用户自己去找')
        eq(t.item(2, 3).text(), '-', '没有记录路径的显示 -')

        def _open_button(row):
            w = t.cellWidget(row, 5)
            if w is None:
                return None
            for b in w.findChildren(QPushButton):
                if b.text() == '打开':
                    return b
            return None

        check(_open_button(0) is not None and _open_button(0).isEnabled(),
              '目录还在 → 「打开」可用')
        check(_open_button(1) is not None and not _open_button(1).isEnabled(),
              '目录已删 → 「打开」置灰（点不动的按钮别装成能点）')
        check('本地文件夹已不存在' in (_open_button(1).toolTip() or ''),
              '置灰的按钮用 tooltip 说明为什么点不了')

        try:
            win._on_open_folder(rows[1])
            check(True, '对已删除目录点「打开」不抛异常')
        except Exception as e:
            check(False, f'点「打开」抛异常: {type(e).__name__}: {e}')
        check('本地文件夹已不存在' in win.status.currentMessage(),
              f'状态栏说明原因（实测 {win.status.currentMessage()!r}）')

        win.close()
        QApplication.processEvents()


def test_reparse_and_action_row(app):
    """v1.7.1 两处交互调整（UI-25）：

    a) 「打开图库 / 另存到… / 下载」必须从左到右排成一排在动作栏里 ——
       曾经放在状态栏里，用户根本注意不到（按钮挪了位置，钉住别再退回去）。
    b) 历史页的「解析」按钮点一下就**直接开解析**，不再只是把链接填回去、
       让用户再按一次「解析图片」；但已有解析在跑时**不许**触发（_on_parse
       的同按钮语义是「取消」，直接调会把正在跑的解析打断）。
    """
    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.history_manager import HistoryEntry

    print('\n[UI-25] 动作栏按钮顺序 + 历史页「解析」直接开解析')
    with _isolated_config():
        win = ImageDownloaderApp()
        win.show()
        QApplication.processEvents()

        # ---- (a) 按钮顺序：左 → 右 = 打开图库 / 另存到… / 下载 ----
        x_lib = win.open_lib_btn.mapTo(win, win.open_lib_btn.rect().topLeft()).x()
        x_save = win.save_as_btn.mapTo(win, win.save_as_btn.rect().topLeft()).x()
        x_dl = win.download_btn.mapTo(win, win.download_btn.rect().topLeft()).x()
        check(x_lib < x_save < x_dl,
              f'从左到右是 打开图库({x_lib}) / 另存到…({x_save}) / 下载({x_dl})')
        # 都得在同一行（y 相近），别一个在动作栏一个在状态栏
        y_lib = win.open_lib_btn.mapTo(win, win.open_lib_btn.rect().topLeft()).y()
        y_save = win.save_as_btn.mapTo(win, win.save_as_btn.rect().topLeft()).y()
        y_dl = win.download_btn.mapTo(win, win.download_btn.rect().topLeft()).y()
        check(max(y_lib, y_save, y_dl) - min(y_lib, y_save, y_dl) < 20,
              f'三个按钮在同一行（y: {y_lib}/{y_save}/{y_dl}）')

        # ---- (b1) 点「解析」→ 直接开解析 ----
        entry = HistoryEntry(id=9, source_url='https://mp.weixin.qq.com/s/XYZ',
                             parsed_at='2026-09-19T08:00:00', total_images=3,
                             success_images=3, save_path='', status='done')
        started = []
        orig_start = win._start_worker
        win._start_worker = lambda text, is_url=True: started.append((text, is_url))
        try:
            win._on_reparse(entry)
            QApplication.processEvents()
        finally:
            win._start_worker = orig_start
        eq(win.stack.currentIndex(), 0, '点「解析」后切回解析页')
        eq(started, [('https://mp.weixin.qq.com/s/XYZ', True)],
           '「解析」直接开解析，不用再按一次「解析图片」')
        eq(win.url_input.toPlainText(), 'https://mp.weixin.qq.com/s/XYZ',
           '链接同时填进了输入框（解析完成后还在，方便看/改）')

        # ---- (b2) 已有解析在跑时，「解析」只填链接不触发 ----
        class _Running:
            def isRunning(self):
                return True

        started.clear()
        win.worker = _Running()
        try:
            win._on_reparse(entry)
            QApplication.processEvents()
        finally:
            win.worker = None
        eq(started, [], '已有解析在跑时不触发新的解析（否则等于按了「取消」）')
        check('已有解析' in win.status.currentMessage(),
              f'状态栏说明为什么不触发（实测 {win.status.currentMessage()!r}）')

        # ---- (b3) 保存进行中点「另存到…」不许开第二个落盘线程 ----
        # 两个 SaveWorker 会互相覆盖 self.save_worker，进度回调全乱
        started.clear()
        dialog_opened = []
        from PySide6.QtWidgets import QFileDialog as _FD
        orig_get = _FD.getExistingDirectory
        _FD.getExistingDirectory = staticmethod(
            lambda *a, **k: dialog_opened.append(1) or '')
        win.worker = None
        class _Saving:
            def isRunning(self):
                return True
        win.save_worker = _Saving()
        try:
            win._on_download_as()
            QApplication.processEvents()
        finally:
            _FD.getExistingDirectory = orig_get
            win.save_worker = None
        eq(dialog_opened, [], '保存进行中「另存到…」不弹目录框')
        check('保存中' in win.status.currentMessage(),
              f'状态栏说明要等这一批完成（实测 {win.status.currentMessage()!r}）')

        # ---- (b4) 没有链接的记录点「解析」不动 ----
        started.clear()
        empty = HistoryEntry(id=10, source_url='', parsed_at='2026-09-19T08:00:00',
                             total_images=0, success_images=0, save_path='', status='failed')
        win._on_reparse(empty)
        eq(started, [], '没有链接的记录点「解析」不触发')
        win.close()
        QApplication.processEvents()


def test_browse_history_batch(app):
    """v1.8.0：历史里已下载的图能在软件里重新打开（UI-26）。

    「历史里只要没删本地图片就能打开」以前只能跳到资源管理器 —— 想翻看还得自己
    找图。现在点「查看」（或双击行）直接在软件里看。这里钉四件容易做错的事：
      a) 是真的从磁盘读回来显示（缩略图 + 大图预览），不是空壳；
      b) **只读**：这些图已在本地，再点下载只会在图库里堆同名副本 → 必须禁用；
      c) 顺序与文件名按磁盘真实情况来（img_10 在 img_02 后面、坏文件跳过不崩）；
      d) 本地文件夹没了/里面没图时，按钮置灰 + 说清原因，不许点了没反应。
    """
    import web_image_dl.app as app_mod
    from PySide6.QtWidgets import QMessageBox, QPushButton

    from web_image_dl.app import ImageDownloaderApp
    from web_image_dl.history_manager import HistoryEntry

    print('\n[UI-26] 历史批次在软件里重新打开（只读浏览）')

    tmp = tempfile.mkdtemp(prefix='imgsnag_browse_')
    batch = os.path.join(tmp, '2026-09-21_100000_历史文章')
    os.makedirs(batch)
    for name, w in (('img_01.png', 40), ('img_02.png', 60), ('img_10.png', 90)):
        with open(os.path.join(batch, name), 'wb') as f:
            f.write(png_bytes(w, 30))
    with open(os.path.join(batch, 'note.txt'), 'w', encoding='utf-8') as f:
        f.write('不是图片')
    # 扩展名像图片、内容却不是：必须跳过这张，而不是报错或显示空白
    with open(os.path.join(batch, 'img_03.png'), 'wb') as f:
        f.write(b'definitely not a png')

    entry = HistoryEntry(id=77, source_url='https://mp.weixin.qq.com/s/HIST',
                         parsed_at='2026-09-21T10:00:00', total_images=3,
                         success_images=3, save_path=batch, status='done')
    gone = HistoryEntry(id=78, source_url='https://mp.weixin.qq.com/s/GONE',
                        parsed_at='2026-09-21T09:00:00', total_images=2,
                        success_images=2,
                        save_path=os.path.join(tmp, '__never_existed__'),
                        status='done')
    empty_dir = tempfile.mkdtemp(prefix='imgsnag_empty_')
    empty = HistoryEntry(id=79, source_url='https://mp.weixin.qq.com/s/EMPTY',
                         parsed_at='2026-09-21T08:00:00', total_images=0,
                         success_images=0, save_path=empty_dir, status='scanned')

    with _isolated_config():
        real_get_all = app_mod.history_manager.get_all
        app_mod.history_manager.get_all = lambda limit=200: [entry, gone, empty]
        orig_info, orig_warn = QMessageBox.information, QMessageBox.warning
        dialogs = []        # 记录弹出的对话框标题（空目录要给提示，不许静默）
        QMessageBox.information = staticmethod(
            lambda parent=None, title='', text='', *a, **k: (dialogs.append(title), QMessageBox.Ok)[1])
        QMessageBox.warning = staticmethod(
            lambda parent=None, title='', text='', *a, **k: (dialogs.append(title), QMessageBox.Ok)[1])
        try:
            win = ImageDownloaderApp()
            win.show()
            QApplication.processEvents()
            win._refresh_history()
            QApplication.processEvents()

            def _btn(row, text):
                w = win.history_table.cellWidget(row, 5)
                if w is None:
                    return None
                for b in w.findChildren(QPushButton):
                    if b.text() == text:
                        return b
                return None

            # ---- (a) 按钮就位：可用/置灰 ----
            view = _btn(0, '查看')
            check(view is not None and view.isEnabled(),
                  '记录 1 有可用的「查看」按钮')
            check(_btn(1, '查看') is not None and not _btn(1, '查看').isEnabled(),
                  '本地文件夹已删 → 「查看」置灰（跟「打开」一致）')
            check(_btn(0, '查看') is not None
                  and '软件里直接翻看' in (_btn(0, '查看').toolTip() or ''),
                  '「查看」的 tooltip 说清是在软件里看')

            # ---- (b) 走真实点击：真的读回磁盘上的图 ----
            view.click()
            QApplication.processEvents()
            eq(win.stack.currentIndex(), 0, '点「查看」切到解析页')
            eq(len(win.thumb_items), 3,
               '三张能读的图进缩略图区（.txt 与坏掉的 img_03 不算）')
            eq([it.checkbox.text() for it in win.thumb_items],
               ['img_01.png', 'img_02.png', 'img_10.png'],
               '显示磁盘上的真实文件名，且 10 排在 02 后面（自然序）')
            eq([it.info.width for it in win.thumb_items], [40, 60, 90],
               '宽高从文件本身读出来（不带 data 的假对象做不到）')
            check(win.preview._source is not None and not win.preview._source.isNull(),
                  '大图预览真的有内容')
            eq(win._article_title, '历史文章',
               '标题从文件夹名剥掉日期得到（供「另存到…」命名）')
            check('历史图库' in win.info_label.text(),
                  f'信息栏说明这是历史图库（实测 {win.info_label.text()!r}）')
            msg = win.status.currentMessage()
            check('不会重复下载' in msg, f'状态栏说明不会重复下载（实测 {msg!r}）')
            check('1 个文件读不出' in msg, f'坏文件被计数并说明（实测 {msg!r}）')

            # ---- (c) 只读：下载被禁用，且真的拦得住 ----
            check(not win.download_btn.isEnabled(),
                  '浏览历史时「下载」被禁用（这些图已在本地）')
            check('已在本地' in win.download_btn.text(),
                  f'按钮文案说明原因（实测 {win.download_btn.text()!r}）')
            check('另存到' in (win.download_btn.toolTip() or ''),
                  '禁用按钮的 tooltip 指向「另存到…」这条出路')
            check(win.save_as_btn.isEnabled(), '「另存到…」仍然可用（要复制到别处是合理需求）')
            before = win.save_worker
            win._on_download()          # 绕过按钮直接调，守卫也得拦住
            check(win.save_worker is before, '直接调 _on_download 也不会开落盘线程')
            check('存在本地' in win.status.currentMessage(),
                  f'状态栏说明为什么不下载（实测 {win.status.currentMessage()!r}）')

            # ---- (d) 单次浏览的字节上限：超限的图不载入但要说清 ----
            huge = os.path.join(tmp, '2026-09-21_110000_大图批次')
            os.makedirs(huge)
            for i in range(3):
                with open(os.path.join(huge, f'img_{i + 1:02d}.png'), 'wb') as f:
                    f.write(png_bytes(30 + i, 30))
            big_entry = HistoryEntry(id=80, source_url='https://mp.weixin.qq.com/s/BIG',
                                     parsed_at='2026-09-21T11:00:00', total_images=3,
                                     success_images=3, save_path=huge, status='done')
            with mock.patch.object(app_mod, 'MAX_BATCH_BYTES', 1):
                win._on_browse_batch(big_entry)
                QApplication.processEvents()
            eq(len(win.thumb_items), 0, '超过上限时一张都不载入（不让内存失控）')
            check('超出单次浏览上限' in win.status.currentMessage(),
                  f'状态栏说明有多少张没载入（实测 {win.status.currentMessage()!r}）')

            # ---- (e) 文件夹已删 / 空目录：说清原因，不崩 ----
            win._on_browse_batch(gone)
            check('本地文件夹已不存在' in win.status.currentMessage(),
                  f'文件夹没了要说清（实测 {win.status.currentMessage()!r}）')
            win._on_browse_batch(empty)
            eq(len(win.thumb_items), 0, '空目录不显示任何图')
            check(any('没有图片' in t for t in dialogs),
                  f'空目录要弹提示说清（实测弹过 {dialogs!r}）')

            # ---- (f) 开始新解析 → 退出只读模式，下载按钮恢复 ----
            check(win._browsing_history, '浏览期间处于只读模式')
            win._reset_before_parse()
            QApplication.processEvents()
            check(not win._browsing_history, '开始新解析后退出只读模式')

            # ---- (g) 关窗不残留线程 ----
            win.close()
            QApplication.processEvents()
        finally:
            app_mod.history_manager.get_all = real_get_all
            QMessageBox.information, QMessageBox.warning = orig_info, orig_warn


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
    test_about_and_update(app)
    test_auto_library_folder(app)
    test_history_missing_marker(app)
    test_reparse_and_action_row(app)
    test_browse_history_batch(app)
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
