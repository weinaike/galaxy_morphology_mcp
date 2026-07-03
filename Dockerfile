FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

WORKDIR /app


RUN apt-get update && apt install -y --no-install-recommends software-properties-common vim git
RUN add-apt-repository ppa:deadsnakes/ppa && apt update

RUN apt install -y python3.11 python3.11-dev python3.11-venv

RUN test -f /usr/bin/python || ln -s /usr/bin/python3.11 /usr/bin/python 

RUN python -m ensurepip && python -m pip install --upgrade pip

RUN python --version && pip --version

COPY . .
RUN cd jnesty && pip install . -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
RUN cd GalfitS && bash ./install_galfits_gpu.sh

# 追加环境变量和别名到 ~/.bashrc
RUN echo 'export PYTHONPATH="/app/GalfitS/src:$PYTHONPATH"' >> ~/.bashrc && \
    echo 'export GS_DATA_PATH="/app/galfits-data"' >> ~/.bashrc && \
    echo 'alias galfits="python /app/GalfitS/src/galfits/galfitS.py --config "' >> ~/.bashrc

RUN pip install --no-cache-dir . -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
