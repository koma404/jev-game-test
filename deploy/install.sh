#!/usr/bin/env bash
# 《十二人》一键部署到 Linux VPS（Ubuntu 20.04+ / Debian 11+）。
#
#   首次安装：sudo bash deploy/install.sh
#   更新代码：sudo bash deploy/install.sh --update     （保留 key、口令等设置，只换代码并重启）
#
# 做的事：装 python3（若没有）→ 代码复制到 /opt/twelve → 设置写到 /etc/twelve/env（权限 600）
#         → 注册 systemd 服务 twelve（开机自启、崩溃自动重启）→ 放行防火墙端口。
# 只用 Python 标准库，不装任何第三方包。
set -euo pipefail

APP_DIR=/opt/twelve
ENV_DIR=/etc/twelve
ENV_FILE=$ENV_DIR/env
DATA_DIR=/var/lib/twelve
UNIT=/etc/systemd/system/twelve.service
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-install}"

say() { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m!! %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo bash deploy/install.sh"
[ -f "$SRC_DIR/twelve/server.py" ] || die "在 $SRC_DIR 下找不到 twelve/server.py，请在项目目录里运行"

say "检查 Python"
if ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y && apt-get install -y python3
  else
    die "没有 python3，也没有 apt-get。请先手动安装 Python 3.8 以上版本"
  fi
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || die "Python 版本太旧（$(python3 --version)），需要 3.8 以上"
echo "$(python3 --version)"

say "复制代码到 $APP_DIR"
id twelve >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin twelve
mkdir -p "$APP_DIR" "$DATA_DIR"
rm -rf "$APP_DIR/game" "$APP_DIR/twelve"
cp -r "$SRC_DIR/game" "$SRC_DIR/twelve" "$APP_DIR/"
rm -rf "$APP_DIR/twelve/logs" "$APP_DIR/game/logs"
find "$APP_DIR" -name '__pycache__' -prune -exec rm -rf {} +
chown -R root:root "$APP_DIR"
chown -R twelve:twelve "$DATA_DIR"

if [ "$MODE" != "--update" ] || [ ! -f "$ENV_FILE" ]; then
  say "设置（会保存在 $ENV_FILE，只有 root 能读）"
  read -r -s -p "OpenRouter key（输入时不显示）: " KEY; echo
  [ -n "$KEY" ] || die "key 不能为空"
  read -r -p "访问口令（玩家要输入，或放在链接 ?code= 里）: " CODE
  [ -n "$CODE" ] || die "访问口令不能为空：挂在公网上不设口令，谁都能消耗你的额度"
  read -r -p "管理口令（打开 /admin?key=管理口令 看统计、下载日志；留空则不开）: " ADMIN
  read -r -p "每日 Jev 花费上限，美元 [0.5]: " BUDGET; BUDGET="${BUDGET:-0.5}"
  read -r -p "端口 [80]: " PORT; PORT="${PORT:-80}"
  mkdir -p "$ENV_DIR"
  umask 077
  cat > "$ENV_FILE" <<EOF
TYPESAFE_API_KEY=$KEY
TYPESAFE_BASE_URL=https://openrouter.ai/api/v1/systemone
TYPESAFE_MODEL=jev-latest
TYPESAFE_PROXY=direct
TWELVE_ACCESS_CODE=$CODE
TWELVE_ADMIN_CODE=$ADMIN
DAILY_BUDGET=$BUDGET
PORT=$PORT
TWELVE_QUIET=1
EOF
  chmod 600 "$ENV_FILE"
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

say "注册 systemd 服务 twelve"
cat > "$UNIT" <<'EOF'
[Unit]
Description=Twelve (Jev jury demo)
After=network-online.target
Wants=network-online.target

[Service]
User=twelve
WorkingDirectory=/opt/twelve
EnvironmentFile=/etc/twelve/env
ExecStart=/usr/bin/env python3 -m twelve.server --jev real --host 0.0.0.0 --port ${PORT} --log-dir /var/lib/twelve --daily-budget ${DAILY_BUDGET} --max-courts-per-ip 6
Restart=always
RestartSec=3
# 普通用户也能监听 80 端口
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/twelve

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable twelve >/dev/null 2>&1 || true
systemctl restart twelve

say "放行防火墙端口 $PORT"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT"/tcp
else
  echo "（没有启用 ufw，跳过；若商家后台有「安全组/防火墙」，记得在那里放行 TCP $PORT）"
fi

say "检查服务"
sleep 2
if curl -s -o /dev/null -m 5 "http://127.0.0.1:$PORT/"; then
  echo "服务已在本机 $PORT 端口响应。"
else
  echo "服务没有响应，看日志：journalctl -u twelve -n 50 --no-pager"
fi
IP="$(curl -s -m 5 https://api.ipify.org || true)"
[ -n "$IP" ] || IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SUFFIX=""; [ "$PORT" = "80" ] || SUFFIX=":$PORT"
cat <<EOF

完成。把这个链接发给朋友：
    http://$IP$SUFFIX/?code=$TWELVE_ACCESS_CODE

常用命令：
    http://$IP$SUFFIX/admin?key=管理口令  # 运行统计、下载日志（设了管理口令才有）
    systemctl status twelve              # 看状态
    journalctl -u twelve -f              # 看实时日志（Ctrl+C 退出）
    sudo bash deploy/install.sh --update # 拉取新代码后更新
    sudo nano /etc/twelve/env && sudo systemctl restart twelve   # 改 key / 口令 / 上限
EOF
