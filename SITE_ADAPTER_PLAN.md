# 站点边界的划分方案

> **状态：已于 v1.9.0（2026-09-28）落地** —— 本文保留为设计依据与验收记录。
> 实际落地与计划的差异只有两处：
> 1. `candidates(url)` 只回答「有哪些画质档位、哪个更好」；**用户偏好**（原图优先 or 压缩优先）
>    的重排放在 worker 里 —— 适配器不必知道用户偏好，将来新增站点也不用重复实现这个开关；
> 2. 额外产出 `tools/name_check.py`（静态自检：未定义名 / 未定义 `self.X`）与 8 条反向验证。

> 目标：让「新增一个站点」变成**只新增文件**，而不是改动十个文件。
> 本文只描述现状诊断与改造设计，不含任何行为变更。

---

## 一、结论先说

整个包 4455 行，**真正需要动的只有 3 个文件**（`extractor.py` / `worker.py` / `app.py`）。

其余 8 个模块的「微信」字样，绝大多数是**产品名**（`ImgSnag` 注册表键、数据库路径、GitHub 仓库名、界面文案），
**不算站点耦合**，一行都不用改。

站点耦合点一共只有 **7 个**，全部可枚举。

---

## 二、现状诊断（逐模块）

| 模块 | 行数 | 微信字样 | 判定 | 要动吗 |
|---|---|---|---|---|
| `extractor.py` | 368 | 62 | **全部是微信逻辑** | ✅ 拆 |
| `worker.py` | 209 | 3 | 通用下载引擎，但直连 extractor | ✅ 改 4 处 |
| `app.py` | 1705 | 23 | 界面；**仅 2 处是逻辑**，其余是文案 | ✅ 改 2 处 |
| `naming.py` | 113 | 2 | 通用；1 个常量 `FALLBACK_TITLE` | ⚠️ 改 1 处 |
| `blocked_config.py` | 336 | 1 | 通用规则，仅注释提到微信 | ❌ 不改 |
| `settings.py` | 119 | 4 | 产品名/目录 | ❌ 不改 |
| `updater.py` | 358 | 5 | 产品名/仓库/UA | ❌ 不改 |
| `history_manager.py` | 161 | 2 | 数据库路径 | ❌ 不改 |
| `widgets.py` | 867 | 1 | 界面文案 | ❌ 不改 |
| `library.py` | 107 | **0** | 纯通用（图片扩展名、自然排序、批次扫描） | ❌ 不改 |
| `file_utils.py` | 23 | **0** | 纯通用（防覆盖命名） | ❌ 不改 |
| `save_worker.py` | 85 | **0** | 纯通用（落盘/转码） | ❌ 不改 |

**这个结果说明当初「收窄到微信」那次砍得很干净** —— 通用能力已经天然分离好了。

---

## 三、7 个耦合点（精确清单）

| # | 耦合点 | 位置 | 性质 |
|---|---|---|---|
| 1 | `extract_image_urls(html, include_scripts)` | `worker.py:118` | 提取逻辑直连 |
| 2 | `extract_title(html)` | `worker.py:114` | 同上 |
| 3 | `to_compressed_url(url)` | `worker.py:82`（`_candidate_urls`） | 画质档位知识直连 |
| 4 | `'wx_fmt=png'` 判扩展名 | `worker.py:150` | 站点特征硬编码 |
| 5 | `is_wechat_url(text)` | `app.py:1221` | URL 准入校验直连 |
| 6 | `"https://mp.weixin.qq.com/s/"` 前缀裁剪 | `app.py:1242` | 显示逻辑硬编码 |
| 7 | `FALLBACK_TITLE = '微信图片'` | `naming.py:33` | 兜底文案硬编码 |

---

## 四、目标结构

```
web_image_dl/
├── sites/                   ← 新增目录：站点都放这里
│   ├── __init__.py            注册表 get_adapter(source)
│   ├── base.py                SiteAdapter 接口契约（零逻辑，只有定义与文档）
│   └── weixin.py              微信适配器（extractor.py 的微信部分迁入）
├── extractor.py             ← 瘦身：只留站点无关的 URL 工具
├── worker.py                ← 改 4 处，改为通过 adapter 调用
├── app.py                   ← 改 2 处
├── naming.py                ← FALLBACK_TITLE 改为由 adapter 提供
└── （其余 8 个模块原封不动）
```

**刻意不做的事**：不把 `naming / library / history / settings` 等通用模块搬进 `core/`。
搬家只有风险（漏改 import、测试大面积改路径）没有收益 ——
**边界靠接口契约保证，不靠目录美观。**

---

## 五、接口契约（7 个方法，正好一一对应上面 7 个耦合点）

```python
class SiteAdapter:
    """一个站点一个实现。新增站点 = 新增一个子类 + 注册，不改任何现有文件。"""

    name: str            # "weixin"        内部标识
    display_name: str    # "微信公众号"      界面文案
    fallback_title: str  # "微信图片"        抓不到标题时的兜底

    def matches(self, url: str) -> bool:
        """① URL 是否归本站点管（必须解析 hostname 精确比对，不能子串匹配）"""

    def extract(self, html: str, include_scripts: bool = False) -> list[str]:
        """② 从 HTML 提取图片 URL，**按正文顺序**返回"""

    def extract_title(self, html: str) -> str:
        """③ 从 HTML 提取标题（不清洗，清洗交给 naming.sanitize_title）"""

    def candidates(self, url: str) -> list[str]:
        """④ 画质档位候选，按优先顺序。单站点只有一档时返回 [url]"""

    def ext_for(self, url: str) -> str:
        """⑤ 由 URL 推断扩展名（".jpg" / ".png" …）"""

    def display_url(self, url: str) -> str:
        """⑥ 界面上显示的短地址（通常剥掉冗长的固定前缀）"""

    def sniff(self, html: str) -> int:
        """⑦ 判断这段 HTML 像不像本站点，返回特征命中数（0 = 不像）
        用于「粘贴 HTML 源码」时自动选择适配器"""
```

### 注册表

```python
ADAPTERS = [WeixinAdapter()]          # 新增站点 = 往这里加一行

def get_adapter(source: str) -> SiteAdapter | None:
    """source 是 URL 或 HTML，自动挑适配器"""
```

---

## 六、一个必须现在想清楚的问题

**粘贴 HTML 源码时，怎么知道这段 HTML 属于哪个站点？**

现在只有一个站点，所以直接当微信处理。多站点后有三条路：

| 方案 | 做法 | 评价 |
|---|---|---|
| A | 界面加站点下拉框 | 最直白，但多一个控件 —— 与「极简界面」冲突 |
| B | 各适配器实现 `sniff(html)` 打分，取最高分 | **推荐**，零 UI 成本 |
| C | 依次尝试全部适配器，取结果最多的 | 慢，且可能误判 |

微信的 `sniff` 只需几行：HTML 里出现 `mmbiz.qpic.cn` / `picture_page_info_list` / `msg_title` 即命中。
**建议走 B**，接口里已预留第 ⑦ 项。

---

## 七、落地步骤（每步独立可验证）

| 步 | 做什么 | 风险 | 怎么验 |
|---|---|---|---|
| 1 | 建 `sites/base.py`（只有接口定义与文档） | 无（纯新增） | `py_compile` + 现有测试全绿 |
| 2 | 建 `sites/weixin.py`，把 `extractor.py` 的微信函数迁入 | 低 | `test_extractor.py` 116 项改 import 后全绿 |
| 3 | `extractor.py` 只留 `clean_url` / `_normalize_urls` 等通用工具 | 低 | 同上 |
| 4 | `worker.py` 改为通过 adapter 调用 | **中**（核心链路） | 离线 UI 测试 + 真实文章端到端 |
| 5 | `app.py` 的 2 处改为走 adapter | 低 | `test_ui_smoke.py` |
| 6 | `naming.FALLBACK_TITLE` 改为参数 | 低 | `test_naming.py` 162 项 |

**做完一步测试全绿再进下一步，不要一口气改完。**

---

## 八、风险与纪律

- **最大风险在步骤 4**：`worker.py` 是下载主链路，改坏 = 功能全废。
  必须做**反向验证**（临时拆掉新写法，确认测试确实失败），证明测试真的覆盖到了这条路径。
- 同文件编辑**严格串行**，改完立刻回读验证（同一轮多次编辑会基于旧快照互相覆盖）。
- 改完用 AST 扫一遍模块级未定义名与 `self.X` 未定义属性 —— `py_compile` 抓不到这类丢失。
- 发版闸门前置：`tests/run_all.py` 当前 **850 项 / 7 文件**，改完必须仍然全绿。

---

## 九、这套设计带来的变化

| | 现在 | 改造后 |
|---|---|---|
| 加一个新站点 | 改 3 个文件、读 1000 行现有逻辑 | **新增 1 个文件 + 注册表加 1 行** |
| 某站点改版 | 全库搜关键词 | 只开那一个站点文件 |
| 站点出错影响 | 可能波及公共链路 | 隔离在自身文件内 |
| 通用模块 | 已分离 | 一行不改 |
