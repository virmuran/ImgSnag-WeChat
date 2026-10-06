# R8/ProGuard 规则
#
# 当前 release 构建设了 isMinifyEnabled = false（见 app/build.gradle.kts 的说明），
# 所以这个文件暂时不参与构建。留着是为了两件事：
#   1. 万一以后要开压缩，规则已经摆在这里，不用现查
#   2. 记录"哪些东西不能被削掉"——这是知识，不是配置
#
# 真要开压缩时必须保留的：
#   · Chaquopy 运行时（Java 与 Python 的桥，全靠反射）
-keep class com.chaquo.python.** { *; }
#   · 本 App 里被 Python 通过 Java 接口反向调用的类（Gallery）
-keep class com.virmuran.imgsnag.Gallery { *; }
-keep class com.virmuran.imgsnag.MainActivity { *; }
#   · 给异常栈留行号
-keepattributes SourceFile,LineNumberTable
