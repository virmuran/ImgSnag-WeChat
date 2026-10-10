"""
站点适配器注册表

**新增一个站点 = 写好 `sites/<站点>.py`，然后往 `ADAPTERS` 里加一行。**
不需要改动 worker / app / naming 里的任何判断。

    from .base import SiteAdapter
    from .weixin import weixin

    ADAPTERS = [
        weixin,
        # next_site,          ← 就是这一行
    ]

`get_adapter` 与 `resolve_adapter` 的区别只在于「认不出来怎么办」：

    get_adapter(source)        认不出 → None      （界面校验用：不支持的链接要明确拒绝）
    resolve_adapter(source)    认不出 → 退回默认  （解析用：粘贴 HTML 时无法要求它自证站点）

这个区别是刻意的。URL 是可以精确判定归属的，判不出来就该拒绝；
而一段 HTML 源码没有"归属声明"，只能在特征上像谁就是谁 ——
要求它必须命中特征，只会让那些模板特殊、特征词少的正常页面失败。
"""
from .base import SiteAdapter   # noqa: F401  （对外导出，供新增站点时继承）
from .weixin import weixin
from .cosmeitu import cosmeitu
from .cosz import cosz

#: 注册表。顺序 = 特征打分的平手判定顺序，也是「认不出时退回谁」的依据。
#:
#: ⚠ **微信必须留在第一位**：`resolve_adapter` 在 HTML 认不出归属时退回 `ADAPTERS[0]`，
#: 而"粘贴源码"这个场景绝大多数是公众号文章（改造前的历史行为）。
#: 新站点往**后面**加。
ADAPTERS = [
    weixin,
    cosmeitu,
    cosz,
]


def get_adapter(source, is_url=None):
    """挑一个能处理 source 的适配器，认不出来返回 None。

    source 是 URL 时按主机名精确匹配；是一段 HTML 时按内容特征打分取最高。
    `is_url` 不传则自动判断（以 `<` 开头视为 HTML）。
    """
    s = (source or '').strip()
    if not s:
        return None

    if is_url is None:
        is_url = not s.lstrip().startswith('<')

    if is_url:
        for adapter in ADAPTERS:
            if adapter.matches(s):
                return adapter
        return None

    best, best_score = None, 0
    for adapter in ADAPTERS:
        score = adapter.sniff(s)
        if score > best_score:
            best, best_score = adapter, score
    return best


def resolve_adapter(source, is_url=None):
    """与 get_adapter 相同，但认不出来时退回注册表里的第一个适配器。

    解析路径用这个：粘贴 HTML 源码时，用户没法定点声明这是哪个站点，
    而只要站点特色不明显就报「不支持的来源」，正确的内容也会被拒。
    当前只有微信一个适配器，所以这等价于「HTML 一律按微信处理」——
    与改造前的行为完全一致。

    将来站点多了，若更希望「不认识就明确报错」，把调用点换成 get_adapter
    并自行处理 None 即可，不必改动本函数。
    """
    adapter = get_adapter(source, is_url=is_url)
    if adapter is not None:
        return adapter
    return ADAPTERS[0] if ADAPTERS else None


def supported_names(sep='、') -> str:
    """所有已注册站点的界面名，拼成一句话，给「不支持的链接」提示用。"""
    return sep.join(a.display_name for a in ADAPTERS)
