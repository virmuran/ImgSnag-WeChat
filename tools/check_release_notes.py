#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发版说明闸门 —— 校验 `CHANGELOG.md` 的当前版本段是否就绪

v1.12.0 起**更新说明只写一处**：`CHANGELOG.md` 里当前版本的段落。
发版时直接把这一段复制成 GitHub Release 正文即可，不再另存 `RELEASE_NOTES_v*.md`
（那份草稿曾经是第二个落地点，改一处忘一处就会两处不一致）。

因此本脚本现在读两个文件、只做三件事：
    1. `CHANGELOG.md` 结构完整（有「更新日志」标题、有当前版本段）
    2. `README.md` 顶部的版本徽章与 `version.py` 一致
    3. `README.md` 里留着一个指向 `CHANGELOG.md` 的入口（读者唯一的路）

在项目根目录运行：
    python tools/check_release_notes.py

退出码：
    0  通过
    1  版本段缺失、版本徽章与 version.py 不符、README 没有入口
"""
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

CHANGELOG_NAME = "CHANGELOG.md"
README_NAME = "README.md"
NOTES_TEMPLATE = "RELEASE_NOTES_TEMPLATE.md"

#: 更新日志的标题（CHANGELOG.md 用一级 `#`，兼容历史写法里的二级 `##`）
LOG_HEADING = re.compile(r"^#{1,2}\s*更新日志\s*$", re.M)
#: 版本段标题：`### v1.2.3`
VERSION_HEADING = re.compile(r"^### v\d+\.\d+\.\d+\b", re.M)
#: 规范骨架里的小标题：出现任一即认为该版说明已按规范撰写
NEW_FORMAT_HEADS = ("**新增**", "**改进**", "**修复**", "**移除**", "**兼容性提示**")


def read(path):
    with io.open(path, encoding="utf-8", newline="") as fp:
        return fp.read()


def read_version():
    """从 version.py 取版本号（正则读取，不 import —— 避免触发任何副作用）"""
    path = os.path.join(ROOT, "version.py")
    m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', read(path), re.M)
    if not m:
        sys.exit('✗ 无法从 version.py 读取 VERSION（预期形如 VERSION = "1.2.3"）')
    return m.group(1)


def extract_section(text, ver):
    """取出更新日志里 `### vX.Y.Z` 那一段

    边界以**下一个版本段标题**为准，而不是泛泛的 `## ` / `### ` —— 版本段内部
    可能自带 `## ` 级小标题（历史格式常见），按泛化标题截断会**静默截短**。
    若当前版本是最后一段，则止于下一个顶级章节 `## ` 或文末。

    只在「更新日志」标题之后搜索，避免误匹配正文别处的版本号示例。
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


def main():
    ver = read_version()
    print(f"发版说明检查 —— v{ver}")
    print(f"  版本来源 : version.py")
    print(f"  更新说明 : {CHANGELOG_NAME}（当前版本段 = Release 正文，直接复制）")
    print(f"  版本徽章 : {README_NAME}")

    ok = True
    changelog_path = os.path.join(ROOT, CHANGELOG_NAME)
    readme_path = os.path.join(ROOT, README_NAME)

    if not os.path.exists(changelog_path):
        print(f"  ✗ 找不到 {CHANGELOG_NAME} —— 更新日志集中放在这个文件里")
        return 1
    changelog = read(changelog_path)
    readme = read(readme_path) if os.path.exists(readme_path) else ""

    # 1) CHANGELOG 结构
    if LOG_HEADING.search(changelog.replace("\r\n", "\n")):
        print(f"  ✓ {CHANGELOG_NAME} 含「更新日志」标题")
    else:
        print(f"  ✗ {CHANGELOG_NAME} 找不到「更新日志」标题")
        ok = False

    # 1b) 版本徽章
    #     它没有任何代码引用，属于纯手工落地点 —— 实测已静默漂过两个版本
    #     （v1.8.2 的徽章一直挂到 v1.9.0 发布之后）。既然是人眼最常看到的地方，
    #     就必须由闸门钉住；判据只认 shields.io 的 `badge/version-<ver>-` 这一段。
    badge = re.search(r"badge/version-([0-9][0-9.]*)-", readme)
    if badge is None:
        print(f"  ! {README_NAME} 顶部没有版本徽章 —— 跳过本项")
    elif badge.group(1) == ver:
        print(f"  ✓ {README_NAME} 版本徽章 = v{ver}")
    else:
        print(f"  ✗ {README_NAME} 版本徽章是 v{badge.group(1)}，与 version.py 的 v{ver} 不一致")
        ok = False

    # 1c) README 必须留一个入口
    #     更新日志搬走以后，README 里那条链接是读者唯一的路 —— 它没有别的东西
    #     盯着（纯手工），所以同样纳入闸门。
    if CHANGELOG_NAME in readme:
        print(f"  ✓ {README_NAME} 有指向 {CHANGELOG_NAME} 的入口")
    else:
        print(f"  ✗ {README_NAME} 里没有指向 {CHANGELOG_NAME} 的链接 —— 读者会找不到更新日志")
        ok = False

    # 2) 当前版本段
    seg = extract_section(changelog, ver)
    if seg is None:
        print(f"  ✗ {CHANGELOG_NAME} 里没有 v{ver} 段 —— 先补上再发版")
        return 1

    lines = [ln for ln in seg.strip().splitlines() if ln.strip()]
    print(f"  ✓ {CHANGELOG_NAME} 含 v{ver} 段（{len(lines)} 行）")

    # 2b) 骨架提示（不计入失败）：按规范撰写的说明至少带一个小标题
    if not any(h in seg for h in NEW_FORMAT_HEADS):
        print(f"  ! v{ver} 段里没有「**新增**」这类小标题 —— 骨架见 {NOTES_TEMPLATE}")
    elif len(lines) < 3:
        print(f"  ! v{ver} 段偏短（{len(lines)} 行）—— 确认是否漏写条目")

    print("\n发布时把上面这一版在 CHANGELOG.md 里的段落整段复制为 Release 正文即可。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
