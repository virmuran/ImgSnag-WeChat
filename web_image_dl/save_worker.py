"""落盘线程：把选中图片写进磁盘（含按需格式转换）放到后台执行。

保存本身是纯 I/O 加编码：图一多（几十张、又开着格式转换），放在 UI 线程里
会把界面冻住，连进度条都不刷。整段搬到 QThread 后，界面只负责显示进度。

注意：QImage 可以在非 GUI 线程里安全地解码/编码（QPixmap 不行），
所以这里只碰 QImage 和 os，不接触任何控件。
"""
import os

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .file_utils import unique_path


def write_image(folder, base, src_ext, data, fmt):
    """把一张图写进 folder，返回 True 表示发生了「转换失败 → 回退原格式」。

    拆成模块级纯函数是为了能脱离线程单测；SaveWorker 只是它的调度壳。

    - fmt 为空或与源格式相同：原样写字节，零重编码（不损失画质）
    - fmt 不同：用 QImage 转码后写出，转码失败则退回原格式，绝不整张丢掉
    """
    if not fmt or fmt == src_ext:
        path = unique_path(folder, base, src_ext)
        with open(path, 'wb') as f:
            f.write(data)
        return False

    img = QImage()
    img.loadFromData(data)
    path = unique_path(folder, base, fmt)
    if img.save(path, fmt.lstrip('.').upper()):
        return False

    # 转换失败 → 原格式原样写出。
    # 路径重新探测：旧实现用 str.replace 改扩展名，保存目录里含该扩展名时会写错地方。
    # 上面那次失败的 save 可能已经落下一个 0 字节文件，先清掉免得留垃圾。
    try:
        if os.path.exists(path) and os.path.getsize(path) == 0:
            os.remove(path)
    except OSError:
        pass
    path = unique_path(folder, base, src_ext)
    with open(path, 'wb') as f:
        f.write(data)
    return True


class SaveWorker(QThread):
    progress = Signal(int, int)      # (当前第几张, 总数)
    done = Signal(int, int, list)    # (成功张数, 转换失败回退原格式的张数, 错误信息)
    failed = Signal(str)             # 整体失败（兜底，正常不会走到）

    def __init__(self, targets, folder, fmt):
        """
        targets : [(data: bytes, base: str, ext: str), ...]
                  base 不含扩展名（如 `img_01`），ext 形如 `.jpg`
        folder  : 保存目录
        fmt     : `''` 表示保持原格式，否则形如 `.png`
        """
        super().__init__()
        self.targets = targets
        self.folder = folder
        self.fmt = fmt

    def run(self):
        saved = 0
        fallback = 0
        errors = []
        total = len(self.targets)
        try:
            for i, (data, base, src_ext) in enumerate(self.targets, 1):
                try:
                    if write_image(self.folder, base, src_ext, data, self.fmt):
                        fallback += 1
                    saved += 1
                except Exception as e:                   # 单张失败不该拖垮整批
                    errors.append(f"{base}{src_ext}: {e}")
                self.progress.emit(i, total)
        except Exception as e:
            self.failed.emit(f"保存失败: {e}")
            return
        self.done.emit(saved, fallback, errors)
