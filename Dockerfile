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
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local
ENV PATH="/home/appuser/.local/bin:$PATH"

# 复制应用代码
COPY . .

# 确保 entrypoint 可执行
RUN chmod +x entrypoint.sh

# 预创建数据目录（named volume 挂载会覆盖，entrypoint 启动时再修复权限）
RUN mkdir -p /app/reports /app/mock_workspace && \
    chown appuser:appuser /app/reports /app/mock_workspace

# 环境变量（HOME 指向 appuser 目录，让 pip --user 安装的包在 root 和 appuser 下都能被 Python 找到）
ENV HOME=/home/appuser
ENV PYTHONUNBUFFERED=1

EXPOSE 18081

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://localhost:18081/')" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:18081"]
