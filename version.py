"""ImgSnag 微信公众号版 版本号 — 唯一来源，所有地方从这里读

版本规范（三段式：主版本.次版本.修订号，如 1.0.0）：

    主版本 MAJOR   不兼容变更 —— 数据格式/存储破坏性调整、技术栈更换
    次版本 MINOR   新增功能 —— 新增提取能力/交互功能，向后兼容
    修订号 PATCH   修 bug、提取规则修正（微信改版适配）、文案、依赖与打包配置

硬性约束（本文件是唯一手写版本号的地方，其余文件由 build_release.py 自动同步）：
    · 必须严格三段纯数字，段内禁止前导零（写 1.0.1，不写 1.0.01）
    · 禁止把日期当版本号（修订号位数超过 3 位即判非法）
    · 禁止"四段丢点"写法：本意 1.0.4.1 写成 1.0.41，会被解析成"修订 41"，
      造成 1.0.23 被判为比 1.0.3 更新的比较倒挂
    · 版本号只增不减；已发布过的号永不复用，即使撤包也发更高号
"""

import re

VERSION = "1.1.0"

#: 规范版本号：三段，无前导零，主/次 1~2 位、修订 1~3 位
VERSION_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"

__all__ = ["VERSION", "VERSION_PATTERN", "is_valid_version", "parse_version", "compare_versions"]


def parse_version(version_str: str) -> tuple:
    """宽容解析版本号为可比较元组（不校验合法性）"""
    parts = []
    for seg in str(version_str).strip().lstrip("vV").split("."):
        seg = re.sub(r"\D", "", seg) or "0"
        parts.append(int(seg))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def compare_versions(a: str, b: str) -> int:
    """> 0 表示 a 比 b 新，< 0 表示旧，0 表示相同"""
    pa, pb = parse_version(a), parse_version(b)
    return (pa > pb) - (pa < pb)


def is_valid_version(version_str: str) -> bool:
    """严格校验：三段纯数字、无前导零、修订号不超过 3 位"""
    s = str(version_str).strip()
    if not re.match(VERSION_PATTERN, s):
        return False
    return len(s.split(".")[2]) <= 3
