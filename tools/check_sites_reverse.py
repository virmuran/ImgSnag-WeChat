# -*- coding: utf-8 -*-
"""反向验证（站点适配层 + 来源提示，常驻工具）：逐条拆坏源码，确认对应的测试**真的会变红**

为什么必须做：自己写的测试全绿，只证明"测试能跑"，不证明"测试有用"。
一个断言写反了、匹配了错误的字符串、或者压根没被执行到，都会安静地绿着。

这一批尤其需要验 —— v1.12.0 新增的第二个站点与来源提示，**源码是照着没有真实
样本的页面结构写的**（cosmeitu 只用一篇真实页面验过一次，微信的署名字段连样本
都没有），测试里跑的又都是自己构造的 HTML。于是"这条防线到底存不存在"必须先
单独验一遍，否则升级上去之后遇到另一篇页面才发现，代价就是用户看到错的署名。

跑法：
    .venv/Scripts/python.exe tools/check_sites_reverse.py

纪律（与 `check_apk_reverse.py` / `check_desktop_reverse.py` 同一套，踩过坑才固化的）：
  · 改之前先写备份、脚本启动时先扫残留自动还原。整套要跑半分钟到一分钟，
    可能被中途掐断，而 **SIGTERM 下 finally 不执行**。
  · 备份统一放 `_rv_backup/`，**绝不能放被测目录里** —— 曾经把备份写成
    `<文件>.rvbak` 放在原地，而检查是"列目录全读"，备份与拆坏件被一起读到，
    断言照绿（同类坑踩过两次）。这里另加了"锚点必须唯一"的检查：锚点出现多次时
    `replace(…, 1)` 会改到第一处，可能改的根本不是目标那行，而报告上一切正常。
  · 拆坏方式**不选会触发真实网络的**：下面这些全是纯文本解析与界面逻辑，
    测试里的下载是注入的假数据，一个请求都不会发出去。
  · 子进程必须显式 timeout，超时算「测试没过」。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # tools/
ROOT = os.path.dirname(HERE)                            # 项目根

TEST_SITES = os.path.join(ROOT, 'tests', 'test_sites.py')       # 站点适配层（纯逻辑）
TEST_UI = os.path.join(ROOT, 'tests', 'test_ui_smoke.py')       # 离屏界面
TEST_ANDROID = os.path.join(ROOT, 'tests', 'test_imgsnag.py')   # 三端同步（副本逐字校验）
LOG = os.path.join(ROOT, '_rv_sites_log.txt')

SITES_BASE = 'web_image_dl/sites/base.py'
WEIXIN = 'web_image_dl/sites/weixin.py'
COSMEITU = 'web_image_dl/sites/cosmeitu.py'
SITES_INIT = 'web_image_dl/sites/__init__.py'
APP = 'web_image_dl/app.py'
SYNC = 'android/sync_core.py'
WORKER = 'web_image_dl/worker.py'
ANDROID_APP = 'android/app/imgsnag.py'
ANDROID_APK = 'android-apk/app/src/main/python/imgsnag.py'

TEST_EXTRACTOR = os.path.join(ROOT, 'tests', 'test_extractor.py')   # 下载头组装

#: (说明, 文件, 原文, 替换为, 预期变红的用例, 跑哪个测试文件, 替换次数, UI 筛选标签)
#:
#: 不写第 6 项就默认跑 test_sites.py；不写第 7 项就是只换第一处；
#: 第 8 项只有界面用例才需要（决定 `IMGSNAG_UI_ONLY` 跑哪一条）。
#: ⚠ `str.replace` 的 0 是"一次都不换"，与 `re.sub` 的 count=0（全部）相反，别写 0。
CASES = [
    # ── 站点归属：判错了，用户粘的链接会被别人接走（或请求真发到伪装域名去）────
    ('cosmeitu 域名改用子串匹配（cosmeitu.com.evil.com 这种伪装域名被放行，请求发到别人那去）',
     COSMEITU,
     '    return _host_of(url) in COSMEITU_HOSTS',
     '    return any(h in url for h in COSMEITU_HOSTS)',
     '伪装域名被拒', TEST_SITES),

    ('注册表里没有 cosmeitu（新站点等于没接，粘贴链接提示不支持）',
     SITES_INIT,
     '    cosmeitu,\n]',
     ']',
     'supported_names 带上新站点', TEST_SITES),

    # ── 正文图筛选与定序：错了图集张数与顺序都不对，而用户很难看出为什么 ──────
    ('正文图不按编号排序，改用 HTML 出现顺序（图集顺序错乱）',
     COSMEITU,
     '        numbered.sort(key=lambda x: x[0])',
     '        pass',
     '编号 1 排在前面', TEST_SITES),

    ('同一编号重复输出不再去重（图集里会多出重复的图）',
     COSMEITU,
     '            if n in seen:                 # 同一编号只留首次出现（防模板重复输出）',
     '            if False:',
     '同一编号重复输出只留首次', TEST_SITES),

    ('不优先 onerror 里的 OSS 地址（拿到的是推不出原图路径的那一份）',
     COSMEITU,
     '    m = _ATTR_ONERROR_URL_RE.search(tag)\n    if m:',
     '    m = _ATTR_ONERROR_URL_RE.search(tag)\n    if False:',
     '返回的是 OSS 那份地址', TEST_SITES),

    # ── 画质档位：错了用户拿到的是压缩档，还以为自己下了原图 ──────────────────
    ('不再剥掉 OSS 压缩参数（拿回来的是 1440 宽压缩档，不是原图）',
     COSMEITU,
     "    stripped = _OSS_PROCESS_RE.sub('', u)",
     '    stripped = u',
     '剥掉 OSS 参数', TEST_SITES),

    # ── 来源署名：错了界面上会显示一个**错的出处**，比不显示更糟 ───────────────
    ('微信署名认不出时编一个（界面显示一个错的公众号名）',
     WEIXIN,
     "            if text and len(text) <= 60:      # 过长的一律视为误抓\n"
     "                return text\n"
     "    return ''",
     "            if text and len(text) <= 60:      # 过长的一律视为误抓\n"
     "                return text\n"
     "    return '微信公众号'",
     '所有来源都认不出时返回空串', TEST_SITES),

    ('微信署名不设长度上限（会把一整段正文当成公众号名）',
     WEIXIN,
     '            if text and len(text) <= 60:      # 过长的一律视为误抓',
     '            if text:',
     '超长字段视为误抓', TEST_SITES),

    ('cosmeitu 署名认不出时返回站点名（本来该说"认不出"）',
     COSMEITU,
     "    if not m:\n"
     "        return ''\n"
     r"    return re.sub(r'\s+', ' ', m.group(1)).strip(' \u3000')",
     "    if not m:\n"
     "        return 'cosmeitu.com'\n"
     r"    return re.sub(r'\s+', ' ', m.group(1)).strip(' \u3000')",
     '取不到署名时返回空串', TEST_SITES),

    ('版权提示有署名也不写站点（用户看不到图是从哪个站来的）',
     SITES_BASE,
     '    if name and site:',
     '    if False:',
     '有署名时同时写出作者与站点', TEST_SITES),

    # ── 界面上的提醒与落盘的来源说明 ─────────────────────────────────────────
    ('刷新完来源提示却不显示（等于界面上没有这条提醒）',
     APP,
     '        self.credit_label.setText(f"{line} — {CREDIT_NOTICE}")\n'
     '        self.credit_label.setVisible(True)',
     '        self.credit_label.setText(f"{line} — {CREDIT_NOTICE}")',
     '界面上显示了来源与版权提示', TEST_UI, 1, 'UI-8'),

    ('进浏览态时不隐藏来源提示（在翻本地图库，却挂着上一篇的出处）',
     APP,
     '        self.credit_label.setVisible(False)\n'
     '        # 上面这行排在 _reset_for_browse 之后，而复制按钮是在那边统一刷新的 ——',
     '        # 上面这行排在 _reset_for_browse 之后，而复制按钮是在那边统一刷新的 ——',
     '浏览本地图库时不硬套一个来源', TEST_UI, 1, 'UI-26'),

    # ⚠ 锚点跟过版：文件名从字面量收成了 base.py 的 CREDIT_FILE（2026-10-10），
    #   旧锚点 `os.path.join(folder, '来源说明.txt')` 就找不到了 ——
    #   而"锚点找不到"在脚本里是当**失败**报出来的（不是静默跳过），这正是要的行为。
    ('批次目录里不写来源说明（图离开软件之后就不再带出处）',
     APP,
     "            path = os.path.join(folder, CREDIT_FILE)",
     "            path = os.path.join(folder, CREDIT_FILE + '.bak')",
     '批次目录里落了「来源说明.txt」', TEST_UI, 1, 'UI-8'),

    ('解析收尾不再刷新来源提示（换一篇后界面留着上一篇的署名）',
     APP,
     '        self._article_author = '
     '(getattr(self.worker, "article_author", "") or "").strip()\n'
     '        self._refresh_credit()',
     '        self._article_author = '
     '(getattr(self.worker, "article_author", "") or "").strip()',
     '解析收尾会刷新来源提示', TEST_UI, 1, 'UI-27'),

    # ── 下载时的 Referer：错了整批图全 403，用户看到的是"一张都没下来" ────────
    ('下载时无条件把页面地址当 Referer（ciyuandao 那张图床带了就 403）',
     WORKER,
     "            referer = adapter.download_referer(page_url) if adapter else ''",
     '            referer = page_url',
     'cosmeitu 下载不带 Referer', TEST_EXTRACTOR),

    ('cosmeitu 也带上 Referer（它的图床是反向防盗链，带了整批图全下不来）',
     COSMEITU,
     '当 Referer，在 ciyuandao 上会让**整批图全部 403**，用户看到一张都没下来。\n'
     '        """\n'
     "        return ''",
     '当 Referer，在 ciyuandao 上会让**整批图全部 403**，用户看到一张都没下来。\n'
     '        """\n'
     '        return page_url',
     '下载**不带** Referer', TEST_SITES),

    ('站点接口的默认 Referer 不再沿用页面地址（微信那条路会失去 Referer）',
     SITES_BASE,
     "        return page_url or ''",
     "        return ''",
     '默认站点沿用页面地址作 Referer', TEST_EXTRACTOR),

    # ⚠ 这个文件在同步清单里：源头与副本必须**一起**改，否则先红的是
    #   test_sync 的「副本不一致」，下面那条断言其实没被验到。
    ('安卓端下载也带 Referer（手机上一抓 cosmeitu 就整批 403）',
     [ANDROID_APP, ANDROID_APK],
     ['        headers = build_headers(adapter.download_referer(source))'] * 2,
     ['        headers = build_headers(source)'] * 2,
     'cosmeitu 下载**不带** Referer', TEST_ANDROID),

    # ⚠ 拆坏后是先挂在「张数」那条上的（整类图都提不出来）；「顺序」那条与
    #   详情页共用同一段排序代码，由上面「正文图不按编号排序」那条覆盖。
    ('预览页的 alt「第N张」不再当序号（这类页面又一张都提不出来）',
     COSMEITU,
     '            if not nm:\n'
     '                continue                  # 无 title 也无 alt 序号 —— 侧栏噪音',
     '            if True:\n'
     '                continue                  # 无 title 也无 alt 序号 —— 侧栏噪音',
     '只收正文两张', TEST_SITES),

    # ── 三端同步：漏一行，手机两端的解析能力就与桌面版悄悄分叉 ────────────────
    ('同步清单里漏掉新站点（新增的站点不会同步到手机两端）',
     SYNC,
     "    'sites/cosmeitu.py': 'cosmeitu.com 全部站点知识"
     "（懒加载 data-src / 编号顺序 / OSS 原图档）',\n",
     '',
     '安卓副本', TEST_ANDROID),

    # ── 安卓端适配（2026-10-10）：来源署名、来源说明文件、命令行提示 ─────────
    # 这一组的共同点：**错了手机上完全看不出来**（表现就是"少一行字"或者
    # "目录里少了那份说明"），没人会为此截图来问 —— 只能靠断言钉住。
    ('手机端不取来源署名（界面与说明文件里只剩站点名）',
     [ANDROID_APP, ANDROID_APK],
     ['    author = adapter.extract_author(html)\n'] * 2,
     ["    author = ''\n"] * 2,
     '署名取到了', TEST_ANDROID),

    ('批次目录里不落来源说明（图离开软件之后就不带出处了）',
     [ANDROID_APP, ANDROID_APK],
     ['        if credit_file:\n            write_credit_file(folder, credit)\n'] * 2,
     ['        if credit_file:\n            pass\n'] * 2,
     '批次目录里落了「来源说明.txt」', TEST_ANDROID),

    ('命令行不报来源（手机上唯一的反馈渠道里没有出处）',
     [ANDROID_APP, ANDROID_APK],
     ["    if res.credit:\n        print(f'\U0001f517 {res.credit}')\n"] * 2,
     [''] * 2,
     '命令行里报出了来源', TEST_ANDROID),

    # ⚠ base.py 在同步清单里：三份（桌面 + 手机两端）必须**一起**改。
    #   只改桌面那份的话，先红的是 test_sync 的「副本不一致」，
    #   下面这条断言根本没被验到 —— 又成了一条"看着在验证、其实没验证"的检查。
    ('来源说明文件丢了 .txt 后缀（图库浏览会把它当图片收进去）',
     [SITES_BASE,
      'android/app/web_image_dl/sites/base.py',
      'android-apk/app/src/main/python/web_image_dl/sites/base.py'],
     ["CREDIT_FILE = '来源说明.txt'\n"] * 3,
     ["CREDIT_FILE = '来源说明'\n"] * 3,
     '说明文件是 .txt', TEST_ANDROID),
]


def cases():
    """归一化成 (说明, [(文件, 原, 新), …], 预期, 测试文件, 次数, UI 标签)。

    `old`/`new` 既可以是一对字符串，也可以是**等长的两个列表** ——
    后者用于同步清单里的文件：改动必须同时落在源头与副本上，
    只改一边的话先红的是 test_sync 的「副本不一致」，目标断言根本没被验到。
    """
    out = []
    for c in CASES:
        desc, rel, old, new = c[0], c[1], c[2], c[3]
        if isinstance(old, (list, tuple)):
            rels = rel if isinstance(rel, (list, tuple)) else [rel] * len(old)
            edits = [(r, o, n) for r, o, n in zip(rels, old, new)]
        else:
            edits = [(rel, old, new)]
        out.append((desc, edits, c[4],
                    c[5] if len(c) > 5 else TEST_SITES,
                    c[6] if len(c) > 6 else 1,
                    c[7] if len(c) > 7 else ''))
    return out


def bak(p):
    """备份路径 —— 放在工程外的 `_rv_backup/`，见模块头部的纪律说明。"""
    rel = os.path.relpath(p, ROOT).replace(os.sep, '__')
    return os.path.join(ROOT, '_rv_backup', rel)


def restore_all(verbose=True):
    """把上一轮留下的备份全部还原（中途被掐断时的救命手段）。"""
    n = 0
    for _desc, edits, _expect, _test, _limit, _only in cases():
        for rel, _old, _new in edits:
            p = os.path.join(ROOT, rel)
            if os.path.exists(bak(p)):
                shutil.copyfile(bak(p), p)
                os.remove(bak(p))
                n += 1
                if verbose:
                    print(f'  ↺ 还原残留 {rel}')
    return n


def run_test(test, only=''):
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    if only:
        env['IMGSNAG_UI_ONLY'] = only        # 只跑指定的那一条界面用例（快）
    try:
        r = subprocess.run([sys.executable, test], cwd=ROOT, env=env,
                           capture_output=True, timeout=240)
        return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 999, 'TIMEOUT（超过 240 秒）'


def main():
    out = []

    def say(line=''):
        print(line, flush=True)
        out.append(line)

    say('=== 站点适配层 + 来源提示 反向验证 ===')
    left = restore_all()
    if left:
        say(f'（启动时还原了 {left} 处上一轮的残留）')

    # 基线：没拆坏时必须全绿，否则后面的结论没意义。
    # 界面全套要 30 秒，这里只跑用到的两条单用例 —— 基线的作用是"确认锚点改动前是绿的"。
    baseline_ok = True
    for name, test, only in (('站点适配层', TEST_SITES, ''),
                             ('三端同步', TEST_ANDROID, ''),
                             ('界面 UI-8', TEST_UI, 'UI-8'),
                             ('界面 UI-26', TEST_UI, 'UI-26'),
                             ('界面 UI-27', TEST_UI, 'UI-27')):
        rc, _ = run_test(test, only)
        flag = '✅' if rc == 0 else '❌ 基线就不绿，先修它'
        say(f'基线（{name}，未拆坏）rc={rc}  {flag}')
        if rc != 0:
            baseline_ok = False
    if not baseline_ok:
        say('基线不绿，后面的结论没意义，终止。')
        open(LOG, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
        return 1

    bad = []
    for i, (desc, edits, expect, test, limit, only) in enumerate(cases(), 1):
        broken = {}          # {文件绝对路径: 备份路径} —— 同一文件只备份一次
        fail = None
        for rel, old, new in edits:
            path = os.path.join(ROOT, rel)
            text = open(path, encoding='utf-8').read()
            n = text.count(old)
            if n == 0:
                # 「锚点找不到」必须报失败而不是静默跳过 —— 源码格式一变锚点就失效，
                # 静默跳过的表现是"这条验证消失了"，而报告上一切正常。
                fail = f'✗ 锚点找不到：{desc}\n      在 {rel} 里找不到：{old!r}'
                break
            if n > 1:
                # 锚点不唯一 = 改动位置不可控：replace(…, 1) 会改到第一处，
                # 可能根本不是目标那行，而报告上一切正常。这条要报失败，不能放过。
                fail = f'✗ 锚点不唯一（{n} 处）：{desc}\n      在 {rel} 里：{old!r}'
                break
            if path not in broken:
                os.makedirs(os.path.dirname(bak(path)), exist_ok=True)
                shutil.copyfile(path, bak(path))  # ← 先备份，再改
                broken[path] = bak(path)
            replaced = text.replace(old, new, limit)
            assert replaced != text, f'替换没有生效：{desc}'   # 防"拆坏没发生"被赖到断言头上
            with open(path, 'w', encoding='utf-8') as f:
                f.write(replaced)

        if fail:
            say(f'{i:>2}. {fail}')
            bad.append(f'{desc}（锚点失效）')
            for p, b in broken.items():           # 已经改掉的那几处要还原
                shutil.copyfile(b, p)
                os.remove(b)
            continue

        try:
            rc, log = run_test(test, only)
        finally:
            for p, b in broken.items():           # ← 立刻还原
                shutil.copyfile(b, p)
                os.remove(b)

        hit = [ln.strip() for ln in log.splitlines() if ln.strip().startswith('✗')]
        if rc == 0:
            say(f'{i:>2}. ❌ 拆坏了但测试照绿 → 这条断言是假的：{desc}')
            bad.append(desc)
        else:
            # 变红了只是必要条件，还要看**红的是不是那条断言** ——
            # 所以把失败行原样打出来供核对（不自动匹配段号，免得又写出一条
            # "看起来在验证、其实没验证"的检查）。
            say(f'{i:>2}. ✅ 拆坏「{desc}」→ 变红（{os.path.basename(test)}'
                f'{f" / {only}" if only else ""}，{len(hit)} 项失败）')
            say(f'        期望命中：{expect}')
            for ln in hit[:3]:
                say(f'          {ln}')

    say()
    if bad:
        say(f'✗ {len(bad)} 条断言是假的：')
        for b in bad:
            say(f'    - {b}')
    else:
        say(f'✅ 全部 {len(CASES)} 条反向验证有效 —— 这些断言真的拦得住问题')

    open(LOG, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    return 1 if bad else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
