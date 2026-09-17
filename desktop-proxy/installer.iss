; AgentSoc 桌面代理安装脚本
; 生成标准 Windows 安装包，双击安装，自动注册服务

#define MyAppName "AgentSoc Desktop Proxy"
#define MyAppVersion "0.3.0"
#define MyAppExeName "agentsoc-proxy.exe"

[Setup]
AppId={{8A1B2C3D-4E5F-4A6B-7C8D-9E0F1A2B3C4D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\AgentSoc
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=AgentSoc-Proxy-Setup
Compression=lzma
SolidCompression=yes
; 需要管理员权限（装证书 + 写 hosts + 注册服务）
PrivilegesRequired=admin
; 静默安装支持（域控用 /VERYSILENT /SUPPRESSMSGBOXES）
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "agentsoc-proxy.exe"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; 安装完成后一键注册服务（-setup 会复制自己、写配置、注册并启动服务）
Filename: "{app}\{#MyAppExeName}"; Parameters: "-setup http://172.17.0.200:8000"; Flags: runhidden

[UninstallRun]
; 卸载前停止并删除服务
Filename: "{app}\{#MyAppExeName}"; Parameters: "-uninstall"; Flags: runhidden; RunOnceId: "RemoveAgentSocService"
