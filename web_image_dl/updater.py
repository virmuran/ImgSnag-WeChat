"""
版本检测 —— 问 GitHub Releases「最新发布是哪个版本」

只做**检测**，不下载、不自我替换。本项目发行产物有两种形态（安装包 / 便携包），
自动挑一个下载反而容易挑错：便携党拿到 `setup.exe` 只会一脸茫然，而公司电脑上
跨网下载还有被拦的风险。所以检出新版本后引导用户去 Release 页面自选。

版本号只认 Release 的 **tag**，资产文件名不参与比较 —— 这是从 ChemCal 学到的教训：
发版时若 tag 写 v1.5.0、资产名却写成 1.6.0，客户端只读 tag 就会永远认为「已是最新」，
更新通道静默失效且不报任何错。所以这里把 tag、资产名、两者是否一致一并记进
UpdateInfo，界面上直接点出来。

网络：走 requests（项目已有依赖，证书用 certifi，不额外引入东西）。
未认证请求按 IP 限流 60 次/小时，所以界面侧默认 6 小时才查一次。

测试：`check_for_updates(fetch=...)` 的 fetch 可注入，整个模块不需要联网就能测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    import requests
except Exception:      # pragma: no cover - requests 是硬依赖，理论上必在
    requests = None

try:
    from version import VERSION as LOCAL_VERSION, compare_versions as _compare_versions
    from version import is_valid_version as _is_valid_version
except Exception:      # pragma: no cover - 打包异常路径下不阻断启动
    LOCAL_VERSION = ""

    def _parse_segments(text: str) -> tuple:
        parts = []
        for seg in str(text).strip().lstrip("vV").split("."):
            parts.append(int(re.sub(r"\D", "", seg) or "0"))
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    def _compare_versions(a: str, b: str) -> int:
        pa, pb = _parse_segments(a), _parse_segments(b)
        return (pa > pb) - (pa < pb)

    def _is_valid_version(text: str) -> bool:
        return bool(re.match(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$", str(text).strip()))


GITHUB_REPO = "virmuran/ImgSnag-WeChat"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
SOURCE_URL = f"https://github.com/{GITHUB_REPO}"

#: 界面侧自动检查的间隔（秒）。GitHub 未认证请求按 IP 限流 60 次/小时，
#: 而且没必要时时刻刻去问 —— 6 小时足够，也不至于让用户觉得「从不检查」。
CHECK_INTERVAL = 6 * 3600

#: 默认网络超时（秒）。GitHub API 在国内网络下常常很慢，太短会天天「检查失败」。
DEFAULT_TIMEOUT = 10.0

ASSET_KIND_LABELS = {"installer": "安装包", "portable": "便携包", "file": "更新文件"}

#: 判断 .exe 是不是安装包（Inno Setup 产物形如 ImgSnagWeChat_1.6.0_setup.exe）
INSTALLER_HINTS = ("setup", "install", "installer")


# ------------------------------------------------------------------ 数据结构

@dataclass
class AssetInfo:
    """Release 里的一个发行文件"""
    name: str
    kind: str            # installer / portable / file
    size: int = 0
    url: str = ""
    version: str = ""    # 从文件名里抠出的版本号（用于发现发布失误）

    @property
    def size_text(self) -> str:
        return human_size(self.size)

    @property
    def kind_label(self) -> str:
        return ASSET_KIND_LABELS.get(self.kind, ASSET_KIND_LABELS["file"])


@dataclass
class UpdateInfo:
    """一次检查的完整结果（成功与失败都走这个结构，界面只有一条分支要处理）"""
    ok: bool = False                 # 接口是否问通了
    error: str = ""                  # 没问通时的原因（人话）
    current: str = ""                # 本地版本
    latest: str = ""                 # 远端 tag（已去掉 v 前缀）
    tag: str = ""                    # 远端 tag 原文
    has_update: bool = False
    notes: str = ""                  # Release 正文（更新说明）
    page_url: str = RELEASES_PAGE
    assets: list = field(default_factory=list)
    version_mismatch: bool = False   # tag 与资产名里的版本号不一致 = 发版配置写错了
    latest_is_valid: bool = True     # 远端 tag 是否规范三段式

    def assets_text(self, limit: int = 3) -> str:
        """一行说清「这次发布了什么」：安装包 29.1 MB · 便携包 30.4 MB"""
        if not self.assets:
            return ""
        parts = [f"{a.kind_label} {a.size_text}" for a in self.assets[:limit]]
        if len(self.assets) > limit:
            parts.append(f"等 {len(self.assets)} 个文件")
        return " · ".join(parts)

    def primary_asset(self):
        """最可能想下载的那个（安装包优先），没有则 None"""
        for a in self.assets:
            if a.kind == "installer":
                return a
        return self.assets[0] if self.assets else None


# ------------------------------------------------------------------ 小工具

def parse_tag(tag: str) -> str:
    """'v1.6.0' → '1.6.0'"""
    return str(tag or "").strip().lstrip("vV").strip()


def classify_asset(name: str) -> str:
    """资产类型：installer（安装包）/ portable（便携 zip）/ file（其他）"""
    low = (name or "").lower()
    if low.endswith(".exe") and any(h in low for h in INSTALLER_HINTS):
        return "installer"
    if low.endswith(".zip"):
        return "portable"
    return "file"


def extract_version_from_name(name: str) -> str:
    """'ImgSnagWeChat_1.6.0_setup.exe' → '1.6.0'（用于发现 tag 与资产名不一致）"""
    m = re.search(r"(\d+(?:\.\d+)+)", name or "")
    return m.group(1) if m else ""


def human_size(n) -> str:
    """字节数转可读文本：52950018 → '50.5 MB'"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "未知大小"
    if n <= 0:
        return "未知大小"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ------------------------------------------------------------------ 网络

def fetch_latest_release(timeout: float = DEFAULT_TIMEOUT) -> dict:
    """请求 GitHub「最新正式发布」接口。失败时抛异常，由 check_for_updates 归类。"""
    if requests is None:                      # pragma: no cover
        raise RuntimeError("缺少 requests 依赖，无法联网检查更新")
    resp = requests.get(
        API_URL,
        timeout=timeout,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"ImgSnag-WeChat/{LOCAL_VERSION or 'dev'}",
        },
    )
    resp.raise_for_status()
    return resp.json()


def _describe_error(exc: Exception) -> str:
    """把网络异常翻译成用户能看懂的一句话（并给出可操作的建议）"""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 403:
        return ("GitHub 接口暂时拒绝访问（同一网络请求过于频繁，或有代理限制），"
                "过一会儿再试")
    if status == 404:
        return "这个仓库还没有正式发布（草稿与预发布不算），或仓库地址有变动"
    if status is not None:
        return f"GitHub 返回 HTTP {status}，请稍后再试"

    name = type(exc).__name__
    if "Timeout" in name or "timeout" in str(exc).lower():
        return ("连接 GitHub 超时。国内网络常见，公司网络可能直接限制访问 —— "
                "不影响本程序其他功能")
    if "Connection" in name or "SSL" in name or "SSLError" in name:
        return "无法连接 GitHub，请检查网络（公司网络可能限制访问）"
    return f"检查失败：{exc}"


# ------------------------------------------------------------------ 主入口

def _build_assets(raw_assets) -> list:
    """把接口给的资产数组整理成 AssetInfo（坏项跳过，不让它拖垮整次检查）"""
    if not isinstance(raw_assets, (list, tuple)):
        return []
    out = []
    for raw in raw_assets:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        if not name:
            continue
        out.append(AssetInfo(
            name=name,
            kind=classify_asset(name),
            size=int(raw.get("size") or 0),
            url=str(raw.get("browser_download_url") or ""),
            version=extract_version_from_name(name),
        ))
    order = {"installer": 0, "portable": 1, "file": 2}
    out.sort(key=lambda a: (order.get(a.kind, 3), a.name))
    return out


def check_for_updates(fetch=None, timeout: float = DEFAULT_TIMEOUT,
                      current: str | None = None) -> UpdateInfo:
    """检查是否有新版本。

    Args:
        fetch: 注入用的取数函数 ``fetch(url, timeout) -> dict``（测试用；默认走 GitHub）
        timeout: 网络超时（秒）
        current: 本地版本号。**None = 用 version.py 里的**；传空串表示「本地版本号
                 读不到」这条异常路径（此时一律不提示有新版本，免得误导用户）

    Returns:
        UpdateInfo。**任何失败都不抛异常** —— 检查更新不该把界面搞崩。
    """
    getter = fetch or fetch_latest_release
    info = UpdateInfo(current=LOCAL_VERSION if current is None else current,
                      page_url=RELEASES_PAGE)

    try:
        data = getter(API_URL, timeout) or {}
        if not isinstance(data, dict):
            # 代理/网关把 API 挡了、返回 HTML 或数组时，别让界面崩在 .get 上
            raise ValueError(f"接口返回了非预期结构（{type(data).__name__}）")
    except Exception as exc:                    # noqa: BLE001 - 全部转成人话
        info.error = _describe_error(exc)
        return info

    info.ok = True
    info.tag = str(data.get("tag_name") or "")
    info.latest = parse_tag(info.tag)
    info.notes = str(data.get("body") or "")
    info.page_url = str(data.get("html_url") or RELEASES_PAGE)
    info.assets = _build_assets(data.get("assets"))

    if not info.latest:
        # 仓库能访问但没发布任何 Release（或 tag 为空）—— 不算错误，只当「查不到」
        return info

    info.latest_is_valid = _is_valid_version(info.latest)
    info.has_update = _compare_versions(info.current, info.latest) < 0 if info.current else False
    info.version_mismatch = any(
        a.version and a.version != info.latest for a in info.assets
    )
    return info
