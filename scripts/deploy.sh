#!/bin/bash
# deploy.sh
# 從本機（WSL）將 Bot 檔案上傳至雲端伺服器並重啟
# 用法：bash scripts/deploy.sh
#
# 首次使用前，修改下方兩個變數：
#   SERVER_IP  — 你的伺服器公開 IP
#   SSH_KEY    — 你的 SSH 私鑰路徑

set -e

# ── 請修改這兩個變數 ──────────────────────────
SERVER_IP="YOUR_SERVER_IP"          # 例如：140.238.xx.xx
SSH_KEY="$HOME/.ssh/oracle.key"     # SSH 私鑰路徑
# ─────────────────────────────────────────────

SERVER_USER="ubuntu"
REMOTE_DIR="/home/ubuntu/discord-music-bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 驗證設定
if [ "$SERVER_IP" = "YOUR_SERVER_IP" ]; then
    echo "[錯誤] 請先編輯 scripts/deploy.sh，填入 SERVER_IP"
    exit 1
fi

if [ ! -f "$SSH_KEY" ]; then
    echo "[錯誤] 找不到 SSH 私鑰：$SSH_KEY"
    echo "請確認路徑正確，或修改 SSH_KEY 變數"
    exit 1
fi

echo "============================================"
echo "  上傳 Bot 至伺服器..."
echo "  目標：$SERVER_USER@$SERVER_IP:$REMOTE_DIR"
echo "============================================"
echo ""

# rsync 上傳（排除敏感檔與快取）
rsync -avz --progress \
    --exclude='.env' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='cookies.txt' \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
    "$SCRIPT_DIR/" \
    "$SERVER_USER@$SERVER_IP:$REMOTE_DIR/"

echo ""
echo "[步驟] 在伺服器上安裝/更新套件..."
ssh -i "$SSH_KEY" "$SERVER_USER@$SERVER_IP" bash <<REMOTE
    set -e
    cd $REMOTE_DIR

    # 建立虛擬環境（若不存在）
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
        echo "虛擬環境建立完成"
    fi

    source .venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    echo "套件安裝完成"
REMOTE

echo ""
echo "[步驟] 重啟 Bot 服務..."
ssh -i "$SSH_KEY" "$SERVER_USER@$SERVER_IP" \
    "sudo systemctl restart music-bot && sudo systemctl status music-bot --no-pager"

echo ""
echo "============================================"
echo "  部署完成！"
echo "  查看 Log：ssh -i $SSH_KEY $SERVER_USER@$SERVER_IP"
echo "            然後執行：journalctl -u music-bot -f"
echo "============================================"
