; Instalátor Podcast Clipperu (Inno Setup 6). Sestavuje se automaticky na GitHubu.
#ifndef AppVersion
  #define AppVersion "0"
#endif

[Setup]
AppId={{6E3C1A52-7B0D-4A55-9D0E-5C1F8B2A9A11}
AppName=Podcast Clipper
AppVersion={#AppVersion}
AppPublisher=Podcast Clipper
DefaultDirName={localappdata}\Programs\Podcast Clipper
DefaultGroupName=Podcast Clipper
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=PodcastClipper-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\PodcastClipper.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"

[Tasks]
Name: "desktopicon"; Description: "Vytvořit ikonu na ploše"; GroupDescription: "Zástupci:"

[InstallDelete]
; před instalací se smaže stará verze aplikace (vaše klipy a nastavení jsou jinde a zůstanou)
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\PodcastClipper.exe"

[Files]
Source: "dist\PodcastClipper\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\Podcast Clipper"; Filename: "{app}\PodcastClipper.exe"
Name: "{autodesktop}\Podcast Clipper"; Filename: "{app}\PodcastClipper.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PodcastClipper.exe"; Description: "Spustit Podcast Clipper"; Flags: nowait postinstall skipifsilent
; po automatické aktualizaci (tichý režim) se aplikace spustí sama
Filename: "{app}\PodcastClipper.exe"; Flags: nowait; Check: WizardSilent
