@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   ImgSnag 微信公众号版 - 一键发版
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [!] 未找到 .venv，正在创建并安装依赖...
    python -m venv .venv
    if errorlevel 1 (
        echo [X] 创建 venv 失败，请确认已安装 Python 3.11+
        pause
        exit /b 1
    )
    .venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
)

.venv\Scripts\python.exe build_release.py %*

echo.
pause
