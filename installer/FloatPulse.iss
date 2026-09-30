; ====================================================================
; FloatPulse 安装包脚本（Inno Setup 6，per-user 免管理员）
; ====================================================================
; 编译（版本号由 build_release.py 传入，**不要在这里手改**）：
;     ISCC /DAPP_VERSION=4.7.0 installer\FloatPulse.iss
;
; 设计口径：
;   · per-user 安装（PrivilegesRequired=lowest）→ 默认装到
;     {localappdata}\Programs\FloatPulse，不需要 UAC，学校/公司机器也能装
;   · 用户数据与安装目录解耦（成熟化 2.2 双轨制）：安装版数据保存在
;     %APPDATA%\FloatPulse\float_data\（碎片/笔记/任务/知识库），升级/
;     重装换目录数据不跟随丢失；zip 便携版走 portable.marker 标记留在
;     exe 同目录，dist2 组包天然没有该标记 → 本安装包自动落在安装版轨道
;   · 用户数据不随卸载删除：float_data 是运行期生成的，Inno 只删它自己
;     装过的文件，[UninstallDelete] 刻意留空 → 卸载后数据自动留在 APPDATA
;   · 不做静默更新、不写注册表 Run 项（开机自启用程序内设置，单一来源）
;   · 检测到程序在跑时提示关闭（AppMutex = single_instance.py 的打包环境名）
;   · 界面语言用 [Messages] 内嵌简中覆盖（Default.isl 为底），零外部依赖
;     ——本机 Inno 不带 ChineseSimplified.isl，且 raw.githubusercontent
;     不可达，覆盖常用向导文案是最稳的自包含方案
; ====================================================================

#ifndef APP_VERSION
  #define APP_VERSION "0.0.0"
#endif

#define MyAppName "FloatPulse"
#define MyAppTitle "FloatPulse · 生活悬浮球"
#define MyAppExe "FloatPulse.exe"
#define DistRoot "..\dist2\FloatPulse"

[Setup]
AppId={{9F6B2C41-8A3D-4E57-B1C0-52A7D9E8F301}}
AppName={#MyAppName}
AppVersion={#APP_VERSION}
AppVerName={#MyAppName} v{#APP_VERSION}
AppPublisher=FloatPulse Contributors
; 版本资源里也带上，方便资源管理器文件属性与升级比对
VersionInfoVersion={#APP_VERSION}
VersionInfoTextVersion={#APP_VERSION}
DefaultDirName={autopf}\{#MyAppName}
PrivilegesRequired=lowest
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=..\宣传页
OutputBaseFilename=FloatPulse-v{#APP_VERSION}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExe}
ArchitecturesInstallIn64BitMode=x64compatible
; 覆盖正在运行的程序前提示用户关闭（配合 AppMutex 检测）
CloseApplications=yes
RestartApplications=no
SetupIconFile=..\FloatPulse.ico

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Messages]
; ---- 简体中文覆盖（Default.isl 是英文底稿；只覆盖用户会看到的文案）----
SetupAppTitle={#MyAppName} 安装程序
SetupWindowTitle={#MyAppTitle} v{#APP_VERSION}
WelcomeLabel2=这将安装 [name/ver] 到你的电脑。%n%n建议在继续之前关闭其他正在运行的程序。
SelectDirDesc=选择安装位置
SelectDirLabel3=安装程序将把 [name] 安装到以下文件夹。%n%n要安装到别的位置，请点击「浏览」。
SelectDirBrowseLabel=点击「下一步」继续。
DiskSpaceGBLabel=安装本程序至少需要 [gb] GB 可用磁盘空间。
DiskSpaceMBLabel=安装本程序至少需要 [mb] MB 可用磁盘空间。
CannotInstallToNetworkDrive=安装程序无法安装到网络驱动器。
CannotInstallToUNCPath=安装程序无法安装到 UNC 路径。
SelectTasksDesc=要执行哪些附加任务？
ReadyLabel1=安装程序已准备好安装 [name] 到你的电脑。
ReadyLabel2a=点击「安装」开始安装，或点击「上一步」修改设置。
InstallingLabel=正在安装 [name]，请稍候…
FinishedHeadingLabel=[name] v{#APP_VERSION} 安装完成
FinishedLabelNoIcons=[name] v{#APP_VERSION} 已安装到你的电脑。%n%n你的数据保存在 %APPDATA%\FloatPulse 文件夹（位于安装目录之外），卸载程序不会删除它。
FinishedLabel=[name] v{#APP_VERSION} 已安装到你的电脑。%n%n你的数据保存在 %APPDATA%\FloatPulse 文件夹（位于安装目录之外），卸载程序不会删除它。
ClickFinish=点击「完成」退出安装向导。
SelectStartMenuFolderDesc=选择「开始菜单」文件夹位置
SelectStartMenuFolderLabel3=安装程序将在以下「开始菜单」文件夹中创建程序的快捷方式。
SelectStartMenuFolderBrowseLabel=点击「下一步」继续。
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonYesToAll=全部是(&A)
ButtonNo=否(&N)
ButtonNoToAll=全部否(&O)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&R)...
ButtonWizardBrowse=浏览(&R)...
ButtonNewFolder=新建文件夹(&M)
ExitSetupTitle=退出安装程序
ExitSetupMessage=安装尚未完成。现在退出吗？%n%n以后可以再次运行安装程序完成安装。
ErrorTitle=错误
; ---- 卸载相关 ----
UninstallAppTitle=卸载 [name]
UninstallAppFullTitle=%1 卸载
UninstallStatusLabel=[name] 正在从你的电脑移除，请稍候…
UninstalledAll=[name] 已成功地从你的电脑移除。
UninstalledMost=[name] 卸载完成。%n%n个别无法自动删除的文件请手动处理。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; \
    GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "{#DistRoot}\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; \
    Comment: "{#MyAppTitle}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; \
    Comment: "{#MyAppTitle}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "运行 {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 刻意留空：float_data\ 是用户数据，绝不随卸载清理
