# -*- coding: utf-8 -*-
"""一次跑完所有测试 —— 发版闸门

用法：
    .venv/Scripts/python.exe tests/run_all.py            # 全部跑
    .venv/Scripts/python.exe tests/run_all.py test_ui_smoke.py   # 只跑指定的

为什么要有这个脚本（而不是在终端里一条条敲）：
  1. 三个测试文件各自会开 QApplication，在同一个进程里连着跑会互相污染（Qt 全局状态、
     QSettings、blocked_config 单例），所以每个文件起独立子进程，互不干扰。
  2. 离屏跑 GUI 需要 QT_QPA_PLATFORM=offscreen，不设就找不到显示器直接失败；
     中文还会因为找不到字体渲染成方块（QT_QPA_FONTDIR）。
  3. 输出同时写一份到 tests/_last_run.txt —— Windows 下通过其它工具转手拿 stdout
     经常拿到空内容，落盘再读才可靠。

退出码：全绿 0，有失败 1。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPORT = os.path.join(HERE, '_last_run.txt')

DEFAULT_FILES = [
    'test_extractor.py',       # 提取/画质/保存链路（纯逻辑）
    'test_filter_rules.py',    # 屏蔽尺寸与过滤规则（纯逻辑）
    'test_naming.py',          # 图库文件夹命名与清洗（纯逻辑）
    'test_library.py',         # 历史批次文件夹扫描与排序（纯逻辑）
    'test_updater.py',         # 版本检测与检查更新（纯逻辑，不联网）
    'test_ui_smoke.py',        # 界面回归（离屏）
]


def main():
    names = [a for a in sys.argv[1:] if not a.startswith('-')]
    files = names or DEFAULT_FILES

    env = dict(os.environ)
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
    env['PYTHONIOENCODING'] = 'utf-8'

    lines, failed = [], []
    for name in files:
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            lines.append(f'!! 找不到 {name}\n')
            failed.append(name)
            continue
        t0 = time.time()
        r = subprocess.run([sys.executable, path], cwd=ROOT, env=env, capture_output=True)
        out = r.stdout.decode('utf-8', 'replace')
        err = r.stderr.decode('utf-8', 'replace')
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-2:]
        lines.append(f'--- {name}  rc={r.returncode}  {time.time() - t0:.1f}s')
        lines.extend('    ' + ln for ln in tail)
        if r.returncode != 0:
            failed.append(name)
            for ln in out.strip().splitlines():
                if '✗' in ln:
                    lines.append('    ' + ln)
            if err.strip():
                lines.append('    stderr: ' + err.strip()[-1500:])
        lines.append('')

    text = '\n'.join(lines)
    print(text)
    total = f'合计 {len(files)} 个测试文件，失败 {len(failed)} 个' + \
            (f'：{", ".join(failed)}' if failed else ' —— 全部通过 ✅')
    print(total)
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write(text + '\n' + total + '\n')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
