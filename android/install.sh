#!/usr/bin/env bash
# ============================================================================
#  ImgSnag Termux —— 一键安装
#
#  在手机的 Termux 里运行：
#
#      bash android/install.sh
#
#  它做五件事：装 Python 与 requests → 把程序放到 ~/imgsnag →
#  申请存储权限 → 装 imgsnag 命令 → 装「分享 → Termux」入口。
#  重复运行是安全的（幂等），以后更新代码后再跑一次即可。
# ============================================================================
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_APP="$SCRIPT_DIR/app"
TARGET="$HOME/imgsnag"
BIN="$HOME/bin"

say()  { printf '%s\n' "$*"; }
die()  { printf '\n✗ %s\n' "$*" >&2; exit 1; }

say "ImgSnag Termux 安装"
say "==================="

# ── 0. 确认在 Termux 里 ──────────────────────────────────────────────
# 这一步不是形式主义：下面的流程会写 ~/ 并申请安卓存储权限，
# 在电脑的 Linux/Git Bash 上误跑会弄出一个没人用的目录树。
case "${PREFIX:-}" in
    *com.termux*) : ;;
    *) die "这看起来不是 Termux 环境（\$PREFIX 不含 com.termux）。
   请在手机的 Termux App 里运行本脚本：bash android/install.sh" ;;
esac

# ── 1. Python 与 requests ────────────────────────────────────────────
say "· 检查 Python…"
if ! command -v python >/dev/null 2>&1; then
    say "  没装，正在安装（第一次会慢一点）…"
    pkg update -y >/dev/null 2>&1 || true
    pkg install -y python || die "Python 安装失败，请检查网络后重试"
fi
say "  $(python --version 2>&1)"

say "· 检查 requests…"
if ! python -c "import requests" >/dev/null 2>&1; then
    say "  正在安装…"
    # 新版 Termux 的 Python 是「外部管理」环境，普通 pip install 会被拒，
    # 所以要带 --break-system-packages 兜底（这是官方推荐的用法，不是绕过安全）
    pip install --quiet requests 2>/dev/null \
        || pip install --quiet --break-system-packages requests \
        || die "requests 安装失败，请检查网络后重试"
fi
python -c "import requests" >/dev/null 2>&1 || die "requests 装了但导入不了"

# ── 2. 安装程序本体 ──────────────────────────────────────────────────
say "· 安装到 $TARGET"
mkdir -p "$TARGET"
# 先删掉旧的 web_image_dl 副本再整体拷贝：直接覆盖会留下"已经删掉的模块"，
# 那种残留最难查（import 到旧文件，行为和新代码对不上）
rm -rf "$TARGET/web_image_dl"
cp -R "$SRC_APP/." "$TARGET/"
chmod +x "$TARGET/imgsnag.py" 2>/dev/null || true

# ── 3. 存储权限 ──────────────────────────────────────────────────────
if [ ! -d "$HOME/storage/shared" ]; then
    say "· 申请存储权限 —— 请在手机上点「允许」"
    termux-setup-storage >/dev/null 2>&1 || true
    sleep 3
    if [ -d "$HOME/storage/shared" ]; then
        say "  ✓ 已授权，图片会存到相册目录"
    else
        say "  ⚠ 还没授权。图片会先存在 Termux 私有目录里，相册看不到。"
        say "    想存到相册：退出 Termux 再打开，重新运行本脚本即可。"
    fi
else
    say "· 存储权限已就绪"
fi

# ── 4. imgsnag 命令 ──────────────────────────────────────────────────
mkdir -p "$BIN"
cat > "$BIN/imgsnag" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
exec python "$HOME/imgsnag/imgsnag.py" "$@"
EOF
chmod +x "$BIN/imgsnag"
say "· 已安装命令：imgsnag"

# Termux 默认不会把 ~/bin 放进 PATH，补进去
case ":$PATH:" in
    *":$BIN:"*) : ;;
    *)
        if ! grep -q 'HOME/bin' "$HOME/.bashrc" 2>/dev/null; then
            printf '\n# ImgSnag: 让 imgsnag 命令直接可用\nexport PATH="$HOME/bin:$PATH"\n' >> "$HOME/.bashrc"
        fi
        ;;
esac

# ── 5. 「分享 → Termux」入口 ─────────────────────────────────────────
cp "$SCRIPT_DIR/termux-url-opener" "$BIN/termux-url-opener"
chmod +x "$BIN/termux-url-opener"
say "· 已安装分享入口：termux-url-opener"

# ── 完成 ─────────────────────────────────────────────────────────────
say ""
say "✅ 装好了"
say ""
say "用法一（推荐）：微信文章右上角「…」→ 分享 → 选 Termux"
say "用法二：复制文章链接，回到 Termux 敲："
say "        imgsnag"
say "        （需装 Termux:API；不想装就把链接直接跟在后面）"
say ""
say "搞不定就看 android/README.md，那里有红米/HyperOS 的两个坑要绕。"
