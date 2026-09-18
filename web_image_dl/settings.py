"""
界面偏好持久化

存储：QSettings 默认位置（Windows 注册表 HKCU\\Software\\ImgSnagWeChat），
写在用户目录里，卸载不删、升级不丢。

为什么单独一个模块而不是散在 app.py 里：QSettings 的键名一旦在两处手写，
写错一个字母**不会报错**，只会表现为「重启后设置没记住」—— 最难查的一类 bug。
所以键名与默认值统一在这里定义，app.py 只调 get/set。

老版本没有任何持久化（全项目零 QSettings），用户每次启动都要重新选排序、重设
保存格式、重新导航到目标文件夹，而且保存目录每次都从「文档」开始。

测试注意：用 ``use_ini_file(path)`` 把存储切到临时文件，别写进真实注册表。
"""
from PySide6.QtCore import QSettings


ORG = "ImgSnagWeChat"
APP = "ImgSnagWeChat"

#: 键名与默认值（唯一手写处）
K_GEOMETRY = "ui/geometry"              # 窗口大小与位置（QByteArray）
K_WINDOW_STATE = "ui/window_state"      # 工具栏/停靠面板状态（QByteArray）
K_SORT_INDEX = "ui/sort_index"          # 排序方式下拉框的序号
K_FILTER_SMALL = "ui/filter_small"      # 「过滤小图与装饰」开关
K_PREFER_ORIGINAL = "quality/prefer_original"   # 「原图画质」开关
K_FORMAT = "io/format"                  # 保存格式（原格式 / JPG / PNG / WebP）
K_LAST_SAVE_DIR = "io/last_save_dir"    # 上次保存到哪个目录

#: 类型由默认值决定：bool / int / str
DEFAULTS = {
    K_SORT_INDEX: 0,
    K_FILTER_SMALL: True,
    K_PREFER_ORIGINAL: True,
    K_FORMAT: "原格式",
    K_LAST_SAVE_DIR: "",
}


class Settings:
    """薄封装：只管类型转换与默认值，业务语义留在 app.py"""

    def __init__(self):
        self._qs = QSettings(ORG, APP)

    # ---------- 存储位置 ----------

    def use_ini_file(self, path: str):
        """把存储切到指定的 ini 文件（测试用，避免污染真实注册表）"""
        self._qs = QSettings(path, QSettings.IniFormat)

    def use_default_store(self):
        """切回正常存储位置"""
        self._qs = QSettings(ORG, APP)

    def path(self) -> str:
        return self._qs.fileName()

    # ---------- 原始值（几何信息这类 QByteArray 用） ----------

    def value(self, key, default=None):
        return self._qs.value(key, default)

    def set_value(self, key, value):
        self._qs.setValue(key, value)

    # ---------- 按 DEFAULTS 类型取值 ----------

    @staticmethod
    def _to_bool(v, default: bool) -> bool:
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        return default          # 存坏了别把开关卡在怪状态上，回默认

    def get(self, key):
        """按该键在 DEFAULTS 里的类型取回；缺失/写坏时回落默认值。

        首次启动时注册表里什么都没有，所以这条路径必须能和「有值但值是垃圾」
        （用户手改 ini、老版本残留）走同一条兜底 —— 否则一升级就是崩在启动里。
        """
        default = DEFAULTS.get(key)
        raw = self._qs.value(key, None)
        if raw is None:
            return default
        if isinstance(default, bool):
            return self._to_bool(raw, default)
        if isinstance(default, int):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        return str(raw)

    def set(self, key, value):
        self._qs.setValue(key, value)

    def sync(self):
        self._qs.sync()

    def clear(self):
        """清空全部偏好（保留给「恢复默认」之类，目前界面没入口）"""
        self._qs.clear()
        self._qs.sync()


# 全局单例
settings = Settings()
