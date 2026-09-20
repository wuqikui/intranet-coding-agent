FROM python:3.11-slim

WORKDIR /app

# 系统依赖（编译/CMake 沙箱环境基础）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake clang git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
