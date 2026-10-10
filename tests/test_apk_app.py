# -*- coding: utf-8 -*-
"""安卓版（APK）回归测试 —— 纯逻辑 + 假网络 + 静态自检，不联网、不动用户目录

运行：
    .venv/Scripts/python.exe tests/test_apk_app.py       # 本地
    python3 tests/test_apk_app.py                        # CI（会在打包前先跑一遍）

────────────────────────────────────────────────────────────────────────
为什么这个文件的存在感特别强
────────────────────────────────────────────────────────────────────────
桌面版和 Termux 版我可以启动起来点两下，APK **不行** —— 手上没有 JDK、没有
Android SDK、没有安卓设备，本机连编译都做不到，构建只能放到 GitHub 上跑。
一次构建要等好几分钟，而且报错信息（Gradle / R8 / AAPT / Chaquopy）通常很难懂。

所以策略是：**把"能在这里判定的事"全部判定掉**，只把真正需要安卓环境的那部分
留给云端。这个文件因此有两类用例：

一、Python 侧的行为（真跑）
   证书合并、urllib 响应包装、报告文本、以及**整条抓取链路**（注入假网络，
    落盘到临时目录再回读）。这些在 Windows 上就能完整验证 ——
    手机端最容易坏的恰恰是接缝处，不是纯函数。

二、跨文件的静态契约（读文件比对）
   安卓工程里有一堆"写错了要到编译甚至运行时才炸"的对应关系：
    · Java 里 `R.id.xxx` / `R.string.xxx` 在布局/资源里必须存在（否则编译不过）
    · Java 的 `package` 必须与目录结构一致（否则编译不过）
    · 清单里的 `.MainActivity` 必须能解析到那个类
    · Java 的 `PENDING_DIR` 与 Python 的 `PENDING_DIR_NAME` 必须相同
      （不同的话：图抓到了、相册里没有、两边都不报错 —— 最难查的一类）
    · `build.gradle.kts` 里的 Python 版本必须与工作流里装的版本一致
      （Chaquopy 17 要求两者主次号完全相同，否则构建直接失败）
    这些用几行正则就能钉死，比等云端构建划算得多。

⚠ 这个文件**不导入 PySide6**，所以 CI 上也能跑（CI 只有裸 python3.12）。
"""
import inspect
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

# ── 路径 ──
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK = os.path.join(ROOT, 'android-apk')
APK_APP = os.path.join(APK, 'app')
SRC_MAIN = os.path.join(APK_APP, 'src', 'main')
PY_DIR = os.path.join(SRC_MAIN, 'python')
JAVA_PKG_DIR = os.path.join(SRC_MAIN, 'java', 'com', 'virmuran', 'imgsnag')
RES_DIR = os.path.join(SRC_MAIN, 'res')
MANIFEST = os.path.join(SRC_MAIN, 'AndroidManifest.xml')
APP_GRADLE = os.path.join(APK_APP, 'build.gradle.kts')
ROOT_GRADLE = os.path.join(APK, 'build.gradle.kts')
SETTINGS_GRADLE = os.path.join(APK, 'settings.gradle.kts')
KEYSTORE = os.path.join(APK, 'keystore', 'imgsnag.p12')
WORKFLOW = os.path.join(ROOT, '.github', 'workflows', 'build-apk.yml')
#: 桌面版的云构建（Publish 后自动把 exe / 便携 zip 挂进同一个 Release）
DESKTOP_WORKFLOW = os.path.join(ROOT, '.github', 'workflows', 'build-desktop.yml')
README = os.path.join(APK, 'README.md')

sys.path.insert(0, os.path.join(ROOT, 'android'))     # sync_core
sys.path.insert(0, PY_DIR)                            # imgsnag / imgsnag_android

import sync_core                                             # noqa: E402
import imgsnag                                               # noqa: E402
import imgsnag_android as android                            # noqa: E402

PACKAGE = 'com.virmuran.imgsnag'

_passed = 0
_failed = []


def check(cond, label):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(label)
        print(f'  ✗ {label}')


def eq(actual, expected, label):
    check(actual == expected, f'{label}（实际 {actual!r}，期望 {expected!r}）')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


# ============================================================================
#  一、Python 侧行为
# ============================================================================

MMBIZ_JPG = 'https://mmbiz.qpic.cn/mmbiz_jpg/AAAAAAAABBBBCCCC/640?wx_fmt=jpeg'
MMBIZ_PNG = 'https://mmbiz.qpic.cn/mmbiz_png/DDDDDDDDEEEEFFFF/640?wx_fmt=png'
ARTICLE_URL = 'https://mp.weixin.qq.com/s/abcdefg'
JPEG = b'\xff\xd8\xff\xe0' + b'J' * 800          # > MIN_IMAGE_BYTES，够假
PNG = b'\x89PNG\r\n\x1a\n' + b'P' * 900

ARTICLE = (
    '<html><head><meta property="og:title" content="秋天的第一杯奶茶"></head>'
    '<body>'
    'var msg_title = "秋天的第一杯奶茶";'
    '<img data-src="' + MMBIZ_JPG + '">'
    '<img data-src="' + MMBIZ_PNG + '">'
    '</body></html>'
)
EMPTY_ARTICLE = ('<html><head><meta property="og:title" content="没有图"></head>'
                 '<body><p>这篇真的没有图片</p></body></html>')

#: 分享出去的样子：标题 + 换行 + 链接（微信就是这么给的）
SHARE_TEXT = '秋天的第一杯奶茶\n' + ARTICLE_URL


class FakeGet:
    """假网络。按 URL 决定返回什么，并记下所有请求。"""

    def __init__(self, pages=None):
        self.pages = pages or {}
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url in self.pages:
            return android._Response(200, self.pages[url].encode('utf-8'))
        if 'mmbiz.qpic.cn' in url:
            return android._Response(200, PNG if 'png' in url else JPEG)
        return android._Response(404, b'not found')

    def hits(self, needle):
        return sum(1 for u in self.calls if needle in u)


def test_ca_bundle():
    print('[1] 系统根证书合并')
    tmp = tempfile.mkdtemp()
    try:
        ca = os.path.join(tmp, 'cacerts')
        os.makedirs(ca)
        pem = b'-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'
        for name in ('aaa.0', 'bbb.0'):
            with open(os.path.join(ca, name), 'wb') as f:
                f.write(pem)
        # 安卓的证书目录里混着元数据文件，必须按内容而不是文件名筛
        with open(os.path.join(ca, 'README'), 'wb') as f:
            f.write(b'this is not a certificate')

        out = os.path.join(tmp, 'bundle.pem')
        got = android.system_ca_bundle(out, dirs=[ca])
        eq(got, out, '读得到证书时返回写出的路径')
        body = read(out)
        eq(body.count('BEGIN CERTIFICATE'), 2, '只收下了两份真正的证书')
        check('this is not a certificate' not in body, '元数据文件没有被当成证书')

        # 一个证书都没有 → 必须返回 None（写空包比不写更糟）
        empty = os.path.join(tmp, 'empty')
        os.makedirs(empty)
        eq(android.system_ca_bundle(os.path.join(tmp, 'x.pem'), dirs=[empty]),
           None, '目录为空时返回 None')
        eq(android.system_ca_bundle(os.path.join(tmp, 'y.pem'),
                                    dirs=[os.path.join(tmp, 'nope')]),
           None, '目录不存在时返回 None')

        check(android.ssl_context(cache_dir=tmp, ca_dirs=[ca]) is not None,
              'ssl_context 能构造出来')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_response_wrapper():
    print('[2] urllib 响应包装')
    r = android._Response(200, '中文'.encode('utf-8'))
    eq(r.text, '中文', 'text 按 utf-8 解码')
    eq(r.content, '中文'.encode('utf-8'), 'content 是原始字节')
    r.raise_for_status()
    check(True, '2xx 不抛异常')

    try:
        android._Response(404, b'x').raise_for_status()
        check(False, '404 应当抛异常')
    except Exception:
        check(True, '404 抛异常（imgsnag 靠它换下一个画质档位）')


def test_polish():
    print('[3] 命令行措辞换成手机话术')
    out = android.polish(f'打开文章失败：证书错误{android.CLI_CERT_HINT}')
    check('--insecure' not in out, '不再让用户去敲手机上不存在的 --insecure')
    check(android.PHONE_CERT_HINT in out, '换成了手机上说得通的提示')
    eq(android.polish(''), '', '空消息原样返回')
    eq(android.polish(None), None, 'None 原样返回')

    # 这句提示是 imgsnag.py 里的原文，那边改了措辞这里必须跟着改 ——
    # 否则 polish 会静默地什么都不替换（不报错、也没效果）
    check(android.CLI_CERT_HINT in read(os.path.join(PY_DIR, 'imgsnag.py')),
          'CLI_CERT_HINT 依然是 imgsnag.py 里的原文（改了要同步）')


def test_build_report():
    print('[4] 报告文本')
    ok = imgsnag.Result(folder='x', saved=['a.jpg', 'b.jpg'], total=2,
                        total_bytes=2048, skipped=0, title='测试标题')
    text = android.build_report(ok, ['打开文章…', '找到 2 张图'])
    check('《测试标题》' in text, '成功时带标题')
    check('共 2 张' in text, '成功时报张数')
    check('打开文章…' in text, '过程输出保留（手机上这是唯一的排查线索）')

    bad = imgsnag.Result(title='测试标题', message='一张图都没下下来')
    text = android.build_report(bad, [], retried=True)
    check('一张图都没下下来' in text, '失败时带原因')
    check('已经换过一种解析方式' in text, '重试过会说明（免得用户以为是卡住了）')


def test_full_chain():
    print('[5] 整条抓取链路（假网络，落盘后回读）')
    tmp = tempfile.mkdtemp()
    try:
        fake = FakeGet(pages={ARTICLE_URL: ARTICLE})
        report = android.snag_text(SHARE_TEXT, tmp, get=fake)

        check('秋天的第一杯奶茶' in report, '从分享文本里抽出了链接并拿到标题')
        check('共 2 张' in report, '报告里有两张')
        check('--insecure' not in report, '报告里没有命令行味的话')

        pending = os.path.join(tmp, android.PENDING_DIR_NAME)
        check(os.path.isdir(pending), f'中转目录用了约定名 {android.PENDING_DIR_NAME}')

        folders = os.listdir(pending)
        eq(len(folders), 1, '一个文章一个文件夹')
        check(re.match(r'^\d{4}-\d{2}-\d{2}_\d{6}_', folders[0]),
              f'文件夹名是「日期_时分秒_标题」：{folders[0]}')
        check('秋天的第一杯奶茶' in folders[0], '文件夹名里带标题')

        files = sorted(os.listdir(os.path.join(pending, folders[0])))
        # 扩展名必须按真实格式给：png 图存成 .jpg 是曾经的线上 bug
        eq(files, ['img_01.jpg', 'img_02.png'], '两张图、扩展名正确、编号连续')
        eq(os.path.getsize(os.path.join(pending, folders[0], 'img_02.png')), len(PNG),
           '写进磁盘的就是下载到的字节（没被转码）')

        eq(fake.hits('mp.weixin.qq.com'), 1, '文章只抓了一次')
        check(fake.hits('mmbiz.qpic.cn') >= 2, '每张图都请求了')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unknown_link():
    print('[6] 认不出的链接与空输入')
    tmp = tempfile.mkdtemp()
    try:
        fake = FakeGet()
        report = android.snag_text('https://example.com/some-page', tmp, get=fake)
        check('这个链接不认识' in report, '不支持的站点明确说不认识')
        check('微信' in report, '顺带告诉用户支持什么')
        eq(fake.calls, [], '认不出就不该发任何请求（省流量也省时间）')

        report = android.snag_text('   ', tmp, get=fake)
        check('没能从这段内容里找到链接' in report, '空输入给出可操作的提示')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scripts_retry():
    print('[7] 一遍找不到图会自动再扫一次脚本')
    tmp = tempfile.mkdtemp()
    try:
        fake = FakeGet(pages={ARTICLE_URL: EMPTY_ARTICLE})
        report = android.snag_text(ARTICLE_URL, tmp, get=fake)
        eq(fake.hits('mp.weixin.qq.com'), 2, '文章被重新抓了一次（第二次带上脚本扫描）')
        check('换一种方式' in report, '过程输出里说明了在重试')
        check('已经换过一种解析方式' in report, '最终报告里也说了')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
#  二、跨文件静态契约
# ============================================================================

def test_sync():
    print('[8] 副本同步（三端同一份规则）')
    missing, stale, extra = sync_core.diff()
    eq(missing, [], '没有缺失的副本')
    eq(stale, [], '没有内容漂移的副本')
    eq(extra, [], '副本里没有多余文件')

    apk_target = os.path.join(APK_APP, 'src', 'main', 'python')
    check(apk_target in sync_core.TARGETS, 'APK 的 python 目录在同步目标里')
    for rel in ('web_image_dl/sites/weixin.py', 'imgsnag.py'):
        check(os.path.exists(os.path.join(apk_target, rel)), f'APK 侧有 {rel}')

    # imgsnag_android.py 是手写的桥接层，**绝不能**进同步清单 ——
    # 进了就会被覆盖，而且是静默覆盖（用户只会觉得"我改的东西没了"）
    synced = {rel for rel, _s, _d in sync_core.planned()}
    check('imgsnag_android.py' not in synced, '桥接层不在同步清单里（不会被覆盖）')


def test_manifest():
    print('[9] 清单（权限与分享入口）')
    A = '{http://schemas.android.com/apk/res/android}'
    root = ET.parse(MANIFEST).getroot()
    eq(root.tag, 'manifest', '根节点是 manifest')

    perms = [p.get(A + 'name') for p in root.findall('uses-permission')]
    check('android.permission.INTERNET' in perms, '申请了联网权限')
    eq([p for p in perms if 'STORAGE' in (p or '')], [],
       '没有申请任何存储权限（写相册走 MediaStore，不需要）')

    app = root.find('application')
    check(app is not None, '有 application 节点')
    eq(app.get(A + 'label'), '@string/app_name', '应用名引用字符串资源')

    activities = {a.get(A + 'name'): a for a in app.findall('activity')}
    check('.MainActivity' in activities, '主 Activity 是 .MainActivity')
    main = activities['.MainActivity']
    eq(main.get(A + 'exported'), 'true', '带 intent-filter 的 Activity 必须 exported')

    actions, mimes = set(), set()
    for f in main.findall('intent-filter'):
        for i in f:
            tag = i.tag.split('}')[-1]
            if tag == 'action':
                actions.add(i.get(A + 'name'))
            elif tag == 'data':
                mimes.add(i.get(A + 'mimeType'))
    check('android.intent.action.MAIN' in actions, '能从桌面图标启动')
    check('android.intent.action.SEND' in actions, '能接收「分享」')
    check('text/plain' in mimes, '只接纯文本（微信分享的就是纯文本）')
    check(None not in mimes, 'data 节点没漏写 mimeType（漏了会导致分享菜单里点不进来）')


def test_resources_match_java():
    print('[10] 引用的资源都存在（写错就编译不过）')
    java_all = '\n'.join(read(os.path.join(JAVA_PKG_DIR, n))
                         for n in sorted(os.listdir(JAVA_PKG_DIR))
                         if n.endswith('.java'))

    # ⚠ 必须按扩展名过滤，不能"把目录里所有文件都读一遍"。
    # 实测踩过：反向验证脚本把备份写成 activity_main.xml.rvbak 放在同一目录，
    # 于是拆坏后的布局与"没拆坏"的备份被一起读进来，两个 id 都在 ——
    # 断言照绿，问题被自己的备份文件掩盖了。编辑器的临时文件同理。
    layouts = {n: read(os.path.join(RES_DIR, 'layout', n))
               for n in sorted(os.listdir(os.path.join(RES_DIR, 'layout')))
               if n.endswith('.xml')}
    layouts_all = '\n'.join(layouts.values())
    strings_xml = read(os.path.join(RES_DIR, 'values', 'strings.xml'))
    colors_xml = read(os.path.join(RES_DIR, 'values', 'colors.xml'))
    manifest = read(MANIFEST)

    id_decl = set(re.findall(r'@\+?id/(\w+)', layouts_all))
    str_decl = set(re.findall(r'<string name="(\w+)"', strings_xml))
    color_decl = set(re.findall(r'<color name="(\w+)"', colors_xml))

    # 字符串可能被三个地方引用：Java、布局、清单。只查 Java 会误报"没人用"
    str_used = (set(re.findall(r'R\.string\.(\w+)', java_all))
                | set(re.findall(r'@string/(\w+)', layouts_all))
                | set(re.findall(r'@string/(\w+)', manifest)))
    color_used = set(re.findall(r'@color/(\w+)', layouts_all))

    eq(sorted(set(re.findall(r'R\.id\.(\w+)', java_all)) - id_decl), [],
       'Java 用到的 R.id 都在布局里声明了')
    eq(sorted(set(re.findall(r'R\.string\.(\w+)', java_all)) - str_decl), [],
       'Java 用到的 R.string 都在 strings.xml 里')
    eq(sorted(color_used - color_decl), [],
       '布局用到的 @color 都在 colors.xml 里')
    eq(sorted(str_decl - str_used), [], '没有声明了却没人引用的字符串')

    for name in re.findall(r'R\.layout\.(\w+)', java_all):
        check(f'{name}.xml' in layouts, f'有布局文件 {name}.xml')


def test_java_structure():
    print('[11] Java 包名与目录一致')
    for name in sorted(os.listdir(JAVA_PKG_DIR)):
        if not name.endswith('.java'):
            continue
        text = read(os.path.join(JAVA_PKG_DIR, name))
        m = re.search(r'^package\s+([\w.]+);', text, re.M)
        check(m is not None, f'{name} 有 package 声明')
        if m:
            eq(m.group(1), PACKAGE, f'{name} 的包名与目录结构一致')
        cls = re.search(r'\b(?:public\s+)?(?:final\s+)?class\s+(\w+)', text)
        check(cls is not None and cls.group(1) == name[:-5],
              f'{name} 里的类名与文件名一致')


def test_cross_language_contract():
    print('[12] Java 与 Python 的握手点')
    main_java = read(os.path.join(JAVA_PKG_DIR, 'MainActivity.java'))
    gal_java = read(os.path.join(JAVA_PKG_DIR, 'Gallery.java'))

    m = re.search(r'PENDING_DIR\s*=\s*"([^"]+)"', main_java)
    check(m is not None, 'Java 里定义了中转目录名')
    if m:
        eq(m.group(1), android.PENDING_DIR_NAME,
           '中转目录名两边一致（不一致＝图抓到了但相册里没有，且不报错）')

    m = re.search(r'ALBUM\s*=\s*"([^"]+)"', gal_java)
    check(m is not None, 'Java 里定义了相册目录名')
    if m:
        eq(m.group(1), imgsnag.DEFAULT_SUBDIR,
           '手机相册目录名与桌面/Termux 版一致')

    check(hasattr(android, 'snag_text'), '桥接层提供 snag_text')
    params = list(inspect.signature(android.snag_text).parameters)
    eq(params[:2], ['text', 'workdir'], 'snag_text 前两个参数与 Java 的调用一致')

    # workdir 的**语义**也要钉死：Python 约定"给它根目录，它自己建 pending 层"。
    # 所以 Java 必须把 filesDir 根传给 snag_text，自己扫描的是底下的 pending 层。
    # （曾翻车：Java 把 pending 层传给了 snag_text，图落在 pending/pending/…，
    #   用户看到「共 4 张…没有新图片可入库」，两边都不报错 —— 真机首跑才发现。
    #   光钉常量名相等抓不住这个 bug，还得钉"传的是哪一层"。）
    check(re.search(r'new File\(\w+, PENDING_DIR\)', main_java) is not None,
          'Java 扫描/清理用的是 filesDir 底下的 pending 层')
    m = re.search(r'callAttr\("snag_text", raw,\s*([^)]+)\)', main_java)
    check(m is not None, '找到 snag_text 的调用')
    if m:
        check(not m.group(1).startswith('workRoot'),
              f'传给 snag_text 的必须是 filesDir 根，不能是 pending 层'
              f'（实际传了 {m.group(1)}，会嵌成 pending/pending）')

    check(os.path.exists(os.path.join(PY_DIR, 'imgsnag_android.py')),
          'Java 里 getModule("imgsnag_android") 对应的文件存在')
    check('imgsnag_android' in main_java, 'Java 里确实调了这个模块')


def test_gradle_config():
    print('[13] 构建配置')
    app = read(APP_GRADLE)
    root = read(ROOT_GRADLE)
    settings = read(SETTINGS_GRADLE)

    eq(re.search(r'namespace\s*=\s*"([^"]+)"', app).group(1), PACKAGE,
       'namespace 与包名一致')
    eq(re.search(r'applicationId\s*=\s*"([^"]+)"', app).group(1), PACKAGE,
       'applicationId 与包名一致')
    eq(re.search(r'compileSdk\s*=\s*(\d+)', app).group(1), '36', 'compileSdk = 36')
    eq(re.search(r'minSdk\s*=\s*(\d+)', app).group(1), '29',
       'minSdk = 29（MediaStore 的 RELATIVE_PATH 需要 API 29）')
    eq(re.search(r'targetSdk\s*=\s*(\d+)', app).group(1), '36', 'targetSdk = 36')

    # 只带一个 ABI 这件事必须**精确**比对：写成 `'arm64-v8a' in app` 是假断言 ——
    # 多带一个 x86_64 时字符串照样在，而 APK 体积已经差不多翻倍了。
    # （这条是被反向验证抓出来的，不是想出来的。）
    m = re.search(r'abiFilters\s*\+=\s*listOf\(([^)]*)\)', app)
    check(m is not None, '有 abiFilters 声明')
    if m:
        eq(m.group(1).strip(), '"arm64-v8a"',
           '只带 arm64-v8a（多带一个 ABI 体积几乎翻倍，而手机上用不到）')
    check(re.search(r'isMinifyEnabled\s*=\s*false', app) is not None,
          'release 不做代码压缩（R8 会削掉 Chaquopy 的反射调用）')

    check(os.path.exists(KEYSTORE), f'签名密钥存在：{os.path.relpath(KEYSTORE, ROOT)}')
    check('keystore/imgsnag.p12' in app, 'build.gradle.kts 指向这个密钥')
    check('storeType = "pkcs12"' in app, '显式声明密钥格式为 pkcs12')
    with open(KEYSTORE, 'rb') as f:
        eq(f.read(1), b'\x30', '密钥是合法的 DER/PKCS12（首字节为 SEQUENCE）')

    check('../version.py' in app, '版本号从项目根的 version.py 读，不手写')

    # 插件版本：Chaquopy 与 AGP 是绑定的，升级要一起升
    check('com.chaquo.python") version "17.0.0"' in root, 'Chaquopy 固定 17.0.0')
    check('com.android.application") version "8.13.1"' in root, 'AGP 固定 8.13.1')
    check('mavenCentral()' in settings, 'settings 里声明了 mavenCentral（Chaquopy 在那）')


def test_python_version_contract():
    print('[14] 构建机 Python 版本三方一致')
    app = read(APP_GRADLE)
    wf = read(WORKFLOW)

    ver = re.search(r'\bversion\s*=\s*"(\d+\.\d+)"', app)
    check(ver is not None, 'build.gradle.kts 里设了 Python 版本')
    build_py = re.search(r'buildPython\("([^"]+)"\)', app)
    check(build_py is not None, 'build.gradle.kts 里设了 buildPython')

    if ver and build_py:
        want = ver.group(1)
        eq(build_py.group(1), f'python{want}',
           'buildPython 与声明的应用 Python 版本一致（Chaquopy 17 要求完全相同）')
        check(f"python-version: '{want}'" in wf,
              f'工作流装的 Python 也是 {want}（不一致构建直接失败）')


def test_workflow():
    print('[15] 云构建工作流')
    check(os.path.exists(WORKFLOW), '工作流文件存在')
    wf = read(WORKFLOW)

    check('runs-on: ubuntu-latest' in wf, '跑在 ubuntu 运行器上')
    check('actions/checkout@v4' in wf, '先取代码')
    check("java-version: '17'" in wf, '装 JDK 17')
    check('assembleRelease' in wf, '构建的是 release 包（debug 包装给别人用不合适）')
    check("gradle-8.13-bin.zip" in wf,
          '直接下载钉死的 Gradle 8.13（与 Chaquopy 17.0.0 官方配套一致，不依赖镜像自带版本）')
    check('actions/upload-artifact@v4' in wf, '把 APK 作为构建产物上传')
    check('contents: write' in wf, '给了发布 Release 的权限')
    check('types: [published]' in wf, '正式发版（Publish Release）时自动给该版本补挂 APK')
    check('gh release upload' in wf, 'APK 会附加到对应的版本 Release（exe/zip/apk 并排）')
    check("!= 'apk-latest'" in wf, 'apk-latest 被手动 Publish 时不会触发套娃构建')
    check('gh release delete' not in wf and '--prerelease' not in wf,
          '不再发布 apk-latest 预发布位（下载入口只有正式 Release）')
    check('workflow_dispatch' in wf, '留着手动按钮（不改版本号也能试出一版包）')
    check('keytool -list' in wf, '构建前先验一次签名密钥能否被 JDK 读取')
    check('libpython3' in wf, '验包会检查 libpython 是否真的进了包')
    check('unzip -l' in wf, '验包看的是包内实际内容')
    check('dump badging' in wf, '图标/包名用 aapt2 badging 判定（不按 res 文件名找，路径会被优化掉）')
    check('test_apk_app.py' in wf, '打包前先跑本文件的快速自检')

    # 推送不再自动构建（预发布位已取消）；只剩「手动」与「正式发版」两种触发。
    # 曾经这里钉的是"推送会触发构建的路径覆盖"，触发器删掉后反过来钉它不在。
    # ⚠ 缩进按 YAML 里 on: 的真实子键来（2 空格）——写成 4 空格的话，
    #   把 push 加回去它也不会红（反向验证实测抓出来的）。
    check(re.search(r'^  push:', wf, re.M) is None,
          '推送代码不再自动构建（预发布位已取消，只保留手动与发版两种触发）')

    # 真·YAML 解析 —— GitHub 只在推送之后才报语法错，等一次要几分钟。
    # pyyaml 装了就严格解析；没装则退回「顶层键」兜底检查。
    # （曾翻车：release 说明整段顶到行首，把 run: | 的字面块提前掐断，
    #   '**下载…' 被 YAML 当成语法节点 → 推上 GitHub 才炸。）
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            yaml.safe_load(wf)
            check(True, '工作流是合法的 YAML')
        except yaml.YAMLError as e:
            check(False, f'工作流 YAML 解析失败：{str(e).splitlines()[0][:120]}')
    else:
        bad = [ln[:40] for ln in wf.split('\n')
               if ln.strip() and not ln.startswith((' ', '#'))
               and not re.match(r'^(name|on|permissions|concurrency|jobs):', ln)]
        check(not bad, f'顶层只能放合法键，顶到行首的可疑行：{bad}')


def test_docs():
    print('[16] 说明文档')
    check(os.path.exists(README), '有面向用户的使用说明')
    doc = read(README)
    check('apk' in doc.lower(), '说明里讲了怎么拿 APK')
    check('未知应用' in doc, '讲了小米/红米要允许安装未知应用')
    check('ImgSnag' in doc, '说明了图片存到哪儿')

    # 路径要**连着**检查。分开查 'Pictures' 和 'ImgSnag' 是假断言 ——
    # 删掉真正的路径那一行，这两个词在别处（目录树、相册截图说明）照样出现，
    # 断言不会红。（这条也是被反向验证抓出来的。）
    flat = re.sub(r'\s+', '', doc)
    check('Pictures/ImgSnag' in flat,
          '写清了完整的相册路径 Pictures/ImgSnag')


def test_gitignore():
    print('[17] 不该进仓库的东西被忽略了')
    ignore = read(os.path.join(ROOT, '.gitignore'))
    for pat in ('build/', '.gradle/', 'local.properties'):
        check(pat in ignore, f'忽略 {pat}')


def test_desktop_workflow():
    """桌面版云构建（Publish 之后 exe / 便携 zip 自己出现）。

    这条断言的重点不是"文件长得对"，而是**钉住自动触发这件事还在**。
    它的失效方式完全静默：把 `release:` 那行注释掉，工作流照样合法、手动跑照样
    出包、日志里一句提示都没有 —— 唯一的区别是 Publish 之后 Release 里少两个
    文件。这种"没有任何代码引用、漂了也没人知道"的落地点，本项目已经栽过两次
    （README 版本徽章静默漂了两个版本），所以必须有人盯着。
    """
    print('[18] 桌面版云构建工作流')
    check(os.path.exists(DESKTOP_WORKFLOW), '桌面工作流文件存在')
    wf = read(DESKTOP_WORKFLOW)

    check('runs-on: windows-latest' in wf,
          '跑在 windows 运行器上（PyInstaller 与 Inno Setup 都是 Windows 工具）')
    check('actions/checkout@v4' in wf, '先取代码')
    check("python-version: '3.13'" in wf, '装的 Python 与本机一致')
    check('python -m venv .venv' in wf,
          '云上照本机路径建 .venv（build_release.py 调的就是 .venv 里那个 python）')
    check('build_release.py' in wf, '跑的就是本机那个打包脚本（两边同一份，不另写一套）')
    check('tests/run_all.py' in wf, '打包前先跑全量自检')
    check('actions/upload-artifact@v4' in wf, '产物作为构建物上传（手动跑时从这里取）')
    check('gh release upload' in wf, '正式发版时把 exe / zip 补进同一个 Release')
    check('contents: write' in wf, '给了写 Release 的权限')
    check("!= 'apk-latest'" in wf, 'apk-latest 被手动 Publish 时不会触发套娃构建')

    # ⚠ 自动触发是这套东西的全部意义。注释掉它，界面/日志/手动跑全都正常，
    #   只有"Publish 完 Release 里没有 exe 和 zip"这一个表现，而且不报错。
    check('workflow_dispatch' in wf, '留着手动按钮（想先试一次、或临时出一版包）')
    check(re.search(r'^  release:', wf, re.M) is not None,
          'release 触发器是打开着的（不是被注释掉的）')
    check('types: [published]' in wf, '正式发版（Publish）时自动触发')

    # 云构建不是替代品，是省事的那条路 —— 本机那条必须留着
    check(os.path.exists(os.path.join(ROOT, 'build_release.py')),
          '本机打包脚本保留着（网络不通 / 云上出问题时的兜底）')

    # ⚠ 简中语言文件必须随仓库分发：官方 Inno Setup 6（云上那版）不自带
    #   ChineseSimplified.isl，本机 Inno 7 才内置。iss 若引 compiler: 相对路径，
    #   本机编译全绿、云上 Inno 编译一步就挂 —— 又是"本地过云端炸"的静默类。
    iss_path = os.path.join(ROOT, 'ImgSnag.iss')
    check(os.path.exists(iss_path), 'Inno 脚本存在')
    iss = read(iss_path)
    m = re.search(r'MessagesFile:\s*"([^"]+)"', iss)
    check(m is not None, 'iss 里有语言文件引用')
    if m:
        # ⚠ 分隔符统一用 '/'：这条测试在 Linux 运行器上也要跑，
        #   反斜杠在 POSIX 下不是分隔符（曾因此云端自检挂、本机全绿）
        rel = m.group(1).replace('\\', '/')
        check(not rel.lower().startswith('compiler:'),
              '语言文件走仓库相对路径（不走 compiler: 安装目录）')
        check(os.path.exists(os.path.join(ROOT, rel)),
              f'语言文件随仓库分发：{rel}')
        isl_path = os.path.join(ROOT, rel)
        if os.path.exists(isl_path):
            with open(isl_path, 'rb') as f:
                head = f.read(3)
            check(head == b'\xef\xbb\xbf',
                  '语言文件带 UTF-8 BOM（无 BOM 时 Inno 按本地代码页解码，中文会花）')

    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            yaml.safe_load(wf)
            check(True, '工作流是合法的 YAML')
        except yaml.YAMLError as e:
            check(False, f'工作流 YAML 解析失败：{str(e).splitlines()[0][:120]}')


# ============================================================================

def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    if not os.path.isdir(APK):
        print('找不到 android-apk/，跳过')
        return 1

    for fn in (test_ca_bundle, test_response_wrapper, test_polish, test_build_report,
               test_full_chain, test_unknown_link, test_scripts_retry,
               test_sync, test_manifest, test_resources_match_java,
               test_java_structure, test_cross_language_contract,
               test_gradle_config, test_python_version_contract, test_workflow,
               test_docs, test_gitignore, test_desktop_workflow):
        fn()

    print()
    if _failed:
        print(f'✗ {len(_failed)} 项失败 / 共 {_passed + len(_failed)} 项')
        for f in _failed:
            print(f'    - {f}')
        return 1
    print(f'✅ 安卓版全部通过：{_passed} 项')
    return 0


if __name__ == '__main__':
    sys.exit(main())
