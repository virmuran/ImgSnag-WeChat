// 顶层构建文件：只声明插件版本，不在这里应用（apply false）
//
// 子模块（app/）里写 `id("com.chaquo.python")` 而不带版本号，
// 版本就是从这里继承的 —— 这是 Gradle 官方推荐的插件版本管理方式。
plugins {
    id("com.android.application") version "8.13.1" apply false
    id("com.chaquo.python") version "17.0.0" apply false
}
