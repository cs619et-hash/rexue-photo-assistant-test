@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 熱血少年照片整理助手 - 安裝
echo 正在檢查電腦環境，請不要關閉這個視窗...
echo.

set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py"
if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
  echo [無法啟動] 這台電腦尚未安裝 Python。
  echo 請到 https://www.python.org/downloads/ 安裝，並勾選 Add Python to PATH。
  echo 安裝完成後，再點兩下本檔案。
  echo.
  pause
  exit /b 1
)

echo [1/2] 安裝必要元件...
%PY_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [失敗] 元件安裝失敗，請將這個畫面截圖傳回來。
  pause
  exit /b 1
)

echo.
echo [2/2] 建立 Windows 程式...
%PY_CMD% -m PyInstaller --noconfirm --clean --onefile --windowed --name "熱血少年照片整理助手" --icon "assets\rexue_app_icon.ico" --add-data "assets\rexue_app_icon.ico;assets" app.py
if errorlevel 1 (
  echo.
  echo [失敗] 程式建立失敗，請將這個畫面截圖傳回來。
  pause
  exit /b 1
)

echo.
echo [完成] 即將開啟 dist 資料夾。
echo 裡面的「熱血少年照片整理助手.exe」就是程式。
start "" "%~dp0dist"
pause
