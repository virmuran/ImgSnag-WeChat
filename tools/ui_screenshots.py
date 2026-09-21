# -*- coding: utf-8 -*-
"""把界面各状态离屏渲染成 PNG，用于 UI 评审（不参与打包）。

用法：
    .venv/Scripts/python.exe tools/ui_screenshots.py [输出目录]

为什么需要它：改 UI 时靠嘴描述「按钮偏左、间距太挤」效率极低，
直接看图说话最快。离屏渲染（QT_QPA_PLATFORM=offscreen）不弹窗、
不抢焦点，也不会碰用户的真实数据 —— 历史页的行是伪造的，
只 monkeypatch 了 history_manager.get_all，绝不写用户的 history.db。
"""
import os
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# 离屏插件找不到 Qt 自带字体目录会导致中文全渲染成方块（tofu），
# 显式把字体目录指到 Windows 系统字体即可正常出字
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtCore import QBuffer, QByteArray, QPointF, Qt
from PySide6.QtGui import (
    QColor, QFont, QImage, QLinearGradient, QPainter, QPen,
)
from PySide6.QtWidgets import QApplication


def shot(widget, path, note=''):
    """截图并落盘（grab 会强制重绘，离屏下同样有效）"""
    QApplication.processEvents()
    pix = widget.grab()
    ok = pix.save(path)
    print(f'  {"OK " if ok else "FAIL"} {os.path.basename(path)}  {pix.width()}x{pix.height()}  {note}')


def fake_photo(w, h, hue, label):
    """造一张有内容的假图，让缩略图和预览看起来像真照片（纯色图会看不出缩放效果）"""
    img = QImage(w, h, QImage.Format_RGB32)
    grad = QLinearGradient(QPointF(0, 0), QPointF(w, h))
    grad.setColorAt(0.0, QColor.fromHsv(hue, 90, 235))
    grad.setColorAt(1.0, QColor.fromHsv((hue + 40) % 360, 150, 120))
    p = QPainter(img)
    p.fillRect(img.rect(), grad)
    pen = QPen(QColor(255, 255, 255, 70))
    pen.setWidth(max(2, w // 300))
    p.setPen(pen)
    step = max(40, w // 12)
    for x in range(0, w, step):                      # 斜纹，方便看出重采样质量
        p.drawLine(x, 0, x - h, h)
    p.setPen(QColor('#ffffff'))
    f = QFont()
    f.setPointSize(max(14, w // 10))
    f.setBold(True)
    p.setFont(f)
    p.drawText(img.rect(), Qt.AlignCenter, label)
    p.end()

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, 'PNG')
    buf.close()
    return bytes(ba)


#: 仿一篇文章：编号 / 分辨率 / 色相（宽图、竖图、方图混合，检验布局）
ARTICLE = [
    (1, 1080, 1440, 210), (2, 2560, 3413, 160), (3, 1080, 608, 30),
    (4, 2000, 2667, 280), (5, 1080, 1080, 330), (6, 1080, 2400, 90),
    (7, 1080, 810, 195), (8, 1799, 2400, 250),
]


def build_article_infos():
    from web_image_dl.worker import ImageInfo
    infos = []
    for idx, w, h, hue in ARTICLE:
        infos.append(ImageInfo(
            url=f'https://mmbiz.qpic.cn/mmbiz_png/FAKE{idx}/0',
            index=idx - 1,
            data=fake_photo(w, h, hue, f'IMG {idx}'),
            width=w, height=h, ext='.png',
        ))
    return infos


def fake_history_rows():
    """伪造历史行（只在内存里，绝不碰用户的 history.db）

    路径按 v1.7.0 的新形态写：各条都指向「图库／日期_时分秒_标题」这样的批次子文件夹。
    其中一条指向**真实存在的临时目录**（「打开」可用），一条指向一个确定不存在的目录
    （应被标成「本地已删除」并把按钮置灰）—— 一次截图就同时看到两种状态。
    """
    from datetime import datetime, timedelta
    from web_image_dl.history_manager import HistoryEntry
    now = datetime.now()
    alive = os.path.join(tempfile.mkdtemp(prefix='imgsnag_shot_lib_'),
                         '2026-09-18_164633_秋天的第一杯奶茶')
    os.makedirs(alive, exist_ok=True)
    gone = os.path.join(tempfile.gettempdir(), '__imgsnag_shot_gone__',
                        '2026-09-17_213015_城市黄昏随拍')
    rows = [
        ("https://mp.weixin.qq.com/s/EoKPKXS7ec89I5cdBrRgQQ", 8, 8, "done", 0, alive),
        ("https://mp.weixin.qq.com/s/dpH_jGk1MogIzRBkgx1Qtw", 5, 6, "done", 1, gone),
        ("https://mp.weixin.qq.com/s/ThisIsAVeryLongArticleUrlForTestingTruncation123456",
         42, 42, "scanned", 2, ''),
        ("https://mp.weixin.qq.com/s/FailedFetchExampleAbcDef", 0, 3, "failed", 3, ''),
    ]
    out = []
    for url, ok, total, status, back, path in rows:
        out.append(HistoryEntry(
            id=100 + back, source_url=url, total_images=total, success_images=ok,
            save_path=path, status=status,
            parsed_at=(now - timedelta(hours=back * 26)).isoformat(timespec='seconds'),
        ))
    return out


def snap_dialog(title, path, note='', tries=25, delay=60):
    """在对话框自己的事件循环里把它截下来再关掉。

    QDialog.exec() 是阻塞的，截图必须排在它的事件循环里做。找不到就重试，
    重试完仍找不到时把可见对话框一律关掉 —— 否则 exec 不返回，脚本会卡死。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    def _tick(n=0):
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible() and w.windowTitle() == title:
                # 刚 show 出来的那一瞬尺寸还没定（QMessageBox 尤其明显，会抓成一条窄带），
                # 先按内容算好尺寸再截
                w.adjustSize()
                QApplication.processEvents()
                shot(w, path, note)
                w.reject()
                return
        if n < tries:
            QTimer.singleShot(delay, lambda: _tick(n + 1))
            return
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible():
                w.reject()

    QTimer.singleShot(delay, _tick)


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_ROOT, '_ui_shots')
    os.makedirs(outdir, exist_ok=True)

    app = QApplication.instance() or QApplication([])

    from web_image_dl.app import ImageDownloaderApp
    import web_image_dl.app as app_mod

    win = ImageDownloaderApp()
    win.show()
    QApplication.processEvents()

    print(f'输出目录: {outdir}')

    # ---- 1. 空状态（刚打开的默认窗口） ----
    shot(win, os.path.join(outdir, '01_空状态_1100x750.png'), '默认启动')

    # ---- 2. 解析中 ----
    win._reset_before_parse()
    win._on_progress(3, 8)
    shot(win, os.path.join(outdir, '02_解析中_可取消.png'), '进度条 + 取消解析')

    # ---- 3. 解析完成（注入 8 张假图，含 1 张重复 1 张被判小图） ----
    infos = build_article_infos()
    infos[3].is_duplicate = True
    # 模拟被「过滤小图」拦掉：一条是启发式（小图），一条是用户自己屏蔽过的尺寸
    infos[5].is_mini_square = True
    infos[6].is_mini_square = True
    infos[6].block_reasons = ('blocked:cover',)
    win._reset_before_parse()
    for i, info in enumerate(infos):
        win._on_image_loaded(i, info)
    win._on_all_done(infos)
    if win.thumb_items:
        win._on_thumb_clicked(infos[1])       # 预览第 2 张（大竖图）
    shot(win, os.path.join(outdir, '03_解析完成_1100x750.png'), '8 张，含重复/已屏蔽/小图')

    # ---- 4. 同样的内容放大到最大化窗口 ----
    win.resize(1500, 950)
    shot(win, os.path.join(outdir, '04_解析完成_1500x950.png'), '模拟最大化')

    # ---- 5. 历史页（伪造历史行 + 伪造屏蔽表） ----
    from web_image_dl.blocked_config import blocked_config

    real_get_all = app_mod.history_manager.get_all
    saved_cfg_path = blocked_config.config_path
    shot_cfg = os.path.join(tempfile.mkdtemp(prefix='imgsnag_shot_'), 'blocked_sizes.json')
    app_mod.history_manager.get_all = lambda limit=200: fake_history_rows()
    blocked_config.config_path = shot_cfg          # 别把假规则写进用户真实的屏蔽表
    blocked_config.reload()
    try:
        blocked_config.add_blocked('ui', (321, 192))
        blocked_config.add_blocked('avatar', (272, 272))
        blocked_config.add_blocked('cover', (1080, 460))
        blocked_config.add_hits({('ui', 321, 192): 14, ('avatar', 272, 272): 6,
                                 ('cover', 1080, 460): 2})
        win._switch_page(1)
        shot(win, os.path.join(outdir, '05_历史页.png'),
             '伪造 4 行 + 已屏蔽尺寸面板（未动真实历史库与真实屏蔽表）')
    finally:
        app_mod.history_manager.get_all = real_get_all
        blocked_config.config_path = saved_cfg_path
        blocked_config.reload()

    # ---- 6. 窄窗口，看会不会挤坏 ----
    win._switch_page(0)
    win.resize(880, 620)
    shot(win, os.path.join(outdir, '06_窄窗口_880x620.png'), '小屏/未最大化')

    # ---- 7. 预览 100%（v1.2.0 新增：看清像素、确认是不是真原图） ----
    win.resize(1500, 950)
    QApplication.processEvents()
    win.preview.zoom_100()
    shot(win, os.path.join(outdir, '07_预览缩放到100%.png'), '右下角显示比例')

    # ---- 8. 关于对话框（v1.6.0 新增：侧边栏底部的版本号可点） ----
    snap_dialog('关于 ImgSnag', os.path.join(outdir, '08_关于.png'), '版本号入口')
    win._show_about()

    # ---- 9. 发现新版本：侧边栏提示 + 更新对话框（伪造检查结果，不联网） ----
    from web_image_dl import updater as U

    cur = win.version_label.text().lstrip('v') or '1.7.0'
    # 演示用的是「比当前高一个次版本」，这样发版后截图不会显得自相矛盾
    parts = (cur.split('.') + ['0', '0'])[:3]
    demo_latest = f'{parts[0]}.{int(parts[1]) + 1}.0'
    win._last_update_info = U.UpdateInfo(
        ok=True, current=cur, latest=demo_latest, tag=f'v{demo_latest}', has_update=True,
        notes=(f'## v{demo_latest}（示例）\n'
               '- 这一屏是「发现新版本」的样子，内容由「检查更新」从 GitHub 拉取\n'
               '- 静默检查发现新版只在侧边栏亮提示，不弹窗打扰\n'
               '- 想看更新内容时点侧边栏提示或「关于」里的检查更新\n'),
        page_url='https://github.com/virmuran/ImgSnag-WeChat/releases/latest',
        assets=[
            U.AssetInfo(f'ImgSnagWeChat_{demo_latest}_setup.exe', 'installer', 30_500_000,
                        'https://example.com/a.exe', demo_latest),
            U.AssetInfo(f'ImgSnagWeChat_{demo_latest}_portable.zip', 'portable', 31_800_000,
                        'https://example.com/b.zip', demo_latest),
        ],
    )
    win._show_update_hint(demo_latest)
    shot(win, os.path.join(outdir, '09_侧边栏提示有新版.png'), f'侧边栏 ⬆ v{demo_latest}')
    snap_dialog('发现新版本', os.path.join(outdir, '10_发现新版本.png'), '更新对话框')
    win._show_update_dialog()
    win._clear_update_hint()

    # ---- 11. 下载完成：告诉用户图存进了哪个专属文件夹（v1.7.0） ----
    demo_folder = os.path.join(
        'C:\\Users\\Administrator\\Pictures\\ImgSnagWeChat',
        '2026-09-18_164633_秋天的第一杯奶茶')
    snap_dialog('下载完成', os.path.join(outdir, '11_下载完成_存进专属文件夹.png'),
                '自动建的批次文件夹，不再让用户自己挑目录')
    win._on_save_done(8, 0, [], demo_folder)

    # ---- 12. 历史批次在软件里打开（v1.8.0：只读浏览） ----
    from web_image_dl.history_manager import HistoryEntry

    batch_dir = os.path.join(tempfile.mkdtemp(prefix='imgsnag_shot_browse_'),
                             '2026-09-18_164633_秋天的第一杯奶茶')
    os.makedirs(batch_dir, exist_ok=True)
    for idx, w, h, hue in ARTICLE[:5]:
        with open(os.path.join(batch_dir, f'img_{idx:02d}.png'), 'wb') as f:
            f.write(fake_photo(w, h, hue, f'IMG {idx}'))
    browse_entry = HistoryEntry(
        id=9901, source_url='https://mp.weixin.qq.com/s/EoKPKXS7ec89I5cdBrRgQQ',
        total_images=5, success_images=5, save_path=batch_dir, status='done',
        parsed_at='2026-09-18T16:46:33',
    )
    win.resize(1500, 950)
    win._on_browse_batch(browse_entry)
    QApplication.processEvents()
    shot(win, os.path.join(outdir, '12_历史批次在软件里打开.png'),
         '只读浏览：图从磁盘读回来，下载按钮禁用并说明原因')

    win.close()
    print('完成')


if __name__ == '__main__':
    main()
