# -*- coding: utf-8 -*-
"""给安卓版的**网页界面**拍几张图（本机、无头浏览器，不需要模拟器也不需要真机）

为什么需要它：
  界面这一层已经能靠 `tests/test_apk_web.py` 验"逻辑对不对"（真服务、真请求），
  但"**看起来对不对**"（挤成两行、按钮压住内容、颜色看不清）测试是说不出来的。
  没有模拟器的条件下，用现成的浏览器内核截几张图是唯一的办法。

跑法：
    .venv/Scripts/python.exe tools/shot_web_ui.py
    产物在 _ui_shots/webui/（已被 .gitignore 忽略）

它不碰任何产品代码：
  · 网络是假的（复用测试里的假 Get，两档地址给不同字节）
  · 相册是假的（写进临时目录）
  · 「点开大图」「切到历史页」这两步是往**内存里那份页面**追加一小段脚本实现的，
    不写回 webui.py —— 免得为了拍张照给产品代码留钩子。

**已知限制（本机实测，2026-10-06）**：
  这台机器上只有 chrome-headless-shell 能跑起来（chrome.exe --headless=new 与
  Edge 都静默失败、什么都不输出），而这个精简内核**不加载图片** ——
  证据是截图跑完打印的「图片请求 0 条」：页面里的 <img loading="lazy">
  连一次网络请求都没发起（把它改成 eager 再重赋 src 也没用）。
  所以截图里的缩略图是"没下下来"占位、大图弹层也弹不出来。
  这**不是页面代码的问题**：真机的 WebView 是完整内核，懒加载正常工作。
  换一台有完整 Chromium 的机器跑本脚本，四张图就都是带图的。
  截图在此处的作用：查**布局**（网格列数、按钮位置、间距、有没有叠住）——
  这个它完全够用。
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'android-apk', 'app', 'src', 'main', 'python'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))

import imgsnag_web as web                                     # noqa: E402
import test_apk_web as T                                      # noqa: E402
import webui                                                  # noqa: E402

OUT = os.path.join(ROOT, '_ui_shots', 'webui')
PHONE = '390,844'          # 逻辑分辨率：跟红米 K80 一个量级
SCALE = '2'                # 2 倍图，看细节用

#: 页面原文。注入脚本时始终以它为底（见 inject）。
ORIG_PAGE = webui.PAGE


def find_chrome():
    """找一个能用的浏览器内核。

    **优先 chrome-headless-shell**：它是专门为无头场景做的那个内核。
    这台机器上 `chrome.exe --headless=new` 与 Edge 都不行 ——
    表现是"命令静默退出、一个字都不输出、截图文件也不生成"，
    而 headless-shell 一次就成。这类问题没有任何报错可看，只能靠换内核试出来。
    """
    home = os.path.expanduser('~')
    cands = [
        os.path.join(home, 'AppData/Local/ms-playwright/'
                            'chromium_headless_shell-*/chrome-headless-shell-win64/'
                            'chrome-headless-shell.exe'),
        os.path.join(home, 'AppData/Local/ms-playwright/'
                            'chromium-*/chrome-win64/chrome.exe'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    ]
    for pat in cands:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def shot(chrome, url, name, budget=60000):
    """拍一张。budget 是"最多让页面跑多少毫秒"（虚拟时间，跑得比真实时间快）。"""
    path = os.path.join(OUT, name)
    # 每张图一个独立的 profile 目录。共用的话后一张会去抢前一张留下的锁，
    # 表现是"前几张都好好的，某一张突然卡死到超时"。
    profile = os.path.join(OUT, '_profile', os.path.splitext(name)[0])
    shutil.rmtree(profile, ignore_errors=True)
    # ⚠ `--no-sandbox` 不能省：这台机器上沙箱起不来（报
    #   "Sandbox cannot access executable … 拒绝访问"）。
    # headless-shell 自己就是无头内核，不能再传 --headless（传了会被当成未知参数）。
    cmd = [chrome, '--no-sandbox', '--disable-gpu',
           '--no-first-run', '--no-default-browser-check', '--disable-extensions',
           '--hide-scrollbars', f'--force-device-scale-factor={SCALE}',
           f'--window-size={PHONE}', f'--user-data-dir={profile}',
           f'--virtual-time-budget={budget}', f'--screenshot={path}', url]
    if 'headless-shell' not in os.path.basename(chrome).lower():
        cmd.insert(1, '--headless=new')
    # ⚠ 一张卡住不该让整批中断：早先 `subprocess.run` 超时直接抛栈退出，
    #   结果是"前三张拍好了、第四张没了"，而报错信息是一整段 panic 回溯，
    #   看半天看不出"其实只是最后一张没拍到"。
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        print(f'  ❌ {name}  超过 180 秒还没截出来（多半是页面里还有没停的轮询，'
              f'见 inject() 的冻结说明）')
        return False
    ok = os.path.isfile(path) and os.path.getsize(path) > 2000
    size = os.path.getsize(path) // 1024 if os.path.isfile(path) else 0
    print(f'  {"✅" if ok else "❌"} {name}  ({size} KB)')
    if not ok:
        err = (r.stderr or b'').decode('utf-8', 'replace')[-400:]
        print('     ', err.replace('\n', ' ')[:380])
    return ok


def inject_prepare(action):
    """注入一段"准备"脚本：等网格画出来，再执行 action。

    为什么等的是 `.tile` 而不是图片加载完：这个内核**不加载图片**（见文件头
    的已知限制），`img.complete` 永远是假 —— 上一版等的就是它，
    结果大图那一步永远等不到，拍出来跟结果页一模一样（两份文件字节都相同）。
    格子是 JS 画的，不依赖图片下载，出现即代表"页面状态已就绪"。

    另外顺手把 `loading="lazy"` 改成 eager：在有完整内核的机器上，
    这样能拍到真的缩略图；在这个内核上没有任何效果，但也不碍事。
    """
    inject(
        "window.addEventListener('load',function(){"
        "var tries=0;"
        "var t=setInterval(function(){"
        "tries++;"
        "var ims=document.querySelectorAll('#grid img');"
        "for(var k=0;k<ims.length;k++){"
        "if(ims[k].getAttribute('loading')!=='eager'){"
        "ims[k].setAttribute('loading','eager');ims[k].src=ims[k].src;"
        "}}"
        "if(document.querySelectorAll('#grid .tile').length||tries>200){"
        "clearInterval(t);" + action +
        "}},150);});")


def inject(js):
    """往**内存里那份**页面追加一段脚本（不写回 webui.py）。

    每次都从**原始页面**拼，不是在上一份注入的基础上再拼 ——
    否则拍第三张时会把第一张的注入脚本也带上（大图页会莫名其妙自己弹出来）。

    ⚠ 花括号必须配对，这里当场查：这批脚本全是手工拼的字符串，
    少写/多写一个 `}` 就是整段语法错误 —— 浏览器**一声不吭地扔掉**，
    表现是"注入的步骤永远不发生"，跟"页面没加载好"长得一模一样
    （大图那张怎么拍都是结果页，排查了一圈内核限制，最后发现是自己括号多了）。

    另外这里**固定**追加一段"冻结轮询"：页面自身有个永不停止的状态轮询
    （`tick()` 每 1.2s 发一次 `/api/state`）。在 `--virtual-time-budget` 下，
    只要还有 fetch 在飞，浏览器就**暂停虚拟时钟** —— 于是预算永远到不了，
    截图卡到真实 180 秒超时（本轮 04 历史页实测，而前一张同样的页面却是好的，
    属"重跑就变、查起来最费劲"的那种）。冻结之后页面不再发请求，
    虚拟时钟一口气跑完预算，四张图都稳。
    """
    a, b = js.count('{'), js.count('}')
    if a != b:
        raise RuntimeError(f'注入的脚本花括号不配对：{{ {a} 个、}} {b} 个 —— '
                           '整段会被当成语法错误扔掉，先修这里再拍')
    # 等状态落定（网格/历史都画完）再把 tick 换空函数：它自己就不会再排下一次
    # ⚠ 必须**同步**掐，不能等 setTimeout 再掐：虚拟时钟在还有 fetch 在飞时
    #   是暂停的，而轮询永远有 fetch 在飞 → "过 2.6 秒再掐"那个定时器自己
    #   就永远等不到（上一版实测 04 还是超时）。注入脚本跑在主脚本之后，
    #   此时 busyTimer 已经排上了 —— 同步 clearTimeout + 换掉 tick，一次掐死；
    #   首屏状态由 refresh()（一次性 fetch）照常画出来，不依赖轮询。
    js += ("try{window.tick=function(){};clearTimeout(busyTimer);}catch(e){}")
    webui.PAGE = ORIG_PAGE.replace('</body>', f'<script>{js}</script></body>')


def wait_ready(timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if web.engine().state()['phase'] in ('ready', 'saved', 'error'):
            return True
        time.sleep(0.1)
    return False


def wait_check(timeout=30):
    """等小图体检跑完 —— 它是解析之后在后台接着跑的。

    不等的话，截图里可能一张「小」标都没有（还没量到），
    看着像是功能没生效，其实是没等够。
    """
    end = time.time() + timeout
    while time.time() < end:
        c = web.engine().state()['check']
        if not c['running'] and c['done']:
            return True
        time.sleep(0.1)
    return False


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    chrome = find_chrome()
    if not chrome:
        print('❌ 没找到 Chrome/Chromium。装一个浏览器，或先跑 playwright install chromium。')
        return 1
    print(f'浏览器：{chrome}')

    os.makedirs(OUT, exist_ok=True)
    shutil.rmtree(os.path.join(OUT, '_profile'), ignore_errors=True)

    tmp = tempfile.mkdtemp()
    try:
        # 三张图：两张正常大小、一张明显偏小 —— 这样截图里能一眼看到
        # 「小」标记、自动不勾选、以及顶部那句"已自动跳过 1 张小图"。
        # ⚠ 匹配键用"文件号"那一段（适配器会把查询串剥掉），见 test_apk_web 里的注释。
        fake = T.FakeGet(pages={T.ARTICLE_URL: T.article((T.PIC_A, T.PIC_B, T.PIC_T))},
                         sizes={'AAAAAAAABBBBCCCC': (T.BIG_A, T.BIG_A),
                                'DDDDDDDDEEEEFFFF': (T.BIG_A, T.BIG_A),
                                'TINYTINYTINY': (T.TINY_T, T.TINY_T)})
        album = T.FakeAlbum(os.path.join(tmp, 'album'))
        port = web.start(root=tmp, version='1.10.0', get=fake, publish=album)
        base = f'http://127.0.0.1:{port}'
        print(f'本地服务：{base}')

        ok = True

        # ① 首页：还没解析，空态（含两个偏好开关）
        ok &= shot(chrome, base + '/', '01_首页空态.png')

        # ② 结果页：先在服务端把文章解析好，页面一进来就是"找到 3 张图"，
        #    并强制缩略图立即加载（无头环境不触发懒加载，见 inject_prepare）
        web.engine().parse(T.TITLE + '\n' + T.ARTICLE_URL)
        if not wait_ready():
            print('❌ 解析没在预期时间内完成')
            return 1
        if not wait_check():
            print('⚠ 小图体检没在预期时间内跑完（截图里可能看不到「小」标）')
        inject_prepare('')
        ok &= shot(chrome, base + '/', '02_结果网格.png')

        # ③ 大图预览：同上，等缩略图真的画出来就点开第一张。
        #    ⚠ 等的是 `.tile` / `complete`，不是"有没有 img"：加载失败时
        #    onerror 会把整个 <img> 换成"没下下来"的占位文本，
        #    这时候网格里一个 img 都没有了，条件会永远不成立。
        inject_prepare('openViewer(1);')
        ok &= shot(chrome, base + '/', '03_大图预览.png')

        # ④ 历史页：解析过就有一条记录了，切过去看
        inject(
            "window.addEventListener('load',function(){"
            "var t=setInterval(function(){"
            "  if(typeof setView==='function'){"
            "    clearInterval(t);setView('history');loadHistory();"
            "  }},150);});")
        ok &= shot(chrome, base + '/', '04_下载历史.png')

        # ⑤ 设置页：版本更新 / 关于（v2.1.0 从首页搬过来）。
        #    静态页不用等数据，setView 一调就能拍 —— 但仍要走轮询等 `setView`
        #    定义好（注入的监听器可能跑在页面脚本前面）。
        inject(
            "window.addEventListener('load',function(){"
            "var t=setInterval(function(){"
            "  if(typeof setView==='function'){"
            "    clearInterval(t);setView('settings');"
            "  }},150);});")
        ok &= shot(chrome, base + '/', '05_设置页.png')

        print()
        # 证据：浏览器侧到底有没有向服务端要过图。
        # 缩略图全是"没下下来"占位时，先看这里 —— 一条都没有说明请求压根没发出
        # （多半是无头环境不触发懒加载），有请求有响应才轮到怀疑页面本身。
        imgs = [u for u in fake.calls if '/img/' in u]
        print(f'（证据）浏览器经服务端发起的图片请求：{len(imgs)} 条')
        for u in imgs[:6]:
            print('   ', u)

        if ok:
            print(f'✅ 五张图都在：{os.path.relpath(OUT, ROOT)}/')
        else:
            print('⚠ 有图没拍成，看上面的报错。')
        return 0 if ok else 1
    finally:
        try:
            web.stop()
        except Exception:                                     # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
