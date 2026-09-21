; ImgSnag 微信公众号版 — Inno Setup 安装包脚本
; 编译：ISCC.exe ImgSnagWeChat.iss
; 发新版时改 AppVersion（build_release.py 会自动同步，勿手改）

#define MyAppName "ImgSnag 微信公众号版"
#define MyAppVersion "1.8.0"
#define MyAppPublisher "ImgSnag"
#define MyAppURL "https://github.com/virmuran"

[Setup]
; 固定 AppId：保证升级/卸载识别同一程序，勿改动
AppId={{C3FBB3E8-43EA-4769-8F6A-AFCCA45BBE05}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\ImgSnagWeChat
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=ImgSnagWeChat_{#MyAppVersion}_setup
SetupIconFile=ImgSnag.ico
UninstallDisplayIcon={app}\ImgSnagWeChat.exe
; LZMA2 极限压缩 + 固实包：Qt DLL 压缩率约 50~60%
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"; Flags: checkedonce

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "dist\ImgSnagWeChat\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\ImgSnagWeChat.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\ImgSnagWeChat.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ImgSnagWeChat.exe"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 注意：用户数据在 %USERPROFILE%\.imgsnag_wechat 与 %APPDATA%\ImgSnagWeChat，卸载时一律不删
