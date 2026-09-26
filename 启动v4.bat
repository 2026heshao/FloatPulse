@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title FloatPulse 启动器

echo ============================================================
echo   FloatPulse 启动器
echo   v4 = 开发主线（默认）      v2 / v3 = 冻结基线（对照用）
echo ============================================================
echo.

REM ---------------- 解析参数 ----------------
set "APPDIR=v4"
set "MODE=console"
set "SCRIPT=knowledge_ball.py"
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="v2"      set "APPDIR=v2"
if /i "%~1"=="v3"      set "APPDIR=v3"
if /i "%~1"=="v4"      set "APPDIR=v4"
if /i "%~1"=="console" set "MODE=console"
if /i "%~1"=="quiet"   set "MODE=quiet"
if /i "%~1"=="demo"    set "SCRIPT=demo_acrylic.py"
shift
goto parse
:parsed

if not exist "%APPDIR%\%SCRIPT%" (
    echo [错误] 找不到 %APPDIR%\%SCRIPT%
    echo        请把这个 bat 放在项目根目录（与 v2 / v3 / v4 同级）。
    echo.
    pause
    exit /b 1
)

REM ---------------- 1. 定位 Python ----------------
echo [1/3] 查找已安装 PyQt6 的 Python 解释器...
set "PYEXE="

call :trypy "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :trypy "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :trypy "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :trypy "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
call :trypy "C:\Python312\python.exe"
call :trypy "C:\Python311\python.exe"
call :trypy "C:\Python310\python.exe"

if not defined PYEXE (
    for /f "delims=" %%P in ('where python 2^>nul') do call :trypy "%%P"
)
if not defined PYEXE (
    for /f "delims=" %%P in ('where py 2^>nul') do call :trypy "%%P"
)

if not defined PYEXE (
    echo.
    echo [错误] 没有找到已装 PyQt6 的 Python。
    echo        请先安装依赖：pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo        解释器：%PYEXE%

REM ---------------- 2. 依赖自检 ----------------
echo [2/3] 检查依赖（PyQt6 / python-docx）...
"%PYEXE%" -c "import PyQt6, docx" 1>"%TEMP%\fp_dep_check.txt" 2>&1
if errorlevel 1 (
    echo [错误] 依赖不完整，详情如下：
    echo ------------------------------------------------------------
    type "%TEMP%\fp_dep_check.txt"
    echo ------------------------------------------------------------
    echo 请在该解释器下执行：pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo        依赖正常

REM ---------------- 3. 启动 ----------------
if /i "%MODE%"=="quiet" goto run_quiet

echo [3/3] 启动 %APPDIR%（控制台模式，可看实时日志）...
echo        关闭本窗口或按 Ctrl+C 即结束程序
echo ------------------------------------------------------------
echo.
"%PYEXE%" "%APPDIR%\%SCRIPT%"
echo.
echo ------------------------------------------------------------
echo 程序已退出（退出码 %ERRORLEVEL%）。
echo 若为异常退出，日志见：data\app.log
echo.
pause
exit /b 0

:run_quiet
echo [3/3] 启动 %APPDIR%（后台模式，无控制台窗口）...
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"
start "" "%PYW%" "%APPDIR%\%SCRIPT%"
echo.
echo        已启动。退出方式：右键悬浮球 - 退出程序，或按 Esc
echo        排查异常：查看 data\app.log
echo.
exit /b 0

REM ---------------- 子过程：探测单个解释器 ----------------
:trypy
if defined PYEXE exit /b 0
if not exist %1 exit /b 0
%1 -c "import PyQt6" >nul 2>&1
if not errorlevel 1 set "PYEXE=%~1"
exit /b 0
