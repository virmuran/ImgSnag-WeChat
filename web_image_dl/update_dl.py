# -*- coding: utf-8 -*-
"""桌面版「应用内更新」—— 下载 / 校验 / 运行形态判断（零 Qt，便于单测）

为什么单独一个模块，而不是塞进 app.py：
`app.py` 已经两千行，而下载恰恰是**最有状态、最容易错**的那部分（边下边算摘要、
半截文件、取消、重定向、代理、截断）。这部分必须能脱离界面单测，所以一个 Qt 都不引。

⚠ **本模块不进 `android/sync_core.py` 的同步清单**。那份清单是「三端共享的零依赖
解析逻辑」，而这里是桌面端专有行为（下载到用户目录、启动 Inno 安装器、解压便携 zip）。
安卓侧的同类事情在 `android-apk/.../imgsnag_web.py`（安卓自有层）里各写一份 ——
两端**规则**一致（都是先写 .part、边下边算 sha256、对不上就删），但**实现**没法共用：
安卓走 urllib + FileProvider 交给系统安装器，桌面走 requests + 启动 setup.exe。

⚠ 判据的底盘是 `updater.py`（形态、资产分类、证书包都在那边），本模块只做
「拿到 UpdateInfo 之后该下哪个、怎么下、下完怎么办」。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import zipfile

from .updater import human_size, system_ca_bundle

try:
    import requests
except Exception:      # pragma: no cover - requests 是硬依赖，理论上必在
    requests = None

try:
    from version import VERSION as LOCAL_VERSION
except Exception:      # pragma: no cover - 打包异常路径下不阻断
    LOCAL_VERSION = ""


# ------------------------------------------------------------------ 常量

#: 运行形态。`installer` = 用安装包装到系统里的；`portable` = 解压即用的。
MODE_INSTALLER = "installer"
MODE_PORTABLE = "portable"

#: Inno 安装版会在程序目录里放这个卸载程序 —— 它是两种形态**唯一可靠的分界**：
#: 便携包解压出来只有程序本体与 _internal/，没有任何安装器留下的痕迹。
UNINSTALLER_NAME = "unins000.exe"

#: 程序主文件名（在解压出来的便携包里找它）
APP_EXE_NAME = "ImgSnagWeChat.exe"

DOWNLOAD_CHUNK = 64 * 1024
#: (连接超时, 读取超时)。连接可以短，**读取必须给足** —— 30MB 的包在慢网络上
#: 每读一块都可能花几秒，读取超时设太短会把「网慢」误报成「下载失败」。
DOWNLOAD_TIMEOUT = (10, 60)


class DownloadError(RuntimeError):
    """下载/解压失败。消息已经是人话，可以直接显示给用户。"""


class DownloadCancelled(DownloadError):
    """用户主动取消。**不是错误**，界面该安静处理，不要弹「失败」。"""


# ------------------------------------------------------------------ 运行形态

def detect_install_mode(executable: str | None = None) -> str:
    """当前跑的是安装版还是便携版。

    判据落在「主程序 exe 所在目录里有没有 unins000.exe」。
    ⚠ 必须用 exe 自己的目录，**不能用当前工作目录** —— 从快捷方式启动时 cwd
    可能是任意地方，拿它判断会得出相反结论（于是安装版被当成便携版，
    更新时跑去解压 zip 而不是调安装器，用户看到的是一份"新版本"却没有装上去）。
    """
    exe = executable if executable is not None else sys.executable
    if not exe:
        return MODE_PORTABLE
    base = os.path.dirname(os.path.abspath(exe))
    if base and os.path.exists(os.path.join(base, UNINSTALLER_NAME)):
        return MODE_INSTALLER
    return MODE_PORTABLE


def pick_asset(info, mode: str):
    """按运行形态挑这次要下载的文件；没有合适的就返回 None。

    安装版要 `*_setup.exe`、便携版要 `*_portable.zip`。返回 None 时**不是错误** ——
    只是这次发布没有对应形态的包（发版时漏传、或只发了另一种）。调用方该做的是
    提示「去发布页手选」，而不是硬塞一个下不了的链接。
    """
    want = MODE_INSTALLER if mode == MODE_INSTALLER else MODE_PORTABLE
    for a in getattr(info, "assets", None) or []:
        if getattr(a, "kind", "") == want:
            return a
    return None


def mode_label(mode: str) -> str:
    """给用户看的形态名（界面要用同一套说法，别一处写「安装版」一处写「安装包」）。"""
    return "安装版" if mode == MODE_INSTALLER else "便携版"


def app_dir(executable: str | None = None) -> str:
    """主程序所在目录。便携版就是用户当初把它解压到的那个地方。"""
    exe = executable if executable is not None else sys.executable
    return os.path.dirname(os.path.abspath(exe)) if exe else ""


# ------------------------------------------------------------------ 落盘位置

def update_dir() -> str:
    """下载暂存目录。

    放用户目录（`~/.imgsnag_wechat/update`）而**不是程序目录**：
    · 安装版装在 Program Files，普通权限写不进去；
    · 便携版可能躺在 U 盘或只读共享盘上。
    和下载历史库同一个用户目录，卸载不删（用户自己的东西不替他清）。
    """
    d = os.path.join(os.path.expanduser("~"), ".imgsnag_wechat", "update")
    os.makedirs(d, exist_ok=True)
    return d


def _silent_remove(path: str) -> None:
    """删文件，删不掉也不吭声（半截文件清理失败不该盖住真正的错误）"""
    try:
        os.remove(path)
    except OSError:
        pass


# ------------------------------------------------------------------ 校验

def normalize_digest(digest: str) -> str:
    """GitHub 资产元数据里的 `sha256:abcdef…` → `abcdef…`。

    不是 sha256 的一律返回空串 = 「这次没有可用的摘要」——
    宁可少校验一项，也不要拿一串认不出的东西去比对，那会让每一次下载都"校验失败"。
    """
    text = str(digest or "").strip().lower()
    if not text:
        return ""
    if ":" in text:
        algo, _, value = text.partition(":")
        if algo.strip() != "sha256":
            return ""
        text = value.strip()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else ""


def sha256_of(path: str, chunk: int = 1 << 20) -> str:
    """算文件的 sha256（大文件分块读，别一次塞进内存）"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def is_ready(path: str, size: int = 0, sha256: str = "") -> bool:
    """磁盘上这个文件是不是**已经下好且校验通过**。

    用途：重试时不用从头再下一遍（30MB 在慢网络上是要等很久的）；
    也顺手把「上次下到一半留下的残件」判成不可用。
    """
    try:
        if os.path.getsize(path) <= 0:
            return False
    except OSError:
        return False
    if size and os.path.getsize(path) != size:
        return False
    want = normalize_digest(sha256)
    if want:
        try:
            return sha256_of(path) == want
        except OSError:
            return False
    return True


def friendly_net_error(exc: Exception) -> str:
    """把网络异常翻成人话（下载语境，与「检查更新」的措辞分开）。

    和 updater._describe_error 的差别：这里要的是「现在该怎么办」——
    下载失败最常见的下一步是「重试」或「去发布页手动下载」，得写出来。
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc)
    if status is None:
        # 状态码有时只嵌在文本里（如 'HTTP Error 404: Not Found'）。
        # ⚠ 必须带 "HTTP" 前缀：裸的 \d{3} 会把「连接 443 端口失败」里的
        # 443 当成状态码（这个坑 updater.py 里踩过一次）。
        m = re.search(r"\bHTTP(?:\s+Error)?[:\s]\s*(\d{3})\b", text)
        if m:
            status = int(m.group(1))
    if status == 404:
        return "下载地址已失效（这个版本的文件可能已被撤下），请去发布页手动下载"
    if status == 403:
        return "下载被拒绝（请求过于频繁或有代理限制），过一会儿再试"
    if status is not None:
        return f"下载失败：GitHub 返回 HTTP {status}"

    name = type(exc).__name__
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text.lower():
        return ("HTTPS 证书验证失败：这条网络链路在用自签名证书转发流量"
                "（公司网络或加速器常见），试试关掉加速器/代理再下载")
    if "Timeout" in name or "timeout" in text.lower():
        return "下载超时。国内网络下大文件常常这样，重试一次，或去发布页手动下载"
    if "Connection" in name or "SSL" in name or "SSLError" in name:
        return "无法连接下载服务器，请检查网络（公司网络可能限制访问）"
    return f"下载失败：{exc}"


# ------------------------------------------------------------------ 下载

def _default_opener(url: str, timeout):
    """真实网络的取数函数：requests 流式 GET。签名与可注入的 opener 一致。

    证书用「certifi + 系统证书存储」的合并包（`updater.system_ca_bundle`）——
    加速器/公司网络的 TLS 转发用自己的根证书，只用 certifi 会 CERTIFICATE_VERIFY_FAILED，
    而那个错误看起来像「网络不通」，用户照着提示去查网络永远查不出。
    """
    if requests is None:                       # pragma: no cover
        raise RuntimeError("缺少 requests 依赖，无法下载更新")
    kwargs = {
        "stream": True,
        "timeout": timeout,
        "headers": {"User-Agent": f"ImgSnag-WeChat/{LOCAL_VERSION or 'dev'}"},
    }
    bundle = system_ca_bundle()
    if bundle:
        kwargs["verify"] = bundle
    resp = requests.get(url, **kwargs)
    resp.raise_for_status()
    return resp


def download_file(url: str, dest: str, *, total: int = 0, sha256: str = "",
                  progress=None, cancelled=None, opener=None,
                  timeout=DOWNLOAD_TIMEOUT) -> str:
    """把 url 流式下到 dest，**校验通过才落位**。返回 dest。

    三条纪律（与安卓侧同款，因为踩过的坑是同一批）：

    ① **先写 `dest + '.part'`** —— 中途失败/被掐断，绝不会留下一个"看起来像成品"
       的半截文件。半截的安装包体积看起来完全正常，双击才报错，是最难查的一类现场。
    ② **边下边算 sha256**，下完同时核对**字节数**与**摘要**。企业网络的出口会把长
       响应截断（ChemCal 实测在 50MB 处被截断），而截断的包装上去只报"解析包时
       出现问题"，用户根本想不到是网络的事。
    ③ **全部通过才 `os.replace` 落位**（同盘原子替换），任何一步失败都删掉 .part 并抛错。

    Args:
        total: 预期字节数（0 = 不知道，跳过字节数核对）
        sha256: GitHub 给的 `sha256:…` 摘要（空 = 这次没有摘要，跳过摘要核对）
        progress: `progress(done, total)`，用于进度条
        cancelled: `cancelled() -> bool`，返回 True 就中止（抛 DownloadCancelled）
        opener: 注入用的 `opener(url, timeout) -> resp`，测试拿它假造网络
    """
    op = opener or _default_opener
    part = dest + ".part"
    _silent_remove(part)                       # 上一轮的残件先清掉

    try:
        resp = op(url, timeout)
    except Exception as exc:                   # noqa: BLE001 - 全部转成人话
        raise DownloadError(friendly_net_error(exc)) from exc

    got = 0
    digest = hashlib.sha256()
    try:
        with open(part, "wb") as f:
            for chunk in resp.iter_content(DOWNLOAD_CHUNK):
                if cancelled is not None and cancelled():
                    raise DownloadCancelled("已取消下载")
                if not chunk:
                    continue
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if progress is not None:
                    progress(got, total)
    except DownloadCancelled:
        _silent_remove(part)
        raise
    except Exception as exc:                   # noqa: BLE001 - 读流中途断线也走这里
        _silent_remove(part)
        raise DownloadError(friendly_net_error(exc)) from exc

    if total and got != total:
        _silent_remove(part)
        raise DownloadError(
            f"下载不完整：应有 {human_size(total)}，实际只收到 {human_size(got)}"
            "（多半是网络中断），请重试")

    want = normalize_digest(sha256)
    if want and digest.hexdigest() != want:
        _silent_remove(part)
        raise DownloadError(
            "下载的文件校验不通过（内容与 GitHub 上的记录对不上），"
            "可能是网络中转改动了内容，请重试")

    try:
        os.replace(part, dest)
    except OSError as exc:
        _silent_remove(part)
        raise DownloadError(f"无法保存到 {os.path.dirname(dest)}：{exc}") from exc
    return dest


# ------------------------------------------------------------------ 下完之后

def unpack_portable(zip_path: str, dest_root: str) -> str:
    """把便携包解压到 dest_root，返回里面主程序的完整路径。

    ⚠ **防 Zip Slip**：逐项把成员路径规范化后必须仍落在 dest_root 内。
    便携包是我们自己打的，但**下载来的 zip 经过网络** —— 校验成本几乎为零，
    省掉的却是「一个被替换过的压缩包覆盖程序目录外的任意文件」这种最坏后果。
    （成员名里的 `\\` 也要当分隔符看：Windows 打的 zip 可能是反斜杠。）
    """
    root = os.path.abspath(dest_root)
    os.makedirs(root, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            for item in z.infolist():
                name = str(item.filename or "").replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                target = os.path.abspath(os.path.join(root, *name.split("/")))
                if target != root and not target.startswith(root + os.sep):
                    raise DownloadError(
                        f"压缩包里有个文件想解压到目录外面（{name}），已中止")
            z.extractall(root)
    except zipfile.BadZipFile as exc:
        raise DownloadError("下载的压缩包已损坏（网络传输不完整），请重试") from exc
    except OSError as exc:
        raise DownloadError(f"解压失败：{exc}") from exc

    exe = find_executable(root)
    if not exe:
        raise DownloadError("压缩包里没找到主程序，可能是包不完整，请重试")
    return exe


def find_executable(root: str) -> str:
    """在解压结果里找主程序（便携包顶层是 `ImgSnagWeChat/`，主程序在其下）"""
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.lower() == APP_EXE_NAME.lower():
                return os.path.join(dirpath, fn)
    return ""


def port_dir_for(near_dir: str, version: str) -> str:
    """便携版新版解压到哪儿：**与程序目录并排**的一个带版本号目录。

    带版本号是刻意的：**不覆盖用户正在用的那份**。程序没法一边跑一边替换自己
    （exe 与已加载的 DLL 都被系统锁着），所以便携版的更新天然是"解压到新目录、
    由用户决定何时换过去"。

    为什么放在**程序目录旁边**而不是下载目录：
    用户得自己找过去，而且多半已经忘了当初把程序解压到哪了。放在旧目录旁边，
    两个文件夹一眼对得上（`ImgSnagWeChat` 与 `ImgSnagWeChat_v1.11.2`），
    替换时才不会左右为难。传进来的是**程序目录本身**，新版落在它的上一级。

    同名已存在时退到 `_2`/`_3`（与图库命名同款做法），绝不覆盖上一次的解压结果。
    """
    near = os.path.abspath(near_dir) if near_dir else update_dir()
    parent = os.path.dirname(near) or near
    base = re.sub(r"[\\/:*?\"<>|]+", "_", os.path.basename(near)).strip(" .")
    base = re.sub(r"_v\d+(?:\.\d+)*$", "", base) or "ImgSnagWeChat"   # 别把版本号越叠越长
    stem = f"{base}_v{version}" if version else f"{base}_new"
    candidate = os.path.join(parent, stem)
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(parent, f"{stem}_{n}")
        n += 1
    return candidate


def launch_installer(path: str) -> bool:
    """启动安装包（等同用户双击它）。返回是否成功交给系统。

    用 `os.startfile` 而不是 `subprocess.Popen`：前者走 shell 的「打开」语义，
    与双击完全一致 —— 安装包需求管理员权限（`PrivilegesRequired=admin`），
    由 shell 弹 UAC 提权是正常路径；Popen 直接拉起来在某些环境下会被静默拒绝。
    """
    try:
        if hasattr(os, "startfile"):           # Windows
            os.startfile(path)                 # noqa: S606 - 这里就是要「打开文件」
            return True
        subprocess.Popen([path])               # pragma: no cover - 非 Windows 兜底
        return True
    except OSError:
        return False


def reveal_folder(path: str) -> bool:
    """在文件管理器里打开一个目录（便携版更新完给用户指路用）"""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)                 # noqa: S606
            return True
        subprocess.Popen(["xdg-open", path])    # pragma: no cover - 非 Windows 兜底
        return True
    except OSError:
        return False


__all__ = [
    "MODE_INSTALLER", "MODE_PORTABLE", "UNINSTALLER_NAME", "APP_EXE_NAME",
    "DownloadError", "DownloadCancelled",
    "detect_install_mode", "pick_asset", "mode_label", "app_dir",
    "update_dir", "normalize_digest", "sha256_of", "is_ready",
    "friendly_net_error", "download_file",
    "unpack_portable", "find_executable", "port_dir_for",
    "launch_installer", "reveal_folder",
]
