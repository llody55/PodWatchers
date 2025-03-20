# 第一阶段
FROM swr.cn-southwest-2.myhuaweicloud.com/llody/python:3.9.10-buster
LABEL maintainer="llody"
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
CMD ["python", "app.py"]

