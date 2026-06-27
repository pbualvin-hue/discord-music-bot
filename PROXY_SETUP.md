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
編輯 `/opt/etc/tinyproxy.conf`（注意是這個路徑，不是子目錄），確認/修改：
```
Port 8888
Listen 127.0.0.1          # 只聽本機，靠反向通道進來
Allow 127.0.0.1

# 安全：擋掉對私有/內網位址的請求（見下方說明）
Filter "/opt/etc/tinyproxy-filter.txt"
FilterExtended On
FilterDefaultDeny No

# ⚠️ 必須以 root 跑 —— 把 User / Group 兩行「註解掉」：
#User nobody
#Group nobody
```
為什麼要 root：tinyproxy 降權成 nobody 後，`getaddrinfo()` 會因為載不到
NSS 模組而 DNS 解析失敗，症狀是連任何網站都回
「500 Unable to connect / Could not retrieve address info」。
因為只 `Listen 127.0.0.1`（外部進不來），用 root 跑沒有安全疑慮。

**不要**加任何 `Bind` 指令：`Bind` 是指定「對外連線的來源位址」，設了反而連不出去。
**不需要**加 `ConnectPort`：預設沒啟用 = 不限制 CONNECT 埠，https 本來就通。

**為什麼要 Filter**：反向通道把 Oracle 的 127.0.0.1:8888 接到這台 tinyproxy，
萬一 Oracle 被入侵，攻擊者可透過此 proxy 連你家**內網**（路由器後台、其他
NAS 服務）或拿你家 IP 當跳板。下面的過濾清單擋掉所有私有網段，只放行公網。
建立 `/opt/etc/tinyproxy-filter.txt`（比對目標主機，命中即拒絕）：
```
^localhost$
^127\.
^10\.
^169\.254\.
^192\.168\.
^172\.(1[6-9]|2[0-9]|3[01])\.
```
（註：此清單擋 IPv4 私有位址；若你家內網有用 IPv6 ULA，再自行補 `^\[fc`、`^\[fd`。）

啟動並設開機自動（init 腳本是 **S21tinyproxy**）：
```bash
/opt/etc/init.d/S21tinyproxy restart
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
⚠️ 這把 key 無密碼保護，**務必加前綴限制權限**——只能開那一條反向通道、
不能拿 shell、不能亂 forward。在 Oracle 的 authorized_keys 裡這樣寫（公鑰本體
接在後面）：
```
command="echo tunnel-only;exit",restrict,port-forwarding,permitlisten="127.0.0.1:8888" ssh-ed25519 AAAA...（NAS 公鑰）... nas-tunnel
```
- `restrict`：關掉 pty／agent／X11／所有 forward（最小權限）。
- `port-forwarding`：把 forward 能力加回來（反向通道需要）。
- `permitlisten="127.0.0.1:8888"`：限定只能監聽這個位址埠，別的都不准。
- `command="echo tunnel-only;exit"`：強制指令——只有在對方「要求執行命令/shell」
  時才會觸發並擋下；通道用 `-N`（不要求命令）所以正常運作不受影響。
  這樣即使 key 外洩，對方連 `ssh ... 'rm -rf'` 這種遠端執行都被擋。

這樣即使 NAS 上的 key 外洩，對方也只能開這條到 tinyproxy 的通道，拿不到
Oracle shell、也不能跑任何命令。

### A-5. 反向通道（autossh）＋ 開機自動執行

先停用 Entware 內建會自動啟動的 autossh init（用「改名」而非 `chmod -x`，
因為 Synology 的 ACL 會讓 `chmod -x` 失效）：
```bash
mv /opt/etc/init.d/S41autossh /opt/etc/init.d/disabled_S41autossh
```

建立 `/opt/etc/start_tunnel.sh`（把 `<ORACLE_IP>` 換成你的 Oracle 公網 IP）：
```sh
#!/bin/sh
export AUTOSSH_GATETIME=0
export AUTOSSH_PATH=/opt/bin/ssh
/opt/sbin/autossh -M 0 -f -N \
  -o "ServerAliveInterval=30" -o "ServerAliveCountMax=3" \
  -o "ExitOnForwardFailure=yes" -o "StrictHostKeyChecking=accept-new" \
  -i /opt/etc/tunnel_key -R 127.0.0.1:8888:127.0.0.1:8888 ubuntu@<ORACLE_IP>
```
重點：
- `AUTOSSH_PATH=/opt/bin/ssh`：指定 Entware 的 ssh，否則 autossh 找不到。
- `AUTOSSH_GATETIME=0`：開機即連、斷線立即重連，不要求先穩定一段時間。
- `ServerAliveCountMax`（**不是** `ServerAliveCountInterval`，後者不存在會讓
  ssh 直接以 255 退出）。

賦予執行權並測試：
```bash
chmod +x /opt/etc/start_tunnel.sh
/opt/etc/start_tunnel.sh
```
這條會讓 **Oracle 的 127.0.0.1:8888** 通到 **DS220j 的 tinyproxy**。

設成開機自動：DSM → 控制台 → 任務排程器 → 新增 → 觸發的任務 → 開機 →
使用者選 `root`，「執行指令」填：
```
/opt/etc/start_tunnel.sh
```

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

## C. 健康檢查與告警（強烈建議）

通道是整套的單點故障，且斷線時不會主動通知（使用者點歌才發現）。在 **Oracle**
放一個小腳本，定期測 proxy，只在「斷掉」與「恢復」的瞬間發 Discord 通知（不洗版）。

先在 Discord 任一頻道建立一個 Webhook（頻道設定 → 整合 → Webhook → 複製網址）。

建立 `/usr/local/bin/check_yt_proxy.sh`：
```sh
#!/bin/sh
WEBHOOK="https://discord.com/api/webhooks/xxx/yyy"   # 換成你的 webhook
STATE=/tmp/yt_proxy_state
notify() {
  curl -fsS -m 10 -H "Content-Type: application/json" \
    -d "{\"content\":\"$1\"}" "$WEBHOOK" >/dev/null 2>&1
}
if curl -fsS -m 15 -x http://127.0.0.1:8888 https://api.ipify.org >/dev/null 2>&1; then
  [ "$(cat $STATE 2>/dev/null)" = "down" ] && notify "✅ YT proxy 通道已恢復。"
  echo up > "$STATE"
else
  [ "$(cat $STATE 2>/dev/null)" != "down" ] && \
    notify "⚠️ YT proxy 通道異常：Oracle 無法經 127.0.0.1:8888 連外，請查 DS220j 的 tunnel / tinyproxy。"
  echo down > "$STATE"
fi
```
設定權限與 cron（每 5 分鐘檢查一次）：
```bash
sudo chmod +x /usr/local/bin/check_yt_proxy.sh
( crontab -l 2>/dev/null; echo "*/5 * * * * /usr/local/bin/check_yt_proxy.sh" ) | crontab -
```

---

## 播放平順度（下載快取，自動）

繞 proxy 後音訊多一段網路路徑，邊串邊播會偶發頓挫（抖動觸發 ffmpeg 重連）。
因此**只要設了 `YT_PROXY`**，非直播的 YouTube/Spotify 歌曲會自動改為
「先透過 proxy 下載到本機，再從本機檔播放」——播放期間不碰網路，根除頓挫。

- 行為自動，無需額外設定；沒設 `YT_PROXY` 時維持原本邊串邊播。
- 快取在系統暫存目錄 `discord-music-bot-cache/`，播完即刪，bot 啟動時也會清空。
- 每首開頭多幾秒下載；下一首會在當前歌播放時預先下載，通常感覺不到延遲。
- 直播（live）/ 電台 / B 站維持串流，不下載。

---

## 疑難排解

| 症狀 | 檢查 |
|------|------|
| `curl -x ...` 在 Oracle 失敗 | 通道沒起來。在 DS220j 重跑 autossh，看 Oracle `ss -tlnp \| grep 8888` 有沒有在聽 |
| 回的是 Oracle 的 IP | tinyproxy 沒生效或通道接錯，確認 DS220j 上 `curl -x` 正常 |
| 仍跳 Sign in | log 的 `YouTube proxy:` 是不是 `(none)`？確認 .env 有設且已重啟 |
| 播放 403 / 秒結束 | yt-dlp 與 ffmpeg 沒走同一 IP；確認用本專案改過的程式碼（ffmpeg 會自動帶 `-http_proxy`） |
| curl 回 `500 / Could not retrieve address info` | tinyproxy 降權成 nobody 導致 DNS 失敗；把 `User nobody` / `Group nobody` 註解掉，以 root 跑（見 A-3） |
| autossh 開機沒連上 / 立刻退出 | 確認用 `ServerAliveCountMax`（非 ...Interval）、有 `export AUTOSSH_PATH=/opt/bin/ssh`，且內建 `S41autossh` 已改名停用 |
| 通道常斷 | `AUTOSSH_GATETIME=0` + ServerAlive 參數會自動重連；確認任務排程器的開機任務指向 start_tunnel.sh |
