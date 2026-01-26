FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 复制代码
COPY tesla_notifier/ tesla_notifier/

# 环境变量说明
# - BARK_ICON: Bark 推送图标 URL（可选，默认使用 Tesla Logo）
# - 其他环境变量请参考 docker-compose.yml 或 .env.example

# 设置时区
ENV TZ=Asia/Shanghai

# 运行
CMD ["python", "-m", "tesla_notifier.main"]
