FROM ubuntu:22.04

WORKDIR /app


RUN apt-get update
RUN apt install -y vim git python3 python3-pip
RUN ln -s /usr/bin/python3 /usr/bin/python
RUN python -m pip install --upgrade pip
RUN pip install regions -i https://mirrors.aliyun.com/pypi/simple

#RUN git clone https://github.com/RuancunLi/GalfitS.git
COPY . .
RUN cd GalfitS && pip install -r requirement.txt -i https://mirrors.aliyun.com/pypi/simple
RUN pip install -U jax==0.6.2 jaxlib==0.6.2

# 追加环境变量和别名到 ~/.bashrc
RUN echo 'export PYTHONPATH="/app/GalfitS/src:$PYTHONPATH"' >> ~/.bashrc && \
    echo 'export GS_DATA_PATH="/app/galfits-data"' >> ~/.bashrc && \
    echo 'alias galfits="python /app/GalfitS/src/galfits/galfitS.py --config "' >> ~/.bashrc

RUN pip install --no-cache-dir . -i https://mirrors.aliyun.com/pypi/simple

#CMD ["python", "src/mcp_server.py", "--transport", "http"]
