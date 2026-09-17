"""
UI 组件：流式布局 / 图片卡片 / 侧边栏按钮 / 大图预览 / 缩略图条
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel,
    QFrame, QLayout, QSizePolicy, QPushButton, QScrollArea,
    QMenu,
)
from PySide6.QtCore import Qt, QPoint, QRect, QSize, Signal
from PySide6.QtGui import QPixmap, QImage, QFont, QAction

from .worker import ImageInfo


SIDEBAR_WIDTH = 72
THUMB_HEIGHT = 100


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
    """缩略图条中的一个子项：小预览图 + 勾选框 + 文件名"""
    clicked = Signal(object)  # info

    # 类级回调：右键菜单操作时调用，由 app.py 设置
    on_block_size = None  # callable(info)

    def __init__(self, info: ImageInfo):
        super().__init__()
        self.info = info
        self._highlight = False
        self.filtered_out = False  # 是否被「过滤小图」等逻辑隐藏（用来计数，避免依赖 isVisible 的布局时序）
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(THUMB_HEIGHT + 60, THUMB_HEIGHT + 52)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # 预览图
        self.thumb_label = QLabel()
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setFixedSize(THUMB_HEIGHT, THUMB_HEIGHT)
        self.set_thumbnail(info.data)
        layout.addWidget(self.thumb_label)

        # 勾选框
        name = f"img_{info.index + 1:02d}{info.ext}"
        self.checkbox = QCheckBox(name)
        self.checkbox.setChecked(not info.is_duplicate)
        self.checkbox.setStyleSheet("font-size: 10px;")
        self.checkbox.toggled.connect(lambda: self._update_style())
        layout.addWidget(self.checkbox)

        # 尺寸信息
        size_text = f"{info.width}x{info.height}"
        dim_label = QLabel(size_text)
        dim_label.setStyleSheet("color: #666; font-size: 9px;")
        dim_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(dim_label)

        kb_text = f"{len(info.data) / 1024:.0f}KB"
        kb_label = QLabel(kb_text)
        kb_label.setStyleSheet("color: #aaa; font-size: 8px;")
        kb_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(kb_label)

        self._update_style()

    def _update_style(self):
        """根据选中状态 + 过滤状态更新边框样式"""
        if self.info.is_mini_square:
            base = "border: 1px solid #d9d9d9; border-radius: 4px;"
        elif self.info.is_duplicate:
            base = "border: 1px solid #fff3cd; border-radius: 4px;"
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
    """大图预览区域"""
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QScrollArea { border: 1px solid #e0e0e0; border-radius: 8px; background: #f5f5f5; }"
        )

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(400, 300)
        self.setWidget(self.preview_label)

    def show_image(self, data: bytes):
        if not data:
            self.preview_label.setText("无预览")
            self.preview_label.setStyleSheet("color: #999; font-size: 14px;")
            return
        img = QImage()
        img.loadFromData(data)
        if img.isNull():
            self.preview_label.setText("无法加载图片")
            return
        pixmap = QPixmap.fromImage(img)
        # 缩放到可用宽度
        avail_w = self.viewport().width() - 20
        avail_h = self.viewport().height() - 20
        if avail_w > 0 and avail_h > 0:
            scaled = pixmap.scaled(
                avail_w, avail_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        else:
            scaled = pixmap
        self.preview_label.setPixmap(scaled)
        self.preview_label.setStyleSheet("")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小变化时重新缩放预览图
        if self.preview_label.pixmap() and not self.preview_label.pixmap().isNull():
            avail_w = self.viewport().width() - 20
            avail_h = self.viewport().height() - 20
            if avail_w > 0 and avail_h > 0:
                scaled = self.preview_label.pixmap().scaled(
                    avail_w, avail_h,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                self.preview_label.setPixmap(scaled)

    def clear(self):
        self.preview_label.clear()
        self.preview_label.setText("加载中...")


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
