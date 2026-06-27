# 住宅 IP 代理設定（DS220j ＋ Oracle）

目的：Oracle 是機房 IP，YouTube 會跳「Sign in to confirm you're not a bot」。
把 YouTube 流量繞經家裡 DS220j 的住宅 IP 出去，就能避開這個封鎖。

```
Discord ⇄ Oracle bot ──http_proxy──▶ 127.0.0.1:8888
                                          │  反向 SSH 通道（DS220j 主動連 Oracle）
                                DS220j ◀──┘  tinyproxy ──▶ YouTube（你家住宅 IP）
```

- DS220j **主動**連 Oracle，所以**不需要**在家裡路由器開 port，CGNAT／浮動 IP 都能用。
- 走你家的只有音訊流（~16–32 KB/s 一首），對家用網路無壓力。
- bot 對 Discord 的連線仍走 Oracle 直出，只有 YouTube 繞道。

---

## A. DS220j 端設定

### A-1. 開啟 SSH
DSM → 控制台 → 終端機與 SNMP → 勾「啟動 SSH 功能」（埠預設 22）。

### A-2. 安裝 Entware（取得 tinyproxy 與 autossh）
DS220j（Realtek RTD1296 / aarch64）原生沒有套件管理器，用 Entware 補上。
SSH 進 NAS（`ssh 你的帳號@NAS內網IP`），切 root：`sudo -i`，然後依
Entware 官方 wiki 的 **aarch64** 步驟安裝（建立 `/opt`、跑 generic installer）。

裝好後：
```bash
opkg update
opkg install tinyproxy autossh
```

### A-3. 設定 tinyproxy
編輯 `/opt/etc/tinyproxy/tinyproxy.conf`，確認/修改這幾行：
```
Port 8888
Listen 127.0.0.1          # 只聽本機，靠反向通道進來
Allow 127.0.0.1
ConnectPort 443           # 允許 https CONNECT（googlevideo 是 https）
ConnectPort 563
```
啟動並設開機自動：
```bash
/opt/etc/init.d/S36tinyproxy start
```
本機測試（應回你家的對外 IP）：
```bash
curl -x http://127.0.0.1:8888 https://api.ipify.org ; echo
```

### A-4. 建立到 Oracle 的免密金鑰
在 DS220j（root）：
```bash
ssh-keygen -t ed25519 -f /opt/etc/tunnel_key -N ""
cat /opt/etc/tunnel_key.pub
```
把印出來的公鑰加到 **Oracle** 的 `~/.ssh/authorized_keys`（ubuntu 帳號）。

### A-5. 反向通道（autossh）＋ 開機自動執行
測試指令（把 `ORACLE_IP` 換成你的 Oracle 公網 IP）：
```bash
autossh -M 0 -f -N \
  -o "ServerAliveInterval=30" -o "ServerAliveCountInterval=3" \
  -o "ExitOnForwardFailure=yes" -o "StrictHostKeyChecking=accept-new" \
  -i /opt/etc/tunnel_key \
  -R 127.0.0.1:8888:127.0.0.1:8888 \
  ubuntu@ORACLE_IP
```
這條會讓 **Oracle 的 127.0.0.1:8888** 通到 **DS220j 的 tinyproxy**。

設成開機自動：DSM → 控制台 → 任務排程器 → 新增 → 觸發的任務 → 開機 →
使用者選 `root`，把上面那條 autossh 指令貼進「執行指令」。

---

## B. Oracle 端設定

### B-1. 驗證通道與代理
通道起來後，在 Oracle 上測試（應回你**家裡**的 IP，不是 Oracle 的）：
```bash
curl -x http://127.0.0.1:8888 https://api.ipify.org ; echo
```

### B-2. 啟用 proxy
編輯 `~/discord-music-bot/.env`，加入：
```
YT_PROXY=http://127.0.0.1:8888
```
（cookie 同時也建議照 .env.example 的「無痕匯出」方式重做一份。）

### B-3. 重啟 bot
```bash
sudo systemctl restart music-bot
journalctl -u music-bot -n 30 --no-pager
```
啟動 log 應出現：
```
YouTube proxy: http://127.0.0.1:8888
```
然後 `/play` 一首歌驗證不再跳 Sign in。

---

## 疑難排解

| 症狀 | 檢查 |
|------|------|
| `curl -x ...` 在 Oracle 失敗 | 通道沒起來。在 DS220j 重跑 autossh，看 Oracle `ss -tlnp \| grep 8888` 有沒有在聽 |
| 回的是 Oracle 的 IP | tinyproxy 沒生效或通道接錯，確認 DS220j 上 `curl -x` 正常 |
| 仍跳 Sign in | log 的 `YouTube proxy:` 是不是 `(none)`？確認 .env 有設且已重啟 |
| 播放 403 / 秒結束 | yt-dlp 與 ffmpeg 沒走同一 IP；確認用本專案改過的程式碼（ffmpeg 會自動帶 `-http_proxy`） |
| 通道常斷 | autossh 的 ServerAlive 參數已內建重連；確認任務排程器的開機任務存在 |
