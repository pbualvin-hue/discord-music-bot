@echo off
REM ============================================================
REM  一鍵更新 Discord Music Bot（雲端伺服器）
REM  作用：SSH 進伺服器 → 升級 yt-dlp 與相依套件 → 重啟 Bot
REM  用途：YouTube 改版導致無法播放時，雙擊此檔即可修復
REM
REM  首次使用前，如伺服器資訊有變，請改下方三個變數。
REM ============================================================

setlocal
chcp 65001 >nul

REM ── 伺服器設定（如有變更請修改）──────────────────────
set SERVER_USER=ubuntu
set SERVER_IP=161.33.147.103
set SSH_KEY=%USERPROFILE%\.ssh\oracle.key
REM ─────────────────────────────────────────────────────

title 更新 Discord Music Bot
echo.
echo ============================================
echo   一鍵更新 Discord Music Bot
echo   目標：%SERVER_USER%@%SERVER_IP%
echo ============================================
echo.

if not exist "%SSH_KEY%" (
  echo [錯誤] 找不到 SSH 私鑰：%SSH_KEY%
  echo 請確認路徑，或編輯本檔的 SSH_KEY 變數。
  echo.
  pause
  exit /b 1
)

echo [連線] 正在更新 yt-dlp 並重啟 Bot，請稍候…
echo.

ssh -i "%SSH_KEY%" -o StrictHostKeyChecking=no %SERVER_USER%@%SERVER_IP% "bash ~/discord-music-bot/scripts/update.sh"

if %ERRORLEVEL% NEQ 0 (
  echo.
  echo [失敗] 更新過程發生錯誤，錯誤碼：%ERRORLEVEL%
  echo 請檢查網路或伺服器狀態。
) else (
  echo.
  echo [完成] Bot 已更新並重啟 ^_^
)

echo.
pause
endlocal
