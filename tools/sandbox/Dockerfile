# 用户代码编译/运行沙箱镜像
# 支持 C/C++ (gcc/g++/cmake/clang-tidy/cppcheck)、Python、Node、Go、Rust
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

# 基础编译工具链 + 多语言运行时 + 安全工具
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc g++ cmake ninja-build clang clang-tidy cppcheck valgrind \
        git make pkg-config \
        python3 python3-pip python3-venv \
        nodejs npm \
        golang-go \
        curl ca-certificates openssh-client \
        libssl-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir --break-system-packages \
        poetry ruff black mypy pytest

# 普通用户运行用户代码（降权）
RUN useradd -m -u 1000 -s /bin/bash coder
WORKDIR /workspace
RUN chown -R coder:coder /workspace

USER coder

# 默认保持运行（供 docker exec 调用）
CMD ["sleep", "infinity"]
