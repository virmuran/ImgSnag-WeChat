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
    整套跑完要**好几分钟**（69 条用例，每条都要起一次子进程跑测试），
    可能被中途掐断，而 SIGTERM 下 finally 不执行。
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
TEST_APP = os.path.join(ROOT, 'tests', 'test_apk_app.py')   # 桥接层 + 工程静态契约
TEST_WEB = os.path.join(ROOT, 'tests', 'test_apk_web.py')   # 网页界面后端（真起本地服务）
LOG = os.path.join(ROOT, '_rv_apk_log.txt')

MANIFEST = 'android-apk/app/src/main/AndroidManifest.xml'
LAYOUT = 'android-apk/app/src/main/res/layout/activity_main.xml'
STRINGS = 'android-apk/app/src/main/res/values/strings.xml'
BRIDGE = 'android-apk/app/src/main/python/imgsnag_android.py'
MAIN_JAVA = 'android-apk/app/src/main/java/com/virmuran/imgsnag/MainActivity.java'
GAL_JAVA = 'android-apk/app/src/main/java/com/virmuran/imgsnag/Gallery.java'
NSC_XML = 'android-apk/app/src/main/res/xml/network_security_config.xml'
APP_GRADLE = 'android-apk/app/build.gradle.kts'
APK_README = 'android-apk/README.md'
SYNCED = 'android-apk/app/src/main/python/web_image_dl/naming.py'
WEB_PY = 'android-apk/app/src/main/python/imgsnag_web.py'
WEBUI = 'android-apk/app/src/main/python/webui.py'
HIST_MGR = 'web_image_dl/history_manager.py'

#: (说明, 文件, 原文, 替换为, 预期变红的用例, 跑哪个测试文件)
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
    ('Java 把 pending 层当 workdir 传给 Python（真机首跑的 bug）', MAIN_JAVA,
     'mod.callAttr("snag_text", raw, scratch.getAbsolutePath())',
     'mod.callAttr("snag_text", raw, workRoot.getAbsolutePath())',
     '[12] 传给 snag_text 的必须是 filesDir 根'),
    ('Java 侧改相册目录名', GAL_JAVA,
     'ALBUM = "ImgSnag"', 'ALBUM = "OtherAlbum"',
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
     '手机存储 / Pictures / ImgSnag', '手机存储 / 某处',
     '[16] 写清了相册路径'),
    ('副本漂移（改了同步过来的文件）', SYNCED,
     'MAX_TITLE_LEN = 24', 'MAX_TITLE_LEN = 25',
     '[8] 没有内容漂移'),

    # ── 网页界面这一层（跑 test_apk_web.py）────────────────────────────
    # 层的价值在于"它在电脑上就能验"，所以这些断言必须是**真的** ——
    # 否则这一层又变成"只能真机发现"，白折腾一场。
    ('网页里少一个 JS 要找的元素（点了没反应）', WEBUI,
     '<div id="grid"></div>', '<div id="gridx"></div>',
     "[9] JS 引用的 id 都存在（缺：['grid']）", TEST_WEB),
    ('页面被当成格式化字符串发出去（CSS 里的 {} 会被吃掉）', WEB_PY,
     "webui.PAGE.encode('utf-8')", "webui.PAGE.format().encode('utf-8')",
     '[9] 页面原样发送', TEST_WEB),
    # 下面两条的最后一个 **-1** 表示「替换**所有**命中」（页面里接口名各出现两次）。
    # ⚠ 别写 0：`str.replace(old, new, 0)` 是"替换 0 次"，跟 `re.sub` 的 count=0
    #   （=全部）**正好相反** —— 写错的表现是"拆坏没发生"，然后脚本报
    #   "断言是假的"，把工具自己的 bug 赖到断言头上（这一条真踩过）。
    # 只换一处会照绿 —— 那不是断言假，是拆坏拆得不到位，两件事得分清楚。
    ('页面把保存接口写错', WEBUI,
     "'/api/save'", "'/api/saveX'", '[9] 有保存到相册的动作', TEST_WEB, -1),
    ('页面把图片接口写错', WEBUI,
     "'/img/'", "'/imgx/'", '[9] 图片走本地接口', TEST_WEB, -1),
    ('Java 投递分享时改了方法名', MAIN_JAVA,
     'webMod.callAttr("push_share", share)', 'webMod.callAttr("push_sharex", share)',
     '[10] Java 调的确实是 push_share', TEST_WEB),
    # ⚠ 这条锚点跟过版：`start` 的调用从"一行写完"改成了多行 + `Kwarg`（传 files_dir），
    #   旧锚点 `callAttr("start", app, BuildConfig.VERSION_NAME)` 就找不到了 ——
    #   而"锚点找不到"在脚本里是被当成**失败**报出来的（不是静默跳过），这正是要的行为。
    #   留意 `,` 一起带上：拆坏后 group(1) 只剩 `app,`，`VERSION_NAME` 不在里面。
    ('Java 忘了把版本号传给 Python', MAIN_JAVA,
     'webMod.callAttr("start", app, BuildConfig.VERSION_NAME,',
     'webMod.callAttr("start", app,',
     '[10] Java 把版本号一起传了进去', TEST_WEB),
    ('Python 侧换了相册目录名', WEB_PY,
     'ALBUM_SUBDIR = imgsnag.DEFAULT_SUBDIR', "ALBUM_SUBDIR = 'Snagged'",
     '[10] 相册目录名 Java 与 Python 一致', TEST_WEB),
    ('清单里去掉回环明文放行（真机白屏）', MANIFEST,
     '        android:networkSecurityConfig="@xml/network_security_config"\n', '',
     '[10] 清单里指向了网络安全配置', TEST_WEB),
    ('Java 侧改了入库方法名', GAL_JAVA,
     'public static int publishDir(', 'public static int publishDirX(',
     '[10] Java 提供 public static int publishDir(...)', TEST_WEB),
    ('轮询时把分享内容消费掉（页面正忙就丢分享）', WEB_PY,
     "                    st['share'] = engine.pending_share\n",
     "                    st['share'] = engine.pending_share\n"
     "                    engine.pending_share = ''\n",
     '[3] **没被消费掉**', TEST_WEB),
    ('history 模块导入时就建库（会往用户主目录写东西）', HIST_MGR,
     '_INSTANCE = None', '_INSTANCE = HistoryManager()',
     '[11] 导入时不建库', TEST_WEB),

    # ── 返回手势、剪贴板、小图过滤、保存画质（第二轮反馈后的新增）────────
    #
    # 下面五条是"侧滑返回到底管不管用"的**全部必要件**。少任何一件，真机上
    # 表现都一模一样：**侧滑直接退回桌面**，而且一句错都不报 —— 只能一条条钉。
    # （上一轮只改了 onBackPressed，就属于"看着改了、其实没接上"。）
    ('没注册返回回调（targetSdk 35+ 上 onBackPressed 根本不会被调用）', MAIN_JAVA,
     'registerOnBackInvokedCallback', 'registerOnBackInvokedCallbackX',
     '[10] API 33+ 注册了返回回调', TEST_WEB),
    ('注册前忘了判系统版本（老系统上找不到那些新类，一进就崩）', MAIN_JAVA,
     'if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {',
     'if (true) {',
     '[10] 注册前判了系统版本', TEST_WEB),
    ('清单里不再声明预测性返回（日后动 targetSdk 会悄悄变回去）', MANIFEST,
     '        android:enableOnBackInvokedCallback="true"\n', '',
     '[10] 清单里显式声明了预测性返回', TEST_WEB),
    ('返回不再让网页退（侧滑直接退回桌面）', MAIN_JAVA,
     'if (webDepth > 0 && web != null) {', 'if (false) {',
     '[10] 有层可退时交给网页自己退', TEST_WEB),
    ('网页漏报一处层级（那一步侧滑就会穿透到底）', WEBUI,
     "    history.pushState({view:v}, '', '#' + v);\n    depth++; reportDepth();",
     "    history.pushState({view:v}, '', '#' + v);",
     '[9] reportDepth() 每次进出层级都调了', TEST_WEB),
    ('翻页也压返回栈（翻 5 张要划 5 次才退得出去）', WEBUI,
     "  history.replaceState({view:view, viewer:i}, '', '');",
     "  history.pushState({view:view, viewer:i}, '', '');",
     '[10] openViewer 里只压一层', TEST_WEB),
    ('剪贴板桥的名字两边不一致', MAIN_JAVA,
     'BRIDGE_NAME = "imgsnag"', 'BRIDGE_NAME = "imgsnagx"',
     '[10] 注入名与页面里读的名字一致', TEST_WEB),
    ('桥方法漏了注解（不暴露给 JS，且一点都不报错）', MAIN_JAVA,
     '        @JavascriptInterface\n        public String read()',
     '        public String read()',
     'read() 标了 @JavascriptInterface', TEST_WEB),
    ('页面不再压返回栈（大图关不掉、层级退不了）', WEBUI,
     'history.pushState', 'history.pushStateX',
     '[9] 视图切换会压一层返回栈', TEST_WEB, -1),
    ('大图去掉左右滑动的手势', WEBUI,
     'touchstart', 'touchstartx',
     '[9] 大图接了触摸手势', TEST_WEB),
    ('默认关掉小图过滤', WEB_PY,
     "'skip_tiny': True,", "'skip_tiny': False,",
     '[9] 默认就开着过滤', TEST_WEB),
    # 这条特别值：体检一旦改去量**原图档**，功能看着没坏（照样能算出大小），
    # 但"零额外流量"这个前提就没了 —— 只有把"碰没碰原图档"钉住才发现得了。
    ('体检改去量原图档（白下一遍原图）', WEB_PY,
     "path, _ = self._fetch_item(sid, 't', i)",
     "path, _ = self._fetch_item(sid, 'f', i)",
     '[9] 体检只碰压缩档', TEST_WEB),
    ('保存画质开关失效（永远取原图档）', WEB_PY,
     "kind = 'f' if original else 't'", "kind = 'f'",
     '[10] 关掉之后一次都不碰原图档', TEST_WEB),
    ('偏好不做类型校验（字符串 false 也当开）', WEB_PY,
     '            if isinstance(value, bool):\n                out[key] = value',
     '            out[key] = bool(value)',
     '[11] 字符串', TEST_WEB),
    ('剪贴板的 HTML 那一路被丢掉', WEB_PY,
     "    if (html or '').strip():\n        return html, False",
     "    if False:\n        return html, False",
     '[12] 把 HTML 当源码', TEST_WEB),

    # ── P3：版本更新检测（第三轮反馈后的新增）─────────────────────────
    # 这一组的共同点：**错了都不报错**，只是"永远说已是最新"或者"永远说有新版"，
    # 而这两种表现用户都不会截图来问 —— 正是最需要钉住的那一类。
    ('去下载不走 Java 的桥（在壳里点外链哪也去不了）', WEBUI,
     'if (window.imgsnag && window.imgsnag.open){ window.imgsnag.open(url); return true; }',
     'if (false){ return true; }',
     '[9] 去下载走 Java 的桥', TEST_WEB),
    # 设置页的「源码与反馈」同理 —— 第一版这里写的是 <a href="https://…">，
    # 在 WebView 壳里点了哪也去不了，必须挂 onclick 走同一个 openExternal。
    ('设置页的源码链接不走桥（在壳里点外链哪也去不了）', WEBUI,
     "openExternal('https://github.com/virmuran/ImgSnag')",
     "window.open('https://github.com/virmuran/ImgSnag')",
     '[9] 设置页的源码链接走 Java 的桥 openExternal', TEST_WEB),
    # 这条是桌面版真踩过的坑：签名与注入契约不一致，而测试全用假 fetch，
    # 真联网那条路从没被走到 —— 用户一点就报"多传了一个参数"。
    ('更新检测的取数函数少一个参数（真联网才发现）', WEB_PY,
     'def _update_fetch(self, url, timeout=None):', 'def _update_fetch(self, url):',
     '[13] 问到了远端信息', TEST_WEB),
    ('时间不做时区归位（"今天 07:12"其实是北京 15:12）', WEB_PY,
     '    return dt.astimezone() if dt.tzinfo else dt',
     '    return dt',
     '[13] 时间按本地时区显示', TEST_WEB),
    ('更新宽限期归零（刚装完就提示有新版本）', WEB_PY,
     'UPDATE_SLACK_MS = 60 * 1000', 'UPDATE_SLACK_MS = 0',
     '[13] 只差几十秒不算更新', TEST_WEB),
    ('挑资产时不看后缀（拿一个文本文件的时间去比）', WEB_PY,
     "        apks = [a for a in (assets or []) if str(a.name).lower().endswith('.apk')]",
     "        apks = [a for a in (assets or [])]",
     '[13] 把要下载的资产名一起带回来', TEST_WEB),
    ('安装时间不合理的值不设防（截断成负数 → 永远说有新版）', WEB_PY,
     '        if installed_at < MIN_INSTALL_MS:\n            installed_at = 0',
     '        pass',
     '[13] 安装时间被截断成负数时不能报有更新', TEST_WEB),
    ('问不到安装时间时硬说"没有新版"', WEB_PY,
     "            'unknown': bool(apk and not installed_at and not by_version),",
     "            'unknown': False,",
     '[13] 两个判据都拿不到时如实说', TEST_WEB),
    # 这一条是"改成比版本号"之后的核心：退回只看时间的话，用户自己装了更新的包
    # 也照样被提示升级（表现无害，只是烦人），而远端包构建时间更早时又会漏报。
    ('版本号不再参与判断（退回只看构建时间）', WEB_PY,
     '        if by_version and cmp_version != 0:', '        if False:',
     '[13] 远端版本更高 → 有更新', TEST_WEB),
    ('版本号相同时不再回退看时间（同版本号重建就漏了）', WEB_PY,
     '            has_update = by_time\n', '            has_update = False\n',
     '[13] 版本号相同但远端构建更晚', TEST_WEB),

    # ── 应用内更新（沐然提的"别让人去网页下载"）───────────────────────
    # 这一组的共同点：**错了只在真机上暴露** —— 本地编译不了、没有安卓设备，
    # 测试是唯一的防线，所以更要确认这些断言真的拦得住。
    ('Java 不再把应用私有目录传给 Python（下载落到别处）', MAIN_JAVA,
     'new Kwarg("files_dir", app.getFilesDir().getAbsolutePath())',
     'new Kwarg("files_dir", "")',
     '[14] 落在 files_dir 下', TEST_WEB),
    ('FileProvider 开放的目录名与约定不一致', 'android-apk/app/src/main/res/xml/file_paths.xml',
     'path="update/"', 'path="updated/"',
     '[10] FileProvider 只开放 update/', TEST_WEB),
    ('FileProvider 把整个 files 目录都放出去（历史库一起暴露）',
     'android-apk/app/src/main/res/xml/file_paths.xml',
     '<files-path name="update" path="update/"/>', '<files-path name="update" path="."/>',
     '[10] FileProvider 只开放 update/', TEST_WEB),
    # ⚠ 这四条是**同一类**假断言：清单/资源 XML 顶上都有一大块说明注释，
    #   把检查要找的字符串原样复述了一遍。所以拆坏必须**只删真代码、留下注释** ——
    #   要是连注释一起删，"断言被注释喂饱"这件事就验不出来（这就是第 2 条
    #   假断言的成因，工具自己抓出来的）。
    #   配套修法见 tests/test_apk_web.py 的 `strip_xml_comments()`。
    ('装包权限被删（安卓 8 起装不了任何包）', MANIFEST,
     '    <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES"/>\n',
     '',
     '[10] 清单里声明了装包权限', TEST_WEB),
    ('明文放行被拿掉（清单顶上注释里也有这个词）', NSC_XML,
     '    <domain-config cleartextTrafficPermitted="true">', '    <domain-config>',
     '[10] 明确放行明文', TEST_WEB),
    ('放行的域名写错（注释里也有 127.0.0.1）', NSC_XML,
     '        <domain includeSubdomains="false">127.0.0.1</domain>',
     '        <domain includeSubdomains="false">localhost</domain>',
     '[10] 放行的是回环地址', TEST_WEB),
    ('FileProvider 不再允许把 uri 临时授权出去（安装器读不到包）', MANIFEST,
     '            android:grantUriPermissions="true">',
     '            android:grantUriPermissions="false">',
     '[10] 允许把这个 uri 临时授权给安装器', TEST_WEB),
    ('装包时不校验"是不是自己下载的文件"', MAIN_JAVA,
     'if (!inUpdateDir(apk) || !apk.isFile())', 'if (!apk.isFile())',
     '[10] 安装前真的拿这个判断拦了一道', TEST_WEB),
    ('桥上的 install 忘了标注解（JS 调不到，且不报错）', MAIN_JAVA,
     '        @JavascriptInterface\n        public String install(String path) {',
     '        public String install(String path) {',
     '[10] install() 标了 @JavascriptInterface', TEST_WEB),
    ('下载完不校验字节数（截断的包也当成功）', WEB_PY,
     '            if size and got != size:', '            if False and size:',
     '[14] 被截断', TEST_WEB),
    ('下载完不校验 sha256（传输途中被改也照装）', WEB_PY,
     '            if want and digest.hexdigest() != want:', '            if False and want:',
     '[14] 摘要对不上', TEST_WEB),
    ('下载完不删上一版安装包（应用目录里 20MB 一个地攒）', WEB_PY,
     '            self._prune_old_apks(dest)', '            pass',
     '[14] 下载目录里只剩这一次下好的那个', TEST_WEB),
    ('下载失败后不删半截文件（.part 留在下载目录）', WEB_PY,
     '        except Exception as e:                          # noqa: BLE001\n'
     '            _silent_remove(tmp)',
     '        except Exception as e:                          # noqa: BLE001\n            pass',
     '[14] 下载目录里什么都不剩', TEST_WEB),
    ('取消后不删半截文件（手机上下到一半反悔是常事）', WEB_PY,
     '        except _Cancelled:\n            _silent_remove(tmp)',
     '        except _Cancelled:\n            pass',
     '[14] 取消后不留半截文件', TEST_WEB),
    ('下载目录不用传进来的那个（只看数据目录）', WEB_PY,
     '        self.update_dir = os.path.join(files_dir or self.root, UPDATE_DIR_NAME)',
     '        self.update_dir = os.path.join(self.root, UPDATE_DIR_NAME)',
     '[14] 落在 files_dir 下', TEST_WEB),

    # ── 「复制标题」（沐然新提的小功能）───────────────────────────────
    # 这一组错了都不报错：按钮点了没反应、或者复制出来是空的 ——
    # 都是"用户会以为软件坏了、但日志里一个字都没有"的那类。
    ('页面改用 navigator.clipboard（http 页面里不可靠，等于点了没反应）', WEBUI,
     "return window.imgsnag.copy(text) === 'ok';",
     'return true;',
     '[9] 写剪贴板走 Java 的桥', TEST_WEB),
    ('没标题时按钮照样可点（点了什么也不会发生）', WEBUI,
     "  $('#copyBtn').disabled = !st.title || st.phase === 'parsing';\n",
     '',
     '[9] 按钮可用状态跟着标题走', TEST_WEB),
    ('Java 的 copy 没标 @JavascriptInterface（JS 调不到，且不报错）', MAIN_JAVA,
     '@JavascriptInterface\n        public String copy(String text) {',
     'public String copy(String text) {',
     '[10] copy() 标了 @JavascriptInterface', TEST_WEB),
    ('Java 的 copy 成了空实现（签名在、剪贴板没写）', MAIN_JAVA,
     'cm.setPrimaryClip(ClipData.newPlainText("标题", text));',
     '// 忘了真写',
     '[10] 真的写进了系统剪贴板', TEST_WEB),

    # ── 来源提示与 Referer（2026-10-10 安卓端适配）─────────────────────
    # ⚠ cosmeitu 的 ciyuandao 图床是「带 Referer 就 403」。手机端有**两条**
    #   下载路径（共用主流程 imgsnag.py 与网页层的 imgsnag_web.py）——
    #   只修一条，另一条照样整批下不来，而且表现一模一样：一张都没有。
    ('网页层又把页面地址当 Referer（手机上抓 cosmeitu 整批 403）', WEB_PY,
     '                self._headers = imgsnag.build_headers(\n'
     '                    adapter.download_referer(source))\n',
     '                self._headers = imgsnag.build_headers(source)\n',
     '[15] cosmeitu 下载**不带** Referer', TEST_WEB),

    ('从历史打开时 Referer 硬用页面地址（重开一次就整批 403）', WEB_PY,
     "                    ad.download_referer(self.source) if ad else '')",
     '                    self.source)',
     '[15] 从历史重开再下载**仍然**不带', TEST_WEB),

    ('状态里不带来源（界面拿不到这一行）', WEB_PY,
     "                'credit': build_credit(self.author, self.site),\n",
     '',
     '[15] 状态里带来源提示', TEST_WEB),

    # ⚠ 页面里 `s.credit` 出现**两处**（判断 + 赋值）—— 只换第一处的话，
    #   另一处还在，`'s.credit' in page` 照样命中，拆坏等于没拆（报"断言是假的"，
    #   其实是拆得不到位，两件事得分清楚）。所以这里用 -1 = 全部替换。
    ('网页拿到来源却不显示（白解析一遍）', WEBUI,
     's.credit', '0',
     '[9] 页面用到了状态里的来源字段', TEST_WEB, -1),
]

#: 老用例不写"跑哪个测试文件"就默认跑 test_apk_app.py —— 免得为了加个字段
#: 把上面十几条全部改一遍（改错一条锚点就失效，脚本会报"锚点找不到"）。
#: 第 7 项是替换次数，不写就是 1（只换第一处）；**-1 = 全部替换**
#: （注意 `str.replace` 的 0 是"一次都不换"，与 `re.sub` 相反）。
def cases():
    out = []
    for c in CASES:
        desc, rel, old, new, expect = c[:5]
        out.append((desc, rel, old, new, expect,
                    c[5] if len(c) > 5 else TEST_APP,
                    c[6] if len(c) > 6 else 1))
    return out



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
    for _desc, rel, _old, _new, _expect, _test, _limit in cases():
        p = os.path.join(ROOT, rel)
        if os.path.exists(bak(p)):
            shutil.copyfile(bak(p), p)
            os.remove(bak(p))
            n += 1
            if verbose:
                print(f'  ↺ 还原残留 {rel}')
    return n


def run_test(test):
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        r = subprocess.run([sys.executable, test], cwd=ROOT, env=env,
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

    baseline_ok = True
    for name, test in (('工程契约', TEST_APP), ('网页界面', TEST_WEB)):
        rc, _ = run_test(test)
        say(f'基线（{name}，未拆坏）rc={rc}  {"✅" if rc == 0 else "❌ 基线就不绿，先修它"}')
        if rc != 0:
            baseline_ok = False
    if not baseline_ok:
        say('基线不绿，后面的结论没意义，终止。')
        open(LOG, 'w', encoding='utf-8').write('\n'.join(out))
        return 1

    bad = []
    for i, (desc, rel, old, new, expect, test, limit) in enumerate(cases(), 1):
        path = os.path.join(ROOT, rel)
        text = open(path, encoding='utf-8').read()
        if old not in text:
            say(f'{i:>2}. ✗ 锚点找不到，跳过：{desc}\n      在 {rel} 里找不到：{old!r}')
            bad.append(f'{desc}（锚点失效）')
            continue

        os.makedirs(os.path.dirname(bak(path)), exist_ok=True)
        shutil.copyfile(path, bak(path))          # ← 先备份，再改
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text.replace(old, new, limit))   # 显式关闭：别指望 GC 帮你 flush
        try:
            rc, log = run_test(test)
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
            say(f'{i:>2}. ✅ 拆坏「{desc}」→ 变红（{os.path.basename(test)}，'
                f'{len(hit)} 项失败）')
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
