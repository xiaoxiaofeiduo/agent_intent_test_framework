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
