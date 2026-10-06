# -*- coding: utf-8 -*-
"""反向验证（安卓版，常驻工具）：逐条把源码拆坏，确认对应的测试**真的会变红**

为什么必须做：我自己写的测试全绿，只证明"测试能跑"，不证明"测试有用"。
一个断言写反了、匹配了错误的字符串、或者压根没被执行到，都会安静地绿着。
做安卓版尤其需要这个 —— 本地编译不了、没有真机，测试是唯一的防线，
防线本身是不是真的存在必须先验一遍。

跑法：
    .venv/Scripts/python.exe tools/check_apk_reverse.py

纪律（上一轮踩坑后固化，别省）：
  · **改之前先写 <文件>.rvbak**，脚本启动时先扫残留自动恢复 ——
    整套跑完要一分多钟，可能被中途掐断，而 SIGTERM 下 finally 不执行。
    没有 .rvbak 的话源码会留在"拆坏"状态，而且看起来很正常。
  · 子进程必须显式 timeout，超时算「测试没过」而不是卡死。
  · 输出同时落盘 + flush（管道会缓冲，被掐断时看不到任何中间输出）。
  · 拆坏方式**不要选会触发真实网络**的那种 —— 这个测试全程假网络，安全。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # tools/
ROOT = os.path.dirname(HERE)                       # 项目根
TEST = os.path.join(ROOT, 'tests', 'test_apk_app.py')
LOG = os.path.join(ROOT, '_rv_apk_log.txt')

MANIFEST = 'android-apk/app/src/main/AndroidManifest.xml'
LAYOUT = 'android-apk/app/src/main/res/layout/activity_main.xml'
STRINGS = 'android-apk/app/src/main/res/values/strings.xml'
BRIDGE = 'android-apk/app/src/main/python/imgsnag_android.py'
MAIN_JAVA = 'android-apk/app/src/main/java/com/virmuran/imgsnag/MainActivity.java'
GAL_JAVA = 'android-apk/app/src/main/java/com/virmuran/imgsnag/Gallery.java'
APP_GRADLE = 'android-apk/app/build.gradle.kts'
APK_README = 'android-apk/README.md'
SYNCED = 'android-apk/app/src/main/python/web_image_dl/naming.py'

#: (说明, 文件, 原文, 替换为, 预期变红的用例)
CASES = [
    ('清单里去掉联网权限', MANIFEST,
     '    <uses-permission android:name="android.permission.INTERNET"/>\n', '',
     '[9] 联网权限'),
    ('清单里去掉「分享」入口', MANIFEST,
     '<action android:name="android.intent.action.SEND"/>',
     '<action android:name="android.intent.action.SENDXX"/>',
     '[9] 分享 intent-filter'),
    ('布局里改掉一个 id', LAYOUT,
     'android:id="@+id/log"', 'android:id="@+id/logx"',
     '[10] Java 用到的 R.id'),
    ('删掉一个被引用的字符串', STRINGS,
     '    <string name="msg_busy">上一次还在跑，等它跑完再试</string>\n', '',
     '[10] Java 用到的 R.string'),
    ('Python 侧改中转目录名', BRIDGE,
     "PENDING_DIR_NAME = 'pending'", "PENDING_DIR_NAME = 'staging'",
     '[12] 中转目录名两边一致'),
    ('Java 侧改中转目录名', MAIN_JAVA,
     'PENDING_DIR = "pending"', 'PENDING_DIR = "staging"',
     '[12] 中转目录名两边一致'),
    ('Java 侧改相册目录名', GAL_JAVA,
     'ALBUM = "ImgSnagWeChat"', 'ALBUM = "OtherAlbum"',
     '[12] 手机相册目录名'),
    ('把 polish 改成空操作', BRIDGE,
     '    return message.replace(CLI_CERT_HINT, PHONE_CERT_HINT)',
     '    return message',
     '[3] 换成手机话术'),
    ('构建配置里改 Python 版本', APP_GRADLE,
     '        version = "3.12"', '        version = "3.10"',
     '[14] 版本三方一致'),
    ('构建配置里放宽 minSdk', APP_GRADLE,
     '        minSdk = 29', '        minSdk = 21',
     '[13] minSdk = 29'),
    ('构建配置里换掉 Chaquopy 版本', 'android-apk/build.gradle.kts',
     'id("com.chaquo.python") version "17.0.0"',
     'id("com.chaquo.python") version "16.1.0"',
     '[13] Chaquopy 固定'),
    ('把只带 arm64 改成多带一个 ABI', APP_GRADLE,
     'abiFilters += listOf("arm64-v8a")',
     'abiFilters += listOf("arm64-v8a", "x86_64")',
     '[13] 只带 arm64-v8a'),
    ('说明文档里删掉相册路径', APK_README,
     '手机存储 / Pictures / ImgSnagWeChat', '手机存储 / 某处',
     '[16] 写清了相册路径'),
    ('副本漂移（改了同步过来的文件）', SYNCED,
     'MAX_TITLE_LEN = 24', 'MAX_TITLE_LEN = 25',
     '[8] 没有内容漂移'),
]


def bak(p):
    """备份路径 —— **必须放在工程目录之外的地方**。

    踩过的坑：一开始备份写成 `<文件>.rvbak` 放在原地，而 `res/layout/` 目录下
    的布局文件是被"列目录 + 全读"的方式加载的，于是备份文件也被当成一个布局读了进去，
    拆坏后的文件与没拆坏的备份同时在手 —— 断言照绿，问题被自己的备份掩盖。
    所以备份统一放到 `_rv_backup/`（既不在会扫描的目录里，路径也稳定，
    被掐断后下次启动还能靠它自动还原）。
    """
    rel = os.path.relpath(p, ROOT).replace(os.sep, '__')
    return os.path.join(ROOT, '_rv_backup', rel)


def restore_all(verbose=True):
    """把上一轮留下的 .rvbak 全部还原（中途被掐断时的救命手段）。"""
    n = 0
    for _desc, rel, _old, _new, _expect in CASES:
        p = os.path.join(ROOT, rel)
        if os.path.exists(bak(p)):
            shutil.copyfile(bak(p), p)
            os.remove(bak(p))
            n += 1
            if verbose:
                print(f'  ↺ 还原残留 {rel}')
    return n


def run_test():
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        r = subprocess.run([sys.executable, TEST], cwd=ROOT, env=env,
                           capture_output=True, timeout=150)
        return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 999, 'TIMEOUT（超过 150 秒）'


def main():
    out = []

    def say(line=''):
        print(line, flush=True)
        out.append(line)

    say('=== 安卓版反向验证 ===')
    left = restore_all()
    if left:
        say(f'（启动时还原了 {left} 处上一轮的残留）')

    rc, _ = run_test()
    say(f'基线：未拆坏时 rc={rc}  {"✅" if rc == 0 else "❌ 基线就不绿，先修它"}')
    if rc != 0:
        say('基线不绿，后面的结论没意义，终止。')
        open(LOG, 'w', encoding='utf-8').write('\n'.join(out))
        return 1

    bad = []
    for i, (desc, rel, old, new, expect) in enumerate(CASES, 1):
        path = os.path.join(ROOT, rel)
        text = open(path, encoding='utf-8').read()
        if old not in text:
            say(f'{i:>2}. ✗ 锚点找不到，跳过：{desc}\n      在 {rel} 里找不到：{old!r}')
            bad.append(f'{desc}（锚点失效）')
            continue

        os.makedirs(os.path.dirname(bak(path)), exist_ok=True)
        shutil.copyfile(path, bak(path))          # ← 先备份，再改
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text.replace(old, new, 1))    # 显式关闭：别指望 GC 帮你 flush
        try:
            rc, log = run_test()
        finally:
            shutil.copyfile(bak(path), path)      # ← 立刻还原
            os.remove(bak(path))

        hit = [ln.strip() for ln in log.splitlines() if ln.strip().startswith('✗')]
        if rc == 0:
            say(f'{i:>2}. ❌ 拆坏了但测试照绿 → 这条断言是假的：{desc}')
            bad.append(desc)
        else:
            # 变红了只是必要条件。还要看**红的是不是那条断言** ——
            # 所以这里把失败行原样打出来供核对（段号不在 ✗ 行里，不自动匹配，
            # 免得又写出一条"看起来在验证、其实没验证"的检查）。
            say(f'{i:>2}. ✅ 拆坏「{desc}」→ 变红（{len(hit)} 项失败）')
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
