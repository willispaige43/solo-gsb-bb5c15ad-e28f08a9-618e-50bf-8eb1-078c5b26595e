FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DB_PATH=/app/data/app.db
EXPOSE 8000

# 启动时自动完成建表、默认设置与演示数据初始化（见 app/db.py: init_db）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
