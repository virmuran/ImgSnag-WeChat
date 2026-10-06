# 签名密钥

`imgsnag.p12` —— 给安卓版 APK 签名用的自用密钥。

## 为什么它被提交进仓库

因为**它必须是固定的**。

安卓靠签名判断「这个新版本是不是同一个应用」。换了密钥的 APK，系统会拒绝覆盖安装，
只能先卸载再装 —— 而卸载会丢掉应用数据。所以密钥不能每次构建现生成
（GitHub 的运行器每次都是干净环境，现生成必然每次都不一样）。

正规做法是把密钥放 CI 的 secret 里，但那样每次发版都得先配 secret，
对一个自用工具是多余的负担。权衡下来直接提交，代价写清楚：

> ⚠️ **这个密钥是公开的。** 谁都能用它签一个同包名的 APK。
> 本应用只自己装、不上架、不处理任何账号或钱财，所以风险可以接受。
> 但**如果你以后要发布给别人用**，请换成私密保管的密钥：
> 生成一个新的 `.p12`（步骤见下），替换本文件，并接受"老用户要卸载重装"这一次代价。

密码与别名都是 `imgsnag`（明文写在 `app/build.gradle.kts` 里），
所以本文件不涉及任何需要保密的字符串。

## 当前密钥的信息

- 格式：PKCS12（`.p12`）
- 算法：RSA 2048，自签，有效期 30 年
- 主题：`CN=ImgSnag, O=virmuran, C=CN`
- 加密：3DES + SHA1 MAC —— **刻意选的老算法**。OpenSSL 3 默认用 AES-256/PBKDF2，
  部分 JDK 版本读不了，报错还是一句容易误判的 "keystore password was incorrect"。
  3DES + SHA1 是各版本 JDK 都能读的组合。
  CI 里 `keytool -list` 那一步就是在构建前先确认这件事。

## 想换一个密钥

在任意装了 OpenSSL 的机器上（Git Bash 自带）：

```sh
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 10950 -nodes \
  -subj "/CN=ImgSnag/O=virmuran/C=CN"

openssl pkcs12 -export -inkey key.pem -in cert.pem -out imgsnag.p12 \
  -name imgsnag -passout pass:imgsnag \
  -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1
```

把生成的 `imgsnag.p12` 覆盖本目录里的同名文件即可（`key.pem` / `cert.pem` 不要提交）。

**换完之后手机上必须先卸载再装**，否则会报「应用未安装」。
