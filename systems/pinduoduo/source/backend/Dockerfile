# backend 服务 Dockerfile —— 拼多多自动回复系统 HTTP API 服务（默认端口 8089）
# ==============================================================================
# 本文件用途：构建 backend（HTTP API）服务镜像。
# 设计要点：
#   1. 基于 python:3.11-slim，与 pyproject.toml 声明的 requires-python>=3.11 一致；
#   2. common 公共库经 sys.path 共享引用（main.py 将项目根目录加入 sys.path），
#      故镜像内同时复制 common 与 backend 两个目录，并设置 PYTHONPATH=/app；
#   3. 依赖从各自 pyproject.toml 的 [project].dependencies 提取后安装，避免重复维护；
#   4. 入口统一为本服务 main.py（最小入口桩，规范 32），监听地址 0.0.0.0、
#      端口经环境变量 BACKEND_WEB_PORT 配置，禁止写死 localhost（规范 21）；
#   5. 全链路北京时间（规范 17），容器时区设为 Asia/Shanghai。

FROM python:3.11-slim

# 配置国内镜像源，加速 apt 与 pip 安装（构建期优化，不影响运行逻辑）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# 安装基础系统依赖：curl（健康检查）、ca 证书、gcc（部分依赖需编译）
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gcc \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 复制 common 公共库与 backend 服务源码（common 经 sys.path 共享，非通过依赖安装）
COPY common /app/common
COPY backend /app/backend

# 从 backend/pyproject.toml 与 common/pyproject.toml 提取依赖并安装。
# 说明：backend 运行期同时使用本服务依赖与 common 公共库依赖，故两份均需安装。
RUN python -c "import tomllib; \
deps = tomllib.load(open('/app/common/pyproject.toml','rb')).get('project',{}).get('dependencies',[]) \
+ tomllib.load(open('/app/backend/pyproject.toml','rb')).get('project',{}).get('dependencies',[]); \
open('/tmp/requirements.txt','w').write('\n'.join(deps))" \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# 创建静态资源与上传目录（部分接口写入上传文件）
RUN mkdir -p /app/backend/static/uploads

# 暴露 HTTP API 端口（与默认 BACKEND_WEB_PORT 一致；实际端口经环境变量配置）
EXPOSE 8089

# 运行环境变量：
# - PYTHONUNBUFFERED：日志实时输出，便于容器日志查看；
# - PYTHONPATH=/app：使 common 公共库与 backend 服务包可被正常导入；
# - TZ=Asia/Shanghai：容器时区统一北京时间（规范 17）。
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV TZ=Asia/Shanghai

# 容器健康检查：探测服务监听端口（端口经环境变量，默认 8089）
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${BACKEND_WEB_PORT:-8089}/health" || exit 1

# 启动服务：入口统一为 backend/main.py（最小入口桩，规范 32）
CMD ["python", "backend/main.py"]
