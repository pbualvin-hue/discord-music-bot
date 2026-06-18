@echo off
chcp 65001 >nul
title Discord Music Bot

echo ================================
echo   Discord Music Bot 啟動中...
echo ================================
echo.

:: 將目前 Windows 路徑轉換為 WSL 路徑
for /f "delims=" %%i in ('wsl wslpath -a "%~dp0"') do set WSL_DIR=%%i

:: 移除尾端換行與斜線
set WSL_DIR=%WSL_DIR: =%

echo [WSL] 路徑：%WSL_DIR%
echo.

:: 在 WSL 中執行啟動腳本
wsl bash -c "cd \"%WSL_DIR%\" && bash start.sh"

echo.
echo [停止] Bot 已停止運行
pause
