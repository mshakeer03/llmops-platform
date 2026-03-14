# Use ARM64-compatible Python image for Apple Silicon (M1/M2)
# Python 3.11 is stable and has good compatibility with vLLM
FROM --platform=linux/arm64 python:3.11-slim

# Install system dependencies for building C++
# Added ccache to speed up re-builds and reduce memory pressure slightly
# Added libnuma-dev for CPU extension "numa.h" dependency
# Added ca-certificates for git clone
RUN apt-get update && apt-get install -y \
    cmake \
    ninja-build \
    git \
    build-essential \
    ccache \
    libnuma-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Configure pip to trust PyPI hosts (bypass SSL proxy issues)
RUN pip config set global.trusted-host "pypi.org pypi.python.org files.pythonhosted.org"

WORKDIR /app

# Clone and install vLLM
# Cloning main branch to match user's local working version (v0.16.0rc2+)
# Disable SSL verify for git to bypass proxy/certificate issues
RUN git config --global http.sslVerify false
RUN git clone https://github.com/vllm-project/vllm.git .
RUN pip install --upgrade pip

# Set build environment for Apple Silicon / CPU
ENV VLLM_TARGET_DEVICE=cpu

# Memory Optimization for Build
# CRITICAL: Limit parallel jobs to avoiding OOM on Podman VM (usually ~2-4GB default)
# User has 32GB system RAM but Podman VM might be limited
ENV MAX_JOBS=1
ENV CMAKE_BUILD_PARALLEL_LEVEL=1

# Disable OneDNN to save build memory (optional, but safer for small VMs)
ENV VLLM_CPU_DISABLE_AVX512="true"

# Install dependencies first (better caching)
RUN pip install -r requirements/cpu.txt

# Install vLLM from source
# This will take longer (15-30m) but ensures correct CPU feature detection
RUN pip install -e .

# Expose the API port
EXPOSE 9000

# Set the entrypoint
ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]
