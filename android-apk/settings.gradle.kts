// 安卓版 ImgSnag —— Gradle 设置
//
// 版本组合不是随手挑的，是照 Chaquopy 17.0.0 官方 demo（tag 17.0.0）配的：
//     Chaquopy 17.0.0  +  AGP 8.13.1  +  Gradle 8.13  +  compileSdk 36
// Chaquopy 文档写明支持的 AGP 范围是 7.3.x ~ 9.2.x；用 8.13.1 是它自己 CI 在跑的版本，
// 也是踩坑最少的组合。**不要**顺手升 AGP —— Chaquopy 的 Gradle 插件与 AGP 的
// 内部 API 绑得很紧，升级后报的错通常很难看懂。

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    // 仓库只在上面统一声明，模块里禁止各写一份 ——
    // 否则同一个库在不同模块解析到不同来源，构建结果就不唯一了
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "ImgSnagAPK"
include(":app")
