"""
屏蔽尺寸配置管理器
存储位置：%APPDATA%/ImgSnagWeChat/blocked_sizes.json

为什么要有「撤销」：早期只有 add_blocked 一个入口 —— 右键误屏蔽一个尺寸之后，那类图
从此**静默消失**，既看不到屏蔽了哪些尺寸、也没有任何撤销入口，只能去手改 JSON 文件。
对目标用户（普通用户）等于「图丢了」。所以这里补齐删除/清空/命中统计，界面侧
（历史页底部）给出「已屏蔽的尺寸」面板。

存储格式（向后兼容：老文件没有 meta 键也照常读入）::

    {
      "ui_exact":     [[100, 100]],
      "avatar_exact": [],
      "cover_exact":  [],
      "meta": {"ui:100x100": {"added_at": "...", "hits": 12, "last_hit": "..."}}
    }

meta 只放统计与时间，判定一律以上面三个列表为准 —— 即使 meta 丢了/写坏了，
屏蔽规则本身不会失效。

写盘用「临时文件 + os.replace」原子替换：直接覆盖写原文件时，一旦进程在写的中途
被杀掉，留下来的就是半截 JSON，下次启动会把攒下的规则整个当损坏文件丢掉。
"""
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple


APP_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "ImgSnagWeChat",
)
CONFIG_PATH = os.path.join(APP_DIR, "blocked_sizes.json")

#: 三类屏蔽维度。标签用于界面展示，判定只用键名。
CATEGORIES = ("ui", "avatar", "cover")
CATEGORY_LABELS = {"ui": "UI 装饰", "avatar": "头像", "cover": "封面横幅"}

#: 兜底规则名（与用户屏蔽表无关的硬编码启发式）
RULE_SMALL = "small"
RULE_UI_NOISE = "ui_noise"

#: 最长边 ≤ 此值视为小图（头像、图标、二维码）
SMALL_MAX_DIM = 200
#: 带透明通道的 PNG 且小于此字节数 → 微信排版用的装饰图
UI_NOISE_MAX_BYTES = 10_000

_DEFAULT_CONFIG = {
    "ui_exact": [],
    "avatar_exact": [],
    "cover_exact": [],
    "meta": {},
}


def size_key(category: str, size: Tuple[int, int]) -> str:
    """meta 里的键：``ui:100x100``"""
    return f"{category}:{int(size[0])}x{int(size[1])}"


def reason_label(reason: str) -> str:
    """把规则名翻成人话，界面/提示里直接用"""
    if reason == RULE_SMALL:
        return f"最长边不超过 {SMALL_MAX_DIM}px 的小图"
    if reason == RULE_UI_NOISE:
        return f"透明 PNG 且小于 {UI_NOISE_MAX_BYTES // 1000}KB 的排版装饰"
    if reason.startswith("blocked:"):
        cat = reason.split(":", 1)[1]
        return f"你屏蔽过的尺寸（{CATEGORY_LABELS.get(cat, cat)}）"
    return reason


def classify(width: int, height: int, ext: str, nbytes: int, has_alpha: bool,
             blocked_sets=None) -> Tuple[str, ...]:
    """统一的「这张图要不要藏起来」判定 —— worker 与界面重算共用同一份规则。

    返回**命中的全部**规则名（可能同时命中多条）：
    ``"small"`` / ``"ui_noise"`` / ``"blocked:ui"`` / ``"blocked:avatar"`` / ``"blocked:cover"``

    返回元组而不是一个 bool，是因为界面撤销屏蔽时要能**只撤掉那一条**：某张图
    既小又命中过屏蔽尺寸，撤销屏蔽后它仍应因为「小」而藏起来。
    """
    reasons: List[str] = []
    if width > 0 and height > 0 and max(width, height) <= SMALL_MAX_DIM:
        reasons.append(RULE_SMALL)
    if ext == ".png" and has_alpha and nbytes < UI_NOISE_MAX_BYTES:
        reasons.append(RULE_UI_NOISE)

    if blocked_sets is None:
        ui = avatar = cover = frozenset()
    else:
        ui, avatar, cover = blocked_sets
    key = (width, height)
    for cat, table in (("ui", ui), ("avatar", avatar), ("cover", cover)):
        if key in table:
            reasons.append(f"blocked:{cat}")
    return tuple(reasons)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class BlockedSizeConfig:
    ui_exact: List[Tuple[int, int]] = field(default_factory=list)
    avatar_exact: List[Tuple[int, int]] = field(default_factory=list)
    cover_exact: List[Tuple[int, int]] = field(default_factory=list)
    meta: Dict[str, dict] = field(default_factory=dict)


class BlockedConfigManager:
    """配置管理器（单例）：线程安全的热读写

    worker 在后台线程里跑解析，解析结束会回写命中次数，所以所有读写都套了可重入锁；
    锁只护内存态与落盘，不护网络请求。
    """

    def __init__(self, config_path: str = CONFIG_PATH):
        #: 测试可以改成临时路径，避免污染真实用户配置
        self.config_path = config_path
        self._lock = threading.RLock()
        self._ensure_dir()
        self._config = self._load()

    # ---------- 落盘 ----------

    def _ensure_dir(self):
        d = os.path.dirname(self.config_path)
        if d:
            os.makedirs(d, exist_ok=True)

    def _write(self, payload: dict):
        """原子写：先写 .tmp，fsync，再 replace 顶替原文件"""
        self._ensure_dir()
        tmp = self.config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.config_path)

    def _save_default(self):
        self._write(dict(_DEFAULT_CONFIG))

    def _save(self):
        self._write({
            "ui_exact": [list(s) for s in self._config.ui_exact],
            "avatar_exact": [list(s) for s in self._config.avatar_exact],
            "cover_exact": [list(s) for s in self._config.cover_exact],
            "meta": self._config.meta,
        })

    # ---------- 读取 ----------

    def _load(self) -> BlockedSizeConfig:
        if not os.path.exists(self.config_path):
            self._save_default()
            return self._from_dict(_DEFAULT_CONFIG)
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("配置根节点不是对象")
            return self._from_dict(data)
        except Exception:
            # 文件坏了也不能把规则一起丢掉：留个现场（.corrupt）再回默认值，
            # 否则用户手改 JSON 改坏一次就再也查不出原来是什么
            try:
                os.replace(self.config_path, self.config_path + ".corrupt")
            except OSError:
                pass
            self._save_default()
            return self._from_dict(_DEFAULT_CONFIG)

    @staticmethod
    def _clean_pairs(raw) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for item in raw or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    pair = (int(item[0]), int(item[1]))
                except (TypeError, ValueError):
                    continue
                if pair not in out:
                    out.append(pair)
        return out

    @classmethod
    def _from_dict(cls, data: dict) -> BlockedSizeConfig:
        meta = data.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        return BlockedSizeConfig(
            ui_exact=cls._clean_pairs(data.get("ui_exact")),
            avatar_exact=cls._clean_pairs(data.get("avatar_exact")),
            cover_exact=cls._clean_pairs(data.get("cover_exact")),
            meta={k: v for k, v in meta.items() if isinstance(v, dict)},
        )

    def _list_for(self, category: str) -> Optional[List[Tuple[int, int]]]:
        return {
            "ui": self._config.ui_exact,
            "avatar": self._config.avatar_exact,
            "cover": self._config.cover_exact,
        }.get(category)

    # ---------- 对外接口 ----------

    def get_blocked_sets(self):
        """返回三组屏蔽尺寸的 set，供 worker.py 直接使用"""
        with self._lock:
            return (
                set(self._config.ui_exact),
                set(self._config.avatar_exact),
                set(self._config.cover_exact),
            )

    def add_blocked(self, category: str, size: Tuple[int, int]) -> bool:
        """添加屏蔽尺寸并持久化；已存在返回 False"""
        with self._lock:
            target = self._list_for(category)
            if target is None:
                return False
            size = (int(size[0]), int(size[1]))
            if size in target:
                return False
            target.append(size)
            key = size_key(category, size)
            entry = self._config.meta.setdefault(key, {})
            entry.setdefault("added_at", _now())
            entry.setdefault("hits", 0)
            self._save()
            return True

    def remove_blocked(self, category: str, size: Tuple[int, int]) -> bool:
        """撤销一条屏蔽，返回是否真的删掉了（界面据此给提示）"""
        with self._lock:
            target = self._list_for(category)
            if target is None:
                return False
            size = (int(size[0]), int(size[1]))
            if size not in target:
                return False
            target.remove(size)
            self._config.meta.pop(size_key(category, size), None)
            self._save()
            return True

    def clear_blocked(self, category: Optional[str] = None) -> int:
        """清空屏蔽表（category 为 None 表示全部），返回清掉的条数"""
        with self._lock:
            if category is None:
                n = sum(len(self._list_for(c) or []) for c in CATEGORIES)
                if not n:
                    return 0
                self._config = BlockedSizeConfig()
            else:
                target = self._list_for(category)
                if target is None:
                    return 0
                n = len(target)
                if not n:
                    return 0
                target.clear()
                for k in [k for k in self._config.meta if k.startswith(f"{category}:")]:
                    self._config.meta.pop(k, None)
            self._save()
            return n

    def count(self) -> int:
        """当前屏蔽了多少条"""
        with self._lock:
            return sum(len(self._list_for(c) or []) for c in CATEGORIES)

    def list_blocked(self) -> List[dict]:
        """给界面用的完整列表：分类 + 尺寸 + 命中次数 + 加入时间"""
        with self._lock:
            rows: List[dict] = []
            for cat in CATEGORIES:
                for size in self._list_for(cat) or []:
                    m = self._config.meta.get(size_key(cat, size)) or {}
                    rows.append({
                        "category": cat,
                        "category_label": CATEGORY_LABELS.get(cat, cat),
                        "width": size[0],
                        "height": size[1],
                        "hits": int(m.get("hits", 0) or 0),
                        "added_at": m.get("added_at", "") or "",
                        "last_hit": m.get("last_hit", "") or "",
                    })
            # 大的排前面（更可能是正文图，误屏蔽的代价最高），再按命中次数
            rows.sort(key=lambda r: (CATEGORIES.index(r["category"]),
                                     -(r["width"] * r["height"]),
                                     -r["hits"]))
            return rows

    def add_hits(self, counter: Dict[Tuple[str, int, int], int]) -> None:
        """一次解析收尾时批量累加命中次数。

        一次解析里同一尺寸常常命中几十张，所以由调用方聚合好再一次性写盘 ——
        每命中一张就写一次 JSON 会把解析拖成磁盘 IO 表演。
        期间被撤销掉的尺寸直接跳过，不给已经不存在的规则记账。
        """
        if not counter:
            return
        with self._lock:
            changed = False
            now = _now()
            for (category, w, h), n in counter.items():
                if n <= 0:
                    continue
                target = self._list_for(category)
                if target is None or (w, h) not in target:
                    continue
                key = size_key(category, (w, h))
                entry = self._config.meta.setdefault(key, {})
                entry["hits"] = int(entry.get("hits", 0) or 0) + int(n)
                entry["last_hit"] = now
                entry.setdefault("added_at", now)
                changed = True
            if changed:
                self._save()

    def reload(self):
        """热加载配置（如果用户手动修改了 JSON）"""
        with self._lock:
            self._config = self._load()


# 全局单例
blocked_config = BlockedConfigManager()
