FROM python:3.11-slim

WORKDIR /app

# 系统依赖（curl 用于健康检查 + gcc 等编译工具，某些纯 Python 包的 C 扩展可能需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# pip 走清华镜像源（国内加速，避免超时/元数据失败）
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# 先复制依赖清单（利用 Docker 缓存分层）
COPY pyproject.toml README.md ./

# 先升级 pip + 装构建后端（避免 metadata-generation-failed）
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir hatchling

# 只装运行时依赖（容器跑服务不需要 pytest/ruff 等 dev 依赖）
# 用 pip install . （非 -e）避免 editable 模式的元数据生成问题
# classifier 依赖（torch ~2GB）默认不装，需本地模型时 --build-arg INSTALL_CLASSIFIER=1
ARG INSTALL_CLASSIFIER=0
RUN pip install --no-cache-dir . \
    && if [ "$INSTALL_CLASSIFIER" = "1" ]; then \
         pip install --no-cache-dir "transformers>=4.46.0" "torch>=2.5.0"; \
       fi

# 复制源码
COPY app ./app
COPY policies ./policies
COPY tests ./tests
COPY examples ./examples
COPY docs ./docs
COPY .env.example ./.env.example

# 数据目录（SQLite + 日志 + 模型缓存）
RUN mkdir -p /app/data /app/logs /app/model-cache

# 非 root 运行（安全加固）
RUN useradd --create-home --uid 1000 agentsoc \
    && chown -R agentsoc:agentsoc /app
USER agentsoc

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 启动
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
