#!/bin/bash
# update.sh
# 在伺服器上直接執行，更新 yt-dlp 並重啟 Bot
# 用法（在伺服器上）：bash ~/discord-music-bot/scripts/update.sh

set -e

BOT_DIR="$HOME/discord-music-bot"
SERVICE_NAME="music-bot"

echo "[更新] 啟動虛擬環境..."
source "$BOT_DIR/.venv/bin/activate"

echo "[更新] 更新 yt-dlp（最常需要更新的套件）..."
pip install --upgrade yt-dlp

echo "[更新] 更新所有套件..."
pip install --upgrade -r "$BOT_DIR/requirements.txt"

echo "[重啟] 重啟 Bot 服務..."
sudo systemctl restart $SERVICE_NAME

echo ""
sudo systemctl status $SERVICE_NAME --no-pager
echo ""
echo "[完成] Bot 已更新並重啟"
