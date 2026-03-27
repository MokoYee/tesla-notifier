FROM python:3.11-slim

WORKDIR /app

# 复制代码
COPY pyproject.toml README.md ./
COPY tesla_notifier/ tesla_notifier/

# 安装依赖和项目本体
RUN pip install --no-cache-dir .

# 环境变量说明
# - BARK_ICON: Bark 推送图标 URL（可选，默认使用 Tesla Logo）
# - 其他环境变量请参考 docker-compose.yml 或 .env.example

# 设置时区
ENV TZ=Asia/Shanghai

# 运行
CMD ["python", "-m", "tesla_notifier.main"]
