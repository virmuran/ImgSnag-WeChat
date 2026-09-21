"""
ImageDownloaderApp — 主窗口（微信公众号专精版）
布局：左侧图标侧边栏 + QStackedWidget（解析页 / 历史页）
"""
import os
import re
import subprocess
import sys
import time

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QScrollArea, QCheckBox, QLabel,
    QProgressBar, QFileDialog, QMessageBox, QStatusBar, QTextEdit,
    QComboBox, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSizePolicy, QSpacerItem, QFrame,
    QDialog, QTextBrowser,
)
from PySide6.QtCore import Qt, QEvent, QTimer, QThread, Signal, QUrl, QStandardPaths
from PySide6.QtGui import QShortcut, QKeySequence, QColor, QDesktopServices, QImage, QImageReader

from .worker import FetchWorker, ImageInfo
from .widgets import FlowLayout, ThumbnailItem, ImageViewer, SidebarButton, SIDEBAR_WIDTH, THUMB_HEIGHT
from .history_manager import history_manager, HistoryEntry
from .blocked_config import blocked_config
from .extractor import is_wechat_url
from .file_utils import unique_path
from .save_worker import SaveWorker
from .naming import folder_name_for, unique_dir
from .library import scan_batch, title_from_folder, MAX_BATCH_BYTES
from .settings import (
    settings, K_GEOMETRY, K_WINDOW_STATE, K_SORT_INDEX,
    K_FILTER_SMALL, K_PREFER_ORIGINAL, K_FORMAT, K_LAST_SAVE_DIR,
    K_LIBRARY_DIR, K_AUTO_CHECK_UPDATE, K_LAST_UPDATE_CHECK,
)
from .updater import (
    check_for_updates, UpdateInfo, CHECK_INTERVAL, SOURCE_URL,
)

try:
    from version import VERSION
except ImportError:  # 打包/异常路径下不阻断启动
    VERSION = ""


class _UpdateCheckThread(QThread):
    """后台问 GitHub「有没有新版本」。

    单独起线程是因为 `api.github.com` 在国内网络下常常要等好几秒甚至超时，
    放在主线程里点一下「检查更新」界面就冻住了。检查失败不抛异常，
    统一通过 UpdateInfo.error 回传（见 updater.check_for_updates）。
    """
    checked = Signal(object)          # UpdateInfo

    def run(self):
        info = check_for_updates()    # 模块级名字，测试里可整体替换
        self.checked.emit(info)


#: 「关于」对话框正文。写成常量便于测试比对，也免得 HTML 和逻辑混在一起。
ABOUT_HTML = f"""
<p style="margin-top:0"><b>这个程序做什么</b><br>
只做一件事：把公众号文章正文里的图按原图画质抓下来。纯 requests 抓取，
不需要浏览器内核，安装包 30 MB。</p>

<p><b>你的数据在哪</b><br>
· 下载历史：<code>~/.imgsnag_wechat/history.db</code><br>
· 已屏蔽的尺寸：<code>%APPDATA%\\ImgSnagWeChat\\blocked_sizes.json</code><br>
· 界面偏好：注册表 <code>HKCU\\Software\\ImgSnagWeChat</code><br>
程序不收集任何信息、不上报任何数据。唯一的对外请求是「检查更新」——
它会访问 GitHub 接口，不想联网可以在下方关掉。</p>

<p><b>源码与反馈</b><br>
<a href="{SOURCE_URL}">{SOURCE_URL.replace('https://', '')}</a><br>
解析不出来的文章、漏过的噪音图，欢迎提 Issue 并附文章链接。</p>

<p><b>第三方依赖</b><br>
· PySide6 —— LGPLv3（Qt for Python）<br>
· requests —— Apache-2.0<br>
· PyInstaller —— 仅打包时使用，不参与运行<br>
· Inno Setup —— 安装包制作</p>

<p><b>免责声明</b><br>
仅供个人学习与合法用途。请遵守微信平台服务条款与著作权法，
不要用于批量抓取或商业转载。</p>
"""


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
        #: 本篇文章的标题（解析时由 worker 取出），用来给下载文件夹命名
        self._article_title = ""
        #: 最近一次真正落盘的目录，「打开图库」优先打开它
        self._last_save_folder = ""
        #: 是否正在「浏览历史图库」——此时展示的图已经在本地，下载按钮要禁用，
        #: 否则再点一次下载只会在图库里堆出一份同名副本（用户以为在重新下载）
        self._browsing_history = False
        #: 历史页当前渲染的记录（双击行要用它反查 entry，行号与列表一一对应）
        self._history_rows: list = []
        self.settings = settings

        # 版本检查：后台线程跑，结果存下来给对话框渲染
        self._update_thread: _UpdateCheckThread | None = None
        self._last_update_info: UpdateInfo | None = None

        # 右键屏蔽回调
        ThumbnailItem.on_block_size = self._on_block_size

        self._build_ui()
        self._apply_style()
        self._restore_settings()
        self._refresh_history()
        self._refresh_blocked()

        # 启动后自动查一次新版本。延迟 2 秒：先让首屏起来，别跟用户抢那一下
        QTimer.singleShot(2000, self._maybe_silent_update_check)

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

        # 「有新版」提示：默认隐藏，只有后台检查发现新版本才亮起来，点击直接看更新内容
        self.update_hint = QLabel("")
        self.update_hint.setObjectName("updateHint")
        self.update_hint.setAlignment(Qt.AlignCenter)
        self.update_hint.setWordWrap(True)
        self.update_hint.setCursor(Qt.PointingHandCursor)
        self.update_hint.setStyleSheet(
            "color: #fa8c16; font-size: 10px; font-weight: bold;"
            "border: 1px solid #ffd591; border-radius: 4px; padding: 2px;"
        )
        self.update_hint.mousePressEvent = lambda e: self._show_update_dialog()
        self.update_hint.hide()
        layout.addWidget(self.update_hint)

        # 版本号即「关于」入口：侧边栏没有菜单栏，这里是最自然的位置。
        # 没有版本号时仍保留入口（显示「关于」），免得版本号读不到就没有关于可看。
        self.version_label = QLabel(f"v{VERSION}" if VERSION else "关于")
        self.version_label.setObjectName("versionLabel")
        self.version_label.setAlignment(Qt.AlignCenter)
        self.version_label.setStyleSheet("color: #bbb; font-size: 10px;")
        self.version_label.setCursor(Qt.PointingHandCursor)
        self.version_label.setToolTip("关于 ImgSnag · 检查更新")
        self.version_label.mousePressEvent = lambda e: self._show_about()
        layout.addWidget(self.version_label)
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
        self.fmt_cb.setMinimumHeight(28); self.fmt_cb.setStyleSheet("font-size: 12px; padding: 2px 4px;")
        action_row.addWidget(self.fmt_cb)

        # 「打开图库」「另存到…」摆在下载按钮左边（用户指定：最右是下载，往左依次另存、图库）。
        # 用紧凑样式压宽度：动作栏是整窗最小宽度的瓶颈，这里每一点 padding 都是
        # 小屏（笔记本半屏约 680px）能不能放下的问题
        ghost_style = (
            "QPushButton { font-size: 12px; color: #555; border: 1px solid #d9d9d9;"
            " border-radius: 6px; background: #fff; padding: 3px 7px; }"
            "QPushButton:hover { border-color: #1677ff; color: #1677ff; }"
        )
        self.open_lib_btn = QPushButton("打开图库")
        self.open_lib_btn.setMinimumHeight(36)
        self.open_lib_btn.setToolTip("打开下载图库目录，看看图都存在哪")
        self.open_lib_btn.setStyleSheet(ghost_style)
        self.open_lib_btn.clicked.connect(self._on_open_library)
        action_row.addWidget(self.open_lib_btn)

        self.save_as_btn = QPushButton("另存到…")
        self.save_as_btn.setMinimumHeight(36)
        self.save_as_btn.setToolTip("这一次放到别的位置（仍会在其中新建一个专属子文件夹）")
        self.save_as_btn.setStyleSheet(ghost_style)
        self.save_as_btn.clicked.connect(self._on_download_as)
        action_row.addWidget(self.save_as_btn)

        self.download_btn = QPushButton("下载选中图片")
        self.download_btn.setEnabled(False); self.download_btn.setMinimumHeight(36)
        self.download_btn.setStyleSheet(
            "QPushButton { background: #1677ff; color: white; border: none; border-radius: 6px; padding: 6px 12px; font-size: 14px; font-weight: bold; }"
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
        self.history_table.setColumnWidth(5, 275)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # 双击整行 = 「查看」的快捷方式（表格本身只读，不会误入编辑态）
        self.history_table.cellDoubleClicked.connect(self._on_history_row_double_clicked)
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
        self._browsing_history = False   # 开始新解析 = 离开历史浏览（下载按钮恢复）
        self._article_title = ""      # 新的一次解析，标题重新取
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
        # 标题由 worker 在 run() 里取出。走到这里 all_done 已经发出来了，
        # run() 早已写完该属性，不存在读到半截的竞态。
        self._article_title = (getattr(self.worker, "article_title", "") or "").strip()
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
        if self._browsing_history:
            # 只读浏览：图已经在本地了。禁用而不是隐藏 —— 按钮突然消失会让人以为
            # 「下载功能坏了」，留着并写清原因才说得过去
            self.download_btn.setEnabled(False)
            self.download_btn.setText("已在本地（历史图库）")
            self.download_btn.setToolTip(
                "这是历史图库里的图片，已经存在本地，不需要再下载。\n"
                "要复制一份到别处，用左边的「另存到…」。"
            )
        else:
            self.download_btn.setEnabled(count > 0)
            self.download_btn.setText(f"下载选中图片 ({count})")
            self.download_btn.setToolTip("")
        # 全选按钮的文案跟着勾选状态走，省掉一个「取消全选」按钮
        selectable = self._selectable_items()
        all_on = bool(selectable) and all(it.checkbox.isChecked() for it in selectable)
        self.select_all_btn.setText("取消全选" if all_on else "全选")

    def _last_save_dir(self) -> str:
        """上次保存目录；已经不在了就回空串（交给系统默认位置，别弹个不存在的路径）"""
        d = self.settings.get(K_LAST_SAVE_DIR)
        return d if d and os.path.isdir(d) else ""

    def _library_root(self) -> str:
        """图库根目录：设置里指定的；没指定就用系统「图片」下的 ImgSnagWeChat。

        默认特意**不放在「下载」里** —— 那是最常被顺手清空的目录，图集躺在里面
        迟早跟着一起被删，历史里的「打开」也就断了。图库独立出来之后，
        只要你自己不删，历史就一直打得开。
        """
        configured = (self.settings.get(K_LIBRARY_DIR) or "").strip()
        if configured:
            return configured
        base = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        if not base:                                    # 极端环境下取不到「图片」位置
            base = os.path.expanduser("~")
        return os.path.join(base, "ImgSnagWeChat")

    def _make_batch_folder(self, parent: str) -> str:
        """在 parent 下建一个「本次专属子文件夹」，返回其路径。

        名字 = 日期_时分秒_文章标题（见 naming 模块）；同一秒重名会自动加 _2。
        建不出来（盘符没了、无写权限）会抛 OSError，由调用方决定怎么退。
        """
        os.makedirs(parent, exist_ok=True)
        folder = unique_dir(parent, folder_name_for(parent, self._article_title))
        os.makedirs(folder, exist_ok=True)
        return folder

    def _on_download(self):
        """下载：直接落进「图库／本次专属子文件夹」，不再弹文件夹选择框。

        以前每下都要选一次目录，而且图是平铺命名直接扔进去的：一直存「下载」的话，
        第二篇文章的 img_01 就变成 img_01_1，越用越乱，也看不出哪张属于哪一篇文章。
        """
        # 浏览历史图库时按钮已被禁用，这里再挡一道：万一有别的路径调进来，
        # 也别在图库里堆一份同名副本（用户以为在「重新下载一遍」）
        if self._browsing_history:
            self.status.showMessage(
                "这是历史图库里的图片，已经存在本地；要复制到别处请用「另存到…」"
            )
            return
        try:
            folder = self._make_batch_folder(self._library_root())
        except OSError as e:
            # 图库建不出来（盘符不在了、没写权限、路径被删）→ 退回让用户自己挑，
            # 而不是直接报错收场 —— 至少还能把这次下完
            self.status.showMessage(f"图库目录不可用（{e}），请手动选择保存位置")
            self._on_download_legacy_pick()
            return
        self._start_save(folder)

    def _on_download_as(self):
        """「另存到…」：这一次放到别的位置，仍会在其中新建一个专属子文件夹"""
        # 按钮在保存期间仍然可见可点，这里必须挡一下：同时开两个落盘线程
        # 会互相覆盖 self.save_worker，进度回调全乱
        if self.save_worker is not None and self.save_worker.isRunning():
            self.status.showMessage("正在保存中，等这一批完成再另存…")
            return
        parent = QFileDialog.getExistingDirectory(
            self, "另存到（会在其中新建一个专属子文件夹）",
            self._last_save_dir() or self._library_root()
        )
        if not parent:
            return
        if parent != self.settings.get(K_LAST_SAVE_DIR):
            self.settings.set(K_LAST_SAVE_DIR, parent)
            self.settings.sync()
        try:
            folder = self._make_batch_folder(parent)
        except OSError as e:
            QMessageBox.warning(self, "无法保存", f"这个位置写不进去：\n{e}")
            return
        self._start_save(folder)

    def _on_download_legacy_pick(self):
        """兜底：图库不可用时，退回「选一个目录直接把图放进去」（不建子文件夹）"""
        folder = QFileDialog.getExistingDirectory(self, "选择保存目录", self._last_save_dir())
        if not folder:
            return
        if folder != self.settings.get(K_LAST_SAVE_DIR):
            self.settings.set(K_LAST_SAVE_DIR, folder)
            self.settings.sync()
        self._start_save(folder)

    def _start_save(self, folder: str):
        """目录已定，这里只负责把选中项快照给落盘线程 + 刷新界面"""
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
        self._last_save_folder = folder

        self.download_btn.setEnabled(False)
        self.download_btn.setText(f"保存中 0/{len(targets)}")
        self.info_label.setText(f"正在保存 {len(targets)} 张...")
        self.status.showMessage(f"正在保存到 {folder} ...")

        self.save_worker = SaveWorker(targets, folder, fmt)
        self.save_worker.progress.connect(self._on_save_progress)
        self.save_worker.done.connect(lambda s, f, e: self._on_save_done(s, f, e, folder))
        self.save_worker.failed.connect(self._on_save_failed)
        self.save_worker.start()

    def _on_open_library(self):
        """打开图库目录：优先最近一次真正落盘的那个子文件夹"""
        target = self._last_save_folder or ""
        if not os.path.isdir(target):
            target = self._library_root()
            try:
                os.makedirs(target, exist_ok=True)
            except OSError as e:
                QMessageBox.warning(self, "打不开图库", f"图库目录不可用：\n{e}")
                return
        self._open_path(target)

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
        if self._update_thread is not None and self._update_thread.isRunning():
            self._update_thread.wait(3000)   # 版本检查只读，等它回来，免得线程被销毁
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
            self._history_rows = []
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
        self._history_rows = list(entries)      # 双击行时按行号反查
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

            # 路径列只显示「那一次的子文件夹名」——日期_时分秒_标题本身就是最好的标识，
            # 完整路径放 tooltip；文件夹不在了就直接标出来（下面「打开」按钮也会置灰）
            gone = bool(entry.save_path) and not os.path.isdir(entry.save_path)
            if entry.save_path:
                shown = os.path.basename(entry.save_path.rstrip("\\/")) or entry.save_path
            else:
                shown = "-"
            if gone:
                shown += "（本地已删除）"
            path_item = QTableWidgetItem(shown[:80])
            path_item.setToolTip(
                f"本地文件夹已不存在：{entry.save_path}" if gone else entry.save_path
            )
            if gone:
                path_item.setForeground(QColor("#999"))
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

            # 「查看」放最前：翻看已下载的图是最常用的动作（双击整行同效）
            view_btn = QPushButton("查看")
            view_btn.setFixedSize(60, 26)
            view_btn.setStyleSheet(
                "font-size: 11px; color: #1677ff; border: 1px solid #1677ff; border-radius: 4px; background: #fff;"
            )
            view_btn.clicked.connect(lambda checked, e=entry: self._on_browse_batch(e))
            view_btn.setEnabled(bool(entry.save_path) and not gone)
            view_btn.setToolTip("本地文件夹已不存在（可能被清理或移动）" if gone
                                else "在这个软件里直接翻看这一批图片（不用跳资源管理器）")
            btn_layout.addWidget(view_btn)

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
            # 本地文件夹已被清理/搬走时别给个点了没反应的按钮 —— 置灰并说明原因
            open_btn.setEnabled(not gone)
            open_btn.setToolTip("本地文件夹已不存在（可能被清理或移动）" if gone
                                else "打开这一批图片所在的文件夹")
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

    def _on_history_row_double_clicked(self, row, _col):
        """双击历史行 = 点「查看」（行号与 _history_rows 一一对应）"""
        if 0 <= row < len(self._history_rows):
            self._on_browse_batch(self._history_rows[row])

    def _reset_for_browse(self):
        """进入「浏览历史图库」：清掉上一屏，并把界面切到只读浏览状态。

        与 _reset_before_parse 的区别：解析是「准备从网络拉图」，会把按钮切成
        「取消解析」并把输入框锁住；浏览是看已经在磁盘上的图，界面必须保持可用
        （排序、筛选、另存到都能用），只是不能再「下载」。
        """
        self._clear_thumbnails()
        self.preview.clear()
        self.images = []
        self._preview_index = -1
        self._browsing_history = True
        self.progress.setVisible(False)
        self.progress.setValue(0)
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("解析图片")
        self.url_input.setEnabled(True)
        self.paste_btn.setEnabled(True)
        self.sort_cb.setEnabled(True)
        self.sort_cb.blockSignals(True)
        self.sort_cb.setCurrentIndex(0)
        self.sort_cb.blockSignals(False)
        self._update_download_btn()
        self._update_hidden_hint()

    def _on_browse_batch(self, entry: HistoryEntry):
        """在软件里打开历史批次：把磁盘上的图读回来，进解析页翻看。

        为什么是只读：这些图**已经在本地**了。要是还允许「下载」，用户以为在
        「重新下载一遍」，实际是在图库里又堆一份同名副本（同名自动加 _1/_2）。
        要复制到别处是另一个需求，交给「另存到…」。
        """
        folder = entry.save_path or ""
        if not os.path.isdir(folder):
            self.status.showMessage(f"本地文件夹已不存在：{folder or '（未记录路径）'}")
            return

        files = scan_batch(folder)
        if not files:
            QMessageBox.information(
                self, "这里没有图片",
                f"{folder}\n\n这个文件夹里没有能显示的图片文件"
                f"（支持 jpg / png / webp / gif / bmp）。"
            )
            return

        self._reset_for_browse()
        # 标题参与「另存到…」的新文件夹命名，所以剥掉日期前缀，别叠成两层
        self._article_title = title_from_folder(folder)
        if entry.source_url:
            self.url_input.setPlainText(entry.source_url)
        self._switch_page(0)

        loaded = unreadable = oversize = 0
        total_bytes = 0
        for f in files:
            if total_bytes + f.size > MAX_BATCH_BYTES:
                oversize += 1
                continue
            try:
                with open(f.path, "rb") as fp:
                    data = fp.read()
            except OSError:
                unreadable += 1
                continue
            if not data:
                unreadable += 1
                continue
            # 宽高只读文件头（不解码整张）：历史批次里可能是几十张大图，
            # 为了拿尺寸把它们全解码一遍纯属浪费
            size = QImageReader(f.path).size()
            w, h = size.width(), size.height()
            if w <= 0 or h <= 0:
                img = QImage.fromData(data)     # 极端格式退一步，真解不出来就当坏文件
                if img.isNull():
                    unreadable += 1
                    continue
                w, h = img.width(), img.height()

            info = ImageInfo(url=f.path, index=loaded, data=data, ext=f.ext,
                             width=w, height=h, name_hint=f.name)
            self._on_image_loaded(loaded, info)
            self.images.append(info)
            loaded += 1
            total_bytes += len(data)

        self.info_label.setText(f"历史图库 · {loaded} 张（{total_bytes / 1048576:.1f} MB）")
        notes = [f"已打开历史图库：{os.path.basename(folder.rstrip(chr(92) + '/'))}"]
        if unreadable:
            notes.append(f"{unreadable} 个文件读不出，已跳过")
        if oversize:
            notes.append(f"{oversize} 张超出单次浏览上限未载入")
        notes.append("这些图已在本地，不会重复下载；要复制到别处用「另存到…」")
        self.status.showMessage("，".join(notes))

    def _on_reparse(self, entry: HistoryEntry):
        """再次解析：填入URL、切回解析页并**直接开解析**（不用再按一次「解析图片」）"""
        if not entry.source_url:
            return
        self.url_input.setPlainText(entry.source_url)
        self._switch_page(0)
        # 已有解析在跑时不抢：_on_parse 的同按钮语义是「取消」，直接调反而会打断
        if self.worker is not None and self.worker.isRunning():
            self.status.showMessage("已有解析在进行，链接已填好，完成后可再点「解析」")
            return
        self._on_parse()

    @staticmethod
    def _open_path(path: str):
        """在系统文件管理器里打开一个目录（跨平台）"""
        if not path:
            return
        if sys.platform == 'win32':
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.run(['open', path])
        else:
            subprocess.run(['xdg-open', path])

    def _on_open_folder(self, entry: HistoryEntry):
        path = entry.save_path or ""
        if not os.path.isdir(path):
            # 本地文件被清理或搬走是常事（这正是把图库移出「下载」的原因），
            # 但要让用户知道是「文件没了」，而不是「按钮坏了」
            self.status.showMessage(f"本地文件夹已不存在：{path or '（未记录路径）'}")
            return
        self._open_path(path)

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

    # ================================================================
    #  关于 / 版本检查
    # ================================================================

    def _show_about(self):
        """关于对话框 —— 侧边栏点版本号进来。

        只放用户真会看的内容：这是什么、我的数据在哪、怎么反馈、依赖了谁的代码。
        第三方依赖许可如实列出；本项目仓库未附 LICENSE 文件，所以这里不声称
        任何开源许可（写了才是造假）。
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("关于 ImgSnag")
        dlg.setMinimumSize(540, 480)
        box = QVBoxLayout(dlg)
        box.setContentsMargins(20, 16, 20, 14)
        box.setSpacing(10)

        head = QLabel(
            "<div style='font-size:17px; font-weight:bold;'>ImgSnag · 微信公众号版"
            f"<span style='font-size:12px; font-weight:normal; color:#888;'>"
            f"  v{VERSION or '未知'}</span></div>"
            "<div style='font-size:12px; color:#666;'>一键提取公众号文章正文图片，"
            "批量下载无水印原图</div>"
        )
        head.setTextFormat(Qt.RichText)
        box.addWidget(head)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(ABOUT_HTML)
        box.addWidget(browser, 1)

        auto_cb = QCheckBox("启动后自动检查新版本（最多 6 小时查一次）")
        auto_cb.setToolTip("关掉之后程序不再主动联网（仍可随时点「检查更新」手动查一次）")
        auto_cb.setChecked(bool(self.settings.get(K_AUTO_CHECK_UPDATE)))
        auto_cb.toggled.connect(lambda v: self.settings.set(K_AUTO_CHECK_UPDATE, bool(v)))
        box.addWidget(auto_cb)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        check_btn = QPushButton("检查更新")
        check_btn.setMinimumHeight(30)

        def _on_check():
            dlg.accept()                          # 先关掉关于，结果才有地方显示
            self._check_updates(silent=False)

        check_btn.clicked.connect(_on_check)
        btn_row.addWidget(check_btn)

        close_btn = QPushButton("关闭")
        close_btn.setMinimumHeight(30)
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        box.addLayout(btn_row)

        dlg.exec()

    def _maybe_silent_update_check(self):
        """启动后的自动检查（可在「关于」里关掉）。

        带 6 小时节流：每次启动都查的话，用户反复开关软件就会把 GitHub 未认证
        请求的配额（60 次/小时/IP）耗掉，之后所有检查都变成 403。
        """
        if not bool(self.settings.get(K_AUTO_CHECK_UPDATE)):
            return
        last = self.settings.get(K_LAST_UPDATE_CHECK)
        try:
            last = int(last)
        except (TypeError, ValueError):
            last = 0
        if last > 0 and time.time() - last < CHECK_INTERVAL:
            return
        self._check_updates(silent=True)

    def _check_updates(self, silent: bool = False):
        """检查新版本。silent=True 表示启动时的自动检查（不弹窗，只亮提示）。"""
        if self._update_thread is not None and self._update_thread.isRunning():
            return                                # 上一次还没回来，别叠请求
        if not silent:
            self.status.showMessage("正在检查新版本…")
        self._update_thread = _UpdateCheckThread(self)
        self._update_thread.checked.connect(
            lambda info: self._on_update_checked(info, silent)
        )
        self._update_thread.start()

    def _on_update_checked(self, info: UpdateInfo, silent: bool = False):
        """版本检查回调（主线程执行）。

        三条分支：问不通 / 有新版本 / 已是最新。**静默检查一律不弹窗** ——
        用户没点，弹窗就是打扰；发现新版本只在侧边栏亮一个提示。
        """
        self.settings.set(K_LAST_UPDATE_CHECK, int(time.time()))
        self._last_update_info = info

        if not info.ok:
            if silent:
                return
            self.status.showMessage("检查更新失败", 6000)
            QMessageBox.warning(self, "检查更新", info.error or "检查失败，请稍后再试")
            return

        if info.has_update:
            self._show_update_hint(info.latest)
            self.status.showMessage(f"发现新版本 v{info.latest}", 8000)
            if not silent:
                self._show_update_dialog()
            return

        self._clear_update_hint()
        if silent:
            return
        msg = f"当前已是最新版本 v{info.current}"
        if info.tag:
            msg += f"\n\nGitHub 最新发布：{info.tag}"
        if info.version_mismatch:
            msg += ("\n\n⚠ 这个 Release 的文件名版本号和 tag 对不上，可能是发版时写错了"
                    " —— 更新检查只认 tag，请去 Releases 页核对。")
        QMessageBox.information(self, "检查更新", msg)

    def _show_update_hint(self, latest: str):
        self.update_hint.setText(f"⬆ v{latest}")
        self.update_hint.setToolTip(f"发现新版本 v{latest} —— 点击查看更新内容")
        self.update_hint.show()

    def _clear_update_hint(self):
        self.update_hint.hide()
        self.update_hint.setText("")

    def _show_update_dialog(self):
        """发现新版本后的对话框（数据取自最近一次检查结果）。

        只给「前往下载」而不内置下载：本项目的安装包与便携包是两种用法
        （安装 vs 解压），自动挑一个下反而容易挑错形态 —— 去 Release 页
        让用户自己选更合适。
        """
        info = self._last_update_info
        if info is None or not info.has_update:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("发现新版本")
        dlg.setMinimumSize(540, 420)
        box = QVBoxLayout(dlg)
        box.setContentsMargins(20, 16, 20, 14)
        box.setSpacing(10)

        extra = f"　·　本次发布：{info.assets_text()}" if info.assets else ""
        head = QLabel(
            f"<div style='font-size:16px; font-weight:bold;'>发现新版本 v{info.latest}</div>"
            f"<div style='font-size:12px; color:#666;'>当前版本 v{info.current}{extra}</div>"
        )
        head.setTextFormat(Qt.RichText)
        box.addWidget(head)

        if info.version_mismatch:
            warn = QLabel("⚠ 这个 Release 的文件名版本号和 tag 不一致，下载前留意一下")
            warn.setStyleSheet("color:#fa8c16; font-size:11px;")
            box.addWidget(warn)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(info.notes.strip() or "（这次发布没有写更新说明）")
        box.addWidget(notes, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        later_btn = QPushButton("稍后")
        later_btn.setMinimumHeight(32)
        later_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(later_btn)

        go_btn = QPushButton("前往下载")
        go_btn.setMinimumHeight(32)
        go_btn.setStyleSheet(
            "QPushButton { background: #1677ff; color: white; border: none;"
            " border-radius: 6px; padding: 6px 18px; font-weight: bold; }"
            "QPushButton:hover { background: #4096ff; }"
        )

        def _open_page():
            QDesktopServices.openUrl(QUrl(info.page_url))
            dlg.accept()

        go_btn.clicked.connect(_open_page)
        btn_row.addWidget(go_btn)
        box.addLayout(btn_row)

        dlg.exec()
