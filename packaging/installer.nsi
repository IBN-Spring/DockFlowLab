; DockFlowLab Windows 安装包（NSIS 3）
;
; 前置条件：先运行 packaging\build_windows.bat 生成 dist\DockFlowLab\。
; 构建：makensis /V2 packaging\installer.nsi
; 或：packaging\build_windows_installer.bat [makensis.exe 路径]
;
; 采用按用户安装：写入 %LOCALAPPDATA%\Programs\DockFlowLab，不需要管理员权限；
; 卸载信息写入 HKCU，避免影响同一台机器上的其他用户。

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"

!define APP_NAME "DockFlowLab"
!define APP_VERSION "2.5.0"
!define APP_PUBLISHER "IBN-Spring"
!define APP_URL "https://github.com/IBN-Spring/DockFlowLab"
!define APP_EXE "DockFlowLab.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\DockFlowLab"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "..\release\DockFlowLab-Setup-${APP_VERSION}-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\DockFlowLab"
InstallDirRegKey HKCU "Software\DockFlowLab" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "${APP_NAME} ${APP_VERSION}"

VIProductVersion "2.5.0.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "GPL-2.0"

!define MUI_ABORTWARNING
!define MUI_ICON "..\src\dockflowlab\resources\app.ico"
!define MUI_UNICON "..\src\dockflowlab\resources\app.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "启动 DockFlowLab"
!define MUI_FINISHPAGE_LINK "项目主页：${APP_URL}"
!define MUI_FINISHPAGE_LINK_LOCATION "${APP_URL}"
!define MUI_WELCOMEPAGE_TITLE "安装 ${APP_NAME} ${APP_VERSION}"
!define MUI_WELCOMEPAGE_TEXT "DockFlowLab 是一个离线运行的分子对接桌面工作台，安装包已内置 AutoDock Vina、QuickVina 2、Smina 与 PyMOL。$\r$\n$\r$\n本安装程序会把程序文件放到当前用户目录，不需要管理员权限；结构、结果和设置仍然保存在你选择的工作目录中。"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!define MUI_LANGDLL_ALLLANGUAGES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Section "DockFlowLab" SEC_MAIN
  SetOutPath "$INSTDIR"
  File /r "..\dist\DockFlowLab\*.*"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\DockFlowLab"
  CreateShortCut "$SMPROGRAMS\DockFlowLab\DockFlowLab.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortCut "$SMPROGRAMS\DockFlowLab\卸载 DockFlowLab.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\DockFlowLab.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0

  WriteRegStr HKCU "Software\DockFlowLab" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "${APP_URL}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\DockFlowLab.lnk"
  Delete "$SMPROGRAMS\DockFlowLab\DockFlowLab.lnk"
  Delete "$SMPROGRAMS\DockFlowLab\卸载 DockFlowLab.lnk"
  RMDir "$SMPROGRAMS\DockFlowLab"

  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\DockFlowLab"

  RMDir /r "$INSTDIR"
SectionEnd
