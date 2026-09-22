FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/app/data/app.db

WORKDIR /app

# 依赖安装（构建期完成，启动无需联网）
COPY requirements.txt .
RUN pip install -r requirements.txt

# 应用代码
COPY app ./app

# SQLite 数据目录（Compose 挂载卷持久化；首次启动自动建表/初始化）
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
