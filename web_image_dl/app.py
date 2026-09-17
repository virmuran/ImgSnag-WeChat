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
    QHeaderView, QAbstractItemView, QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence, QColor

from .worker import FetchWorker, ImageInfo
from .widgets import FlowLayout, ThumbnailItem, ImageViewer, SidebarButton, SIDEBAR_WIDTH, THUMB_HEIGHT
from .history_manager import history_manager, HistoryEntry
from .blocked_config import blocked_config
from .extractor import is_wechat_url

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
        self._last_parsed_url = ""
        self._current_history_id = None

        # 右键屏蔽回调
        ThumbnailItem.on_block_size = self._on_block_size

        self._build_ui()
        self._apply_style()
        self._refresh_history()

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

        self.btn_parse = SidebarButton("解析", "")
        self.btn_parse.setChecked(True)
        self.btn_parse.clicked.connect(lambda: self._switch_page(0))
        layout.addWidget(self.btn_parse)

        self.btn_history = SidebarButton("历史", "")
        self.btn_history.clicked.connect(lambda: self._switch_page(1))
        layout.addWidget(self.btn_history)

        layout.addStretch()
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

        self.paste_btn = QPushButton("粘贴链接")
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
            "精确匹配已知噪音尺寸（正文图几乎不可能碰巧等于这些固定像素）：\n"
            "  • 微信 UI 装饰：321×192 / 72×170 / 71×170\n"
            "  • 公众号头像：272×272 / 144×144 / 132×132\n"
            "  • 封面横幅：1080×460 / 900×383\n"
            "  • 兜底：最长边 ≤ 200px 或 RGBA PNG < 10KB\n"
            "取消勾选可重新显示"
        )
        self.filter_square_cb.setChecked(True)
        self.filter_square_cb.toggled.connect(self._on_filter_toggled)
        self.filter_square_cb.setStyleSheet("font-size: 12px; color: #666;")
        action_row.addWidget(self.filter_square_cb)

        self.select_all_btn = QPushButton("全选"); self.select_all_btn.setEnabled(False)
        self.select_all_btn.clicked.connect(lambda: self._toggle_all(True))
        action_row.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("取消全选"); self.deselect_all_btn.setEnabled(False)
        self.deselect_all_btn.clicked.connect(lambda: self._toggle_all(False))
        action_row.addWidget(self.deselect_all_btn)
        action_row.addSpacing(20)

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
        self.thumb_strip.addStretch()
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

        return page

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

    def _switch_page(self, index):
        self.stack.setCurrentIndex(index)
        self.btn_parse.setChecked(index == 0)
        self.btn_history.setChecked(index == 1)
        if index == 1:
            self._refresh_history()

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
        self.deselect_all_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
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
        # 插入到 stretch 之前
        self.thumb_strip.insertWidget(self.thumb_strip.count() - 1, item)
        self.thumb_items.append(item)
        # 第一张图自动预览
        if self._preview_index < 0 and not info.is_mini_square and not info.is_duplicate:
            self._preview_index = len(self.thumb_items) - 1
            item.set_highlight(True)
            self.preview.show_image(info.data)
        self._update_download_btn()

    def _on_block_size(self, info, category, size):
        """右键菜单屏蔽此尺寸：写入 JSON + 立即重新过滤当前结果"""
        ok = blocked_config.add_blocked(category, size)
        if not ok:
            return  # 已存在

        w, h = size
        self.status.showMessage(f"已添加屏蔽 {category}: {w}×{h}，刷新中...")
        # 重新过滤当前所有缩略图
        for item in self.thumb_items:
            if (item.info.width, item.info.height) == size:
                item.info.is_mini_square = True
                if self.filter_square_cb.isChecked():
                    item.set_visible_state(False)
            elif item.info.is_mini_square:
                # 重新检查（以防之前被过滤的）
                w2, h2 = item.info.width, item.info.height
                _ui, _av, _cv = blocked_config.get_blocked_sets()
                if (w2, h2) in _ui or (w2, h2) in _av or (w2, h2) in _cv:
                    item.info.is_mini_square = True
                    if self.filter_square_cb.isChecked():
                        item.set_visible_state(False)
        self._update_download_btn()

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

    def _on_all_done(self, images):
        self.images = images
        self._on_parse_done(True)
        self.progress.setVisible(False)
        unique = sum(1 for i in images if not i.is_duplicate)
        dup = sum(1 for i in images if i.is_duplicate)
        text = f"共 {len(images)} 张"
        if unique:
            text += f"（{unique} 张有效"
        if dup:
            text += f"，{dup} 张重复"
        if unique:
            text += "）"
        self.info_label.setText(text)
        self.status.showMessage(text + " — 勾选后点击「下载选中图片」")
        self.select_all_btn.setEnabled(True)
        self.deselect_all_btn.setEnabled(True)
        self.sort_cb.setEnabled(True)
        self._update_download_btn()

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

    def _toggle_all(self, checked):
        for item in self.thumb_items:
            if not item.filtered_out and not item.info.is_duplicate:
                item.set_checked(checked)
        self._update_download_btn()

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
            self.thumb_strip.insertWidget(self.thumb_strip.count() - 1, item)

    def _update_download_btn(self):
        count = sum(1 for item in self.thumb_items if not item.filtered_out and item.checkbox.isChecked())
        self.download_btn.setEnabled(count > 0)
        self.download_btn.setText(f"下载选中图片 ({count})")

    def _on_download(self):
        folder = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not folder:
            return
        fmt_map = {"原格式": "", "JPG": ".jpg", "PNG": ".png", "WebP": ".webp"}
        fmt = fmt_map.get(self.fmt_cb.currentText(), "")
        count = 0
        for item in self.thumb_items:
            if not item.checkbox.isChecked() or item.filtered_out:
                continue
            ext = fmt if fmt else item.info.ext
            filename = f"img_{item.info.index + 1:02d}{ext}"
            filepath = os.path.join(folder, filename)
            if os.path.exists(filepath):
                base, _ = os.path.splitext(filename)
                filepath = os.path.join(folder, f"{base}_{count}{ext}")
            if fmt and fmt != item.info.ext:
                from PySide6.QtGui import QImage
                img = QImage()
                img.loadFromData(item.info.data)
                if not img.save(filepath, fmt.lstrip('.').upper()):
                    filepath = filepath.replace(fmt, item.info.ext)
                    with open(filepath, 'wb') as f:
                        f.write(item.info.data)
            else:
                with open(filepath, 'wb') as f:
                    f.write(item.info.data)
            count += 1

        if self._current_history_id:
            history_manager.update(
                self._current_history_id,
                success_images=count,
                save_path=folder,
                status="done" if count > 0 else "failed"
            )
        self._current_history_id = None
        self._last_parsed_url = ""

        QMessageBox.information(self, "下载完成", f"已保存 {count} 张图片到:\n{folder}")
        self.status.showMessage(f"已保存 {count} 张图片到 {folder}")

    def _clear_thumbnails(self):
        while self.thumb_strip.count() > 1:  # 保留最后的 stretch
            item = self.thumb_strip.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _start_worker(self, source, is_url):
        self._reset_before_parse()
        if is_url:
            self._last_parsed_url = source
        include_scripts = is_url and self.script_cb.isChecked()
        self.worker = FetchWorker(source, is_url, include_scripts)
        self.worker.progress.connect(self._on_progress)
        self.worker.image_loaded.connect(self._on_image_loaded)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_paste_html(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.url_input.setPlainText(text)
            self.status.showMessage(f"已粘贴 {len(text)} 字符 — 点击「解析图片」")
        else:
            self.status.showMessage("剪贴板为空")

    def _on_parse(self):
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

    def _refresh_history(self):
        entries = history_manager.get_all(limit=200)
        self.history_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            url_item = QTableWidgetItem(entry.source_url[:100])
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
