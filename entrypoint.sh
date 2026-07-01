#!/bin/bash
set -e

CONFIG_FILE="${CONFIG_FILE:-/app/config.yaml}"

# 从环境变量生成 config.yaml
cat > "$CONFIG_FILE" << JSONEOF
{
  "device_url": "${DEVICE_URL:-http://10.10.121.15:18081/v1/chat/completions}",
  "api_key": "${API_KEY:-}",
  "model": "${MODEL:-mock-agent-intent-model}",
  "timeout_seconds": ${TIMEOUT_SECONDS:-30},
  "default_stream": ${DEFAULT_STREAM:-false},
  "mock_workspace": "mock_workspace",
  "headers": {},
  "origin_url": "${ORIGIN_URL:-}",
  "origin_api_key": "${ORIGIN_API_KEY:-}",
  "origin_headers": {}
}
JSONEOF

echo "Generated config.yaml:"
cat "$CONFIG_FILE"

# 修复 named volume 挂载目录的权限（volume 初始属主为 root，appuser 无法写入）
for dir in /app/reports /app/mock_workspace; do
  mkdir -p "$dir"
  chown appuser:appuser "$dir"
done

# 运行 Django 数据库迁移（幂等操作）
python manage.py migrate --noinput

# 切换到 appuser 执行应用（使用 Python 内置 os.setuid，不依赖 runuser/gosu）
exec python -c "
import os, pwd, sys
pw = pwd.getpwnam('appuser')
os.setgid(pw.pw_gid)
os.setuid(pw.pw_uid)
os.execvp(sys.argv[1], sys.argv[1:])
" "$@"
