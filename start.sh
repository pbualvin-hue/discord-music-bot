#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================"
echo "  Discord Music Bot 啟動中..."
echo "================================"
echo ""

# 確認 .env 存在
if [ ! -f ".env" ]; then
    echo "[錯誤] 找不到 .env 檔案！"
    echo "請執行：cp .env.example .env"
    echo "並填入你的 DISCORD_TOKEN"
    exit 1
fi

# 確認 Python 3.12+
if ! command -v python3 &>/dev/null; then
    echo "[錯誤] 找不到 python3，請先安裝 Python 3.12+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[Python] 版本：$PYTHON_VERSION"

# 建立虛擬環境（若不存在）
if [ ! -d ".venv" ]; then
    echo "[步驟] 建立虛擬環境..."
    python3 -m venv .venv
    echo "[完成] 虛擬環境建立成功"
fi

# 啟動虛擬環境
source .venv/bin/activate

# 安裝 / 更新套件
echo "[步驟] 檢查套件..."
pip install -q -r requirements.txt
echo "[完成] 套件已就緒"
echo ""

echo "[啟動] Bot 正在運行，按 Ctrl+C 可停止"
echo "================================"
echo ""

python bot.py
