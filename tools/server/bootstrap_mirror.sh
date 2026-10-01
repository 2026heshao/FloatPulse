#!/usr/bin/env bash
# FloatPulse 镜像站引导脚本（在腾讯云轻量服务器上以 root 执行一次）
#
# 做四件事：
#   1. 建 /var/www/floatpulse 站点目录
#   2. 装 /usr/local/bin/floatpulse-sync.sh（从 GitHub latest Release 拉安装包+插件包+市场索引）
#   3. 装 systemd 定时器（每 30 分钟自动同步一次）并立即跑一次
#   4. 用服务器上已有的 caddy 起一个静态文件服务（:8080），不碰现有 80 端口站点
#
# 产物 URL（同步完成后）：
#   http://49.233.85.219:8080/marketplace/index.json
#   http://49.233.85.219:8080/FloatPulse-v4.7.0-win64.zip
#   http://49.233.85.219:8080/plugin-assets/kb-search.fpplug
set -euo pipefail

WEBROOT=/var/www/floatpulse
SYNC_BIN=/usr/local/bin/floatpulse-sync.sh
REPO=2026heshao/FloatPulse

echo "== 1/4 建站点目录 =="
mkdir -p "$WEBROOT/plugin-assets" "$WEBROOT/marketplace"

echo "== 2/4 写同步脚本 =="
cat > "$SYNC_BIN" <<'EOS'
#!/usr/bin/env bash
# 从 GitHub latest Release 同步 FloatPulse 发行物到本地站点目录（原子替换）
set -uo pipefail
WEBROOT=/var/www/floatpulse
REPO=2026heshao/FloatPulse
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
log(){ echo "[$(date "+%F %T")] $*"; }

API="https://api.github.com/repos/$REPO"
# latest release 元数据（tag + assets）
if ! curl -fsS --connect-timeout 10 --max-time 60 \
     -H "User-Agent: floatpulse-mirror/1.0" \
     "$API/releases/latest" -o "$TMP/rel.json"; then
  log "FAIL 拉取 release 元数据失败"; exit 1
fi
TAG=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["tag_name"])' "$TMP/rel.json")
log "同步 $TAG 开始"

# 主程序 zip + setup.exe + 全部 .fpplug 附件
mkdir -p "$TMP/out"
python3 - "$TMP/rel.json" "$TMP/out" <<'EOP'
import json, sys, urllib.request, os
rel, out = json.load(open(sys.argv[1])), sys.argv[2]
base = "https://api.github.com/repos/2026heshao/FloatPulse/releases/assets/"
for a in rel.get("assets", []):
    name, aid = a["name"], a["id"]
    if not (name.endswith(".zip") or name.endswith(".exe") or name.endswith(".fpplug")):
        continue
    dst = os.path.join(out, name)
    req = urllib.request.Request(base + str(aid), headers={
        "User-Agent": "floatpulse-mirror/1.0",
        "Accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    print("ok", name, os.path.getsize(dst))
EOP
[ $? -ne 0 ] && { log "FAIL 附件下载失败"; exit 1; }

# 市场索引（仓库内 marketplace/index.json，经 contents 接口取 raw）
if ! curl -fsS --connect-timeout 10 --max-time 60 \
     -H "User-Agent: floatpulse-mirror/1.0" \
     -H "Accept: application/vnd.github.raw+json" \
     "$API/contents/marketplace/index.json" -o "$TMP/out/index.json"; then
  log "WARN 拉取 index.json 失败（保留旧版）"
fi

# 宣传页（仓库 docs/promo-v4.html → 站点首页）
if ! curl -fsS --connect-timeout 10 --max-time 60 \
     -H "User-Agent: floatpulse-mirror/1.0" \
     -H "Accept: application/vnd.github.raw+json" \
     "$API/contents/docs/promo-v4.html" -o "$TMP/out/page.html"; then
  log "WARN 拉取宣传页失败（保留旧版）"
fi

# 原子替换：全部就绪后才动站点目录
mkdir -p "$WEBROOT/plugin-assets" "$WEBROOT/marketplace"
find "$TMP/out" -maxdepth 1 -name "*.fpplug" -exec mv {} "$WEBROOT/plugin-assets/" \;
find "$TMP/out" -maxdepth 1 \( -name "*.zip" -o -name "*.exe" \) -exec mv {} "$WEBROOT/" \;
[ -f "$TMP/out/index.json" ] && mv "$TMP/out/index.json" "$WEBROOT/marketplace/index.json"
[ -f "$TMP/out/page.html" ] && mv "$TMP/out/page.html" "$WEBROOT/index.html"
# 清掉站点里旧版本号的主程序包（只保留最新）
find "$WEBROOT" -maxdepth 1 -name "FloatPulse-*.zip" ! -name "FloatPulse-*$TAG*" -delete
find "$WEBROOT" -maxdepth 1 -name "FloatPulse-*-setup.exe" ! -name "FloatPulse-*$TAG*" -delete
log "同步 $TAG 完成"
EOS
chmod +x "$SYNC_BIN"

echo "== 3/4 装 systemd 定时器（每 30 分钟） =="
cat > /etc/systemd/system/floatpulse-sync.service <<'EOS'
[Unit]
Description=FloatPulse mirror sync from GitHub
[Service]
Type=oneshot
ExecStart=/usr/local/bin/floatpulse-sync.sh
EOS
cat > /etc/systemd/system/floatpulse-sync.timer <<'EOS'
[Unit]
Description=Sync FloatPulse mirror every 30 min
[Timer]
OnCalendar=*:0/30
Persistent=true
[Install]
WantedBy=timers.target
EOS
systemctl daemon-reload
systemctl enable --now floatpulse-sync.timer
"$SYNC_BIN"

echo "== 4/4 起静态文件服务（:8080，不碰现有 80 站点） =="
CADDY_BIN=$(command -v caddy 2>/dev/null || ls /usr/bin/caddy /usr/local/bin/caddy 2>/dev/null | head -1 || true)
if [ -n "$CADDY_BIN" ]; then
  START_LINE="$CADDY_BIN file-server --root $WEBROOT --listen :8080"
else
  echo "(未找到 caddy，改用 python3 http.server)"
  START_LINE="/usr/bin/python3 -m http.server 8080 --directory $WEBROOT"
fi
cat > /etc/systemd/system/floatpulse-web.service <<EOF
[Unit]
Description=FloatPulse mirror static server (:8080)
After=network.target
[Service]
ExecStart=$START_LINE
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now floatpulse-web
sleep 1
curl -s -o /dev/null -w "SELF-TEST http://127.0.0.1:8080/ -> HTTP %{http_code}\n" http://127.0.0.1:8080/

echo "== 完成 =="
echo "站点目录: $WEBROOT"
ls -la "$WEBROOT" "$WEBROOT/plugin-assets" "$WEBROOT/marketplace" 2>/dev/null
