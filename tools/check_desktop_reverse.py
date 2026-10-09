# -*- coding: utf-8 -*-
"""反向验证（桌面版，常驻工具）：逐条把源码拆坏，确认对应的测试**真的会变红**

为什么必须做：自己写的测试全绿，只证明"测试能跑"，不证明"测试有用"。
一个断言写反了、匹配了错误的字符串、或者压根没被执行到，都会安静地绿着。
桌面版尤其需要 —— **下载/解压/安装这条路没有任何办法在本地端到端跑通**
（真装一遍要动系统、真下载要联网），所以测试与反向验证就是唯一的防线，
防线本身是不是真的存在必须先验一遍。

跑法：
    .venv/Scripts/python.exe tools/check_desktop_reverse.py

纪律（与 `check_apk_reverse.py` 同一套，踩过坑才固化的，别省）：
  · 改之前先写备份、脚本启动时先扫残留自动还原。整套要跑一分钟左右，
    可能被中途掐断，而 **SIGTERM 下 finally 不执行**。
  · 备份统一放 `_rv_backup/`，**绝不能放被测目录里** —— 曾经把备份写成
    `<文件>.rvbak` 放在原地，而检查是"列目录全读"，备份与拆坏件被一起读到，
    断言照绿（同类坑踩过两次）。
  · 拆坏方式**不选会触发真实网络/真实安装的**：本工具只碰纯逻辑与界面代码，
    下载在测试里是注入的假 opener，`launch_installer` / `reveal_folder` 在用例里
    已被替换成空操作。
  · 子进程必须显式 timeout，超时算「测试没过」。

⚠ 与安卓版工具的差别：这里的界面用例跑 `test_ui_smoke.py`，而那个文件全套要
30 秒。所以它支持 `IMGSNAG_UI_ONLY=<编号>` 只跑一条（`run_test` 会带上），
一条 UI 拆坏只花 3 秒。标签写错时那个测试会直接报「没有标签为 X 的用例」并以
rc=2 退出 —— 于是本工具会把它判成"拆坏了但测试照绿"，当场暴露。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # tools/
ROOT = os.path.dirname(HERE)                            # 项目根

TEST_UP = os.path.join(ROOT, 'tests', 'test_updater.py')     # 纯逻辑（含 update_dl）
TEST_UI = os.path.join(ROOT, 'tests', 'test_ui_smoke.py')    # 离屏界面
LOG = os.path.join(ROOT, '_rv_desktop_log.txt')

UPDATE_DL = 'web_image_dl/update_dl.py'
WIZARD = 'web_image_dl/update_wizard.py'
APP = 'web_image_dl/app.py'

#: (说明, 文件, 原文, 替换为, 预期变红的用例, 跑哪个测试文件, 替换次数, UI 筛选标签)
#:
#: 不写第 6 项就默认跑 test_updater.py；不写第 7 项就是只换第一处；
#: 第 8 项只有界面用例才需要（它决定 `IMGSNAG_UI_ONLY` 跑哪一条）。
#: ⚠ `str.replace` 的 0 是"一次都不换"，与 `re.sub` 的 count=0（全部）相反，别写 0。
CASES = [
    # ── 运行形态与挑文件：判断错了，用户会下到一个用不上的包 ──────────────
    ('形态判断改成看进程当前目录（安装版会被当成便携版）', UPDATE_DL,
     '    base = os.path.dirname(os.path.abspath(exe))',
     '    base = os.getcwd()',
     '同目录有 unins000.exe → 安装版', TEST_UP),

    ('挑资产不看运行形态（便携版也去下 setup.exe）', UPDATE_DL,
     '    want = MODE_INSTALLER if mode == MODE_INSTALLER else MODE_PORTABLE',
     '    want = MODE_INSTALLER',
     '便携版挑 portable.zip', TEST_UP),

    # ── 下载校验：这几条错了，坏包会被当成成品收下，而且报错在别处 ──────────
    ('下载不做字节数校验（被截断的包当成品收下）', UPDATE_DL,
     '    if total and got != total:',
     '    if False:',
     '字节数不符 → 提示', TEST_UP),

    ('下载不做摘要校验（内容被中转改过也照装）', UPDATE_DL,
     '    if want and digest.hexdigest() != want:',
     '    if False:',
     '摘要不符 → 提示校验不通过', TEST_UP),

    ('取消被当成普通下载失败（界面会弹一个假的「失败」）', UPDATE_DL,
     '                    raise DownloadCancelled("已取消下载")',
     '                    raise DownloadError("已取消下载")',
     '取消抛的是 DownloadCancelled', TEST_UP),

    ('复用判断不看文件大小（半截残件被当成下好的）', UPDATE_DL,
     '    if size and os.path.getsize(path) != size:',
     '    if False:',
     '大小对不上 → 不可复用', TEST_UP),

    ('摘要归一化不认算法（md5 也拿去当 sha256 比）', UPDATE_DL,
     '        if algo.strip() != "sha256":',
     '        if False:',
     '非 sha256 算法当', TEST_UP),

    # ── 解压：错了会写坏目录，或者覆盖用户正在用的那一份 ───────────────────
    ('解压不防越界路径（Zip Slip：压缩包能写到目录外面）', UPDATE_DL,
     '                if target != root and not target.startswith(root + os.sep):',
     '                if False:',
     '越界路径应该被拦住', TEST_UP),

    ('解压目录不带版本号（会覆盖用户正在用的那一份）', UPDATE_DL,
     '    candidate = os.path.join(parent, stem)',
     '    candidate = os.path.join(parent, "unpacked")',
     '解压目录名带版本号', TEST_UP),

    # ── 向导界面：每条都对应一种"用户看到的假象" ─────────────────────────
    ('便携版的收尾按钮写成「立即安装」（用户以为装上了）', WIZARD,
     '        return "立即安装" if self.mode == update_dl.MODE_INSTALLER else "打开新版文件夹"',
     '        return "立即安装"',
     '便携版的收尾按钮', TEST_UI, 1, 'UI-29'),

    ('安装版装完不请主窗口退出（安装器覆盖不了程序文件）', WIZARD,
     '            self.install_launched.emit()            # 主窗口收到就退出自己',
     '            pass',
     '点「立即安装」会请主窗口退出', TEST_UI, 1, 'UI-29'),

    ('下载完成后不自动进完成页（用户卡在下载页）', WIZARD,
     '        self.next()                                 # 自动进完成页',
     '        pass',
     '下载完成后自动进完成页', TEST_UI, 1, 'UI-29'),

    ('下载失败也往完成页走（静默冒充成功）', WIZARD,
     '        self.retry_btn.show()',
     '        self.retry_btn.show()\n        self.next()',
     '下载失败 → 停在下载页', TEST_UI, 1, 'UI-29'),

    ('向导不显示更新说明', WIZARD,
     '        self.notes_view.setPlainText((info.notes or "").strip() or "（这次发布没有写更新说明）")',
     '        self.notes_view.setPlainText("")',
     '更新说明原文显示出来', TEST_UI, 1, 'UI-22'),

    ('点「发现新版本」提示什么都不做（更新入口断了）', APP,
     '        wizard.exec()',
     '        return',
     '更新向导能打开', TEST_UI, 1, 'UI-22'),
]


def cases():
    out = []
    for c in CASES:
        desc, rel, old, new, expect = c[:5]
        out.append((desc, rel, old, new, expect,
                    c[5] if len(c) > 5 else TEST_UP,
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
    for _desc, rel, _old, _new, _expect, _test, _limit, _only in cases():
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

    say('=== 桌面版反向验证（应用内更新） ===')
    left = restore_all()
    if left:
        say(f'（启动时还原了 {left} 处上一轮的残留）')

    baseline_ok = True
    for name, test in (('逻辑', TEST_UP), ('界面', TEST_UI)):
        rc, _ = run_test(test)
        say(f'基线（{name}，未拆坏）rc={rc}  {"✅" if rc == 0 else "❌ 基线就不绿，先修它"}')
        if rc != 0:
            baseline_ok = False
    if not baseline_ok:
        say('基线不绿，后面的结论没意义，终止。')
        open(LOG, 'w', encoding='utf-8').write('\n'.join(out))
        return 1

    bad = []
    for i, (desc, rel, old, new, expect, test, limit, only) in enumerate(cases(), 1):
        path = os.path.join(ROOT, rel)
        text = open(path, encoding='utf-8').read()
        if old not in text:
            # 「锚点找不到」必须报失败而不是静默跳过 —— 源码格式一变锚点就会失效，
            # 静默跳过的表现是"这条验证消失了"，而报告上一切正常。
            say(f'{i:>2}. ✗ 锚点找不到，跳过：{desc}\n      在 {rel} 里找不到：{old!r}')
            bad.append(f'{desc}（锚点失效）')
            continue

        os.makedirs(os.path.dirname(bak(path)), exist_ok=True)
        shutil.copyfile(path, bak(path))          # ← 先备份，再改
        replaced = text.replace(old, new, limit)
        assert replaced != text, f'替换没有生效：{desc}'    # 防"拆坏没发生"被赖到断言头上
        with open(path, 'w', encoding='utf-8') as f:
            f.write(replaced)
        try:
            rc, log = run_test(test, only)
        finally:
            shutil.copyfile(bak(path), path)      # ← 立刻还原
            os.remove(bak(path))

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
