FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 复制代码
COPY tesla_notifier/ tesla_notifier/

# 设置时区
ENV TZ=Asia/Shanghai

# 运行
CMD ["python", "-m", "tesla_notifier.main"]
