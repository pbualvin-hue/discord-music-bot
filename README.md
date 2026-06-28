# Discord Music Bot

私人 Discord 音樂 Bot，運行於 Oracle Cloud 免費方案，支援 YouTube 播放、互動式控制面板、KTV 歌詞、統計系統等進階功能。

---

## 功能特色

| 分類 | 功能 |
|------|------|
| **播放** | YouTube 連結 / 關鍵字搜尋 / Playlist 批次加入 |
| **隊列** | 互動式分頁 Queue 面板、移動/移除/隨機排列 |
| **控制** | 2 排 10 鍵控制面板（含音量 Modal） |
| **音訊** | 5 種濾鏡：bass / nightcore / slow / 8D |
| **歌詞** | 翻頁式歌詞（Genius API） |
| **KTV** | 隨音樂同步滾動的 LRC 歌詞 |
| **統計** | 播放記錄、排行榜、個人統計、年度回顧 |
| **評分** | 每首歌播完後彈出 1–5 星評分 |
| **播放清單** | 儲存 / 載入 / 刪除 SQLite 播放清單 |
| **頻道** | 專屬音樂頻道（持續 Now Playing 面板） |
| **自動電台** | 隊列空時自動搜尋相關歌曲 |
| **個性** | 節日裝飾、類型感知回應、成就解鎖公告 |

---

## 快速開始

### 前提條件

- Python 3.12+
- FFmpeg（`sudo apt install ffmpeg`）
- Node.js（`sudo apt install nodejs`，yt-dlp 解析用）
- Discord Bot Token（[Discord Developer Portal](https://discord.com/developers/applications)）

### 安裝

```bash
git clone <your-repo-url> discord-music-bot
cd discord-music-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 設定

```bash
cp .env.example .env
nano .env   # 必填：DISCORD_TOKEN、GUILD_ID
```

| 變數 | 說明 | 必填 |
|------|------|------|
| `DISCORD_TOKEN` | Bot Token | ✅ |
| `GUILD_ID` | 伺服器 ID（即時同步指令用） | 建議 |
| `GENIUS_API_KEY` | Genius API Token（/lyrics 用） | ❌ |
| `COOKIES_FILE` | YouTube cookies 路徑（繞過限流） | ❌ |
| `FFMPEG_PATH` | FFmpeg 路徑（預設 `ffmpeg`） | ❌ |
| `AUTO_DISCONNECT_SECONDS` | 無人自動離開秒數（預設 300） | ❌ |
| `MAX_QUEUE_SIZE` | 最大隊列數（預設 100） | ❌ |
| `MAX_PLAYLIST_SONGS` | Playlist 匯入上限（預設 50） | ❌ |

### 啟動

```bash
python bot.py
```

---

## 部署到 Oracle Cloud

```bash
# 從本機（WSL）一鍵部署並重啟
bash scripts/deploy.sh
```

更新 yt-dlp（在伺服器上執行，建議每週一次）：

```bash
bash ~/discord-music-bot/scripts/update.sh
```

---

## 指令一覽

### 播放控制
| 指令 | 說明 | 權限 |
|------|------|------|
| `/play <query> [message]` | 播放歌曲（URL / 關鍵字 / Playlist） | 所有人 |
| `/replay` | 重播上一首 | 所有人 |
| `/skip` | 跳過目前歌曲 | 點歌者 / DJ |
| `/voteskip` | 發起投票跳歌 | 所有人 |
| `/pause` / `/resume` | 暫停 / 繼續 | DJ |
| `/stop` | 停止並清空 Queue | DJ |
| `/join` / `/leave` | 加入 / 離開語音頻道 | 所有人 / DJ |

### 隊列管理
| 指令 | 說明 | 權限 |
|------|------|------|
| `/queue` | 互動式 Queue 面板 | 所有人 |
| `/nowplaying` | Now Playing 控制台 | 所有人 |
| `/remove <pos>` | 移除指定歌曲 | 點歌者 / DJ |
| `/move <from> <to>` | 調整歌曲順序 | DJ |
| `/shuffle` | 隨機排列 Queue | DJ |
| `/loop [off\|song\|queue]` | 切換循環模式 | 所有人 |

### 音訊設定
| 指令 | 說明 | 權限 |
|------|------|------|
| `/volume <1-200>` | 調整音量 | 所有人 |
| `/filter <effect>` | 套用音訊濾鏡 | 所有人 |
| `/autoradio` | 切換自動電台 | 所有人 |
| `/sfx` | 切換入場音效 | DJ |

### 歌詞與統計
| 指令 | 說明 |
|------|------|
| `/lyrics` | 翻頁式歌詞（Genius） |
| `/karaoke` | KTV 即時滾動歌詞 |
| `/songinfo` | 歌曲詳細資訊與評分 |
| `/stats` | 播放排行榜 |
| `/mystats` | 個人播放統計 |
| `/history` | 最近播放記錄 |
| `/yearwrap [year]` | 年度音樂回顧 |
| `/playlist <save\|load\|list\|delete>` | 播放清單管理 |

### 伺服器設定
| 指令 | 說明 | 權限 |
|------|------|------|
| `/setchannel` | 設定專屬音樂頻道 | 管理員 |
| `/clearchannel` | 清除音樂頻道設定 | 管理員 |
| `/ping` | Bot 狀態與 yt-dlp 診斷 | 管理員 |

---

## 使用說明書

互動式使用手冊（含指令說明、面板操作、故障排除）：

→ 在瀏覽器開啟 [`docs/index.html`](docs/index.html)；需要 PDF 可在瀏覽器按 `Ctrl+P` 列印。

---

## 常見問題

### `/play` 找不到歌曲

Oracle Cloud 的 IP 會被 YouTube 封鎖。解決方式：

```bash
# 1. 更新 yt-dlp（最優先）
bash ~/discord-music-bot/scripts/update.sh

# 2. 確認 Node.js 已安裝（yt-dlp 解析需要）
node --version

# 3. 在 Discord 輸入 /ping 查看診斷結果
```

若 `/ping` 仍顯示搜尋失敗，請設定 YouTube Cookies：用瀏覽器擴充套件
「Get cookies.txt LOCALLY」**以無痕視窗**匯出 `cookies.txt`，並在 `.env`
設定 `COOKIES_FILE` 指向它。

### Bot 有聲音但串流中斷

確認 FFmpeg 版本：`ffmpeg -version`（建議 6.x 以上）

### Slash 指令不出現

填寫 `GUILD_ID` 後重啟 Bot，指令即時生效。

---

## 專案結構

```
discord-music-bot/
├── bot.py                          # 主程式入口
├── config.py                       # 環境變數載入
├── requirements.txt
├── .env.example
├── commands/
│   └── music.py                    # 所有 Slash Commands + Cog
├── services/
│   ├── music_player.py             # 播放引擎、Queue、迴圈
│   ├── youtube_service.py          # yt-dlp 封裝（搜尋 / 串流 / Playlist）
│   ├── stats_service.py            # SQLite 統計、評分、播放清單
│   ├── filter_service.py           # 音訊濾鏡
│   ├── lyrics_service.py           # Genius 歌詞
│   ├── lyrics_karaoke_service.py   # LRC 同步歌詞（KTV）
│   ├── personality_service.py      # 個性回應、節日、成就
│   └── playlist_service.py         # 播放清單 JSON 序列化
├── models/
│   ├── song.py
│   ├── guild_state.py
│   └── loop_mode.py
├── ui/
│   ├── control_panel_view.py       # 2 排 10 鍵控制面板
│   ├── queue_panel_view.py         # 互動式 Queue 面板
│   ├── search_select_view.py       # 搜尋結果選單
│   ├── lyrics_paged_view.py        # 翻頁式歌詞
│   ├── karaoke_view.py             # KTV 模式關閉按鈕
│   ├── rating_view.py              # 評分按鈕
│   └── vote_skip_view.py           # 投票跳歌
├── utils/
│   ├── logger.py
│   └── permissions.py
├── docs/
│   └── index.html                  # 互動式使用手冊（可列印為 PDF）
├── data/                           # SQLite DB（自動建立，不入 git）
├── sounds/                         # 入場音效 join.mp3 / leave.mp3（選用）
└── scripts/
    ├── deploy.sh                   # 本機 → 伺服器一鍵部署
    ├── update.sh                   # 伺服器上快速更新 yt-dlp
    └── server-setup.sh             # 伺服器初始化（首次使用）
```
