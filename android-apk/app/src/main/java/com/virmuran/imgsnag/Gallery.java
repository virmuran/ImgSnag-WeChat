package com.virmuran.imgsnag;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Arrays;
import java.util.Locale;

/**
 * 把抓下来的图片放进系统相册。
 *
 * ── 为什么不能直接写文件路径 ──
 * Android 10 起应用不能直接往相册目录写文件了（分区存储）。想出现在相册里，
 * 唯一正路是通过 MediaStore 登记一个新条目，再往它给的输出流里写。
 * 好处也随之而来：**全程不需要任何存储权限** —— 系统知道这是你自己新建的文件。
 * （这也是本 App 只申请了 INTERNET 一个权限的原因。）
 *
 * ── 为什么要 IS_PENDING ──
 * 大图写盘要几十毫秒到几秒。不加这个标记的话，相册可能在文件写了一半时就来读，
 * 结果是「图库缩略图显示一张灰图/坏图」。标上 1 表示"还没写完，先别看"，
 * 写完再标 0 通知系统可以收录了。出错时把整个条目删掉，不留半张图。
 *
 * ── 为什么这段逻辑在 Java 而不是 Python ──
 * Python 那边（web_image_dl/）的定位是"零 Qt、零第三方依赖的纯逻辑"，
 * 换到任何平台都能跑。碰安卓系统 API 是平台特有的事，所以留在这层。
 *
 * ── 与 Python 的握手 ──
 * {@link #publishDir} 是 Python 唯一会调用的入口：Python 用全限定类名
 * `com.virmuran.imgsnag.Gallery` 找过来，按 (上下文, 目录, 相册子目录) 传参，
 * 拿回成功张数。**改签名/改名字会让两边对不上**，而症状是
 * 「图抓到了但相册里没有、两边都不报错」（本项目真机上踩过一次），
 * 所以测试把类名、方法名、参数顺序、相册目录名逐条钉住了。
 */
public final class Gallery {

    /** 相册里的相册名（Pictures 下的子目录）。
     *  ⚠ Python 侧（imgsnag_web.ALBUM_SUBDIR）必须与此一致，测试盯着。 */
    public static final String ALBUM = "ImgSnag";

    private Gallery() {
    }

    /** 按文件扩展名猜 MIME。猜错的影响只是某些看图工具不认，不影响能否打开。 */
    public static String mimeOf(String name) {
        String n = name == null ? "" : name.toLowerCase(Locale.ROOT);
        if (n.endsWith(".png")) {
            return "image/png";
        }
        if (n.endsWith(".gif")) {
            return "image/gif";
        }
        if (n.endsWith(".webp")) {
            return "image/webp";
        }
        if (n.endsWith(".bmp")) {
            return "image/bmp";
        }
        return "image/jpeg";
    }

    /**
     * 把一个本地文件登记进相册。
     *
     * @param ctx         用 ApplicationContext（用 Activity 会泄漏，抓取可能跑很久）
     * @param src         源文件
     * @param subDir      相册下的一级子目录，用文章文件夹名（日期_标题）
     * @param displayName 相册里显示的文件名
     * @return 新条目的 content Uri
     */
    public static Uri publish(Context ctx, File src, String subDir, String displayName)
            throws IOException {
        ContentResolver cr = ctx.getContentResolver();

        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, displayName);
        values.put(MediaStore.MediaColumns.MIME_TYPE, mimeOf(displayName));
        values.put(MediaStore.MediaColumns.RELATIVE_PATH,
                Environment.DIRECTORY_PICTURES + "/" + ALBUM + "/" + subDir);
        values.put(MediaStore.MediaColumns.IS_PENDING, 1);

        Uri uri = cr.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
        if (uri == null) {
            throw new IOException("系统拒绝了相册条目（MediaStore.insert 返回 null）");
        }

        try (InputStream in = new FileInputStream(src)) {
            OutputStream out = cr.openOutputStream(uri);
            if (out == null) {
                throw new IOException("打不开相册条目的输出流");
            }
            try {
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                }
                out.flush();
            } finally {
                out.close();
            }
        } catch (IOException e) {
            cr.delete(uri, null, null);
            throw e;
        }

        ContentValues done = new ContentValues();
        done.put(MediaStore.MediaColumns.IS_PENDING, 0);
        cr.update(uri, done, null, null);
        return uri;
    }

    /**
     * 把一个目录里的图片**全部**登记进相册，返回成功张数。
     *
     * 这是与 Python 的握手点：`imgsnag_web._java_publish` 按
     * `(上下文, 目录, 相册子目录)` 调它。参数顺序与名字都被测试钉住。
     *
     * 单张失败不中断其余 —— 记下最后一个错误，只有"一张都没成"才抛出来。
     * 这样一张坏图不至于让整篇文章白抓，而部分成功时 Python 会把这次记成
     * 「部分成功」，用户回头能看出哪次没拿全。
     */
    public static int publishDir(Context ctx, String srcDir, String albumSub) throws IOException {
        File[] files = new File(srcDir).listFiles();
        if (files == null) {
            throw new IOException("读不到待入库的目录：" + srcDir);
        }
        // 排序只是为了让相册里的顺序与 img_01、img_02… 一致（File 按路径排）
        Arrays.sort(files);

        int ok = 0;
        IOException lastError = null;
        for (File f : files) {
            if (!f.isFile()) {
                continue;
            }
            String name = f.getName();
            // 半截文件不入库：Python 侧是"先写 .part 再改名"，正常情况下见不到，
            // 但真见到时宁可不收，也不能往相册塞一张打不开的图。
            if (name.endsWith(".part") || name.startsWith(".")) {
                continue;
            }
            try {
                publish(ctx, f, albumSub, name);
                ok++;
            } catch (IOException e) {
                lastError = e;
            }
        }
        if (ok == 0 && lastError != null) {
            throw lastError;
        }
        return ok;
    }
}
