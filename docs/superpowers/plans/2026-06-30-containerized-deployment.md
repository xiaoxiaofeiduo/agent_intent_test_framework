# 容器化部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为项目新增 5 个容器化部署文件（Dockerfile、docker-compose.yml、.dockerignore、.env.example、entrypoint.sh），零现有代码改动。

**Architecture:** 多阶段 Docker 构建（builder → runtime），docker-compose profiles 区分 dev/prod，entrypoint.sh 在启动时从环境变量生成 config.yaml。

**Tech Stack:** Docker, Docker Compose, Python 3.14-slim, Bash

## Global Constraints

- 零现有 Python 代码修改，所有配置通过 entrypoint.sh 从环境变量生成 config.yaml 注入
- 最终镜像基于 python:3.14-slim，非 root 用户 `appuser` 运行
- 端口 18081
- 健康检查间隔 30s，超时 5s，重试 3 次
- 开发 profile：源码挂载 + hot reload；生产 profile：named volumes + restart unless-stopped
- 环境变量通过 `.env` 文件配置，`.env.example` 提供模板（不包含真实凭据）

---

### Task 1: .dockerignore

**Files:**
- Create: `.dockerignore`

**Interfaces:**
- Consumes: nothing
- Produces: `.dockerignore` — 被 `docker build` 和 Docker Compose 构建阶段使用，排除不需要进镜像的文件

- [ ] **Step 1: 创建 `.dockerignore` 文件**

```dockerignore
# Python
__pycache__/
*.py[cod]
.Python

# Virtual environments
.venv/
venv/
env/

# VCS
.git/
.github/

# Runtime generated data
db.sqlite3
reports/
mock_workspace/
logs/

# Development files
.claude/
docs/
.DS_Store

# Documentation (except README)
*.md
!README.md
```

- [ ] **Step 2: 验证 `.dockerignore` 语法有效**

Run: `docker build --dry-run . 2>&1 || true`（或 `docker build --no-cache --target builder . 2>&1 | head -20` 检查是否读取了 dockerignore）
Expected: 无 .dockerignore 相关错误

- [ ] **Step 3: 提交**

```bash
git add .dockerignore
git commit -m "feat: add .dockerignore for containerized deployment"
```

---

### Task 2: .env.example

**Files:**
- Create: `.env.example`

**Interfaces:**
- Consumes: nothing
- Produces: `.env.example` — 用户复制为 `.env` 后填入真实值，被 docker-compose.yml 通过 `${VAR:-default}` 读取

- [ ] **Step 1: 创建 `.env.example` 文件**

```bash
# 保护设备地址
DEVICE_URL=http://10.10.121.15:18081/v1/chat/completions

# 保护设备 API Key（如需要）
API_KEY=

# 模型名称
MODEL=mock-agent-intent-model

# 请求超时（秒）
TIMEOUT_SECONDS=30

# 是否默认使用流式响应
DEFAULT_STREAM=false

# 原站对比地址（可选，用于响应对比测试）
ORIGIN_URL=

# 原站 API Key（可选）
ORIGIN_API_KEY=

# 服务端口
APP_PORT=18081
```

- [ ] **Step 2: 提交**

```bash
git add .env.example
git commit -m "feat: add .env.example for containerized deployment"
```

---

### Task 3: entrypoint.sh

**Files:**
- Create: `entrypoint.sh`

**Interfaces:**
- Consumes: 环境变量 `DEVICE_URL`, `API_KEY`, `MODEL`, `TIMEOUT_SECONDS`, `DEFAULT_STREAM`, `ORIGIN_URL`, `ORIGIN_API_KEY`
- Produces: `/app/config.yaml`（从环境变量生成），然后执行 `python manage.py migrate --noinput`，最后 `exec "$@"`

- [ ] **Step 1: 创建 `entrypoint.sh`**

```bash
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

# 执行传入的命令
exec "$@"
```

- [ ] **Step 2: 设置可执行权限**

Run: `chmod +x entrypoint.sh`

- [ ] **Step 3: 验证脚本语法**

Run: `bash -n entrypoint.sh`
Expected: 无输出，退出码 0

- [ ] **Step 4: 提交**

```bash
git add entrypoint.sh
git commit -m "feat: add entrypoint.sh for container config generation"
```

---

### Task 4: Dockerfile

**Files:**
- Create: `Dockerfile`

**Interfaces:**
- Consumes: `requirements.txt`, `entrypoint.sh`（复制到镜像中）, 项目源码
- Produces: Docker 镜像 `agent-intent-test:latest`，暴露端口 18081，非 root 运行

- [ ] **Step 1: 创建 `Dockerfile`**

```dockerfile
# ---- Builder Stage ----
FROM python:3.14-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ---- Runtime Stage ----
FROM python:3.14-slim AS runtime

# 创建非 root 用户
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# 复制依赖
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH="/home/appuser/.local/bin:$PATH"

# 复制应用代码
COPY . .

# 确保 entrypoint 可执行
RUN chmod +x entrypoint.sh

# 设置非 root 拥有者
RUN chown -R appuser:appuser /app
USER appuser

# 环境变量
ENV PYTHONUNBUFFERED=1

EXPOSE 18081

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://localhost:18081/')" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:18081"]
```

- [ ] **Step 2: 构建镜像**

Run: `docker build -t agent-intent-test:latest .`
Expected: 构建成功，输出包含 `naming to docker.io/library/agent-intent-test:latest`

- [ ] **Step 3: 验证镜像基本结构**

Run: `docker run --rm agent-intent-test:latest python -c "import django; print(django.VERSION)"`
Expected: 输出 Django 版本号，如 `(5, 2, 0, 'final', 0)`

- [ ] **Step 4: 验证非 root 用户**

Run: `docker run --rm agent-intent-test:latest whoami`
Expected: 输出 `appuser`

- [ ] **Step 5: 提交**

```bash
git add Dockerfile
git commit -m "feat: add multi-stage Dockerfile with non-root user"
```

---

### Task 5: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

**Interfaces:**
- Consumes: `Dockerfile`（构建镜像）, `.env`（环境变量）, named volumes
- Produces: 两个 profiles：
  - `dev`: 源码挂载，`restart: "no"`，热重载
  - `prod`: 数据卷持久化，`restart: unless-stopped`，健康检查

- [ ] **Step 1: 创建 `docker-compose.yml`**

```yaml
services:
  app:
    build: .
    image: agent-intent-test:latest
    container_name: agent_intent_test
    ports:
      - "${APP_PORT:-18081}:18081"
    environment:
      - DEVICE_URL=${DEVICE_URL:-http://10.10.121.15:18081/v1/chat/completions}
      - API_KEY=${API_KEY:-}
      - MODEL=${MODEL:-mock-agent-intent-model}
      - TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-30}
      - DEFAULT_STREAM=${DEFAULT_STREAM:-false}
      - ORIGIN_URL=${ORIGIN_URL:-}
      - ORIGIN_API_KEY=${ORIGIN_API_KEY:-}
    volumes:
      # 仅持久化运行时数据目录，db.sqlite3 在 /app 下自动创建（项目无 Django models，无需持久化）
      - reports_data:/app/reports
      - workspace_data:/app/mock_workspace
    restart: unless-stopped
    profiles:
      - prod

  app-dev:
    build: .
    image: agent-intent-test:latest
    container_name: agent_intent_test_dev
    ports:
      - "${APP_PORT:-18081}:18081"
    environment:
      - DEVICE_URL=${DEVICE_URL:-http://10.10.121.15:18081/v1/chat/completions}
      - API_KEY=${API_KEY:-}
      - MODEL=${MODEL:-mock-agent-intent-model}
      - TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-30}
      - DEFAULT_STREAM=${DEFAULT_STREAM:-false}
      - ORIGIN_URL=${ORIGIN_URL:-}
      - ORIGIN_API_KEY=${ORIGIN_API_KEY:-}
    volumes:
      - .:/app
      - /app/__pycache__
      - /app/.venv
    restart: "no"
    profiles:
      - dev

volumes:
  reports_data:
  workspace_data:
```

- [ ] **Step 2: 验证 YAML 语法**

Run: `docker compose config --profile prod 2>&1 | head -5`
Expected: 输出解析后的 compose 配置，无语法错误

- [ ] **Step 3: 创建 `.env` 文件用于本地测试**

Run: `cp .env.example .env`

- [ ] **Step 4: 启动开发 profile 并验证服务**

Run: `docker compose --profile dev up -d`
Expected: 容器启动成功

- [ ] **Step 5: 检查健康状态**

Run: `sleep 5 && docker compose ps`
Expected: `agent_intent_test_dev` 容器状态为 healthy（或 running，启动中）

- [ ] **Step 6: 测试 Web 控制台可访问**

Run: `curl -s -o /dev/null -w "%{http_code}" http://localhost:18081/`
Expected: 输出 `200`

- [ ] **Step 7: 测试 Mock LLM 端点**

Run: `curl -s -X POST http://localhost:18081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test","messages":[{"role":"user","content":"hello"}]}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('object','FAIL'))" 2>&1`
Expected: 输出含 `chat.completion` 的正常响应

- [ ] **Step 8: 验证 config.yaml 在容器内正确生成**

Run: `docker compose exec app-dev cat /app/config.yaml`
Expected: 输出 JSON，包含 `device_url`、`model` 等字段

- [ ] **Step 9: 停止开发容器**

Run: `docker compose --profile dev down`

- [ ] **Step 10: 提交**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose.yml with dev and prod profiles"
```

---

### Task 6: 更新 README（可选文档任务）

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: 新增的容器化部署文件
- Produces: README 中新增"容器化部署"章节

- [ ] **Step 1: 在 README.md 中添加容器化部署说明**

在 README.md 的部署相关章节后追加以下内容：

```markdown
## 容器化部署（Docker）

### 准备工作

```bash
cp .env.example .env
# 编辑 .env，填入实际的 DEVICE_URL 和 API_KEY
```

### 本地开发

```bash
docker compose --profile dev up
```

源码挂载模式，修改代码后自动热重载。

### 生产部署

```bash
docker compose --profile prod up -d
```

数据（SQLite、报告、mock workspace）通过 Docker volumes 持久化。

### 常用命令

```bash
# 查看日志
docker compose logs -f app

# 查看状态
docker compose ps

# 停止
docker compose --profile prod down

# 重新构建
docker compose build --no-cache
```
```

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs: add containerized deployment section to README"
```

---

### Task 7: 最终端到端验证

所有文件就绪后，执行完整的端到端验证。

- [ ] **Step 1: 清理所有已有容器和镜像**

```bash
docker compose --profile dev down --volumes 2>/dev/null || true
docker compose --profile prod down --volumes 2>/dev/null || true
docker rmi agent-intent-test:latest 2>/dev/null || true
```

- [ ] **Step 2: 从零构建并启动生产 profile**

```bash
docker compose --profile prod up -d --build
```

- [ ] **Step 3: 等待健康检查通过**

Run: `sleep 35 && docker compose ps`
Expected: 容器状态显示 healthy

- [ ] **Step 4: 运行快速功能验证**

```bash
# Web 控制台
curl -sf http://localhost:18081/ > /dev/null && echo "✓ Web console OK" || echo "✗ Web console FAIL"

# Mock LLM 非流式
curl -sf -X POST http://localhost:18081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test","messages":[{"role":"user","content":"hello"}]}' > /dev/null && echo "✓ Mock LLM OK" || echo "✗ Mock LLM FAIL"
```

Expected: 两项均输出 `✓ ... OK`

- [ ] **Step 5: 验证数据持久化**

```bash
# 在容器中生成测试报告
docker compose exec app python -m intent_console.runner --config config.yaml --scenarios-dir scenarios --dry-run
# 检查 reports 目录挂载
ls -la reports/ 2>/dev/null && echo "✓ Reports volume OK" || echo "✗ Reports volume FAIL"
```

- [ ] **Step 6: 清理**

```bash
docker compose --profile prod down
```

- [ ] **Step 7: 提交（如有 README 更新）**

```bash
git status
```
