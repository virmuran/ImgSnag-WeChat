"""
UI 组件：流式布局 / 图片卡片 / 侧边栏按钮 / 大图预览 / 缩略图条
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel,
    QFrame, QLayout, QSizePolicy, QPushButton, QScrollArea,
    QMenu,
)
from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QSize, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage, QFont, QAction

from .worker import ImageInfo


SIDEBAR_WIDTH = 72
THUMB_HEIGHT = 100
#: 卡片总高 = 缩略图 + 勾选框 + 信息行 + 内边距（比原来的三行小字矮 6px，一屏多显示一张）
THUMB_CARD_H = THUMB_HEIGHT + 46


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, h_spacing=12, v_spacing=12):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items = []

    def __del__(self):
        while self._items:
            self._items.pop(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_space
            if next_x - self._h_space > rect.right() and line_height > 0:
                x, y, line_height = rect.x(), y + line_height + self._v_space, 0
                next_x = x + hint.width() + self._h_space
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x, line_height = next_x, max(line_height, hint.height())
        return y + line_height - rect.y()


class ThumbnailItem(QFrame):
    """缩略图条中的一个子项：小预览图 + 勾选框 + 一行信息

    信息行从原来的三行小字（文件名 10px / 尺寸 9px / 体积 8px）并成两行：
    勾选框写文件名，下面一行写「宽×高 · 体积」。字号整体调大一档。
    """
    clicked = Signal(object)  # info

    # 类级回调：右键菜单操作时调用，由 app.py 设置
    on_block_size = None  # callable(info)

    def __init__(self, info: ImageInfo):
        super().__init__()
        self.info = info
        self._highlight = False
        self.filtered_out = False  # 是否被「过滤小图」等逻辑隐藏（用来计数，避免依赖 isVisible 的布局时序）
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(THUMB_HEIGHT + 60, THUMB_CARD_H)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # 预览图（角标挂它身上，跟着缩略图走）
        self.thumb_label = QLabel()
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setFixedSize(THUMB_HEIGHT, THUMB_HEIGHT)
        self.set_thumbnail(info.data)
        layout.addWidget(self.thumb_label)

        self.badge = QLabel("", self.thumb_label)   # 「重复」/「小图」角标
        self.badge.setStyleSheet(
            "background: #fff3cd; color: #ad6800; border-radius: 3px;"
            "font-size: 9px; padding: 0 3px;"
        )
        self.badge.hide()

        # 勾选框（文件名）
        name = f"img_{info.index + 1:02d}{info.ext}"
        self.checkbox = QCheckBox(name)
        self.checkbox.setChecked(not info.is_duplicate)
        self.checkbox.setStyleSheet("font-size: 11px;")
        self.checkbox.toggled.connect(lambda: self._update_style())
        layout.addWidget(self.checkbox)

        # 分辨率 + 体积（合成一行）
        self.info_label = QLabel(self._info_text())
        self.info_label.setStyleSheet("color: #888; font-size: 10px;")
        self.info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.info_label)

        self.setToolTip(self._tooltip_text())
        self._update_style()
        self._update_badge()

    # ---------- 文案 ----------

    def _info_text(self):
        kb = len(self.info.data) / 1024
        size = f"{kb / 1024:.1f}MB" if kb >= 1024 else f"{kb:.0f}KB"
        return f"{self.info.width}×{self.info.height} · {size}"

    def _tooltip_text(self):
        src = "原图档 /0" if self.info.used_original else "压缩档 /640（原图取不到，已回退）"
        lines = [
            f"img_{self.info.index + 1:02d}{self.info.ext}",
            f"{self.info.width}×{self.info.height} · {self.info_label.text().split(' · ')[-1]}",
            f"来源：{src}",
        ]
        if self.info.is_duplicate:
            lines.append(f"与第 {self.info.dup_of + 1} 张重复")
        if self.info.is_mini_square:
            lines.append("被「过滤小图与装饰」判定为小图/装饰")
        lines.append(self.info.url)
        return "\n".join(lines)

    def _update_badge(self):
        """角标：重复图淡黄边框几乎看不出，加个文字角标才认得出来"""
        if self.info.is_duplicate:
            text = "重复"
        elif self.info.is_mini_square:
            text = "小图"
        else:
            self.badge.hide()
            return
        self.badge.setText(text)
        self.badge.adjustSize()
        self.badge.move(max(0, self.thumb_label.width() - self.badge.width() - 2), 2)
        self.badge.show()
        self.badge.raise_()

    # ---------- 状态 ----------

    def _update_style(self):
        """根据选中状态 + 过滤状态更新边框样式"""
        if self.info.is_mini_square:
            base = "border: 1px solid #d9d9d9; border-radius: 4px;"
        elif self.info.is_duplicate:
            base = "border: 1px solid #ffe58f; border-radius: 4px;"
        elif self._highlight:
            base = "border: 2px solid #1677ff; border-radius: 4px;"
        else:
            base = "border: 1px solid #e8e8e8; border-radius: 4px;"
        self.setStyleSheet(
            f"ThumbnailItem {{ {base} background: {'#e6f7ff' if self._highlight else '#fff'}; }}"
            f"ThumbnailItem:hover {{ border-color: #1677ff; }}"
        )

    def set_thumbnail(self, data: bytes):
        if not data:
            return
        img = QImage()
        img.loadFromData(data)
        pixmap = QPixmap.fromImage(img)
        scaled = pixmap.scaled(
            THUMB_HEIGHT, THUMB_HEIGHT,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.thumb_label.setPixmap(scaled)

    def set_checked(self, checked: bool):
        self.checkbox.setChecked(checked)

    def set_highlight(self, on: bool):
        self._highlight = on
        self._update_style()

    def mousePressEvent(self, event):
        self.clicked.emit(self.info)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        w, h = self.info.width, self.info.height
        cat = "cover" if w * 2 <= h * 5 and w > 400 else (
            "avatar" if w == h and w <= 400 else "ui"
        )
        action = QAction(f"屏蔽此尺寸 ({w}×{h})", self)
        action.triggered.connect(lambda: self._block_this(cat, (w, h)))
        menu.addAction(action)
        menu.exec_(event.globalPos())

    def _block_this(self, category: str, size: tuple):
        """触发屏蔽回调（由 app.py 处理持久化）"""
        if ThumbnailItem.on_block_size:
            ThumbnailItem.on_block_size(self.info, category, size)

    def set_visible_state(self, visible: bool):
        self.filtered_out = not visible
        self.setVisible(visible)
        if not visible:
            self.set_checked(False)


class ImageViewer(QScrollArea):
    """大图预览区域

    画质要点：始终保留**原始像素**（self._source），每次缩放都从原图算。
    旧实现是在 resizeEvent 里对 `preview_label.pixmap()`（已经缩放过的结果）
    再缩放，拖动窗口几次就逐级糊掉，而且再也回不去。

    缩放：默认「适应窗口」（图比视口大就缩小、比视口小就按原始尺寸显示，不放大）。
    Ctrl+滚轮 / Ctrl+= / Ctrl+- 可自由缩放，双击在「适应窗口」和 100% 之间切换，
    右下角常驻显示当前比例 —— 判断下载的到底是不是真原图，靠的就是能看 100%。
    """
    ZOOM_MIN = 0.05
    ZOOM_MAX = 8.0
    ZOOM_STEP = 1.25
    #: 单次渲染的像素上限。8 倍看着爽，但套在 2560×3413 的图上就是 5.6 亿像素、
    #: 一个 QPixmap 要吞 2GB 内存，会把程序直接撑爆 —— 所以按这张图的实际尺寸
    #: 反推一个安全的倍率上限（100% 永远保留，看原图像素是核心用途）。
    MAX_RENDER_PIXELS = 36_000_000

    def __init__(self):
        super().__init__()
        # 缩放后要靠滚动条看全图，所以不能再强制子控件铺满视口
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QScrollArea { border: 1px solid #e0e0e0; border-radius: 8px; background: #f5f5f5; }"
        )

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(1, 1)
        self.setWidget(self.preview_label)

        #: 原始像素，只在这里保存一份，缩放不污染它
        self._source = QPixmap()
        #: True = 适应窗口；False = 按 _zoom 的固定倍率
        self._fit = True
        self._zoom = 1.0

        # 缩放比例指示器：挂在视口上（不随图片滚动），右下角常驻
        self.zoom_label = QLabel(self.viewport())
        self.zoom_label.setStyleSheet(
            "background: rgba(0,0,0,120); color: #fff; border-radius: 4px;"
            "font-size: 11px; padding: 2px 6px;"
        )
        self.zoom_label.hide()

        #: 防抖：拖窗口会连发几十次 resizeEvent，大图逐次重算会卡顿
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(60)
        self._resize_timer.timeout.connect(self._apply_scale)

        # 事件交给子控件（label）接收，所以两个都要装过滤器
        self.preview_label.installEventFilter(self)
        self.viewport().installEventFilter(self)

        self.show_guide()

    # ---------- 对外接口 ----------

    def show_guide(self, text=None):
        """空状态引导（首次打开或还没解析时显示）"""
        self._source = QPixmap()
        self._fit = True
        self._zoom = 1.0
        self.zoom_label.hide()
        self.preview_label.clear()
        self.preview_label.setStyleSheet("color: #b0b0b0; font-size: 15px; line-height: 160%;")
        self.preview_label.setText(text or (
            "①  粘贴公众号文章链接\n"
            "②  点「解析图片」\n"
            "③  右侧勾选，点「下载选中图片」"
        ))
        self._fit_placeholder()

    def show_image(self, data: bytes):
        if not data:
            self._source = QPixmap()
            self.zoom_label.hide()
            self.preview_label.clear()
            self.preview_label.setText("无预览")
            self.preview_label.setStyleSheet("color: #999; font-size: 14px;")
            self._fit_placeholder()
            return
        img = QImage()
        img.loadFromData(data)
        if img.isNull():
            self._source = QPixmap()
            self.zoom_label.hide()
            self.preview_label.clear()
            self.preview_label.setText("无法加载图片")
            self._fit_placeholder()
            return
        self._source = QPixmap.fromImage(img)
        self._fit = True          # 换图回到「适应窗口」，避免上一张的倍率套到新图
        self._zoom = 1.0
        self.preview_label.setStyleSheet("")
        self._apply_scale()

    def clear(self):
        """解析开始的占位状态"""
        self._source = QPixmap()
        self._fit = True
        self._zoom = 1.0
        self.zoom_label.hide()
        self.preview_label.clear()
        self.preview_label.setStyleSheet("color: #999; font-size: 14px;")
        self.preview_label.setText("加载中...")
        self._fit_placeholder()

    # ---------- 缩放 ----------

    def fit_to_window(self):
        self._fit = True
        self._apply_scale()

    def zoom_100(self):
        self.set_zoom(1.0)

    def zoom_in(self):
        self._zoom_by(self.ZOOM_STEP)

    def zoom_out(self):
        self._zoom_by(1 / self.ZOOM_STEP)

    def max_zoom(self) -> float:
        """这张图当前允许的最大倍率（受 MAX_RENDER_PIXELS 限制，至少 1.0）"""
        if self._source.isNull():
            return self.ZOOM_MAX
        px = self._source.width() * self._source.height()
        if px <= 0:
            return self.ZOOM_MAX
        return max(1.0, min(self.ZOOM_MAX, (self.MAX_RENDER_PIXELS / px) ** 0.5))

    def set_zoom(self, zoom: float):
        if self._source.isNull():
            return
        self._zoom = max(self.ZOOM_MIN, min(self.max_zoom(), zoom))
        self._fit = False
        self._apply_scale()

    def toggle_fit(self):
        """双击切换：适应窗口 ↔ 100%"""
        if self._source.isNull():
            return
        if self._fit:
            self.zoom_100()
        else:
            self.fit_to_window()

    def current_zoom(self) -> float:
        """当前实际倍率（适应窗口模式下按实际显示尺寸反推）"""
        if self._source.isNull() or self._source.width() == 0:
            return 1.0
        if self._fit:
            pm = self.preview_label.pixmap()
            return (pm.width() / self._source.width()) if pm and not pm.isNull() else 1.0
        return self._zoom

    def _zoom_by(self, factor):
        if self._source.isNull():
            return
        if self._fit:
            # 从「适应窗口」开始缩放时，先换算成等效倍率，避免画面跳变
            self._zoom = self.current_zoom()
            self._fit = False
        self.set_zoom(self._zoom * factor)

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self._zoom_by(self.ZOOM_STEP if delta > 0 else 1 / self.ZOOM_STEP)
                return True
        elif etype == QEvent.MouseButtonDblClick:
            self.toggle_fit()
            return True
        return super().eventFilter(obj, event)

    # ---------- 渲染 ----------

    def _fit_placeholder(self):
        """无图时让占位文字在预览区居中铺开"""
        vp = self.viewport().size()
        self.preview_label.setFixedSize(max(1, vp.width()), max(1, vp.height()))

    def _apply_scale(self):
        """按当前模式从原图重算一张预览图"""
        if self._source.isNull():
            return
        sw, sh = self._source.width(), self._source.height()
        avail_w = self.viewport().width() - 20
        avail_h = self.viewport().height() - 20
        if avail_w <= 0 or avail_h <= 0:
            return

        if self._fit:
            # 适应窗口：小图不放大（放大只会让原本清晰的图变糊，且没有信息增益）
            target_w = min(avail_w, sw)
            target_h = min(avail_h, sh)
            if target_w >= sw and target_h >= sh:
                pixmap = self._source          # 原始尺寸直接显示，零重采样
            else:
                pixmap = self._source.scaled(
                    target_w, target_h,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
        else:
            # 手动缩放：允许放大到 100% 以上（看像素就得靠这个）
            pixmap = self._source.scaled(
                max(1, round(sw * self._zoom)), max(1, round(sh * self._zoom)),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )

        self.preview_label.setPixmap(pixmap)
        self.preview_label.setFixedSize(pixmap.size())
        self._update_zoom_label()

    def _update_zoom_label(self):
        if self._source.isNull():
            self.zoom_label.hide()
            return
        pct = round(self.current_zoom() * 100)
        self.zoom_label.setText(f"适应窗口 {pct}%" if self._fit else f"{pct}%")
        self.zoom_label.adjustSize()
        self.zoom_label.show()
        self._place_zoom_label()

    def _place_zoom_label(self):
        vp = self.viewport()
        size = self.zoom_label.size()
        self.zoom_label.move(
            max(0, vp.width() - size.width() - 10),
            max(0, vp.height() - size.height() - 8),
        )
        self.zoom_label.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._source.isNull():
            self._fit_placeholder()
            return
        self._place_zoom_label()
        if self._fit:
            self._resize_timer.start()     # 适应窗口才需要重算；固定倍率保持不动
        else:
            self._resize_timer.stop()


class SidebarButton(QPushButton):
    """侧边栏图标按钮"""
    def __init__(self, text, icon_char="", parent=None):
        super().__init__(parent)
        self.setText(f"{icon_char}\n{text}" if icon_char else text)
        self.setCheckable(True)
        self.setFixedSize(SIDEBAR_WIDTH - 12, 56)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("sidebarBtn")
        self.setStyleSheet("""
            QPushButton#sidebarBtn {
                background: transparent; border: none; border-radius: 8px;
                font-size: 12px; color: #999; padding: 4px;
            }
            QPushButton#sidebarBtn:hover { background: #ebebeb; color: #333; }
            QPushButton#sidebarBtn:checked { background: #e6f0ff; color: #1677ff; font-weight: bold; }
        """)
