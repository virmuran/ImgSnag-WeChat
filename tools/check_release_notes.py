#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发版说明一致性闸门 —— 校验 README 的当前版本段与发布说明逐字一致

一份内容、两处使用：README「更新日志」的当前版本段就是 GitHub Release 正文。
手写两份迟早写飘（改了一处忘了另一处，或者干脆各写各的），本脚本把
「逐字一致」变成可执行检查。

在项目根目录运行：
    python tools/check_release_notes.py

退出码：
    0  通过（含历史版本软跳过）
    1  当前版本两处不一致，或 README 结构缺失
"""
import difflib
import io
import os
import re
import sys

# 输出统一 UTF-8：管道/重定向下 Windows 默认走 GBK，print ⚠ ✗ 会直接
# UnicodeEncodeError 崩掉（本项目的 build_release.py 在 v1.8.0 真实踩过）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

#: 兜底用的版本号阈值：自本版起「README 段 = 发布说明正文」逐字同文。
#: 实际判据是「段内含 NEW_FORMAT_HEADS 任一标题」**或**「版本号 ≥ 本阈值」——
#: 所以即便忘了同步这个常量，只要说明照新格式写就仍会自动纳入硬校验。
FORMAT_SINCE = (1, 10, 0)

NOTES_TEMPLATE = "RELEASE_NOTES_TEMPLATE.md"
LOG_HEADING = re.compile(r"^##\s*更新日志\s*$", re.M)
#: 版本段标题：`### v1.2.3`
VERSION_HEADING = re.compile(r"^### v\d+\.\d+\.\d+\b", re.M)
#: 新格式骨架里的小标题：出现任一即认为该版说明已按规范撰写
NEW_FORMAT_HEADS = ("**新增**", "**改进**", "**修复**", "**移除**", "**兼容性提示**")


def read(path):
    with io.open(path, encoding="utf-8", newline="") as fp:
        return fp.read()


def ver_tuple(ver):
    return tuple(int(x) for x in ver.split("."))


def read_version():
    """从 version.py 取版本号（正则读取，不 import —— 避免触发任何副作用）"""
    path = os.path.join(ROOT, "version.py")
    m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', read(path), re.M)
    if not m:
        sys.exit('✗ 无法从 version.py 读取 VERSION（预期形如 VERSION = "1.2.3"）')
    return m.group(1)


def extract_section(text, ver):
    """取出 README「更新日志」里 `### vX.Y.Z` 那一段

    边界以**下一个版本段标题**为准，而不是泛泛的 `## ` / `### ` —— 版本段内部
    可能自带 `## ` 级小标题（历史格式常见），按泛化标题截断会**静默截短**，
    两边都截短后反而被判成「一致」（假通过，最危险的一种错）。
    若当前版本是最后一段，则止于下一个顶级章节 `## ` 或文末。

    只在「## 更新日志」标题之后搜索，避免误匹配正文别处的版本号示例。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    head = LOG_HEADING.search(text)
    scope = text[head.end():] if head else text

    m = re.search(rf"^### v{re.escape(ver)}\b", scope, re.M)
    if m is None:
        return None
    start = m.start()

    nxt = VERSION_HEADING.search(scope, m.end())        # 下一个版本段
    if nxt is not None:
        end = nxt.start()
    else:                                               # 最后一段：到顶级章节
        tail = re.search(r"^## ", scope[m.end():], re.M)
        end = m.end() + tail.start() if tail is not None else len(scope)
    return scope[start:end]


def norm(text):
    """比对用归一化：换行统一、每行去尾部空白、整体去首尾空行"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(ln.rstrip() for ln in text.strip().split("\n")).strip()


def main():
    ver = read_version()
    print(f"发版说明一致性检查 —— v{ver}")
    print(f"  版本来源 : version.py")
    print(f"  更新日志 : README.md")

    ok = True
    readme = read(os.path.join(ROOT, "README.md"))

    # 1) README 结构
    if LOG_HEADING.search(readme.replace("\r\n", "\n")):
        print("  ✓ README 含「## 更新日志」标题")
    else:
        print("  ✗ README 找不到「## 更新日志」标题")
        ok = False

    # 2) 当前版本段
    seg = extract_section(readme, ver)
    if seg is None:
        print(f"  ✗ README 更新日志里没有 v{ver} 段 —— 先补上再发版")
        ok = False
    else:
        print(f"  ✓ README 含 v{ver} 段（{len(seg.strip().splitlines())} 行）")

    # 3) 历史版本：软跳过
    #    判据二选一 —— 段内含新格式小标题（写法已切换），或版本号已到规范生效版本
    since = ".".join(str(x) for x in FORMAT_SINCE)
    is_new_format = (any(h in (seg or "") for h in NEW_FORMAT_HEADS)
                     or ver_tuple(ver) >= FORMAT_SINCE)
    if not is_new_format:
        print(f"  · v{ver} 仍是格式统一之前的写法 —— 跳过逐字比对")
        print(f"    （自 v{since} 起，或说明里出现「**新增**」等小标题后，自动转为硬校验）")
        return 0 if ok else 1

    # 4) 逐字比对
    notes_name = f"RELEASE_NOTES_v{ver}.md"
    notes_path = os.path.join(ROOT, notes_name)
    if not os.path.exists(notes_path):
        print(f"  ! 未找到 {notes_name} —— 按 {NOTES_TEMPLATE} 撰写后本项转为硬校验")
        print("    （提示项，不计入失败）")
        return 0 if ok else 1

    a, b = norm(seg or ""), norm(read(notes_path))
    if a == b:
        print(f"  ✓ README 段与 {notes_name} 逐字一致（{len(a)} 字符）")
        return 0 if ok else 1

    print(f"  ✗ README 段与 {notes_name} 不一致"
          f"（README {len(a)} 字符 / 文件 {len(b)} 字符）")
    diff = list(difflib.unified_diff(a.split("\n"), b.split("\n"),
                                     "README", notes_name, lineterm="", n=1))
    for ln in diff[:40]:
        print("     " + ln[:118])
    if len(diff) > 40:
        print(f"     ...（共 {len(diff)} 行差异）")
    print("\n  一份内容两处使用 —— 改完这一处，请把同一份内容粘到另一处。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
