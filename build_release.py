"""ImgSnag 微信公众号版 一键发版脚本
用法:  .venv/Scripts/python.exe build_release.py [--skip-build]
流程:  校验版本号 -> 同步 .iss 版本号 -> PyInstaller onedir -> Inno Setup 安装包 -> 便携 zip
产物:  installer/ImgSnagWeChat_<版号>_setup.exe
       dist/ImgSnagWeChat_<版号>_portable.zip

升版本号：改 version.py 里的 VERSION（唯一手写处），本脚本会自动同步到 .iss。
"""
import os
import re
import subprocess
import sys
import zipfile

# 输出统一 UTF-8：管道/重定向下 Windows 默认走 GBK，print ⚠ ✗ 这类字符会直接
# UnicodeEncodeError 崩掉（v1.8.0 打包时真实踩过 —— installer 里已有同版本产物
# 时 version_gate 打 ⚠，当场炸在闸门上）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

ISCC = r"C:\Program Files\Inno Setup 7\ISCC.exe"
APP = "ImgSnagWeChat"
SPEC = f"{APP}.spec"
ISS = f"{APP}.iss"


def step(msg):
    print(f"\n=== {msg} ===", flush=True)


def run(cmd):
    print("  $", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        sys.exit(f"步骤失败: {cmd[0]}")
    return r


def version_gate(ver: str):
    """发版前的版本号闸门 —— 不合规直接中断打包。

    拦两类坑：
      ① 格式不合法（日期当号 / 四段 / 前导零 / 带 v 前缀）
      ② 该版本号已打过包（改了代码却忘了升号，会覆盖上一份产物）
    """
    step("校验版本号")
    from version import is_valid_version

    if not is_valid_version(ver):
        sys.exit(f"✗ 版本号 '{ver}' 不符合规范（须为三段纯数字 X.Y.Z，禁止日期/四段/前导零）。\n"
                 f"  改 version.py 里的 VERSION")

    prev = os.path.join(ROOT, "installer", f"{APP}_{ver}_setup.exe")
    if os.path.exists(prev):
        print(f"  ⚠ v{ver} 已存在安装包 {os.path.basename(prev)}，本次会覆盖它")
        print("     （改了代码就该升号：改 version.py 的 VERSION）")
    else:
        print(f"  v{ver} 格式合法，无同名产物")


def main():
    # 0) 版本号
    sys.path.insert(0, ROOT)
    from version import VERSION  # noqa: E402
    ver = VERSION
    print(f"ImgSnag 微信公众号版 v{ver}")
    version_gate(ver)

    # 1) 同步 .iss 的 AppVersion（防两处不同步）
    step("同步 ImgSnagWeChat.iss 版本号")
    path = os.path.join(ROOT, ISS)
    text = open(path, encoding="utf-8").read()
    new = re.sub(r'#define MyAppVersion "[^"]*"', f'#define MyAppVersion "{ver}"', text)
    if new != text:
        open(path, "w", encoding="utf-8").write(new)
        print(f"  {ISS} AppVersion -> {ver}")
    else:
        print(f"  已一致: {ver}")

    # 2) PyInstaller onedir
    if "--skip-build" not in sys.argv:
        step("PyInstaller 打包（约 1~2 分钟）")
        run([os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
             "-m", "PyInstaller", SPEC, "--noconfirm"])

    # 3) Inno Setup 安装包
    step("Inno Setup 编译安装包")
    run([ISCC, ISS])

    # 4) 便携 zip（ZIP_LZMA；勿用 bsdtar 默认 deflate，体积差数倍）
    step("制作便携 zip")
    src = os.path.join(ROOT, "dist", APP)
    out = os.path.join(ROOT, "dist", f"{APP}_{ver}_portable.zip")
    if os.path.exists(out):
        os.remove(out)
    n = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_LZMA, compresslevel=9) as z:
        for root, _dirs, files in os.walk(src):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, os.path.join(ROOT, "dist")))
                n += 1

    # 5) 汇总
    setup = os.path.join(ROOT, "installer", f"{APP}_{ver}_setup.exe")
    mb = lambda p: round(os.path.getsize(p) / 1048576, 1)
    print("\n=== 打包完成 ===")
    print(f"  安装包  {setup}  ({mb(setup)} MB)")
    print(f"  便携包  {out}  ({mb(out)} MB, {n} 文件)")
    print(f"  免安装目录  {src}")
    print("\n分发说明:")
    print("  · 安装包：双击下一步到底，自动建开始菜单和桌面快捷方式")
    print("  · 便携包：解压即用，双击 ImgSnagWeChat.exe 直接跑，零联网")
    print("  · 用户数据（历史库/屏蔽尺寸）在用户目录，卸载不删、升级不丢")


if __name__ == "__main__":
    main()
