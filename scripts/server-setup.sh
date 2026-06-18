#!/bin/bash
# server-setup.sh
# 在全新的 Oracle Cloud / VPS Ubuntu 伺服器上執行一次
# 用法：bash server-setup.sh

set -e

BOT_DIR="$HOME/discord-music-bot"
SERVICE_NAME="music-bot"
SERVICE_USER="$USER"

echo "============================================"
echo "  Discord Music Bot — 伺服器初始化腳本"
echo "  User: $SERVICE_USER"
echo "  Bot 目錄: $BOT_DIR"
echo "============================================"
echo ""

# ── 1. 更新系統 ────────────────────────────────
echo "[1/6] 更新系統套件..."
sudo apt-get update -y
sudo apt-get upgrade -y
echo ""

# ── 2. 安裝 Python 3.12 ────────────────────────
echo "[2/6] 安裝 Python 3.12..."
sudo apt-get install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update -y
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev build-essential
echo ""

# 設定 python3 預設指向 3.12
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1
python3 --version

# ── 3. 安裝 FFmpeg ─────────────────────────────
echo "[3/6] 安裝 FFmpeg..."
sudo apt-get install -y ffmpeg
ffmpeg -version | head -n 1
echo ""

# ── 4. 安裝其他工具 ────────────────────────────
echo "[4/6] 安裝 git、pip..."
sudo apt-get install -y git curl
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12
echo ""

# ── 5. 建立 Bot 目錄與虛擬環境 ─────────────────
echo "[5/6] 建立 Bot 目錄..."
mkdir -p "$BOT_DIR"
echo "目錄建立完成：$BOT_DIR"
echo ""

# ── 6. 建立 systemd service ────────────────────
echo "[6/6] 建立 systemd service..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Discord Music Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${BOT_DIR}
ExecStart=${BOT_DIR}/.venv/bin/python ${BOT_DIR}/bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
echo "systemd service 已建立並設為開機自啟"
echo ""

# ── 完成提示 ───────────────────────────────────
echo "============================================"
echo "  初始化完成！接下來請執行："
echo ""
echo "  1. 上傳 Bot 檔案（在你的本機執行 deploy.sh）"
echo "  2. 設定 .env："
echo "     cp $BOT_DIR/.env.example $BOT_DIR/.env"
echo "     nano $BOT_DIR/.env"
echo ""
echo "  3. 首次啟動："
echo "     cd $BOT_DIR && python3 -m venv .venv"
echo "     source .venv/bin/activate"
echo "     pip install -r requirements.txt"
echo "     sudo systemctl start music-bot"
echo ""
echo "  4. 查看 Log："
echo "     journalctl -u music-bot -f"
echo "============================================"
