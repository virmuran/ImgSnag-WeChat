"""
UI 组件：流式布局 / 图片卡片 / 侧边栏按钮 / 大图预览 / 缩略图条
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel,
    QFrame, QLayout, QSizePolicy, QPushButton, QScrollArea,
    QMenu, QApplication, QLineEdit, QTextEdit, QPlainTextEdit,
    QAbstractSpinBox,
)
from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QSize, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage, QFont, QAction, QCursor

from .worker import ImageInfo
from .blocked_config import reason_label


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

        # 勾选框（文件名）：历史浏览时用磁盘上的真实文件名，其余按序号拼
        name = getattr(info, 'name_hint', '') or f"img_{info.index + 1:02d}{info.ext}"
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
        reasons = getattr(self.info, "block_reasons", ())
        if reasons:
            # 说清楚「是被哪一条拦下的」—— 否则用户只看到一张图不见了，
            # 想放回来还得去猜是哪条规则（其中一条是他在历史页能撤销的屏蔽）
            lines.append("被「过滤小图与装饰」拦下：")
            lines.extend(f"  · {reason_label(r)}" for r in reasons)
        elif self.info.is_mini_square:
            lines.append("被「过滤小图与装饰」判定为小图/装饰")
        lines.append(self.info.url)
        return "\n".join(lines)

    def _update_badge(self):
        """角标：重复图淡黄边框几乎看不出，加个文字角标才认得出来"""
        reasons = getattr(self.info, "block_reasons", ())
        if self.info.is_duplicate:
            text = "重复"
        elif any(r.startswith("blocked:") for r in reasons):
            text = "已屏蔽"      # 与「小图」区分开：这条是用户自己屏蔽的，可以去历史页撤销
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

    def refresh_filter_state(self, filter_enabled: bool, check_on_show: bool = False):
        """规则变了（例如用户刚撤销一条屏蔽）→ 重新决定这张该隐藏还是显示。

        早期只有「隐藏」这一条路：一旦被拦就再也回不来，工具栏那个开关也只是把它
        重新显示出来、不带任何判定。现在撤销屏蔽后要能原样退回，并且**回退的那张直接
        勾上** —— 用户撤销屏蔽的本意就是「这些我要下」，让他再一张张点一遍是白费力气。
        """
        visible = not (filter_enabled and self.info.is_mini_square)
        self.filtered_out = not visible
        self.setVisible(visible)
        if not visible:
            self.set_checked(False)
        elif check_on_show and not self.info.is_duplicate:
            self.set_checked(True)
        self._update_badge()
        self._update_style()
        self.setToolTip(self._tooltip_text())


class ImageViewer(QScrollArea):
    """大图预览区域

    画质要点：始终保留**原始像素**（self._source），每次缩放都从原图算。
    旧实现是在 resizeEvent 里对 `preview_label.pixmap()`（已经缩放过的结果）
    再缩放，拖动窗口几次就逐级糊掉，而且再也回不去。

    缩放：默认「适应窗口」（图比视口大就缩小、比视口小就按原始尺寸显示，不放大）。
    Ctrl+滚轮 / Ctrl+= / Ctrl+- 可自由缩放，双击在「适应窗口」和 100% 之间切换，
    右下角常驻显示当前比例 —— 判断下载的到底是不是真原图，靠的就是能看 100%。

    缩放锚点：Ctrl+滚轮以**鼠标所在位置**为锚点（鼠标下的那个像素缩放前后钉在原地），
    而不是永远从左上角长出来。做法是把鼠标位置反算成原图坐标，缩放后重设滚动条，
    让那个点在视口里的位置保持不变（见 _center_on）。

    拖动：图片放大到超出视口后，**直接按住左键拖**就行（中键也可以）。按住空格再拖
    属于备选姿势 —— 习惯「先抓手再拖」的人会用，但**不再需要**它。唯一一条几何前提：
    图必须超出视口。整张图都装在窗口里时，往哪边拖都没有被挡住的画面可以露出来，
    所以按下去不会有反应 —— 但会在右下角提示「先放大再拖」，并且告诉用户可以直接拖，
    免得按半天毫无反应让人以为坏了（见 _mouse_press）。

    左键拖动的体验细节：

    - 起步门槛 PAN_START_SLOP：按下后移动不到 4px 就松开，算单击，画面一点都不动 ——
      否则点一下选图会把图带歪一两像素。拖起来之后不回弹，也不会「先歪一下再跟手」。
    - 光标就是提示：只要图超出视口，不按任何键也挂一只张开的手（见 _update_pan_cursor）。
      这是「能直接拖」唯一的可见信号 —— 别再让它依赖按空格。
    - 指针位置用 Qt 自己的 Enter/Leave 状态（under_mouse）判断，不去问系统要屏幕坐标，
      缩放/多屏下更稳。空格键仍挂在应用级过滤器上（见 _handle_space_key）：只装在本
      控件上收不到，焦点通常在别的控件（输入框/按钮）手里。
    """
    ZOOM_MIN = 0.05
    ZOOM_MAX = 8.0
    ZOOM_STEP = 1.25
    #: 左键直接拖动时的起步门槛（像素）：小于它算单击，不算拖动，
    #: 这样点一下选图、双击切 100% 都不会把画面带歪
    PAN_START_SLOP = 4
    #: 单次渲染的像素上限。8 倍看着爽，但套在 2560×3413 的图上就是 5.6 亿像素、
    #: 一个 QPixmap 要吞 2GB 内存，会把程序直接撑爆 —— 所以按这张图的实际尺寸
    #: 反推一个安全的倍率上限（100% 永远保留，看原图像素是核心用途）。
    MAX_RENDER_PIXELS = 36_000_000

    def __init__(self):
        super().__init__()
        # 应用级事件过滤器在构造过程中就会被触发（setStyleSheet / setWidget 都会发事件），
        # 那时 preview_label 还不存在。所以先上闸门，构造完再开。
        self._ready = False
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

        # 拖动的临时状态。_pending_pan 是「左键按下了但还没走够门槛」——先当点击看待，
        # 超过 PAN_START_SLOP 才算拖动（见 _pan_step）。
        self._space_held = False
        self._panning = False
        self._pending_pan = False
        self._pan_origin = QPoint()
        self._pan_start_h = 0
        self._pan_start_v = 0

        # 缩放比例指示器：挂在视口上（不随图片滚动），右下角常驻。
        # 必须让鼠标事件穿过去：它浮在图片上方，挡住的那一小块拖不动。
        self.zoom_label = QLabel(self.viewport())
        self.zoom_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
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

        #: 「没得拖」这类一次性说明借右下角指示器的位置显示，2 秒后自动退回比例
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.setInterval(2200)
        self._hint_timer.timeout.connect(self._update_zoom_label)

        # 事件交给子控件（label）接收，所以两个都要装过滤器
        self.preview_label.installEventFilter(self)
        self.viewport().installEventFilter(self)

        self.show_guide()
        # 空格拖拽必须挂在应用级：焦点通常不在预览区（在输入框/按钮上），
        # 只装在本控件上的话空格事件根本送不过来。过滤器里做了多重条件放行，见 _handle_space_key。
        self._ready = True
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    # ---------- 对外接口 ----------

    def show_guide(self, text=None):
        """空状态引导（首次打开或还没解析时显示）"""
        self._source = QPixmap()
        self._fit = True
        self._zoom = 1.0
        self._end_pan()
        self.zoom_label.hide()
        self.preview_label.clear()
        self.preview_label.setStyleSheet("color: #b0b0b0; font-size: 15px; line-height: 160%;")
        self.preview_label.setText(text or (
            "①  粘贴公众号文章链接\n"
            "②  点「解析图片」\n"
            "③  右侧勾选，点「下载选中图片」"
            "\n\n"
            "预览：Ctrl+滚轮缩放（以鼠标位置为中心）· 双击切 100%\n"
            "放大超出窗口后：直接按住左键拖动（按住空格拖也行）"
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
        self._end_pan()           # 换图时若还按着空格在拖，先收掉
        self.preview_label.setStyleSheet("")
        self._apply_scale()

    def clear(self):
        """解析开始的占位状态"""
        self._source = QPixmap()
        self._fit = True
        self._zoom = 1.0
        self._end_pan()
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

    def set_zoom(self, zoom: float, anchor=None, src_pt=None):
        """设置固定倍率。

        anchor : 视口坐标，缩放围绕它进行（一般传鼠标位置）；None = 视口中心
        src_pt : 预先算好的原图坐标（调用方在改倍率之前算的）。传了就复用，
                 避免在倍率已经变了之后再反算，锚点会偏。
        """
        if self._source.isNull():
            return
        if anchor is None:
            anchor = self._viewport_center()
        if src_pt is None:
            src_pt = self._source_point_at(anchor)
        self._zoom = max(self.ZOOM_MIN, min(self.max_zoom(), zoom))
        self._fit = False
        self._apply_scale()
        if src_pt is not None:
            self._center_on(anchor, src_pt)

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

    # ---------- 缩放锚点：让鼠标下的像素钉在原地 ----------

    def _viewport_center(self) -> QPoint:
        """默认锚点：视口中心（键盘缩放时用）"""
        return QPoint(self.viewport().width() // 2, self.viewport().height() // 2)

    def _viewport_pos(self, event) -> QPoint:
        """事件坐标 → 视口坐标。

        滚轮可能落在 label 上（图占据的地方），也可能落在 viewport 上（周围的留白），
        用全局坐标回推就不用管事件是谁发的了。
        """
        return self.viewport().mapFromGlobal(event.globalPosition().toPoint())

    def _source_point_at(self, vp_pos):
        """视口坐标 → 原图像素坐标。

        比例用 pixmap 的实际显示尺寸反推，而不是 self._zoom：适应窗口模式下显示尺寸
        是算出来的，和 _zoom 并不相等。
        """
        pm = self.preview_label.pixmap()
        sw, sh = self._source.width(), self._source.height()
        if not pm or pm.isNull() or sw <= 0 or sh <= 0:
            return None
        disp_w, disp_h = pm.width(), pm.height()
        if disp_w <= 0 or disp_h <= 0:
            return None
        origin = self.preview_label.mapTo(self.viewport(), QPoint(0, 0))
        return ((vp_pos.x() - origin.x()) / disp_w * sw,
                (vp_pos.y() - origin.y()) / disp_h * sh)

    def _center_on(self, anchor, src_pt):
        """缩放后重设滚动条，把原图中的 src_pt 摆回视口坐标 anchor 处。

        这就是「鼠标在哪就放大哪」的实现：先记下鼠标指着的原图像素，缩放后把这个像素
        重新推到鼠标位置，于是被放大的部分就是鼠标底下的那块，而不是永远从左上角长出来。
        图比视口小时滚动条范围是 0，Qt 会自己居中，锚点自然不起作用——图都装得下了，也不必锚。
        """
        pm = self.preview_label.pixmap()
        if not pm or pm.isNull():
            return
        scale_x = pm.width() / (self._source.width() or 1)
        scale_y = pm.height() / (self._source.height() or 1)
        # anchor == 显示图左上角 + src_pt × 比例  →  左上角 = anchor - src_pt × 比例
        self._set_scroll(-(anchor.x() - src_pt[0] * scale_x),
                         -(anchor.y() - src_pt[1] * scale_y))

    def _set_scroll(self, x, y):
        """设定滚动位置。

        自己先把范围算好再 setValue：setFixedSize 之后 Qt 内部的范围更新不一定在
        这一帧就绪，直接 setValue 会被旧范围夹掉，画面就会跳。
        """
        vp = self.viewport().size()
        content = self.preview_label.size()
        pairs = (
            (self.horizontalScrollBar(), content.width(), vp.width(), x),
            (self.verticalScrollBar(), content.height(), vp.height(), y),
        )
        for bar, length, view, want in pairs:
            bar.setPageStep(view)
            bar.setRange(0, max(0, length - view))
            bar.setValue(max(0, min(bar.maximum(), round(want))))

    def _zoom_by(self, factor, anchor=None):
        if self._source.isNull():
            return
        if anchor is None:
            anchor = self._viewport_center()
        # 锚点必须在改倍率**之前**换算：变成固定倍率后显示尺寸就变了
        src_pt = self._source_point_at(anchor)
        if self._fit:
            # 从「适应窗口」开始缩放时，先换算成等效倍率，避免画面跳变
            self._zoom = self.current_zoom()
            self._fit = False
        self.set_zoom(self._zoom * factor, anchor=anchor, src_pt=src_pt)

    # ---------- 空格拖拽平移 ----------

    def under_mouse(self) -> bool:
        """鼠标是不是正压在预览区上。

        用 Qt 自己维护的 Enter/Leave 状态（`underMouse()`），而不是拿屏幕坐标反算：
        后者要求「系统给的指针坐标」和「控件映射」在同一套坐标系里，缩放/多屏下容易出错。
        滚动条不算 viewport 的一部分，但指针压在滚动条上时也该能空格拖动，
        所以再补一条「落在整个预览区矩形内」的兜底。
        """
        if not self.isVisible():
            return False
        if self.viewport().underMouse() or self.preview_label.underMouse():
            return True
        return self.rect().contains(self.mapFromGlobal(QCursor.pos()))

    @staticmethod
    def _text_input_focused() -> bool:
        return isinstance(QApplication.focusWidget(),
                          (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))

    def _has_overflow(self) -> bool:
        """图片是否已经超出视口 —— 超出才有得拖"""
        if self._source.isNull():
            return False
        vp = self.viewport().size()
        content = self.preview_label.size()
        return content.width() > vp.width() or content.height() > vp.height()

    def _pan_available(self) -> bool:
        """此刻能不能拖：指针压在预览区上 + 图确实超出了视口。

        注意这里**不看键盘焦点**。早先加过「焦点在文本框里就不给拖」的守卫，结果
        用户在链接框里点一下就再也拖不动了，而且毫无反馈 —— 那个守卫本意是别把正在
        输入的空格吃掉，但指针就压在预览区上时，用户显然是想拖图，不是想打字。
        """
        return self.under_mouse() and self._has_overflow()

    def _handle_space_key(self, event) -> bool:
        """空格按下/松开 → 备选的「抓手」姿势。返回 True 表示事件已被吃掉。

        直接左键就能拖之后，空格已经不是必需的了 —— 但习惯「先抓手再拖」的人还会按它，
        所以保留：按住空格期间光标一样是张开的手，松开空格结束拖动。
        """
        if event.key() != Qt.Key_Space:
            return False
        if event.modifiers() not in (Qt.NoModifier, Qt.KeypadModifier):
            return False                      # Shift/Ctrl+空格 留给别人
        if event.type() == QEvent.KeyPress:
            if event.isAutoRepeat():
                return self._space_held
            if self._space_held:
                self._update_pan_cursor()     # 按住空格期间又缩放了，手形光标要跟上
                return True
            if self._source.isNull() or not self.under_mouse():
                return False                  # 指针不在预览区上：这空格是用户在打字
            if not self._has_overflow():
                # 有图，但整张都装在窗口里 —— 没得拖。
                # 这件事必须说出来：否则用户按半天空格毫无反应，只会以为拖动功能坏了。
                if self._text_input_focused():
                    return False              # 正在打字，不抢
                self._flash_hint("整图已全部可见 · 先 Ctrl+滚轮放大再拖")
                self._space_held = True       # 吃掉空格，免得它飘进输入框
                self._update_pan_cursor()
                return True
            # 指针压在预览区上 → 用户是要拖图。焦点在哪个输入框都不影响这个判断
            self._space_held = True
            self._update_pan_cursor()
            return True
        was_held = self._space_held
        self._space_held = False
        self._end_pan()
        return was_held

    def _end_pan(self):
        self._panning = False
        self._pending_pan = False
        self._update_pan_cursor()

    def _update_pan_cursor(self):
        """光标：拖动中握拳；否则只要「有得拖」就挂一只张开的手。

        这里不再要求「按住空格」—— 直接左键就能拖了，光标就是唯一的提示。
        唯一保留的关键是：图超出视口才给手形，装得下时保持箭头（没得拖，别骗人）。
        """
        if self._panning or self._pending_pan:
            shape = Qt.ClosedHandCursor
        elif self._has_overflow():
            shape = Qt.OpenHandCursor
        else:
            shape = None
        for w in (self.viewport(), self.preview_label):
            if shape is None:
                w.unsetCursor()
            else:
                w.setCursor(QCursor(shape))

    def _flash_hint(self, text: str):
        """借右下角比例指示器显示一句一次性说明，2 秒后自动退回比例"""
        if self._source.isNull():
            return
        self.zoom_label.setText(text)
        self.zoom_label.adjustSize()
        self.zoom_label.show()
        self._place_zoom_label()
        self._hint_timer.start()

    def _mouse_press(self, event) -> bool:
        """按下鼠标 → 要不要开始拖？返回 True 表示这次按下归我们处理。"""
        button = event.button()
        if button == Qt.MiddleButton:
            if not self._pan_available():
                return False
            self._begin_pan(event, pending=False)   # 中键拖动：不用按空格，随按随拖
            return True
        if button != Qt.LeftButton:
            return False
        if not self._has_overflow():
            # 图装得下就是没得拖，按下去也不会有反应 —— 直接说出来，别让人反复试。
            # 有图才提示；空状态引导里已经写过一遍了，不必重复。
            if not self._source.isNull() and self._text_input_focused() is False:
                self._flash_hint("整图已全部可见 · 先 Ctrl+滚轮放大再拖")
            return False
        if not self.under_mouse():
            return False
        # 先记下起点，走够 PAN_START_SLOP 才算拖。不能按下就拖：那样双击切 100%
        # 会被两次微小的位移带歪，单击也会让画面跳一两像素。
        self._begin_pan(event, pending=True)
        return True

    def _begin_pan(self, event, pending: bool):
        self._pan_origin = event.globalPosition().toPoint()
        self._pan_start_h = self.horizontalScrollBar().value()
        self._pan_start_v = self.verticalScrollBar().value()
        self._panning = not pending
        self._pending_pan = pending
        self._update_pan_cursor()

    def _pan_step(self, event) -> bool:
        """拖动中：按位移设滚动条。返回 True 表示这次移动被吃掉。"""
        if not (self._panning or self._pending_pan):
            return False
        # 用全局坐标算位移，省得纠结事件是 label 还是 viewport 发的（坐标系不同）
        delta = event.globalPosition().toPoint() - self._pan_origin
        if self._pending_pan:
            if max(abs(delta.x()), abs(delta.y())) < self.PAN_START_SLOP:
                return True                   # 还在门槛内：当作点击，先不动画面
            self._pending_pan = False
            self._panning = True
            self._update_pan_cursor()
        self.horizontalScrollBar().setValue(self._pan_start_h - delta.x())
        self.verticalScrollBar().setValue(self._pan_start_v - delta.y())
        return True

    def eventFilter(self, obj, event):
        if not self._ready:
            return False          # 构造期间不参与，见 __init__ 里的说明
        etype = event.type()

        # 空格键挂的是应用级过滤器，所以任何控件的键盘事件都会经过这里 —— 先做钥匙判断
        if etype == QEvent.KeyPress or etype == QEvent.KeyRelease:
            if self._handle_space_key(event):
                return True
            return super().eventFilter(obj, event)

        if obj is not self.viewport() and obj is not self.preview_label:
            # 窗口失活时收不到 KeyRelease，空格会「卡住」在按下状态，这里兜一下
            if etype == QEvent.WindowDeactivate or etype == QEvent.ApplicationDeactivate:
                self._space_held = False
                self._end_pan()
            return super().eventFilter(obj, event)

        if etype == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                # 以鼠标位置为锚点缩放 —— 「鼠标在哪就放大哪」就是在这一步
                self._zoom_by(self.ZOOM_STEP if delta > 0 else 1 / self.ZOOM_STEP,
                              anchor=self._viewport_pos(event))
            return True                       # Ctrl+滚轮一律吃掉，别漏给外层滚动
        if etype == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
            self.toggle_fit()
            return True
        if etype == QEvent.MouseButtonPress:
            if self._mouse_press(event):
                return True
            # 没走成拖动时落到 super()，事件照常继续派发 —— 不要在这里把事件
            # 手动 sendEvent 给 preview_label：过滤器同时装在 viewport 和 label 上，
            # 在过滤器里发事件会立刻重新进这个过滤器，当场递归爆栈（试过，26MB 堆栈）。
        elif etype == QEvent.MouseMove:
            if self._pan_step(event):
                return True
        elif (etype == QEvent.MouseButtonRelease
                and event.button() in (Qt.LeftButton, Qt.MiddleButton)
                and (self._panning or self._pending_pan)):
            # 松开一律认账。旧实现只在「正在拖」时结束拖动，万一松开事件落到别处
            # （比如压在右下角指示器上），状态就卡在拖动中，光标一直握拳、之后再也拖不动。
            self._end_pan()
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
        self._update_pan_cursor()     # 「还能不能拖」随缩放变化，手形光标得跟着变
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
