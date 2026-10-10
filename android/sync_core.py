# -*- coding: utf-8 -*-
"""把主项目的「纯逻辑核心」同步到手机端目标 —— 三端共用同一份代码

跑法（在项目根目录，用项目的 venv）：

    .venv/Scripts/python.exe android/sync_core.py           # 同步（写出副本）
    .venv/Scripts/python.exe android/sync_core.py --check    # 只校验是否一致，不一致 rc=1

────────────────────────────────────────────────────────────────────────
为什么要同步（而不是重写、也不是让手机端去 import 主项目）
────────────────────────────────────────────────────────────────────────
`web_image_dl/sites/weixin.py` 是这套程序**最值钱也最容易失效**的部分 ——
微信改版（换 CDN 域名、换 JS 变量名、改尺寸段规则）它就得跟着改。
两份独立维护必然漂移，而且漂移只会在「手机上突然抓不到图」时才被发现，
那正是你最不想 debug 的时候。而让手机端反过来去 import 主项目也不行：
手机上根本没有项目目录，让代码去猜"主项目在哪儿"换个位置就废。

所以定死一条：**主项目是唯一来源，各手机端目录下的副本全部自动生成**。

────────────────────────────────────────────────────────────────────────
两个目标（同一份源码，两种用法）
────────────────────────────────────────────────────────────────────────
    ① android/app/                      Termux 命令行版（直接跑这个目录里的文件）
    ② android-apk/app/src/main/python/  安卓 APK（Chaquopy 把 Python 打进包里）

APK 版必须有自己的副本，理由和 Termux 版不同：Chaquopy 是从
`app/src/main/python/` 这个**固定位置**把 Python 收进 APK 的，
它不会、也不能去读项目根目录。所以那个目录同样只能靠本脚本生成。

⚠ 有一处不对称，是刻意的：`imgsnag.py`（抓取主流程）的**老家就在
`android/app/`** 里 —— Termux 版直接跑它。所以「搬给 Termux 目标」这一步
源和目标是同一个文件，属于本来就到位，跳过（见 `pairs()`）。
真正需要生成副本的是 APK 目标与 `web_image_dl/` 清单里的所有文件。

────────────────────────────────────────────────────────────────────────
为什么只拷这几个文件
────────────────────────────────────────────────────────────────────────
判据是「**手机端跑得动吗**」：这些都是零 Qt 的，只用 re / os / datetime /
sqlite3 / urllib 这些标准库，拿过去就能跑。唯一的例外是 `updater.py` ——
它 `import requests`，但那是**可选的**（包在 try 里）：安卓版不装任何 pip 包，
调用时把取数函数注入进去，走 urllib，规则本身照用。
其余模块要么依赖 PySide6（app/widgets/save_worker/settings），
要么是桌面端专有的运行时数据管理（blocked_config/library/file_utils），
手机端不需要也不该有。

顺带一个好处：零依赖 = APK 里不用装任何 pip 包。
Chaquopy 装包要走它自己的仓库、还得为 arm64 现编，是构建最容易失败的环节；
一个包都不装，这条风险直接消失。

具体清单见下面 FILES / EXTRA，每条都标了作用。新增站点时**必须**回这里
补一行 —— 漏了的表现是"手机上认不出这个站点的链接"，测试里有一条会拦住它。
"""
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # android/
ROOT = os.path.dirname(HERE)                                # 项目根
SRC = os.path.join(ROOT, 'web_image_dl')

#: 同步目标（各自的根目录，其下会生成 web_image_dl/ 副本）。
TARGETS = [
    os.path.join(HERE, 'app'),                                          # ① Termux 命令行版
    os.path.join(ROOT, 'android-apk', 'app', 'src', 'main', 'python'),  # ② 安卓 APK
]

#: 兼容旧名字：主目标（Termux 版）的副本目录。老测试引用了它，别删。
DST = os.path.join(TARGETS[0], 'web_image_dl')

#: 同步清单。左侧是相对 web_image_dl/ 的路径，右侧是它在手机端的作用。
#: **新增站点适配器时，这里必须同步加一行 sites/<站点>.py**（并更新 sites/__init__.py
#: 的注册表 —— 那个文件本来就在清单里，会被一并同步）。
FILES = {
    '__init__.py':      '包标识（只有版本号，无依赖）',
    'extractor.py':     '通用 URL 清洗（洗转义残留 / 补协议），各站点共用',
    'naming.py':        '下载文件夹命名与非法字符清洗',
    # 网页版界面要从历史里"再打开一次"，所以手机端也需要它。
    # 纯 stdlib（sqlite3），而且**导入无副作用**（单例是惰性的），
    # 手机端会把库建在应用私有目录里（见 imgsnag_web.py 的 root 参数）。
    'history_manager.py': '下载历史（SQLite），手机端建在应用目录里',
    # 版本检测（问 GitHub 「最新发布是哪个版本」）。手机端复用同一份规则，
    # 因为它里面的版本比较、tag 解析、错误归类是"写错一次就静默失效"的东西，
    # 两份必然漂移。它本身只依赖 stdlib + requests（requests 是可选导入，
    # 导入失败只是让"默认那个取数函数"不可用，注入 fetch 照样工作 ——
    # 安卓版走 urllib，就是这么用的）。
    'updater.py':       '版本检测（比对 GitHub 上的发布，手机端复用同一套规则）',
    'sites/__init__.py': '适配器注册表（决定"认不认识这个链接"）',
    'sites/base.py':    '适配器接口与通用扩展名推断',
    'sites/weixin.py':  '微信公众号全部站点知识（原图档 / 去水印 / 正文顺序）',
    'sites/cosmeitu.py': 'cosmeitu.com 全部站点知识（懒加载 data-src / 编号顺序 / OSS 原图档）',
    'sites/cosz.py':    'cosz.com 全部站点知识（正文容器 entry-content / 懒加载 data-src / 自托管图）',
}

#: 除 web_image_dl/ 之外还要一起搬的文件：(源文件, 目标内相对路径, 作用)。
#: `imgsnag.py` 是抓取的**主流程**（拿 HTML → 提图 → 按档位下载 → 去重 → 落盘），
#: 零第三方依赖，所以 Termux 命令行版与 APK 版共用同一份 —— APK 侧只多一层
#: `imgsnag_android.py`（把结果对接安卓的相册与界面）。
EXTRA = [
    (os.path.join(HERE, 'app', 'imgsnag.py'), 'imgsnag.py',
     '手机端抓取主流程（Termux 与 APK 共用）'),
]


def dst_of(target: str, rel: str) -> str:
    """把目标内相对路径（形如 'web_image_dl/naming.py'）拼成绝对路径。"""
    return os.path.join(target, *rel.split('/'))


def planned():
    """要同步的全部文件：[(目标内相对路径, 源绝对路径, 作用说明)]。"""
    out = [(f'web_image_dl/{rel}', os.path.join(SRC, rel), desc)
           for rel, desc in FILES.items()]
    out += [(rel, src, desc) for src, rel, desc in EXTRA]
    return out


def pairs():
    """实际要写出/比对的 (相对路径, 源, 目标绝对路径, 目标根)。

    跳过**源与目标同一文件**的项 —— 那是 `imgsnag.py` 搬给 Termux 目标的情形
    （它的老家就在那个目录里）。不跳过的话 `shutil.copyfile` 会直接抛
    SameFileError 把整个同步打断，这是真踩过的坑，不是理论问题。
    """
    for rel, src, _desc in planned():
        for target in TARGETS:
            dst = dst_of(target, rel)
            if os.path.abspath(src) == os.path.abspath(dst):
                continue
            yield rel, src, dst, target


def _digest(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def _label(target: str) -> str:
    try:
        return os.path.relpath(target, ROOT).replace(os.sep, '/') + '/'
    except ValueError:                                    # 跨盘符时 relpath 会抛
        return target + '/'


def diff():
    """比对源与各目标副本，返回 (缺失, 不一致, 多余) 三个列表。

    列表项形如 `android-apk/app/src/main/python/:web_image_dl/naming.py`，
    带目标前缀，一眼看出是哪一份副本出了问题。

    比对是**纯字节**的 —— 不做换行归一化之类的"宽容"处理。
    理由是这里的失败模式很具体：换行符漂移只可能来自手工编辑或编辑器自动转换，
    而它恰好会让「同一份代码在两台机器上表现不同」这种事变得难查。
    宁可这里红一下，也不要手机端跑出跟电脑端不一样的结果。
    """
    items = planned()
    missing, stale, extra = [], [], []

    for rel, src, _desc in items:
        if not os.path.exists(src):
            missing.append(f'源文件缺失:{rel}')

    for rel, src, dst, target in pairs():
        if not os.path.exists(src):
            continue                                        # 上面已经报过
        if not os.path.exists(dst):
            missing.append(f'{_label(target)}:{rel}')
        elif _digest(src) != _digest(dst):
            stale.append(f'{_label(target)}:{rel}')

    # 反向：副本里有、清单里没有的 .py。只在各目标的 web_image_dl/ 下找 ——
    # 目标根目录里本来就允许有手写文件（imgsnag_android.py、安装脚本等），不该报。
    known = {rel for rel, _s, _d in items}
    for target in TARGETS:
        pkg = os.path.join(target, 'web_image_dl')
        if not os.path.isdir(pkg):
            continue
        for dirpath, dirnames, filenames in os.walk(pkg):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for fn in sorted(filenames):
                if not fn.endswith('.py'):
                    continue
                rel = 'web_image_dl/' + os.path.relpath(
                    os.path.join(dirpath, fn), pkg).replace(os.sep, '/')
                if rel not in known:
                    extra.append(f'{_label(target)}:{rel}')

    return missing, stale, extra


def sync(verbose=True):
    """把清单里的文件原样拷到各目标副本目录。返回写出的文件数。

    **每一个文件都拷到每一个目标**，不做"这个目标不需要那个文件"的裁剪 ——
    三端跑的是同一份代码，清单一旦出现例外，就等于给自己留了一个
    "某端行为和别处不一样"的口子。
    """
    count = 0
    for rel, src, dst, target in pairs():
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        count += 1
        if verbose:
            print(f'  {_label(target)}{rel}')
    return count


def main(argv):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    check_only = '--check' in argv
    missing, stale, extra = diff()
    total = sum(1 for _ in pairs())

    if check_only:
        print('=== 手机端副本同步校验 ===')
        print(f'  {total} 份副本（{len(planned())} 个文件 × {len(TARGETS)} 个目标，'
              f'减去 {len(planned()) * len(TARGETS) - total} 份同位无需拷贝）')
        if not (missing or stale or extra):
            print('  全部与主项目逐字一致 ✅')
            return 0
        for rel in missing:
            print(f'  ✗ 缺失/未生成: {rel}')
        for rel in stale:
            print(f'  ✗ 内容不一致  : {rel}')
        for rel in extra:
            print(f'  ✗ 副本里多出来: {rel}')
        print('\n跑 `python android/sync_core.py` 重新同步。')
        return 1

    print(f'同步 {SRC}')
    for target in TARGETS:
        print(f'  -> {target}')
    n = sync()
    missing, stale, extra = diff()
    left = len(missing) + len(stale) + len(extra)
    print(f'已写入 {n} 个文件，' + ('校验通过 ✅' if not left else f'仍有 {left} 处问题 ❌'))
    return 0 if not left else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
