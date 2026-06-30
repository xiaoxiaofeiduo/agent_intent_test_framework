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

# 运行 Django 数据库迁移（幂等操作）
python manage.py migrate --noinput

# 执行传入的命令
exec "$@"
