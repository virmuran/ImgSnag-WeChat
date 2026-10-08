// app 模块 —— 把 Python 版的抓取逻辑包成原生安卓 App
//
// 三层结构，各管一段：
//     Java（MainActivity / Gallery）  界面、接收微信分享、把图写进相册
//     imgsnag_android.py              桥接：URL → 抓 HTML → 解析 → 下载
//     web_image_dl/（自动同步的副本）  站点知识：微信原图档 / 去水印 / 顺序
//
// **一个 pip 包都不装**。Chaquopy 装包要走它自己的仓库、还要为 arm64 现编，
// 是整条链路最容易失败的环节；而 `web_image_dl/` 本来就是零第三方依赖的，
// 抓取用自带的 urllib 就够。少装一个包，就少一处构建会挂的地方。

import org.gradle.api.GradleException

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// 版本号从项目根的 version.py 读 —— 与桌面版同源。
// 好处很实在：APK 的"关于"与桌面版对得上，用户不会拿到一个"1.0.0 的 1.9.0 功能"。
val appVersion: String = run {
    val f = rootProject.file("../version.py")
    val text = if (f.exists()) f.readText() else ""
    Regex("""VERSION\s*=\s*"([^"]+)"""").find(text)?.groupValues?.get(1) ?: "0.0.0"
}
val verParts = appVersion.split(".")
val appVersionCode = verParts.getOrElse(0) { "0" }.toInt() * 10000 +
                     verParts.getOrElse(1) { "0" }.toInt() * 100 +
                     verParts.getOrElse(2) { "0" }.toInt()

android {
    namespace = "com.virmuran.imgsnag"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.virmuran.imgsnag"
        minSdk = 29          // 写相册用的是 MediaStore 的 RELATIVE_PATH（Android 10 起）——
                             // 有它就不需要任何存储权限。再往下兼容得写另一套老 API，
                             // 对自用工具不值当。
        targetSdk = 36
        versionName = appVersion
        versionCode = appVersionCode

        ndk {
            // 只带 arm64-v8a —— 手机是红米 K80（arm64）。
            // 每多一个 ABI 就多复制一份 Python 运行时，体积几乎翻倍；
            // 这个 App 只在真机上手装，不必为模拟器留 x86_64。
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        create("selfSigned") {
            // 自用签名。**必须稳定**：换了密钥后新版装不上旧版（签名不匹配），
            // 只能先卸载再装（数据会丢）。所以密钥是提交进仓库的固定文件，
            // 不是每次构建现生成 —— 详见 keystore/README.md。
            val ks = rootProject.file("keystore/imgsnag.p12")
            if (!ks.exists()) {
                throw GradleException(
                    "缺少签名密钥 ${ks.absolutePath}\n" +
                    "release 包不签名就装不上手机，所以这里直接报错而不是悄悄跳过。\n" +
                    "生成方法见 android-apk/keystore/README.md"
                )
            }
            storeFile = ks
            storeType = "pkcs12"
            storePassword = "imgsnag"
            keyAlias = "imgsnag"
            keyPassword = "imgsnag"
        }
    }

    buildTypes {
        getByName("release") {
            // 不做代码压缩/资源裁剪。
            // Chaquopy 的 Python 代码是靠反射调进 Java 的，R8 看不见这些调用；
            // 为省几 MB 换来"某个功能上线后莫名失效"（而且只在小尺寸 APK 上出现）
            // 完全不划算。
            isMinifyEnabled = false
            isShrinkResources = false
            signingConfig = signingConfigs.getByName("selfSigned")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        // 界面外壳要用 BuildConfig.VERSION_NAME 把版本号传给 Python 侧
        // （网页底部那行版本号就是它）。AGP 8 起默认不生成 BuildConfig，
        // 不显式打开的话编译直接报 "找不到符号 BuildConfig"。
        buildConfig = true
    }

    lint {
        // 静态检查只提示、不阻断构建：这是自用工具，不追求上架规范
        abortOnError = false
    }

    packaging {
        resources {
            excludes += listOf("META-INF/*.kotlin_module", "META-INF/DEPENDENCIES")
        }
    }
}

chaquopy {
    defaultConfig {
        // Python 3.12：**构建机上的 Python 主次版本必须与这里完全一致**
        // （Chaquopy 17 起这条从"最低版本"变成了"必须相同"）。
        // CI 跑在 ubuntu-latest 上，系统自带 python3.12，所以两边都能对上；
        // 本机是 Windows + Python 3.13，**本地构建会失败**，这是预期行为 ——
        // 这个工程本来就设计成只在 GitHub 的服务器上构建。
        version = "3.12"
        buildPython("python3.12")
    }
}

dependencies {
    // FileProvider 在这个包里 —— 应用内更新要把下载好的 APK 以 content://
    // 交给系统安装器，安卓 7 起 file:// 会直接抛 FileUriExposedException。
    // 这是本项目**唯一**的 Gradle 依赖：自己手写一个 ContentProvider 去顶替它
    // 也能做，但代码量与踩坑面都不划算（而且同样要写这一个依赖的位置）。
    // 注意区分：这里不是 Chaquopy 的 pip 包 —— 那种要走 Chaquopy 自己的仓库
    // 并为 arm64 现编，才是构建最容易挂的一环（本项目一个都不装）。
    implementation("androidx.core:core:1.13.1")
}
