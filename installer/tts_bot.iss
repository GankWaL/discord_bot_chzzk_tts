; 디스코드 TTS 봇 설치 마법사 (Inno Setup 6).
; installer\build.ps1 이 PyInstaller 빌드 뒤에 /DAppVersion=<src\app_version.py 의 VERSION> 으로 컴파일한다.
#ifndef AppVersion
  #error AppVersion is not defined - run installer\build.ps1
#endif
#define AppName "디스코드 TTS 봇"
#define GuiExe "tts_bot_gui.exe"
#define BotExe "tts_bot.exe"

[Setup]
AppId={{3F6B2C1E-8D4A-4B7F-9E2C-5A1D7C3B9E64}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=GankWaL
AppPublisherURL=https://github.com/GankWaL/discord_bot_chzzk_tts
; 관리자 권한 없이 현재 사용자에게 설치 → {autopf} = %LOCALAPPDATA%\Programs
; 설정·녹음·모델·커스텀 TTS 환경도 이 폴더에 쌓이므로 사용자 쓰기 권한이 있어야 한다
PrivilegesRequired=lowest
DefaultDirName={autopf}\DiscordTTSBot
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\build\release
OutputBaseFilename=DiscordTTSBot-Setup-{#AppVersion}
SetupIconFile=..\icon\icon.ico
UninstallDisplayIcon={app}\{#GuiExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Windows 시작 시 컨트롤 패널과 봇 자동 실행"; GroupDescription: "실행 옵션:"; Flags: unchecked
Name: "ffmpeg"; Description: "FFmpeg 설치 (음성 재생에 필요, winget)"; GroupDescription: "필수 구성 요소:"; Check: not FfmpegFound

[Files]
Source: "..\build\pyinstaller\DiscordTTSBot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 커스텀 TTS(내 목소리) 추론 서버는 tts_env 파이썬으로 소스를 직접 실행한다
Source: "..\src\custom_tts_server.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "..\src\setup_custom_tts.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "..\bat\setup_tts_server.bat"; DestDir: "{app}\bat"; Flags: ignoreversion
Source: "..\bat\start_tts_server.bat"; DestDir: "{app}\bat"; Flags: ignoreversion
Source: "..\icon\icon.ico"; DestDir: "{app}\icon"; Flags: ignoreversion
Source: "..\icon\icon.png"; DestDir: "{app}\icon"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#GuiExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#GuiExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#GuiExe}"; Parameters: "--autostart"; Tasks: startup

[Run]
Filename: "{cmd}"; Parameters: "/c winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements || pause"; StatusMsg: "FFmpeg 설치 중 (winget)..."; Tasks: ffmpeg; Flags: waituntilterminated
; GUI 자동 업데이트(/SILENT)에서도 다시 켜지도록 skipifsilent 를 붙이지 않는다
Filename: "{app}\{#GuiExe}"; Parameters: "--autostart"; Description: "지금 컨트롤 패널 실행"; Flags: nowait postinstall

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#GuiExe} /IM {#BotExe}"; Flags: runhidden; RunOnceId: "StopApp"
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{app}\src\custom_tts_server*' } | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }"""; Flags: runhidden; RunOnceId: "StopTtsServer"

[Code]
function FfmpegFound: Boolean;
begin
  Result := (FileSearch('ffmpeg.exe', GetEnv('PATH')) <> '') or
    FileExists(ExpandConstant('{localappdata}\Microsoft\WinGet\Links\ffmpeg.exe'));
end;

{ 실행 중인 컨트롤 패널·봇을 끄고 덮어쓴다 (GUI 자동 업데이트는 스스로 먼저 종료한다) }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#GuiExe} /IM {#BotExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

{ 설치 폴더에 쌓인 사용자 데이터는 물어보고 지운다 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox('설정(.env)·유저 설정·로그·내 목소리 녹음/모델·커스텀 TTS 환경(tts_env, GPT-SoVITS)도 모두 삭제할까요?' + #13#10#13#10 +
              '다시 설치해서 계속 쓸 거라면 [아니요] 를 누르세요.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{app}'), True, True, True);
end;
