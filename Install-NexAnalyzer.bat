@echo off
setlocal EnableExtensions
title NexAnalyzer Installer

:: ============================================================================
:: One-time setup for a new machine. Download this single file, double-click it,
:: and walk away: it installs Python and Git if they are missing, downloads
:: NexAnalyzer, puts a shortcut on the Desktop, and starts the app.
::
:: Nobody needs a terminal after this. The Desktop shortcut runs start.bat,
:: which updates the app from GitHub and then launches it.
::
:: Optional overrides, all normally unset:
::   NEXA_DIR         install folder      (default: %USERPROFILE%\nexanalyzer)
::   NEXA_REPO        repository to clone (default: the GitHub URL below)
::   NEXA_SETUP_ONLY  set to 1 to install without launching the app afterwards
:: ============================================================================

if not defined NEXA_REPO set "NEXA_REPO=https://github.com/Nexstrom/NexAnalyzer.git"
if not defined NEXA_DIR set "NEXA_DIR=%USERPROFILE%\nexanalyzer"

echo ============================================
echo   NexAnalyzer - one-time setup
echo ============================================
echo.
echo   Install folder: %NEXA_DIR%
echo   Source:         %NEXA_REPO%
echo.
echo The first run takes a few minutes. Leave this window alone until it
echo says "Setup complete".
echo.

:: --------------------------------------------------- 0. Check the location ---
:: Both of these are real failures rather than style preferences, so stop now
:: instead of letting them surface halfway through a long dependency install.

:: pywin32 unpacks files about 100 characters deep inside the environment. Much
:: past 100 characters of base path, pip aborts partway through installing
:: dependencies with "WinError 206: The filename or extension is too long".
:: %VAR:~100% is empty unless the value is longer than 100 characters.
if not "%NEXA_DIR:~100%"=="" goto :path_too_long

:: A sync client rewriting files under venv\ while pip is writing them corrupts
:: the environment, and re-uploads tens of thousands of files on every launch.
echo %NEXA_DIR% | findstr /i /c:"OneDrive" /c:"Dropbox" /c:"Google Drive" /c:"Box Sync" >nul
if not errorlevel 1 goto :path_is_synced

:: ------------------------------------------------------------------ 1. Git ---
echo [1/4] Checking Git...
git --version >nul 2>&1
if not errorlevel 1 goto :git_ok

echo       Not found - installing Git for you.
winget --version >nul 2>&1
if errorlevel 1 goto :no_winget_git
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements

:: A fresh winget install never reaches the PATH of this already-running
:: window, so look where Git actually landed rather than trusting PATH.
set "PATH=%PATH%;%ProgramFiles%\Git\cmd;%LOCALAPPDATA%\Programs\Git\cmd"
git --version >nul 2>&1
if errorlevel 1 goto :reopen_needed
goto :git_ok

:no_winget_git
echo.
echo [ERROR] Git is missing, and winget is not available to install it.
echo         Install Git by hand from https://git-scm.com/downloads
echo         ^(accept all the defaults^), then double-click this file again.
goto :fatal

:git_ok
for /f "delims=" %%v in ('git --version') do echo       OK - %%v

:: --------------------------------------------------------------- 2. Python ---
echo [2/4] Checking Python...
python --version >nul 2>&1
if not errorlevel 1 goto :python_ok

echo       Not found - installing Python for you.
winget --version >nul 2>&1
if errorlevel 1 goto :no_winget_python
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements

:: Same PATH problem as Git. winget installs Python per-user, so search there.
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do set "PYDIR=%%d"
if defined PYDIR set "PATH=%PATH%;%PYDIR%;%PYDIR%\Scripts"
python --version >nul 2>&1
if errorlevel 1 goto :reopen_needed
goto :python_ok

:no_winget_python
echo.
echo [ERROR] Python is missing, and winget is not available to install it.
echo         Install Python 3.10+ from https://www.python.org/downloads/
echo         and TICK "Add Python to PATH", then double-click this file again.
goto :fatal

:python_ok
for /f "delims=" %%v in ('python --version') do echo       OK - %%v

:: ------------------------------------------------------------ 3. Get the app ---
echo [3/4] Downloading NexAnalyzer...
if exist "%NEXA_DIR%\.git" goto :already_installed
if exist "%NEXA_DIR%\" goto :folder_in_the_way

git clone "%NEXA_REPO%" "%NEXA_DIR%"
if errorlevel 1 goto :clone_failed
echo       OK - downloaded.
goto :have_app

:already_installed
echo       Already installed - the launcher will update it in a moment.
goto :have_app

:folder_in_the_way
echo.
echo [ERROR] The folder "%NEXA_DIR%" already exists but is not a NexAnalyzer
echo         download. Rename or delete it, then double-click this file again.
goto :fatal

:clone_failed
echo.
echo [ERROR] Could not download NexAnalyzer.
echo         Check your internet connection, and that this address opens in
echo         your browser:
echo         %NEXA_REPO%
echo         If your company blocks GitHub, ask IT for the proxy settings.
goto :fatal

:have_app
:: A clone that reported success but left no launcher means something truncated
:: it -- better to say so than to fail confusingly two steps later.
if not exist "%NEXA_DIR%\start.bat" goto :incomplete_download

:: -------------------------------------------------------- 4. Desktop shortcut ---
echo [4/4] Creating a Desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; $s = $w.CreateShortcut((Join-Path $w.SpecialFolders('Desktop') 'NexAnalyzer.lnk')); $s.TargetPath = Join-Path $env:NEXA_DIR 'start.bat'; $s.WorkingDirectory = $env:NEXA_DIR; $s.Description = 'Launch NexAnalyzer (updates itself first)'; $s.Save()"
if errorlevel 1 (
    echo       [WARN] Could not create the shortcut. Not a problem - the app can
    echo              still be started from %NEXA_DIR%\start.bat
) else (
    echo       OK - "NexAnalyzer" is on your Desktop.
)

echo.
echo ============================================
echo   Setup complete.
echo ============================================
echo.
echo From now on, just double-click NexAnalyzer on your Desktop. It updates
echo itself to the latest version every time it starts.
echo.

if "%NEXA_SETUP_ONLY%"=="1" goto :setup_only

echo Starting NexAnalyzer for the first time. Installing the Python packages
echo it needs takes a few minutes - the browser opens by itself when ready.
echo.
:: Call it by full path. A bare "call start.bat" relies on cmd searching the
:: current directory, which hardened environments switch off via
:: NoDefaultCurrentDirectoryInExePath.
cd /d "%NEXA_DIR%"
call "%NEXA_DIR%\start.bat"
exit /b 0

:setup_only
echo NEXA_SETUP_ONLY was set, so the app was not started.
exit /b 0

:: ------------------------------------------------------------------- errors ---
:path_too_long
echo [ERROR] That install folder sits too deep in the filesystem ^(its path is
echo         over 100 characters^). Installing dependencies would fail partway
echo         through with "the filename or extension is too long".
echo         Use a short path such as %USERPROFILE%\nexanalyzer
goto :fatal

:path_is_synced
echo [ERROR] That install folder is inside a cloud-synced folder ^(OneDrive,
echo         Dropbox, and so on^). Sync clients corrupt the Python environment
echo         this app builds, and re-upload thousands of files every launch.
echo         Use a plain local path such as %USERPROFILE%\nexanalyzer
goto :fatal

:incomplete_download
echo.
echo [ERROR] The download finished but start.bat is missing, so it was
echo         incomplete. Delete "%NEXA_DIR%" and run this file again.
goto :fatal

:reopen_needed
echo.
echo [ACTION NEEDED] The install worked, but this window cannot see the new
echo                 program yet. Close this window and double-click this
echo                 file again - it carries on from where it stopped.
echo.
pause
exit /b 1

:fatal
echo.
pause
exit /b 1
