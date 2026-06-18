# 部署注意事項

## 伺服器規格

**機型：VM.Standard.E2.1.Micro（Oracle Cloud Free Tier）**

| 項目 | 規格 |
|------|------|
| CPU | 1/8 OCPU（AMD，共享核心） |
| RAM | 1 GB |
| 磁碟 | 47 GB Boot Volume |
| 網路 | 最高 480 Mbps |
| 費用 | 永久免費 |

---

## ⚠️ 最重要：立即設定 Swap

1 GB RAM 在 yt-dlp 解析 Playlist 或同時多人點歌時**極易觸發 OOM（記憶體不足）導致 Bot 被系統強制殺掉**。

**請在伺服器上立即執行以下指令（一次性設定）：**

```bash
# 建立 1GB Swap 檔
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 設為開機自動掛載
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 確認 Swap 已啟用
free -h
```

預期輸出：
```
              total        used        free
Mem:          967Mi        ...
Swap:         1.0Gi        0Bi       1.0Gi   ← 這行出現即成功
```

---

## 資源監控指令

```bash
# 即時 CPU / 記憶體使用率
top -bn1 | head -20

# 記憶體概況
free -h

# 磁碟使用量
df -h

# Bot 服務狀態
sudo systemctl status music-bot

# 即時 Log（Ctrl+C 離開）
journalctl -u music-bot -f

# 過去 100 行 Log
journalctl -u music-bot -n 100 --no-pager
```

---

## 定期維護清單

### 每週建議

```bash
# 更新 yt-dlp（YouTube 規則常變動，版本過舊容易出現 429 或解析失敗）
bash ~/discord-music-bot/scripts/update.sh
```

### 每月建議

```bash
# 更新系統套件
sudo apt update && sudo apt upgrade -y

# 清理舊 Log（避免磁碟慢慢被填滿）
sudo journalctl --vacuum-time=30d
```

### Cookie 更新（若有設定）

YouTube Cookie 的有效期通常為數週到數月。若出現 429 或「登入要求」錯誤：

1. 在瀏覽器重新登入 YouTube
2. 用擴充套件重新匯出 `cookies.txt`
3. 上傳到伺服器：
   ```bash
   # 在 WSL 本機執行
   scp -i ~/.ssh/oracle.key \
     /mnt/c/Users/Hen/Downloads/cookies.txt \
     ubuntu@161.33.147.103:~/discord-music-bot/cookies.txt
   ```
4. 重啟 Bot：`sudo systemctl restart music-bot`

---

## E2.1.Micro 已知限制與對應措施

### CPU 過慢導致 yt-dlp 解析很慢

**症狀**：`/play` 指令要等 3–8 秒才有回應

**原因**：1/8 OCPU 處理 yt-dlp 的 JavaScript 解析較慢

**對應**：這是正常現象，Discord 的 `defer()` 給了 15 分鐘的回應時間，不會 timeout。無需特別處理。

---

### 記憶體不足（OOM Kill）

**症狀**：Bot 突然離線，Log 顯示 `Killed` 或 `Out of memory`

**對應**：
1. 確認 Swap 已設定（見上方）
2. 降低 Playlist 匯入上限，在 `.env` 加入：
   ```
   MAX_PLAYLIST_SONGS=20
   MAX_QUEUE_SIZE=50
   ```
3. 重啟 Bot：`sudo systemctl restart music-bot`

---

### 磁碟空間不足

**症狀**：Bot 崩潰，Log 顯示 `No space left on device`

**對應**：
```bash
# 查看哪裡佔用空間
du -sh ~/* | sort -hr | head -10

# 清理 Log
sudo journalctl --vacuum-size=100M

# 清理 apt 快取
sudo apt clean
```

---

## 部署相關資訊

| 項目 | 資訊 |
|------|------|
| 伺服器 IP | `161.33.147.103` |
| SSH Key 位置 | `~/.ssh/oracle.key`（WSL 本機） |
| Bot 目錄 | `~/discord-music-bot`（伺服器上） |
| systemd 服務名稱 | `music-bot` |
| Python 虛擬環境 | `~/discord-music-bot/.venv` |

### 常用 SSH 連線指令

```bash
# 連線伺服器（在 WSL 中執行）
ssh -i ~/.ssh/oracle.key ubuntu@161.33.147.103
```

### 更新 Bot 程式碼

```bash
# 在 WSL 本機的專案目錄執行
bash scripts/deploy.sh
```

---

## .env 備份提醒

`.env` 只存在於伺服器上，**不會被 Git 追蹤**。若伺服器重建，Token 需要重新設定。

建議將 Token 另外儲存在安全的地方（密碼管理器）：
- `DISCORD_TOKEN`
- `GUILD_ID`

---

## 檔案說明

```
discord-music-bot/
├── bot.py                          # Bot 主程式入口
├── config.py                       # 環境變數載入
├── start.bat / start.sh            # 本機快速啟動
├── requirements.txt
├── .env.example
├── README.md                       # 安裝教學 + 指令一覽
├── NOTES.md                        # 本檔案：部署與維護注意事項
├── commands/music.py               # 所有 Slash Commands + Cog
├── services/
│   ├── music_player.py             # 播放引擎
│   ├── youtube_service.py          # yt-dlp 封裝
│   ├── stats_service.py            # SQLite 統計與評分
│   ├── filter_service.py           # 音訊濾鏡
│   ├── lyrics_service.py           # Genius 歌詞
│   ├── lyrics_karaoke_service.py   # LRC KTV 歌詞
│   ├── personality_service.py      # 個性回應
│   └── playlist_service.py         # 播放清單序列化
├── models/
│   ├── song.py
│   ├── guild_state.py
│   └── loop_mode.py
├── ui/
│   ├── control_panel_view.py       # 2 排 10 鍵控制面板
│   ├── queue_panel_view.py
│   ├── search_select_view.py
│   ├── lyrics_paged_view.py
│   ├── karaoke_view.py
│   ├── rating_view.py
│   └── vote_skip_view.py
├── utils/
│   ├── logger.py
│   └── permissions.py
├── docs/user_manual.html           # 完整說明書（可列印為 PDF）
├── data/                           # SQLite DB（不入 git）
├── sounds/                         # 入場音效（選用）
└── scripts/
    ├── deploy.sh                   # 本機 → 伺服器一鍵部署
    ├── update.sh                   # 伺服器上快速更新套件
    └── server-setup.sh             # 伺服器初始化（首次使用）
```
