# 使用官方 Python 3.11 镜像作为基础
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 复制并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用程序代码
COPY main.py .

# 定义 Render 容器启动端口 (Render 自动注入环境变量 PORT)
ENV PORT 10000

# 暴露端口 (可选，仅用于文档)
EXPOSE 10000

# 启动命令 (使用 Uvicorn 监听所有接口 0.0.0.0:$PORT)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
