"""
屏蔽尺寸配置管理器
存储位置：%APPDATA%/ImgSnag/blocked_sizes.json
支持热读写：新增尺寸即时生效，下次解析自动屏蔽
"""
import json
import os
from dataclasses import dataclass, field
from typing import List, Tuple


APP_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "ImgSnagWeChat",
)
CONFIG_PATH = os.path.join(APP_DIR, "blocked_sizes.json")

_DEFAULT_CONFIG = {
    "ui_exact": [],
    "avatar_exact": [],
    "cover_exact": [],
}


@dataclass
class BlockedSizeConfig:
    ui_exact: List[Tuple[int, int]] = field(default_factory=list)
    avatar_exact: List[Tuple[int, int]] = field(default_factory=list)
    cover_exact: List[Tuple[int, int]] = field(default_factory=list)


class BlockedConfigManager:
    """单例配置管理器，线程安全的热读写"""

    def __init__(self):
        self._ensure_dir()
        self._config = self._load()

    def _ensure_dir(self):
        os.makedirs(APP_DIR, exist_ok=True)

    def _load(self) -> BlockedSizeConfig:
        if not os.path.exists(CONFIG_PATH):
            self._save_default()
            return self._from_dict(_DEFAULT_CONFIG)

        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._from_dict(data)
        except (json.JSONDecodeError, Exception):
            self._save_default()
            return self._from_dict(_DEFAULT_CONFIG)

    def _save_default(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)

    def _save(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "ui_exact": [list(s) for s in self._config.ui_exact],
                "avatar_exact": [list(s) for s in self._config.avatar_exact],
                "cover_exact": [list(s) for s in self._config.cover_exact],
            }, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _from_dict(data: dict) -> BlockedSizeConfig:
        return BlockedSizeConfig(
            ui_exact=[tuple(s) for s in data.get("ui_exact", [])],
            avatar_exact=[tuple(s) for s in data.get("avatar_exact", [])],
            cover_exact=[tuple(s) for s in data.get("cover_exact", [])],
        )

    def get_blocked_sets(self):
        """返回三组屏蔽尺寸的 set，供 worker.py 直接使用"""
        return (
            set(self._config.ui_exact),
            set(self._config.avatar_exact),
            set(self._config.cover_exact),
        )

    def add_blocked(self, category: str, size: Tuple[int, int]):
        """添加屏蔽尺寸并持久化"""
        target = None
        if category == "ui":
            if size not in self._config.ui_exact:
                self._config.ui_exact.append(size)
                target = "ui_exact"
        elif category == "avatar":
            if size not in self._config.avatar_exact:
                self._config.avatar_exact.append(size)
                target = "avatar_exact"
        elif category == "cover":
            if size not in self._config.cover_exact:
                self._config.cover_exact.append(size)
                target = "cover_exact"
        else:
            return False

        if target:
            self._save()
        return bool(target)

    def reload(self):
        """热加载配置（如果用户手动修改了 JSON）"""
        self._config = self._load()


# 全局单例
blocked_config = BlockedConfigManager()
