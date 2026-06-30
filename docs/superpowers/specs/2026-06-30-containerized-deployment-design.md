# 容器化部署设计规格

**日期:** 2026-06-30
**状态:** 已确认

## 背景

项目当前通过 systemd service 在裸机上部署，开发时需要手动配置 Python venv 和依赖。需要新增统一的容器化方案，覆盖本地开发、CI/CD 集成和生产部署场景。

## 设计目标

1. **统一方案**：一份 Dockerfile + docker-compose.yml 覆盖开发/测试/生产
2. **不修改现有代码**：entrypoint.sh 在容器启动时从环境变量生成 `config.yaml`，所有 Python 代码零改动
3. **简洁实用**：内部测试工具，使用 Django runserver，不引入不必要的复杂度
4. **安全基线**：非 root 运行、多阶段构建、健康检查

## 新增文件

```
项目根目录/
├── Dockerfile              # 多阶段构建（builder → runtime）
├── .dockerignore           # 排除 .venv、__pycache__、.git 等
├── docker-compose.yml      # 开发（dev profile）和生产（prod profile）编排
├── .env.example            # 环境变量模板，供用户复制为 .env
└── entrypoint.sh           # 容器启动脚本
```

共 5 个新文件，零现有文件修改。

## Dockerfile

- **Builder 阶段**：python:3.14-slim，安装 pip 依赖到 `--user` 路径
- **Runtime 阶段**：python:3.14-slim，复制依赖和源码，创建 `appuser` 非 root 用户
- **HEALTHCHECK**：每 30 秒探测 `http://localhost:18081/`
- **ENTRYPOINT** 指向 `entrypoint.sh`，**CMD** 为 `python manage.py runserver 0.0.0.0:18081`

## docker-compose.yml

使用 Docker Compose profiles 区分场景：

| Profile | 行为 | 用途 |
|---------|------|------|
| `prod` | 构建镜像、数据卷持久化、`restart: unless-stopped` | 服务器部署、CI |
| `dev` | 挂载源码（支持热重载）、`restart: "no"` | 本地开发 |

环境变量 `DEVICE_URL`、`API_KEY`、`MODEL` 等通过 `${VAR:-default}` 方式映射，用户在 `.env` 文件中配置。

数据卷：`db.sqlite3`、`reports/`、`mock_workspace/` 通过 named volumes 持久化。

## .dockerignore

排除以下内容：
- `.venv/`、`__pycache__/`、`*.py[cod]`
- `.git/`、`.github/`
- `reports/`、`mock_workspace/`、`db.sqlite3`（运行时数据）
- `logs/`、`.DS_Store`
- `.claude/`、`docs/`
- `*.md`（除 requirements 相关）

## entrypoint.sh

```bash
#!/bin/bash
set -e

# 从环境变量生成 config.yaml（运行时，不修改源码中的 config.example.yaml）
CONFIG_FILE="${CONFIG_FILE:-/app/config.yaml}"
cat > "$CONFIG_FILE" << EOF
{
  "device_url": "${DEVICE_URL:-http://10.10.121.15:18081/v1/chat/completions}",
  "api_key": "${API_KEY:-}",
  "model": "${MODEL:-mock-agent-intent-model}",
  "timeout_seconds": ${TIMEOUT_SECONDS:-30},
  "default_stream": ${DEFAULT_STREAM:-false}
}
EOF

# 运行 Django 数据库迁移（幂等）
python manage.py migrate --noinput

# 执行传入的命令
exec "$@"
```

这样无需修改任何现有 Python 代码，runner.py 读取 `config.yaml` 的逻辑保持不变。`config.example.yaml` 作为本地开发的模板保留不动。

## 配置注入

config.yaml 的配置项通过 docker-compose environment 覆盖：

| config.yaml 字段 | 环境变量 | 默认值 |
|------------------|---------|--------|
| `device_url` | `DEVICE_URL` | `http://10.10.121.15:18081/v1/chat/completions` |
| `api_key` | `API_KEY` | 空 |
| `model` | `MODEL` | `mock-agent-intent-model` |
| `timeout_seconds` | `TIMEOUT_SECONDS` | `30` |
| `default_stream` | `DEFAULT_STREAM` | `false` |

## 使用方式

```bash
# 准备环境变量
cp .env.example .env
# 编辑 .env，填入实际的 DEVICE_URL 和 API_KEY

# 本地开发
docker compose --profile dev up

# 生产部署
docker compose --profile prod up -d

# 查看状态
docker compose ps
docker compose logs -f app
```
