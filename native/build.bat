@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem Builds bdo_decklink.dll and copies it into the add-on's bin folder.
rem   DECKLINK_SDK_DIR  folder that contains Win\include\DeckLinkAPI.idl
rem                     (default: the SDK headers in third_party\decklink-sdk)
setlocal
set "HERE=%~dp0"
if not defined DECKLINK_SDK_DIR set "DECKLINK_SDK_DIR=%HERE%..\third_party\decklink-sdk"
set "SDK_INC=%DECKLINK_SDK_DIR%\Win\include"
if not exist "%SDK_INC%\DeckLinkAPI.idl" goto :no_sdk

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR goto :no_vs
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || goto :no_vs

set "OUT=%HERE%build"
if not exist "%OUT%" mkdir "%OUT%"
echo [build] MIDL %SDK_INC%\DeckLinkAPI.idl
midl /nologo /env x64 /h DeckLinkAPI_h.h /iid DeckLinkAPI_i.c /tlb DeckLinkAPI.tlb /out "%OUT%" /I "%SDK_INC%" "%SDK_INC%\DeckLinkAPI.idl" || goto :failed

echo [build] cl bdo_decklink.dll
cl /nologo /LD /O2 /MT /EHsc /std:c++17 /W3 /DUNICODE /D_UNICODE /I"%OUT%" "%HERE%bdo_decklink.cpp" "%OUT%\DeckLinkAPI_i.c" /Fo"%OUT%\\" /Fe"%OUT%\bdo_decklink.dll" /link /NOLOGO || goto :failed

if not exist "%HERE%..\blender_decklink_output\bin" mkdir "%HERE%..\blender_decklink_output\bin"
copy /y "%OUT%\bdo_decklink.dll" "%HERE%..\blender_decklink_output\bin\bdo_decklink.dll" >nul || goto :failed
echo [build] OK: blender_decklink_output\bin\bdo_decklink.dll
exit /b 0

:no_sdk
echo [build] DeckLink SDK not found: "%SDK_INC%\DeckLinkAPI.idl"
echo [build] set DECKLINK_SDK_DIR to the SDK folder (download from blackmagicdesign.com/developer)
exit /b 1
:no_vs
echo [build] Visual Studio C++ build tools not found
exit /b 1
:failed
echo [build] FAILED
exit /b 1
