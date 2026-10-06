package com.virmuran.imgsnag;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.view.View;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 唯一的界面。它只做四件事，其余全在 Python 里：
 *
 *   1. 接收「分享」过来的文本（微信分享的是「标题 + 链接」混在一起的整段文本，
 *      不一定是干净 URL —— 抽链接是 Python 侧 extract_url 的活）
 *   2. 读剪贴板（用户复制链接后回来点按钮）
 *   3. 在后台线程调 Python 抓图，把日志贴到屏幕上
 *   4. 把 Python 下好的图片交给 {@link Gallery} 写进系统相册
 *
 * ── 为什么第 3 步和第 4 步都必须在后台线程 ──
 * 抓一篇文章要下载几十张原图，几十秒很正常。放在主线程会直接把界面卡死，
 * 安卓在 5 秒无响应时还会弹「应用无响应」（ANR）让用户选择杀掉它。
 * 所以这里从线程到 UI 的每一处回写都走 Handler。
 *
 * ── Python 运行时在主线程启动，抓取在后台线程 ──
 * 启动 Python 是一次性的初始化（Chaquopy 要先把它打包进去的运行时准备好），
 * 放在 onCreate 里最稳 —— 官方文档与 demo 都这么写。我本来放到后台线程去了
 * （体感也许更好），但那条路没人验证过，而这个 App 我没法在真机上试，
 * 不值得为一次性的几十毫秒去冒险。
 * 真正耗时的网络抓取则一律在后台线程：那是几十秒的活，放主线程会直接 ANR。
 */
public class MainActivity extends Activity {

    /** Python 侧约定的中转目录名。抓好的图先落这里，再由 Java 搬进相册。 */
    private static final String PENDING_DIR = "pending";

    private TextView logView;
    private ScrollView scroller;
    private final Handler ui = new Handler(Looper.getMainLooper());

    /** 同一时间只允许一次抓取 —— 连点两次会开两条线程抢同一个中转目录。 */
    private volatile boolean running = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        logView = findViewById(R.id.log);
        scroller = findViewById(R.id.scroll);

        // 在这里（主线程）启动 Python —— 照 Chaquopy 文档与官方 demo 的写法。
        // 用 ApplicationContext 而不是 this：Python 运行时活得比 Activity 久，
        // 拿 Activity 当上下文会把它一起钉住（经典的内存泄漏）。
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(getApplicationContext()));
        }

        findViewById(R.id.btn_clip).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                fromClipboard();
            }
        });
        findViewById(R.id.btn_gallery).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openGallery();
            }
        });

        // 从「分享」进来时，intent 里就带着链接，直接开跑
        handleIntent(getIntent());
    }

    /**
     * 应用已经在后台时再从别处分享过来，不会走 onCreate 而是走这里。
     * 不重写它的话表现是「第一次分享能抓，第二次点分享没反应」。
     */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    // ──────────────────────────── 输入 ────────────────────────────

    private void handleIntent(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) {
            return;
        }
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text == null) {
            CharSequence subject = intent.getCharSequenceExtra(Intent.EXTRA_SUBJECT);
            text = subject;
        }
        if (text == null || text.toString().trim().isEmpty()) {
            log("分享过来的内容是空的（可以试试先复制链接，再用左边按钮）");
            return;
        }
        log("收到分享内容");
        start(text.toString());
    }

    private void fromClipboard() {
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
        start(text.toString());
    }

    private void openGallery() {
        try {
            Intent i = new Intent(Intent.ACTION_VIEW, MediaStore.Images.Media.EXTERNAL_CONTENT_URI);
            i.setType("image/*");
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            toast(getString(R.string.msg_no_gallery_app));
        }
    }

    // ──────────────────────── 抓取（后台线程）────────────────────────

    private void start(final String raw) {
        if (running) {
            toast(getString(R.string.msg_busy));
            return;
        }
        running = true;
        log(getString(R.string.msg_started));

        new Thread(new Runnable() {
            @Override
            public void run() {
                final String text = snagAndPublish(raw);
                ui.post(new Runnable() {
                    @Override
                    public void run() {
                        running = false;
                        log(text);
                    }
                });
            }
        }, "imgsnag-worker").start();
    }

    /** 全部跑在后台线程：Python 抓图 → 搬进相册。返回要显示给用户的整段文本。 */
    private String snagAndPublish(String raw) {
        Context app = getApplicationContext();
        File workRoot = new File(app.getFilesDir(), PENDING_DIR);

        // ── ① Python 抓图 ──
        String report;
        try {
            // onCreate 里已经启动过了；这里只是兜底（万一那边因为别的原因没成）。
            // 不加这个判断的话，重复 Python.start 会直接崩。
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(app));
            }
            PyObject mod = Python.getInstance().getModule("imgsnag_android");
            PyObject out = mod.callAttr("snag_text", raw, workRoot.getAbsolutePath());
            report = out == null ? "(Python 没有返回任何内容)" : out.toString();
        } catch (Throwable t) {
            // 这里必须兜住 Throwable 而不是 Exception：Python 侧的语法/导入错误
            // 由 Chaquopy 以 Error 的子类抛出，只 catch Exception 会让整个后台线程静默死掉，
            // 用户看到的就是「点了按钮没反应」——最难查的那种症状。
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

        // ── ③ 清掉中转目录（图片已经进相册，留着白占空间）──
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
        String line = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date()) + "  " + msg;
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
