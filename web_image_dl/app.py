"""
ImageDownloaderApp — 主窗口（微信公众号专精版）
布局：左侧图标侧边栏 + QStackedWidget（解析页 / 历史页）
"""
import os
import re
import subprocess
import sys

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QScrollArea, QCheckBox, QLabel,
    QProgressBar, QFileDialog, QMessageBox, QStatusBar, QTextEdit,
    QComboBox, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSizePolicy, QSpacerItem, QFrame,
)
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QShortcut, QKeySequence, QColor

from .worker import FetchWorker, ImageInfo
from .widgets import FlowLayout, ThumbnailItem, ImageViewer, SidebarButton, SIDEBAR_WIDTH, THUMB_HEIGHT
from .history_manager import history_manager, HistoryEntry
from .blocked_config import blocked_config
from .extractor import is_wechat_url
from .file_utils import unique_path
from .save_worker import SaveWorker
from .settings import (
    settings, K_GEOMETRY, K_WINDOW_STATE, K_SORT_INDEX,
    K_FILTER_SMALL, K_PREFER_ORIGINAL, K_FORMAT, K_LAST_SAVE_DIR,
)

try:
    from version import VERSION
except ImportError:  # 打包/异常路径下不阻断启动
    VERSION = ""


class ImageDownloaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ImgSnag 微信公众号版 v{VERSION}" if VERSION else "ImgSnag 微信公众号版")
        self.resize(1100, 750)
        self.images: list[ImageInfo] = []
        self.thumb_items: list[ThumbnailItem] = []
        self._preview_index = -1
        self.worker: FetchWorker | None = None
        self.save_worker: SaveWorker | None = None
        self._last_parsed_url = ""
        self._current_history_id = None
        self._pending_history_id = None
        self.settings = settings

        # 右键屏蔽回调
        ThumbnailItem.on_block_size = self._on_block_size

        self._build_ui()
        self._apply_style()
        self._restore_settings()
        self._refresh_history()
        self._refresh_blocked()

    # ================================================================
    #  UI Building
    # ================================================================

    def _build_ui(self):
        outer = QWidget()
        self.setCentralWidget(outer)
        h_root = QHBoxLayout(outer)
        h_root.setContentsMargins(0, 0, 0, 0)
        h_root.setSpacing(0)

        sidebar = self._build_sidebar()
        h_root.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.parse_page = self._build_parse_page()
        self.history_page = self._build_history_page()
        self.stack.addWidget(self.parse_page)
        self.stack.addWidget(self.history_page)
        h_root.addWidget(self.stack, 1)

        # 预览缩放快捷键（配合 Ctrl+滚轮 / 双击，用于 100% 查验是不是真原图）
        for seq, slot in (("Ctrl+0", self.preview.fit_to_window),
                          ("Ctrl+1", self.preview.zoom_100),
                          ("Ctrl+=", self.preview.zoom_in),
                          ("Ctrl+-", self.preview.zoom_out)):
            QShortcut(QKeySequence(seq), self).activated.connect(slot)

        self.status = QStatusBar()
        self.status.showMessage("就绪")
        self.setStatusBar(self.status)

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar.setObjectName("sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(6, 16, 6, 16)
        layout.setSpacing(8)

        self.btn_parse = SidebarButton("解析", "▣")
        self.btn_parse.setToolTip("解析公众号文章，挑图下载")
        self.btn_parse.setChecked(True)
        self.btn_parse.clicked.connect(lambda: self._switch_page(0))
        layout.addWidget(self.btn_parse)

        self.btn_history = SidebarButton("历史", "≡")
        self.btn_history.setToolTip("查看解析与下载记录")
        self.btn_history.clicked.connect(lambda: self._switch_page(1))
        layout.addWidget(self.btn_history)

        layout.addStretch()

        if VERSION:
            ver_label = QLabel(f"v{VERSION}")
            ver_label.setAlignment(Qt.AlignCenter)
            ver_label.setStyleSheet("color: #bbb; font-size: 10px;")
            ver_label.setToolTip("当前版本号（唯一来源 version.py）")
            layout.addWidget(ver_label)
        return sidebar

    def _build_parse_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # 输入区
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self.url_input = QTextEdit()
        self.url_input.setPlaceholderText("粘贴公众号文章链接 或 HTML 源码（自动识别；Ctrl+Enter 解析）")
        self.url_input.setMinimumHeight(32)
        self.url_input.setMaximumHeight(32)
        self.url_input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.url_input.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.url_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.url_input.setLineWrapMode(QTextEdit.NoWrap)
        self.url_input.setStyleSheet("""
            QTextEdit { border: 1px solid #d9d9d9; border-radius: 6px; padding: 4px 8px; font-size: 14px; background: #fff; }
            QTextEdit:focus { border-color: #1677ff; }
        """)
        QShortcut(QKeySequence("Ctrl+Return"), self.url_input).activated.connect(self._on_parse)
        self.url_input.installEventFilter(self)
        input_row.addWidget(self.url_input, 1)

        self.paste_btn = QPushButton("粘贴HTML")
        self.paste_btn.setToolTip(
            "从剪贴板读入**整篇网页源码**（浏览器里 Ctrl+U 全选复制的那种）。\n"
            "链接直接在左边输入框里粘贴即可，不需要这个按钮。"
        )
        self.paste_btn.setMinimumHeight(32); self.paste_btn.setMaximumHeight(32)
        self.paste_btn.clicked.connect(self._on_paste_html)
        input_row.addWidget(self.paste_btn)

        self.parse_btn = QPushButton("解析图片")
        self.parse_btn.setMinimumHeight(32); self.parse_btn.setMaximumHeight(32)
        self.parse_btn.setFixedWidth(90)
        self.parse_btn.clicked.connect(self._on_parse)
        input_row.addWidget(self.parse_btn)

        self.script_cb = QCheckBox("补充Script扫描")
        self.script_cb.setToolTip(
            "默认只从 <img data-src> 提取正文图\n"
            "如果解析到 0 张图，勾选此项重新解析，会额外扫描 <script> 内的隐藏图片"
        )
        self.script_cb.setStyleSheet("font-size: 12px; color: #666; padding: 0 4px;")
        input_row.addWidget(self.script_cb)

        self.original_cb = QCheckBox("原图画质")
        self.original_cb.setToolTip(
            "微信正文图地址默认带 /640，直接下载得到的是宽 640 的压缩版（常常只有几百 KB）。\n"
            "勾选后改为请求 /0 原图，清晰度与体积都会明显提升（实测同一张图最大差 4 倍）。\n"
            "原图取不到时会自动回退压缩版，不会导致整张失败。"
        )
        self.original_cb.setChecked(True)
        self.original_cb.setStyleSheet("font-size: 12px; color: #666; padding: 0 4px;")
        input_row.addWidget(self.original_cb)
        root.addLayout(input_row)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setVisible(False); self.progress.setTextVisible(True)
        root.addWidget(self.progress)

        # 操作栏
        action_row = QHBoxLayout(); action_row.setSpacing(8)
        self.info_label = QLabel("就绪")
        self.info_label.setStyleSheet("color: #666;")
        action_row.addWidget(self.info_label)
        action_row.addStretch()

        self.sort_cb = QComboBox()
        self.sort_cb.addItems(["默认顺序", "名称", "尺寸 ↓", "尺寸 ↑", "分辨率 ↓", "分辨率 ↑"])
        self.sort_cb.setMinimumHeight(28)
        self.sort_cb.setStyleSheet("font-size: 12px; padding: 2px 6px;")
        self.sort_cb.currentIndexChanged.connect(self._on_sort)
        self.sort_cb.setEnabled(False)
        action_row.addWidget(self.sort_cb)

        self.filter_square_cb = QCheckBox("过滤小图与装饰")
        self.filter_square_cb.setToolTip(
            "自动隐藏「不像正文图」的图片，规则共三条：\n"
            "  • 最长边 ≤ 200px 的小图（头像、图标、二维码等）\n"
            "  • 透明的 PNG 且小于 10KB（微信排版用的装饰图）\n"
            "  • 你右键点过「屏蔽此尺寸」的尺寸 —— 学到的规则会在下次解析自动生效\n"
            "隐藏只是不显示、不勾选，不会删掉任何文件；\n"
            "取消勾选就能把这些图重新显示出来再挑；\n"
            "屏蔽过的尺寸在「历史」页底部列着，随时可以逐条恢复或全部恢复。"
        )
        self.filter_square_cb.setChecked(True)
        self.filter_square_cb.toggled.connect(self._on_filter_toggled)
        self.filter_square_cb.setStyleSheet("font-size: 12px; color: #666;")
        action_row.addWidget(self.filter_square_cb)

        # 全选/取消全选合成一个按钮，文案随当前状态切换（少一个按钮，窄窗口也放得下）
        self.select_all_btn = QPushButton("全选"); self.select_all_btn.setEnabled(False)
        self.select_all_btn.setToolTip("选中/取消选中当前所有可下载的图（重复图不参与批量选择）")
        self.select_all_btn.clicked.connect(self._on_select_all_clicked)
        action_row.addWidget(self.select_all_btn)

        fmt_label = QLabel("保存为"); fmt_label.setStyleSheet("font-size: 12px; color: #666;")
        action_row.addWidget(fmt_label)
        self.fmt_cb = QComboBox()
        self.fmt_cb.addItems(["原格式", "JPG", "PNG", "WebP"])
        self.fmt_cb.setMinimumHeight(28); self.fmt_cb.setStyleSheet("font-size: 12px; padding: 2px 6px;")
        action_row.addWidget(self.fmt_cb)

        self.download_btn = QPushButton("下载选中图片")
        self.download_btn.setEnabled(False); self.download_btn.setMinimumHeight(36)
        self.download_btn.setStyleSheet(
            "QPushButton { background: #1677ff; color: white; border: none; border-radius: 6px; padding: 6px 20px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #4096ff; }"
            "QPushButton:disabled { background: #d9d9d9; color: #999; }"
        )
        self.download_btn.clicked.connect(self._on_download)
        action_row.addWidget(self.download_btn)
        root.addLayout(action_row)

        # 大图预览区（左）+ 缩略图条（右）
        content_row = QHBoxLayout()
        content_row.setSpacing(0)

        self.preview = ImageViewer()
        content_row.addWidget(self.preview, 1)

        # 缩略图条（右侧竖向滚动）
        self.thumb_scroll = QScrollArea()
        self.thumb_scroll.setWidgetResizable(True)
        self.thumb_scroll.setFixedWidth(THUMB_HEIGHT + 80)
        self.thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.thumb_scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #e0e0e0; border-radius: 4px; background: #fafafa; }"
        )
        self.thumb_container = QWidget()
        self.thumb_strip = QVBoxLayout(self.thumb_container)
        self.thumb_strip.setContentsMargins(8, 8, 8, 8)
        self.thumb_strip.setSpacing(6)
        # 用显式 spacer 而不是 addStretch()：新图要插到它前面，
        # 底部还要挂一个「已隐藏 N 张小图」的按钮，两者都不能被插入顺序搞乱
        self._strip_spacer = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.thumb_strip.addSpacerItem(self._strip_spacer)

        # 被过滤掉的图原来是"凭空消失"，用户不知道拦了什么 —— 给个可点开的入口
        self.hidden_btn = QPushButton("")
        self.hidden_btn.setCursor(Qt.PointingHandCursor)
        self.hidden_btn.setStyleSheet(
            "QPushButton { border: 1px dashed #d9d9d9; border-radius: 4px; color: #888;"
            " font-size: 11px; padding: 4px 2px; background: #fff; }"
            "QPushButton:hover { border-color: #1677ff; color: #1677ff; }"
        )
        self.hidden_btn.clicked.connect(self._on_toggle_hidden)
        self.hidden_btn.setVisible(False)
        self.thumb_strip.addWidget(self.hidden_btn)

        self.thumb_scroll.setWidget(self.thumb_container)
        content_row.addWidget(self.thumb_scroll)

        root.addLayout(content_row, 1)

        return page

    def _build_history_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("下载历史")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #333;")
        header.addWidget(title)
        header.addStretch()

        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索链接 / 保存路径 / 状态")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.setFixedWidth(240)
        self.history_search.textChanged.connect(lambda _: self._refresh_history())
        header.addWidget(self.history_search)

        clear_btn = QPushButton("清空历史")
        clear_btn.setStyleSheet("color: #ff4d4f; font-size: 12px;")
        clear_btn.clicked.connect(self._on_clear_history)
        header.addWidget(clear_btn)
        root.addLayout(header)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels(["URL", "时间", "图片", "保存路径", "状态", "操作"])
        self.history_table.horizontalHeader().setStretchLastSection(False)
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self.history_table.setColumnWidth(1, 130)
        self.history_table.setColumnWidth(2, 60)
        self.history_table.setColumnWidth(4, 80)
        self.history_table.setColumnWidth(5, 210)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        root.addWidget(self.history_table, 1)

        root.addWidget(self._build_blocked_panel())

        return page

    def _build_blocked_panel(self):
        """历史页底部的「已屏蔽的尺寸」面板。

        这个面板本身就是修复：旧的 add_blocked 是**单向门** —— 在缩略图上右键误屏蔽
        一个尺寸之后，那类图从此静默消失，既看不到屏蔽了哪些尺寸、也没有任何撤销入口，
        只能去手改 %APPDATA%/ImgSnagWeChat/blocked_sizes.json。对目标用户（普通用户）
        而言等于「图丢了」。这里把它变成双向的：列得出来、撤销得掉、还看得到命中多少张。
        """
        panel = QFrame()
        panel.setObjectName("blockedPanel")
        panel.setStyleSheet(
            "#blockedPanel { background: #fafafa; border: 1px solid #ececec; border-radius: 6px; }"
        )
        box = QVBoxLayout(panel)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.blocked_title = QLabel("已屏蔽的尺寸")
        self.blocked_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #333; border: none;")
        head.addWidget(self.blocked_title)

        self.blocked_sub = QLabel("")
        self.blocked_sub.setStyleSheet("font-size: 11px; color: #999; border: none;")
        head.addWidget(self.blocked_sub)
        head.addStretch()

        self.blocked_clear_btn = QPushButton("全部恢复")
        self.blocked_clear_btn.setToolTip("清空屏蔽表 —— 所有被屏蔽过的尺寸重新参与解析")
        self.blocked_clear_btn.setStyleSheet(
            "QPushButton { font-size: 11px; color: #ff4d4f; border: 1px solid #ffccc7;"
            " border-radius: 4px; background: #fff; padding: 2px 10px; }"
            "QPushButton:hover { border-color: #ff4d4f; }"
        )
        self.blocked_clear_btn.clicked.connect(self._on_clear_blocked)
        head.addWidget(self.blocked_clear_btn)
        box.addLayout(head)

        self.blocked_empty = QLabel(
            "还没有屏蔽任何尺寸。在缩略图上点右键可以屏蔽它的尺寸，"
            "之后解析遇到同类尺寸会按你教的规则自动过滤 —— 在这里随时可以撤销。"
        )
        self.blocked_empty.setWordWrap(True)
        self.blocked_empty.setStyleSheet("font-size: 11px; color: #999; border: none;")
        box.addWidget(self.blocked_empty)

        self.blocked_scroll = QScrollArea()
        self.blocked_scroll.setWidgetResizable(True)
        self.blocked_scroll.setFrameShape(QFrame.NoFrame)
        self.blocked_scroll.setMaximumHeight(126)
        self.blocked_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.blocked_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self.blocked_rows = QVBoxLayout(inner)
        self.blocked_rows.setContentsMargins(0, 0, 0, 0)
        self.blocked_rows.setSpacing(4)
        self.blocked_rows.addStretch()          # 末尾占位：新行插到它前面
        self.blocked_scroll.setWidget(inner)
        box.addWidget(self.blocked_scroll)
        return panel

    #: 分类标签的配色，让「哪一类」一眼分得开
    _CAT_COLORS = {"ui": "#8c8c8c", "avatar": "#722ed1", "cover": "#fa8c16"}

    def _make_blocked_row(self, row: dict):
        """一条屏蔽记录：分类 + 尺寸 + 命中次数 + 「恢复」按钮"""
        cat = row["category"]
        color = self._CAT_COLORS.get(cat, "#8c8c8c")

        line = QWidget()
        line.setStyleSheet("background: transparent;")
        h = QHBoxLayout(line)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        tag = QLabel(row["category_label"])
        tag.setStyleSheet(
            f"font-size: 10px; color: {color}; border: 1px solid {color};"
            " border-radius: 3px; padding: 0 5px; background: #fff;"
        )
        h.addWidget(tag)

        size_lbl = QLabel(f"{row['width']}×{row['height']}")
        size_lbl.setStyleSheet("font-size: 12px; color: #333;")
        size_lbl.setMinimumWidth(86)
        h.addWidget(size_lbl)

        hits = row["hits"]
        hit_lbl = QLabel(f"已过滤 {hits} 张" if hits else "尚未命中")
        hit_lbl.setStyleSheet(
            "font-size: 11px; color: %s;" % ("#fa8c16" if hits else "#bbb")
        )
        hit_lbl.setToolTip(
            "这条规则一共拦下过多少张图（含屏蔽当下在页面里立刻过滤掉的那些）"
        )
        h.addWidget(hit_lbl)

        added = (row.get("added_at") or "")[:10]
        if added:
            when = QLabel(f"· {added} 加入")
            when.setStyleSheet("font-size: 11px; color: #bbb;")
            h.addWidget(when)

        h.addStretch()

        undo = QPushButton("恢复")
        undo.setCursor(Qt.PointingHandCursor)
        undo.setToolTip(
            f"撤销这条屏蔽：{row['width']}×{row['height']} 的图以后不再被自动过滤，\n"
            "当前页面里被它拦下的图会立刻放回来并勾上"
        )
        undo.setStyleSheet(
            "QPushButton { font-size: 11px; color: #1677ff; border: 1px solid #91caff;"
            " border-radius: 4px; background: #fff; padding: 1px 10px; }"
            "QPushButton:hover { border-color: #1677ff; }"
        )
        undo.clicked.connect(
            lambda _=False, c=cat, w=row["width"], hh=row["height"]: self._on_unblock(c, w, hh)
        )
        h.addWidget(undo)
        return line

    def _refresh_blocked(self):
        """重建「已屏蔽的尺寸」面板（条数很少，直接全量重建最不容易出错）"""
        rows = blocked_config.list_blocked()

        while self.blocked_rows.count() > 1:        # 末尾的 stretch 留着
            item = self.blocked_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for row in rows:
            self.blocked_rows.insertWidget(self.blocked_rows.count() - 1, self._make_blocked_row(row))

        total = len(rows)
        self.blocked_title.setText(f"已屏蔽的尺寸 ({total})" if total else "已屏蔽的尺寸")
        self.blocked_sub.setText("下次解析遇到这些尺寸会自动过滤，点「恢复」即可撤销" if total else "")
        self.blocked_scroll.setVisible(bool(total))
        self.blocked_clear_btn.setVisible(bool(total))
        self.blocked_empty.setVisible(not total)

    def _reevaluate_filters(self) -> int:
        """屏蔽表变了 → 用同一套规则把当前每张图重新判一遍，返回新放回来的张数。

        判定走 blocked_config.classify（与 worker 共用一份），所以界面显示和解析结果
        不会说两套话：取消屏蔽后仍该藏起来的（比如它本来就够小）会继续藏着。

        新被拦下的张数当场记进统计 —— 用户在面板上看到的「已过滤 N 张」要包括刚刚
        眼睁睁看着消失的那几张，否则一屏蔽完面板写「尚未命中」，等于自相矛盾。
        """
        blocked_sets = blocked_config.get_blocked_sets()
        enabled = self.filter_square_cb.isChecked()
        changed = back = 0
        new_hits = {}
        for item in self.thumb_items:
            info = item.info
            old = tuple(info.block_reasons)
            reasons = info.classify_reasons(blocked_sets)
            if reasons == old:
                continue
            was_hidden = info.is_mini_square
            info.block_reasons = reasons
            info.is_mini_square = bool(reasons)
            item.refresh_filter_state(enabled, check_on_show=was_hidden and not reasons)
            changed += 1
            if was_hidden and not reasons:
                back += 1
            for r in set(reasons) - set(old):
                if r.startswith("blocked:"):
                    k = (r.split(":", 1)[1], info.width, info.height)
                    new_hits[k] = new_hits.get(k, 0) + 1
        if changed:
            self._update_download_btn()
            self._update_hidden_hint()
        blocked_config.add_hits(new_hits)
        return back

    def _on_unblock(self, category: str, width: int, height: int):
        """撤销一条屏蔽，并把当前结果里被它拦掉的图当场放回来 —— 撤销要立刻看得见"""
        if not blocked_config.remove_blocked(category, (width, height)):
            self.status.showMessage("这条屏蔽已经不存在了")
            self._refresh_blocked()
            return
        back = self._reevaluate_filters()
        self._refresh_blocked()
        msg = f"已恢复 {width}×{height}"
        msg += f" —— 本页放回 {back} 张" if back else "（当前页面没有被它拦下的图）"
        self.status.showMessage(f"{msg}，下次解析不再过滤该尺寸")

    def _on_clear_blocked(self):
        rows = blocked_config.list_blocked()
        if not rows:
            return
        reply = QMessageBox.question(
            self, "确认",
            f"恢复全部 {len(rows)} 条被屏蔽的尺寸？\n恢复后这些尺寸的图不再被自动过滤。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        n = blocked_config.clear_blocked()
        back = self._reevaluate_filters()
        self._refresh_blocked()
        self.status.showMessage(f"已恢复 {n} 条屏蔽，本页放回 {back} 张")

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #ffffff; }
            #sidebar { background: #f5f5f5; border-right: 1px solid #e8e8e8; }
            QLineEdit { border: 1px solid #d9d9d9; border-radius: 6px; padding: 4px 12px; font-size: 14px; }
            QLineEdit:focus { border-color: #1677ff; }
            QPushButton {
                border: 1px solid #d9d9d9; border-radius: 6px;
                padding: 4px 14px; font-size: 13px; background: #fff;
            }
            QPushButton:hover { border-color: #1677ff; color: #1677ff; }
            QPushButton:disabled { color: #ccc; }
            QProgressBar { border: none; border-radius: 4px; background: #f0f0f0; text-align: center; font-size: 12px; }
            QProgressBar::chunk { background: #1677ff; border-radius: 4px; }
            QCheckBox { font-size: 12px; }
            QTextEdit { border: 1px solid #d9d9d9; border-radius: 6px; padding: 8px; font-size: 12px; }
        """)

    # ================================================================
    #  界面偏好持久化
    # ================================================================

    def _restore_settings(self):
        """把上次退出时的界面状态装回去。

        这些选项以前一个都不记住：每次启动都要重新选排序、重设保存格式、重新勾选过滤
        开关，窗口还得重新拖到顺手的大小。用户对这类细节的感受就是「这软件不记事」。
        """
        geo = self.settings.value(K_GEOMETRY)
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value(K_WINDOW_STATE)
        if state is not None:
            self.restoreState(state)

        idx = self.settings.get(K_SORT_INDEX)
        if isinstance(idx, int) and 0 <= idx < self.sort_cb.count():
            self.sort_cb.blockSignals(True)
            self.sort_cb.setCurrentIndex(idx)
            self.sort_cb.blockSignals(False)

        fmt_idx = self.fmt_cb.findText(self.settings.get(K_FORMAT))
        if fmt_idx >= 0:
            self.fmt_cb.setCurrentIndex(fmt_idx)

        # 两个复选框默认值为 True，值相同不会触发信号，值不同才需要重新过滤一遍
        self.filter_square_cb.setChecked(bool(self.settings.get(K_FILTER_SMALL)))
        self.original_cb.setChecked(bool(self.settings.get(K_PREFER_ORIGINAL)))

    def _save_settings(self):
        """退出时落盘。几何信息用 QByteArray 原样存，别自己拆成四个数字"""
        self.settings.set_value(K_GEOMETRY, self.saveGeometry())
        self.settings.set_value(K_WINDOW_STATE, self.saveState())
        self.settings.set(K_SORT_INDEX, self.sort_cb.currentIndex())
        self.settings.set(K_FORMAT, self.fmt_cb.currentText())
        self.settings.set(K_FILTER_SMALL, self.filter_square_cb.isChecked())
        self.settings.set(K_PREFER_ORIGINAL, self.original_cb.isChecked())
        self.settings.sync()

    def _switch_page(self, index):
        self.stack.setCurrentIndex(index)
        self.btn_parse.setChecked(index == 0)
        self.btn_history.setChecked(index == 1)
        if index == 1:
            self._refresh_history()
            self._refresh_blocked()

    # ================================================================
    #  解析页核心逻辑
    # ================================================================

    def _reset_before_parse(self):
        self._clear_thumbnails()
        self.preview.clear()
        self.images.clear()
        self.thumb_items.clear()
        self._preview_index = -1
        self.download_btn.setEnabled(False)
        self.select_all_btn.setEnabled(False)
        self.select_all_btn.setText("全选")
        self._update_hidden_hint()
        # 解析中按钮转为「取消解析」并保持可点 —— 若禁用就没法中止长文章的解析
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("取消解析")
        self.url_input.setEnabled(False)
        self.paste_btn.setEnabled(False)
        self.sort_cb.setEnabled(False)
        self.sort_cb.blockSignals(True)
        self.sort_cb.setCurrentIndex(0)
        self.sort_cb.blockSignals(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.info_label.setText("解析中...")
        self.status.showMessage("解析中...")

    def _on_parse_done(self, success):
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("解析图片")
        self.url_input.setEnabled(True)
        self.paste_btn.setEnabled(True)
        self.script_cb.setChecked(False)  # 单次生效，解析后自动取消
        if not success:
            self.progress.setVisible(False)

    def _on_progress(self, current, total):
        if total > 0:
            self.progress.setValue(int(current / total * 100))
            self.info_label.setText(f"下载中 {current}/{total}")
        else:
            self.info_label.setText("正在解析...")

    def _on_image_loaded(self, index, info):
        item = ThumbnailItem(info)
        item.checkbox.toggled.connect(self._update_download_btn)
        item.clicked.connect(self._on_thumb_clicked)
        if self.filter_square_cb.isChecked() and info.is_mini_square:
            item.checkbox.blockSignals(True)
            item.checkbox.setChecked(False)
            item.checkbox.blockSignals(False)
            item.set_visible_state(False)
        # 插到 spacer 之前：所有缩略图之后、「已隐藏 N 张」按钮之前
        self.thumb_strip.insertWidget(self.thumb_strip.indexOf(self._strip_spacer), item)
        self.thumb_items.append(item)
        # 第一张图自动预览
        if self._preview_index < 0 and not info.is_mini_square and not info.is_duplicate:
            self._preview_index = len(self.thumb_items) - 1
            item.set_highlight(True)
            self.preview.show_image(info.data)
        self._update_download_btn()
        self._update_hidden_hint()

    def _on_block_size(self, info, category, size):
        """右键菜单屏蔽此尺寸：写入 JSON + 立即重新过滤当前结果

        过滤交给 _reevaluate_filters 而不是就地改标志位：会命中的规则可能不止一条
        （比如这张图本来就小），让统一的那份规则去算才不会出现「屏蔽前后表现不一致」。
        """
        if not blocked_config.add_blocked(category, size):
            self.status.showMessage(f"尺寸 {size[0]}×{size[1]} 已在屏蔽列表中")
            return

        self._reevaluate_filters()
        self._refresh_blocked()
        w, h = size
        hit = sum(1 for it in self.thumb_items if (it.info.width, it.info.height) == size)
        self.status.showMessage(
            f"已屏蔽 {w}×{h}（本页命中 {hit} 张）—— 下次解析遇到该尺寸会自动过滤，"
            f"「历史」页底部可以撤销"
        )

    def _on_thumb_clicked(self, info):
        """点击缩略图切换大图预览"""
        # 取消旧高亮
        if 0 <= self._preview_index < len(self.thumb_items):
            self.thumb_items[self._preview_index].set_highlight(False)
        # 找到对应的 thumb_item
        for i, item in enumerate(self.thumb_items):
            if item.info is info:
                self._preview_index = i
                item.set_highlight(True)
                self.preview.show_image(info.data)
                break

    def _on_all_done(self, images, was_cancelled=False):
        self.images = images
        self._on_parse_done(True)
        self.progress.setVisible(False)
        unique = sum(1 for i in images if not i.is_duplicate)
        dup = sum(1 for i in images if i.is_duplicate)
        total_mb = sum(len(i.data) for i in images) / 1048576
        text = f"共 {len(images)} 张"
        if unique:
            text += f"（{unique} 张有效"
        if dup:
            text += f"，{dup} 张重复"
        if unique:
            text += "）"
        if images:
            text += f"，合计 {total_mb:.1f} MB"
        fallback = sum(1 for i in images if not i.used_original)
        if fallback:
            text += f"（{fallback} 张回退压缩版）"
        if was_cancelled:
            text = "已取消 — " + text
        self.info_label.setText(text)
        # 状态栏不再复述统计信息（原来顶部信息栏与状态栏显示的是同一句话）
        if was_cancelled:
            self.status.showMessage("已取消解析，已下载的图照样可以勾选保存")
        else:
            self.status.showMessage("解析完成，勾选右侧图片后点「下载选中图片」")
        self.select_all_btn.setEnabled(True)
        self.sort_cb.setEnabled(True)
        self._update_download_btn()
        self._update_hidden_hint()
        self._refresh_blocked()      # 命中次数变了，屏蔽面板跟着更新

        if self._last_parsed_url:
            self._current_history_id = history_manager.add(
                source_url=self._last_parsed_url,
                total_images=len(images),
                success_images=0,
                status="scanned"
            )

    def _on_error(self, msg):
        self._on_parse_done(False)
        self.progress.setVisible(False)
        self.info_label.setText("解析失败")
        self.status.showMessage(msg)
        if self._last_parsed_url:
            history_manager.add(
                source_url=self._last_parsed_url,
                total_images=0, success_images=0, status="failed", note=msg[:200]
            )
            self._last_parsed_url = ""
            self._current_history_id = None
        QMessageBox.warning(self, "解析失败", msg)

    def _selectable_items(self):
        """参与批量选择/下载的图：被过滤掉的和重复图都不算"""
        return [it for it in self.thumb_items if not it.filtered_out and not it.info.is_duplicate]

    def _toggle_all(self, checked):
        for item in self._selectable_items():
            item.set_checked(checked)
        self._update_download_btn()

    def _on_select_all_clicked(self):
        """一个按钮两用：已全选则取消全选，否则全选（文案由 _update_download_btn 同步）"""
        selectable = self._selectable_items()
        all_on = bool(selectable) and all(it.checkbox.isChecked() for it in selectable)
        self._toggle_all(not all_on)

    def _update_hidden_hint(self):
        """缩略图条底部提示：被过滤了多少张、点一下能看回来"""
        n = sum(1 for it in self.thumb_items if it.info.is_mini_square)
        if not n:
            self.hidden_btn.setVisible(False)
            return
        if self.filter_square_cb.isChecked():
            self.hidden_btn.setText(f"已隐藏 {n} 张小图/装饰\n点此显示")
        else:
            self.hidden_btn.setText(f"正在显示 {n} 张小图/装饰\n点此隐藏")
        self.hidden_btn.setVisible(True)

    def _on_toggle_hidden(self):
        """切换「过滤小图与装饰」——保持单一数据源，不自己另存一份状态"""
        self.filter_square_cb.setChecked(not self.filter_square_cb.isChecked())

    def _on_filter_toggled(self, checked):
        for item in self.thumb_items:
            if item.info.is_mini_square:
                item.set_visible_state(not checked)
        # 如果当前预览的图被隐藏，自动切到下一张可见的
        if self._preview_index >= 0:
            current = self.thumb_items[self._preview_index]
            if current.filtered_out:
                self._preview_next_visible()
        self._update_download_btn()
        self._update_hidden_hint()

    def _preview_next_visible(self):
        """切换到下一张可见图预览"""
        for i, item in enumerate(self.thumb_items):
            if not item.filtered_out and not item.info.is_duplicate:
                if 0 <= self._preview_index < len(self.thumb_items):
                    self.thumb_items[self._preview_index].set_highlight(False)
                self._preview_index = i
                item.set_highlight(True)
                self.preview.show_image(item.info.data)
                return

    def _on_sort(self, index):
        key_map = {
            0: lambda c: c.info.index,
            1: lambda c: f"img_{c.info.index + 1:02d}{c.info.ext}",
            2: lambda c: -len(c.info.data),
            3: lambda c: len(c.info.data),
            4: lambda c: -(c.info.width * c.info.height),
            5: lambda c: c.info.width * c.info.height,
        }
        self.thumb_items.sort(key=key_map.get(index, key_map[0]))
        self._reorder_strip()

    def _reorder_strip(self):
        for item in self.thumb_items:
            self.thumb_strip.removeWidget(item)
        for item in self.thumb_items:
            self.thumb_strip.insertWidget(self.thumb_strip.indexOf(self._strip_spacer), item)

    def _update_download_btn(self):
        count = sum(1 for item in self.thumb_items if not item.filtered_out and item.checkbox.isChecked())
        self.download_btn.setEnabled(count > 0)
        self.download_btn.setText(f"下载选中图片 ({count})")
        # 全选按钮的文案跟着勾选状态走，省掉一个「取消全选」按钮
        selectable = self._selectable_items()
        all_on = bool(selectable) and all(it.checkbox.isChecked() for it in selectable)
        self.select_all_btn.setText("取消全选" if all_on else "全选")

    def _last_save_dir(self) -> str:
        """上次保存目录；已经不在了就回空串（交给系统默认位置，别弹个不存在的路径）"""
        d = self.settings.get(K_LAST_SAVE_DIR)
        return d if d and os.path.isdir(d) else ""

    def _on_download(self):
        # 从上次存过的地方接着存：以前每次都从「文档」开始，一套图集存多次就得反复导航
        folder = QFileDialog.getExistingDirectory(self, "选择保存目录", self._last_save_dir())
        if not folder:
            return
        if folder != self.settings.get(K_LAST_SAVE_DIR):
            self.settings.set(K_LAST_SAVE_DIR, folder)
            self.settings.sync()
        fmt_map = {"原格式": "", "JPG": ".jpg", "PNG": ".png", "WebP": ".webp"}
        fmt = fmt_map.get(self.fmt_cb.currentText(), "")

        # 先把要写的图快照成纯数据交给后台线程，避免线程回头去读界面控件
        targets = []
        for item in self.thumb_items:
            if not item.checkbox.isChecked() or item.filtered_out:
                continue
            # 编号取自解析顺序（= 正文顺序），所以按尺寸/分辨率排序只影响显示，不会改掉文件名
            targets.append((item.info.data, f"img_{item.info.index + 1:02d}", item.info.ext))
        if not targets:
            return

        # 历史记录的 id 先摘下来，免得保存期间用户又解析一次把它覆盖掉
        self._pending_history_id = self._current_history_id
        self._current_history_id = None
        self._last_parsed_url = ""

        self.download_btn.setEnabled(False)
        self.download_btn.setText(f"保存中 0/{len(targets)}")
        self.info_label.setText(f"正在保存 {len(targets)} 张...")
        self.status.showMessage(f"正在保存到 {folder} ...")

        self.save_worker = SaveWorker(targets, folder, fmt)
        self.save_worker.progress.connect(self._on_save_progress)
        self.save_worker.done.connect(lambda s, f, e: self._on_save_done(s, f, e, folder))
        self.save_worker.failed.connect(self._on_save_failed)
        self.save_worker.start()

    def _on_save_progress(self, current, total):
        self.download_btn.setText(f"保存中 {current}/{total}")

    def _on_save_done(self, saved, fallback, errors, folder):
        if self._pending_history_id:
            history_manager.update(
                self._pending_history_id,
                success_images=saved,
                save_path=folder,
                status="done" if saved > 0 else "failed"
            )
            self._pending_history_id = None

        self._update_download_btn()   # 恢复按钮文案与可用状态

        msg = f"已保存 {saved} 张图片到:\n{folder}"
        if fallback:
            msg += f"\n\n其中 {fallback} 张格式转换失败，已按原格式保存。"
        if errors:
            shown = "\n".join(errors[:5])
            msg += f"\n\n以下 {len(errors)} 张未能写出：\n{shown}"
            if len(errors) > 5:
                msg += "\n…"
        if errors:
            QMessageBox.warning(self, "下载完成（有失败）", msg)
        else:
            QMessageBox.information(self, "下载完成", msg)
        self.status.showMessage(f"已保存 {saved} 张图片到 {folder}")

    def _on_save_failed(self, msg):
        self._pending_history_id = None
        self._update_download_btn()
        self.status.showMessage(msg)
        QMessageBox.warning(self, "保存失败", msg)

    def _clear_thumbnails(self):
        """只清缩略图：spacer 与底部的「已隐藏 N 张」按钮要留着"""
        for item in self.thumb_items:
            self.thumb_strip.removeWidget(item)
            item.setParent(None)
            item.deleteLater()
        self.thumb_items.clear()
        self._preview_index = -1

    def eventFilter(self, obj, event):
        """输入框里按回车直接解析（占位提示写的 Ctrl+Enter 也照旧可用）。

        注意本方法必须存在：只调 `installEventFilter(self)` 而没有实现，
        Qt 会退回 QObject.eventFilter 直接返回 False —— 等于装了个没用的过滤器，
        这也是本文件里早先残留的一处死代码。
        """
        if obj is getattr(self, 'url_input', None) and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
            ):
                self._on_parse()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        """关窗前等后台线程收尾。

        QThread 在线程仍在运行时被销毁，Qt 会直接报
        `QThread: Destroyed while thread is still running` 并可能崩溃退不出。
        """
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        if self.save_worker is not None and self.save_worker.isRunning():
            self.save_worker.wait(5000)   # 落盘不做半途中断，等它写完
        self._save_settings()
        super().closeEvent(event)

    def _start_worker(self, source, is_url):
        self._reset_before_parse()
        if is_url:
            self._last_parsed_url = source
        include_scripts = is_url and self.script_cb.isChecked()
        prefer_original = self.original_cb.isChecked()
        self.worker = FetchWorker(source, is_url, include_scripts, prefer_original)
        self.worker.progress.connect(self._on_progress)
        self.worker.image_loaded.connect(self._on_image_loaded)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.cancelled.connect(self._on_parse_cancelled)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_parse_cancelled(self, images):
        """用户中止解析：已经下载下来的部分照常交付，不丢弃"""
        self._on_all_done(images, was_cancelled=True)

    def _on_paste_html(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.url_input.setPlainText(text)
            self.status.showMessage(f"已粘贴 {len(text)} 字符 — 点击「解析图片」")
        else:
            self.status.showMessage("剪贴板为空")

    def _on_parse(self):
        # 解析进行中：同一个按钮转为「取消解析」
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.parse_btn.setEnabled(False)
            self.parse_btn.setText("正在取消...")
            self.info_label.setText("正在取消...")
            self.status.showMessage("正在取消解析（等当前这张图收尾，最多 20 秒）...")
            return

        text = self.url_input.toPlainText().strip()
        if not text:
            return
        url_match = re.search(r'https?://[^\s]+', text)
        if url_match and not text.startswith(('http://', 'https://')):
            extracted = url_match.group(0)
            self.url_input.setPlainText(extracted)
            self.status.showMessage(f"自动提取链接: {extracted[:60]}...")
            text = extracted
        is_url = text.lower().startswith(('http://', 'https://'))
        if is_url and not text.lower().startswith('https://'):
            text = 'https://' + text
            self.url_input.setPlainText(text)
        if is_url and not is_wechat_url(text):
            QMessageBox.warning(self, "不支持的链接",
                "本版本专精微信公众号文章图片提取。\n\n"
                "请粘贴 mp.weixin.qq.com 开头的公众号文章链接，\n"
                "或直接粘贴公众号文章的 HTML 源码。")
            return
        if not is_url and len(text) < 100:
            QMessageBox.warning(self, "提示",
                "HTML 内容太短（<100字符），请确认已复制完整的页面源码。\n\n"
                "获取方式：浏览器 F12 → 元素面板 → 右键 <html> → 复制 → 复制 outerHTML")
            return
        self._start_worker(text, is_url=is_url)

    # ================================================================
    #  历史页逻辑
    # ================================================================

    @staticmethod
    def _short_url(url):
        """URL 列只留尾部 ID —— 反正全是 mp.weixin.qq.com/s/ 开头，前缀白占大半列宽"""
        u = url or ""
        for prefix in ("https://mp.weixin.qq.com/s/", "http://mp.weixin.qq.com/s/"):
            if u.startswith(prefix):
                return u[len(prefix):]
        return u

    def _refresh_history(self):
        entries = history_manager.get_all(limit=200)
        keyword = self.history_search.text().strip().lower()
        if keyword:
            # 状态既比对显示文案（中文「失败」）也比对原始值（failed），
            # 用户按哪种搜都能命中
            entries = [
                e for e in entries
                if keyword in (e.source_url or "").lower()
                or keyword in (e.save_path or "").lower()
                or keyword in e.status_text().lower()
                or keyword in (e.status or "").lower()
            ]

        if not entries:
            self.history_table.setRowCount(1)
            tip = "没有匹配的记录" if keyword else "还没有记录 —— 去「解析」页解析一篇文章试试"
            empty = QTableWidgetItem(tip)
            empty.setForeground(QColor("#999"))
            empty.setTextAlignment(Qt.AlignCenter)
            self.history_table.setItem(0, 0, empty)
            self.history_table.setSpan(0, 0, 1, 6)
            self.history_table.setRowHeight(0, 40)
            return

        self.history_table.clearSpans()
        self.history_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            url_item = QTableWidgetItem(self._short_url(entry.source_url)[:100])
            url_item.setToolTip(entry.source_url)
            url_item.setData(Qt.UserRole, entry.id)
            self.history_table.setItem(row, 0, url_item)

            time_str = entry.parsed_at_dt.strftime("%m-%d %H:%M") if entry.parsed_at else ""
            time_item = QTableWidgetItem(time_str)
            time_item.setTextAlignment(Qt.AlignCenter)
            self.history_table.setItem(row, 1, time_item)

            count_item = QTableWidgetItem(f"{entry.success_images}/{entry.total_images}")
            count_item.setTextAlignment(Qt.AlignCenter)
            self.history_table.setItem(row, 2, count_item)

            path_item = QTableWidgetItem(entry.save_path[:80] if entry.save_path else "-")
            path_item.setToolTip(entry.save_path)
            self.history_table.setItem(row, 3, path_item)

            status_item = QTableWidgetItem(entry.status_text())
            status_item.setTextAlignment(Qt.AlignCenter)
            if entry.status == "done":
                status_item.setForeground(QColor("#16b83e"))
            elif entry.status == "failed":
                status_item.setForeground(QColor("#ff4d4f"))
            elif entry.status == "scanned":
                status_item.setForeground(QColor("#1677ff"))
            else:
                status_item.setForeground(QColor("#fa8c16"))
            self.history_table.setItem(row, 4, status_item)

            btn_widget = QWidget()
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(4, 2, 4, 2)
            btn_layout.setSpacing(6)

            reparse_btn = QPushButton("解析")
            reparse_btn.setFixedSize(60, 26)
            reparse_btn.setStyleSheet(
                "font-size: 11px; color: #1677ff; border: 1px solid #1677ff; border-radius: 4px; background: #fff;"
            )
            reparse_btn.clicked.connect(lambda checked, e=entry: self._on_reparse(e))
            btn_layout.addWidget(reparse_btn)

            open_btn = QPushButton("打开")
            open_btn.setFixedSize(60, 26)
            open_btn.setStyleSheet("font-size: 11px; border: 1px solid #d9d9d9; border-radius: 4px; background: #fff;")
            open_btn.clicked.connect(lambda checked, e=entry: self._on_open_folder(e))
            btn_layout.addWidget(open_btn)

            del_btn = QPushButton("删除")
            del_btn.setFixedSize(60, 26)
            del_btn.setStyleSheet(
                "font-size: 11px; color: #ff4d4f; border: 1px solid #ffccc7; border-radius: 4px; background: #fff;"
            )
            del_btn.clicked.connect(lambda checked, e=entry: self._on_delete_entry(e))
            btn_layout.addWidget(del_btn)

            btn_layout.addStretch()

            self.history_table.setCellWidget(row, 5, btn_widget)
            self.history_table.setRowHeight(row, 42)

    def _on_reparse(self, entry: HistoryEntry):
        """再次解析：填入URL并切回解析页"""
        if entry.source_url:
            self.url_input.setPlainText(entry.source_url)
            self._switch_page(0)

    def _on_open_folder(self, entry: HistoryEntry):
        if entry.save_path and os.path.isdir(entry.save_path):
            if sys.platform == 'win32':
                os.startfile(entry.save_path)
            elif sys.platform == 'darwin':
                subprocess.run(['open', entry.save_path])
            else:
                subprocess.run(['xdg-open', entry.save_path])

    def _on_delete_entry(self, entry: HistoryEntry):
        history_manager.delete(entry.id)
        self._refresh_history()
        self.status.showMessage("已删除记录")

    def _on_clear_history(self):
        reply = QMessageBox.question(self, "确认", "确定要清空全部下载历史吗？", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            history_manager.clear()
            self._refresh_history()
            self.status.showMessage("历史已清空")
