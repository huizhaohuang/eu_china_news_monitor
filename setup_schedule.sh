#!/bin/bash
# 安装/卸载 launchd 定时任务:
#   com.chinaeu.monitor      — 监测台常驻 (登录自启,崩溃自动重启, http://localhost:8501)
#   com.chinaeu.digest-0700  — 每天 07:00 生成早报并弹通知+打开浏览器 (时间窗 13h,覆盖前晚 18 点起)
#   com.chinaeu.digest-1200  — 每天 12:00 生成午报 (时间窗 6h)
# 用法:  bash setup_schedule.sh               # 只装 07:00/12:00 两个定时推送
#        bash setup_schedule.sh with-monitor  # 额外安装监测台常驻(登录自启+保活)
#        bash setup_schedule.sh remove        # 全部卸载
# 注意:launchd 用的是绝对路径 —— 移动本仓库目录后需重新运行本脚本。
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
PY="$REPO/.venv/bin/python"
STREAMLIT="$REPO/.venv/bin/streamlit"
AGENTS="$HOME/Library/LaunchAgents"
LOGDIR="$HOME/Library/Logs"
mkdir -p "$AGENTS" "$LOGDIR"

unload() { launchctl unload "$AGENTS/$1.plist" 2>/dev/null || true; }

if [[ "${1:-}" == "remove" ]]; then
  for l in com.chinaeu.monitor com.chinaeu.digest-0700 com.chinaeu.digest-1200; do
    unload "$l"; rm -f "$AGENTS/$l.plist"
  done
  echo "已卸载全部定时任务"
  exit 0
fi

[[ -x "$PY" ]] || { echo "错误: 找不到 $PY —— 先运行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

write_digest_plist() {  # $1=label $2=hour $3=hours-window
cat > "$AGENTS/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$REPO/digest_push.py</string>
    <string>--hours</string><string>$3</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$2</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StandardOutPath</key><string>$LOGDIR/chinaeu-digest.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/chinaeu-digest.log</string>
</dict></plist>
EOF
}

LABELS=(com.chinaeu.digest-0700 com.chinaeu.digest-1200)

# 监测台常驻(可选,with-monitor 时安装)
if [[ "${1:-}" == "with-monitor" ]]; then
LABELS+=(com.chinaeu.monitor)
cat > "$AGENTS/com.chinaeu.monitor.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.chinaeu.monitor</string>
  <key>ProgramArguments</key><array>
    <string>$STREAMLIT</string><string>run</string>
    <string>$REPO/china_europe_monitor.py</string>
    <string>--server.headless</string><string>true</string>
    <string>--server.port</string><string>8501</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StandardOutPath</key><string>$LOGDIR/chinaeu-monitor.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/chinaeu-monitor.log</string>
</dict></plist>
EOF
fi

write_digest_plist com.chinaeu.digest-0700 7 13
write_digest_plist com.chinaeu.digest-1200 12 6

for l in "${LABELS[@]}"; do
  unload "$l"
  launchctl load -w "$AGENTS/$l.plist"
done
echo "已安装: ${LABELS[*]}"
launchctl list | grep chinaeu || true
