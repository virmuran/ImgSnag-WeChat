package com.virmuran.imgsnag;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.window.OnBackInvokedCallback;
import android.window.OnBackInvokedDispatcher;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 界面外壳。真正干活的全在 Python 里，这里只做四件事：
 *
 *   1. 启动 Python 侧的本地服务（{@code imgsnag_web.start}），拿回端口号；
 *   2. 用一个 WebView 加载 {@code http://127.0.0.1:<端口>/} ——
 *      网格、大图预览、历史页都在那个网页里；
 *   3. 把「微信分享过来的文本」转交给界面（{@code imgsnag_web.push_share}）；
 *   4. 把返回键（含侧边滑动返回）交给网页的层级去处理 —— 见 {@link #setupBack()}。
 *      另有一个只读的桥（剪贴板 / 层级上报 / 开外链 / 本机安装时间）。
 *
 * ── 为什么这套东西跑在本机的一个端口上 ──
 * 页面与接口**同源**：不需要处理跨域，也不需要写 JS↔Java 的桥。
 * 服务只绑 127.0.0.1，同一 Wi-Fi 下的别的设备连不上；
 * 清单里也只给这一条回环地址放行明文 HTTP（见 res/xml/network_security_config.xml）。
 *
 * ── 为什么还留着一条「不走网页」的老路 ──
 * 网页这条路多了一个前提：手机允许 App 连自己的回环地址。这一条我**没法在本机验证**，
 * 只能等真机。所以兜底留着：读剪贴板 → 直接调 {@code snag_text} 抓图 → 搬进相册。
 * 万一网页起不来（白屏），App 至少还能用，而且日志里会写清楚它为什么没起来。
 *
 * ── 线程 ──
 * 启动 Python、起服务都在后台线程（Python 运行时的初始化 + 建监听套接字，几十到几百毫秒，
 * 放主线程是没必要的 ANR 风险）；所有 UI 回写一律走 Handler。
 * 抓图那种几十秒的活更是只能在后台线程 —— 主线程超过 5 秒无响应就会弹「应用无响应」。
 */
public class MainActivity extends Activity {

    /** Python 侧约定的中转目录名。兜底抓图时图先落这里，再由 Java 搬进相册。 */
    private static final String PENDING_DIR = "pending";

    /** 本地服务的地址头。端口由 Python 侧随机分配（避免撞上别的 App 占用的端口）。 */
    private static final String LOOPBACK = "http://127.0.0.1:";

    /** 等网页起来的上限。超过就认定失败并把兜底按钮亮出来。 */
    private static final int BOOT_TIMEOUT_MS = 15000;

    /**
     * 注入给网页的全局对象名（网页里用 `window.imgsnag.read()` 调）。
     * 两边的名字必须一致，测试钉住这条 —— 改名忘了改另一边，
     * 表现是「从剪贴板」按钮永远提示读不到，且不报错。
     */
    private static final String BRIDGE_NAME = "imgsnag";

    private WebView web;
    private View splash;
    private ProgressBar spinner;
    private TextView statusView;
    private TextView logView;
    private ScrollView scroller;

    private final Handler ui = new Handler(Looper.getMainLooper());

    /** Python 里的 imgsnag_web 模块。 */
    private PyObject webMod;

    /** 本地服务端口；0 表示还没起来。 */
    private volatile int port = 0;

    /** 服务是否已就绪。 */
    private volatile boolean served = false;

    /** 服务没起来时，先把分享内容存这儿；起来并加载完页面后再交出去。 */
    private volatile String pendingShare = null;

    /** 网页是否已经露面（用过它就能判断该不该再显示遮罩）。 */
    private boolean webShown = false;

    /** 兜底抓取的重入守卫：连点两次会开两条线程抢同一个中转目录。 */
    private volatile boolean running = false;

    /**
     * 网页报上来的"还有几层可退"。0 = 已经在最外层，返回就是退出 App。
     *
     * **为什么不用 `WebView.canGoBack()`**：那个值取决于 WebView 内部怎么记
     * pushState 产生的历史条目，实机行为不可预期。第一版就是拿它当判据，
     * 结果侧滑依旧直接退出 App，还因为"看着像改过了"白等了一轮云构建。
     * 这个数由网页自己数、每次 push/pop 都同步一次，语义明确且本机可测。
     */
    private volatile int webDepth = 0;

    /** 网页迟迟不来时的看门狗。 */
    private final Runnable watchdog = new Runnable() {
        @Override
        public void run() {
            if (!webShown) {
                webFailed("等了 " + (BOOT_TIMEOUT_MS / 1000) + " 秒还没起来");
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        splash = findViewById(R.id.splash);
        spinner = findViewById(R.id.spin);
        statusView = findViewById(R.id.status);
        logView = findViewById(R.id.log);
        scroller = findViewById(R.id.scroll);

        // Python 运行时在主线程启动 —— Chaquopy 官方文档与 demo 都这么写。
        // 用 ApplicationContext 而不是 this：运行时活得比 Activity 久，
        // 拿 Activity 当上下文会把整棵树钉住（经典的内存泄漏）。
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(getApplicationContext()));
        }
        webMod = Python.getInstance().getModule("imgsnag_web");

        setupWebView();
        setupBack();

        findViewById(R.id.retry).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                bootWeb();
            }
        });
        findViewById(R.id.fallback).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                fallbackFromClipboard();
            }
        });

        bootWeb();

        // 从「分享」进来时，intent 里就带着链接
        handleIntent(getIntent());
    }

    /**
     * 应用已在后台时再从别处分享过来，走的是这里而不是 onCreate。
     * 不重写它的表现是「第一次分享能抓，第二次点分享没反应」。
     */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    // ──────────────────────────── 网页 ────────────────────────────

    private void setupWebView() {
        WebSettings s = web.getSettings();
        // 界面全靠 JS（轮询进度、画网格、翻大图），必须开。
        s.setJavaScriptEnabled(true);
        // 页面是自己发的，不需要读手机里的文件，也不需要读别的 App 的内容。
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        // 状态是实时轮询来的，缓存只会让人看到旧的一眼。
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);

        // 剪贴板桥（只读，给「从剪贴板」按钮用）。
        // 必须在 loadUrl 之前加：加晚了在部分 WebView 版本上要刷新页面才注入，
        // 表现是"第一次打开点了没反应，退出去再进就好了"——最难查的那种。
        web.addJavascriptInterface(new ClipBridge(), BRIDGE_NAME);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                showWeb();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req,
                                        WebResourceError err) {
                // 只关心主文档的失败：某张缩略图没下下来不该把整页判死。
                if (req != null && req.isForMainFrame()) {
                    webFailed(String.valueOf(err == null ? "未知原因" : err.getDescription()));
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req == null ? null : req.getUrl();
                // 自己的页面：留在 WebView 里。
                if (u != null && "127.0.0.1".equals(u.getHost())) {
                    return false;
                }
                // 页面里若出现外部链接，交给系统浏览器 —— 别在这个"壳"里迷路。
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) {
                    // 没有浏览器就没有吧，不能为此崩掉
                }
                return true;
            }
        });
    }

    /**
     * 接管返回 —— 返回键与**手机的侧边滑动返回手势**最终都落到这里。
     *
     * ⚠ 光覆写 `onBackPressed()` **不够**：安卓 13 起系统改用
     * `OnBackInvokedDispatcher` 派发返回，而 targetSdk 35+ 的应用默认就走
     * 这条路 —— `onBackPressed()` 根本不会被调用（覆写得再对也白搭，
     * 而且一句错都不报）。所以 API 33+ 得注册一个回调，低版本才走旧方法。
     * 真机上"侧滑永远直接退回桌面"就是踩在这上面：第一次只改了旧方法。
     */
    private void setupBack() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {        // 33
            try {
                getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                        OnBackInvokedDispatcher.PRIORITY_DEFAULT,
                        new OnBackInvokedCallback() {
                            @Override
                            public void onBackInvoked() {
                                handleBack();
                            }
                        });
            } catch (Throwable t) {
                // 个别 ROM 在这上面有毛病。注册不上就退回旧路 ——
                // 旧路在新系统上通常已经不被调用，但总比在这里崩掉强。
                log("⚠ 返回手势没接管上：" + t);
            }
        }
    }

    /**
     * 返回到底做什么。新旧两条派发路径都走这一个函数 ——
     * 分成两份实现，迟早会出现"侧滑一套、返回键另一套"的怪事。
     */
    private void handleBack() {
        if (webDepth > 0 && web != null) {
            // 交给网页自己退：它 popstate 里会把界面收拾干净（关大图 / 换视图）。
            // 用 history.back() 而不是 WebView.goBack()，为的是让"点关闭"
            // 和"按返回"走同一条路 —— 否则会出现一个能退、一个退不掉的怪事。
            web.evaluateJavascript("history.back()", null);
            return;
        }
        finish();               // 已经在最外层：退出 App
    }

    /** 安卓 13 以下走的是这条老路。 */
    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        handleBack();
    }

    // ──────────────────────────── 剪贴板桥 ────────────────────────────

    /**
     * 给网页用的**只读**桥（外加一个"打开链接"的动作）。
     *
     * 四个方法各管一件事，都不碰文件、不执行命令：
     *   · read()       —— 剪贴板里的纯文本与 HTML（「从剪贴板」按钮）
     *   · depth(n)     —— 网页现在在第几层（返回手势的唯一判据）
     *   · open(url)    —— 用系统浏览器打开 https（更新说明 / 下载页）
     *   · installedAt()—— 本机安装时间（更新检查要跟远端比）
     *
     * 网页是从 127.0.0.1 加载的自家页面，而且外链一律交给系统浏览器打开
     * （见 shouldOverrideUrlLoading），所以这个桥不会被别的页面碰到。
     *
     * ⚠ 方法必须标 `@JavascriptInterface` —— API 17 起只有标了的才暴露给 JS。
     * 忘了标的表现是"点了没反应"，而且**一点报错都没有**。
     */
    private class ClipBridge {
        @JavascriptInterface
        public String read() {
            JSONObject out = new JSONObject();
            try {
                ClipboardManager cm =
                        (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                ClipData clip = cm == null ? null : cm.getPrimaryClip();
                if (clip != null && clip.getItemCount() > 0) {
                    ClipData.Item item = clip.getItemAt(0);
                    CharSequence text = item.coerceToText(MainActivity.this);
                    out.put("text", text == null ? "" : text.toString());
                    // 浏览器里复制的网页会带一份 HTML，正文里的图片地址就在里面
                    CharSequence html = item.getHtmlText();
                    if (html != null) {
                        out.put("html", html.toString());
                    }
                }
            } catch (Throwable t) {
                // 有的 ROM 在应用没有焦点时读剪贴板会抛。读不到就当空的，
                // 绝不能因为"读剪贴板失败"把整个界面弄崩。
                return "{}";
            }
            return out.toString();
        }

        /**
         * 把一段文字放进系统剪贴板（「复制标题」用）。
         *
         * 返回 "ok" / "fail" 而不是 void：网页那边要按结果给提示 —— 写剪贴板在
         * 个别 ROM 上会抛（应用没有焦点、剪贴板被别的应用占着），**静默失败**
         * 恰恰是用户最讨厌的那种"点了没反应"。
         */
        @JavascriptInterface
        public String copy(String text) {
            if (text == null || text.isEmpty()) {
                return "fail";
            }
            try {
                ClipboardManager cm =
                        (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                if (cm == null) {
                    return "fail";
                }
                cm.setPrimaryClip(ClipData.newPlainText("标题", text));
                return "ok";
            } catch (Throwable t) {
                // 与 read() 同样的理由：写不进也不能把整个界面弄崩
                return "fail";
            }
        }

        /**
         * 网页上报"现在在第几层"。只往内存里记一个数，不读不写任何东西。
         * 这是返回手势唯一的判据，见 {@link #handleBack()}。
         */
        @JavascriptInterface
        public void depth(int n) {
            webDepth = Math.max(0, n);
        }

        /**
         * 用系统浏览器打开一个链接（更新说明 / 下载页）。
         *
         * **只放行 https**：页面是我们自己发的，但它显示的内容来自抓来的文章，
         * 白名单一条比事后追查便宜得多。
         */
        @JavascriptInterface
        public String open(String url) {
            if (url == null || !url.startsWith("https://")) {
                return "bad";
            }
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                return "ok";
            } catch (Exception e) {
                return "fail";      // 没装浏览器也得静静收场，不能崩
            }
        }

        /**
         * 本机这个 App 是什么时候装的（毫秒时间戳）。0 = 问不到。
         *
         * 更新检查靠它跟 GitHub 上那个安装包的构建时间比大小 —— 比版本号靠谱：
         * 滚动发布位的 tag 里没有版本号，而且"同版本号重新构建"时版本号压根不变，
         * 那恰恰是最常见的情况。返回 String 而不是 long：跨语言传数值的精度
         * 问题没必要去踩，反正马上就变成数字了。
         */
        @JavascriptInterface
        @SuppressWarnings("deprecation")
        public String installedAt() {
            try {
                PackageInfo pi = getPackageManager().getPackageInfo(getPackageName(), 0);
                return String.valueOf(pi.lastUpdateTime);
            } catch (Exception e) {
                return "0";
            }
        }
    }

    /** 起本地服务，好了就加载页面。失败则留在遮罩上并写清原因。 */
    private void bootWeb() {
        if (served) {
            loadHome();
            return;
        }
        spinner.setVisibility(View.VISIBLE);
        statusView.setText(R.string.status_starting);

        new Thread(new Runnable() {
            @Override
            public void run() {
                Integer got = null;
                Throwable err = null;
                try {
                    // ctx 传 Application 上下文 —— Python 侧要拿它调 Gallery 写相册。
                    Context app = getApplicationContext();
                    PyObject out = webMod.callAttr("start", app, BuildConfig.VERSION_NAME);
                    got = out == null ? 0 : out.toInt();
                } catch (Throwable t) {
                    // 必须兜 Throwable：Python 侧的语法/导入错误由 Chaquopy 以
                    // Error 的子类抛出，只 catch Exception 会让线程静默死掉，
                    // 用户看到的就是「点了没反应」——最难查的那种症状。
                    err = t;
                }
                final Integer fPort = got;
                final Throwable fErr = err;
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        if (fErr != null) {
                            webFailed(String.valueOf(fErr));
                            return;
                        }
                        port = fPort == null ? 0 : fPort;
                        if (port <= 0) {
                            webFailed("服务没有返回端口");
                            return;
                        }
                        served = true;
                        loadHome();
                    }
                });
            }
        }, "imgsnag-web").start();
    }

    private void loadHome() {
        ui.removeCallbacks(watchdog);
        ui.postDelayed(watchdog, BOOT_TIMEOUT_MS);
        web.loadUrl(LOOPBACK + port + "/");
    }

    /** 网页露出来了：收起遮罩，并把攒着的分享内容交出去。 */
    private void showWeb() {
        ui.removeCallbacks(watchdog);
        if (!webShown) {
            webShown = true;
            web.setVisibility(View.VISIBLE);
            splash.setVisibility(View.GONE);
            log("网页界面已就绪");
        }
        deliverShare();
    }

    /** 网页没起来：留在遮罩上，写明原因，把两个按钮亮出来给用户选。 */
    private void webFailed(String why) {
        if (webShown) {
            // 已经看到网页了，别再盖回去
            return;
        }
        spinner.setVisibility(View.GONE);
        statusView.setText(R.string.status_failed);
        log("❌ 界面没能启动：" + why);
    }

    // ────────────────────────── 分享与输入 ──────────────────────────

    private void handleIntent(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) {
            return;
        }
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text == null) {
            // 有些 App 把内容塞在 SUBJECT 里
            text = intent.getCharSequenceExtra(Intent.EXTRA_SUBJECT);
        }
        if (text == null || text.toString().trim().isEmpty()) {
            log("分享过来的内容是空的（可以先复制链接，再回来点「直接抓取」）");
            return;
        }
        log("收到分享内容");
        queueShare(text.toString());
    }

    /** 先把内容存住，等网页真的露面了再交出去（没露面就交，对方收不到）。 */
    private void queueShare(String s) {
        pendingShare = s;
        deliverShare();
    }

    private void deliverShare() {
        String share = pendingShare;
        if (share == null || webMod == null || !served || !webShown) {
            return;
        }
        try {
            // 只投递、不解析：页面可能正忙着看上一篇文章，
            // 什么时候开始由界面决定（Python 侧还会再等页面来取）。
            webMod.callAttr("push_share", share);
            pendingShare = null;
        } catch (Throwable t) {
            log("⚠ 分享内容没能送给界面：" + t);
        }
    }

    // ─────────────────── 兜底：不走网页，直接抓图 ───────────────────

    private void fallbackFromClipboard() {
        ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        ClipData clip = cm == null ? null : cm.getPrimaryClip();
        if (clip == null || clip.getItemCount() == 0) {
            toast(getString(R.string.msg_clip_empty));
            return;
        }
        CharSequence text = clip.getItemAt(0).coerceToText(this);
        if (text == null || text.toString().trim().isEmpty()) {
            toast(getString(R.string.msg_clip_empty));
            return;
        }
        runFallback(text.toString());
    }

    private void runFallback(final String raw) {
        if (running) {
            toast(getString(R.string.msg_busy));
            return;
        }
        running = true;
        spinner.setVisibility(View.VISIBLE);
        statusView.setText(R.string.msg_started);
        log(getString(R.string.msg_started));

        new Thread(new Runnable() {
            @Override
            public void run() {
                final String report = snagAndPublish(raw);
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        running = false;
                        spinner.setVisibility(View.GONE);
                        log(report);
                    }
                });
            }
        }, "imgsnag-worker").start();
    }

    /** 全部跑在后台线程：Python 抓图 → 搬进相册。返回要显示给用户的整段文本。 */
    private String snagAndPublish(String raw) {
        Context app = getApplicationContext();
        // 约定：给 Python 一个**根目录**，它自己在底下建 pending/<文章文件夹>/。
        // 所以传给 Python 的是 filesDir 根（下面叫 scratch），
        // 而扫描/清理用的 workRoot 是它底下的 pending 那一层。
        // 若把 workRoot 直接传过去，路径会嵌成 pending/pending/…：
        // 扫描时内层 pending 被当成"文章文件夹"，里面全是文件夹没有图 ——
        // 表现是「共 N 张…没有新图片可入库」，两边都不报错（真机首跑踩过）。
        File scratch = app.getFilesDir();
        File workRoot = new File(scratch, PENDING_DIR);

        // ── ① Python 抓图 ──
        String report;
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(app));
            }
            PyObject mod = Python.getInstance().getModule("imgsnag_android");
            PyObject out = mod.callAttr("snag_text", raw, scratch.getAbsolutePath());
            report = out == null ? "(Python 没有返回任何内容)" : out.toString();
        } catch (Throwable t) {
            report = "❌ Python 侧出错：\n" + t;
        }

        // ── ② 把下好的图搬进相册 ──
        StringBuilder tail = new StringBuilder();
        int ok = 0, failed = 0;
        String firstError = null;
        String lastFolder = "";

        File[] folders = workRoot.listFiles();
        if (folders != null) {
            for (File dir : folders) {
                File[] imgs = dir.isDirectory() ? dir.listFiles() : null;
                if (imgs == null) {
                    continue;
                }
                for (File img : imgs) {
                    if (!img.isFile()) {
                        continue;
                    }
                    try {
                        Gallery.publish(app, img, dir.getName(), img.getName());
                        ok++;
                    } catch (Throwable t) {
                        failed++;
                        if (firstError == null) {
                            firstError = t.toString();
                        }
                    }
                }
                lastFolder = dir.getName();
            }
        }

        if (ok > 0) {
            tail.append("\n✅ 已存入相册：Pictures/")
                .append(Gallery.ALBUM).append('/').append(lastFolder)
                .append("（").append(ok).append(" 张）");
        } else {
            tail.append("\n").append(getString(R.string.msg_publish_none));
        }
        if (failed > 0) {
            tail.append("\n⚠ 有 ").append(failed).append(" 张写相册失败：").append(firstError);
        }

        // ── ③ 清掉中转目录（图已进相册，留着白占空间）──
        deleteTree(workRoot);

        return report + tail;
    }

    // ──────────────────────────── 杂项 ────────────────────────────

    private static void deleteTree(File f) {
        File[] kids = f.listFiles();
        if (kids != null) {
            for (File k : kids) {
                deleteTree(k);
            }
        }
        // 删不掉也不影响功能，下次同名会被覆盖前先清空
        //noinspection ResultOfMethodCallIgnored
        f.delete();
    }

    private void log(final String msg) {
        String line = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date())
                    + "  " + msg;
        logView.append("\n" + line);
        scroller.post(new Runnable() {
            @Override
            public void run() {
                scroller.fullScroll(View.FOCUS_DOWN);
            }
        });
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }
}
