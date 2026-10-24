# frontend 服务 Dockerfile —— 拼多多自动回复系统前端（Vue 3 + Vite，多阶段构建）
# ==============================================================================
# 本文件用途：构建前端镜像。采用多阶段构建：
#   1. 构建阶段（node）：安装依赖并执行 vite build 产出静态资源；
#   2. 运行阶段（nginx）：由 nginx 提供静态资源并反向代理后端 API。
# 关键约束（规范 21 / 需求 25.4）：后端地址禁止写死 localhost，统一经环境变量
# 配置。nginx.conf 以「配置模板」形式置于 /etc/nginx/templates/，容器启动时由
# nginx 官方镜像内置 envsubst 将 ${BACKEND_API_URL} 替换为实际地址后生成最终配置。

# ====== 第一阶段：构建（Node + Vite） ======
FROM node:18-alpine AS builder

# 配置国内 npm 镜像源，加速依赖安装
RUN npm config set registry https://registry.npmmirror.com

WORKDIR /app

# 优先复制依赖清单，利用 Docker 层缓存（依赖未变时跳过重装）
COPY frontend/package.json frontend/package-lock.json ./

# 使用锁文件进行确定性安装
RUN npm ci --no-audit --no-fund

# 复制前端源码
COPY frontend/ .

# 构建生产版本（前端 API 基址使用相对路径 /api，经 nginx 反向代理到后端，
# 见 .env.production 中 VITE_API_BASE_URL=/api，不写死 localhost）
RUN npm run build

# ====== 第二阶段：运行（Nginx 提供静态资源） ======
FROM nginx:alpine

# 配置国内镜像源
RUN sed -i 's|dl-cdn.alpinelinux.org|mirrors.aliyun.com|g' /etc/apk/repositories

# 将 nginx 配置以「模板」形式放入官方镜像的模板目录；容器启动时由内置 envsubst
# 用环境变量渲染为 /etc/nginx/conf.d/default.conf（仅替换已声明的环境变量）。
COPY frontend/nginx.conf /etc/nginx/templates/default.conf.template

# 复制构建产物（已混淆压缩，不含可读源码）
COPY --from=builder /app/dist /usr/share/nginx/html

# 后端 API 地址默认值（docker-compose 服务名，禁止写死 localhost；可经环境变量覆盖）
ENV BACKEND_API_URL=http://backend:8089

# 暴露 HTTP 端口
EXPOSE 80

# 设置时区为北京时间（规范 17）
ENV TZ=Asia/Shanghai

# 健康检查：探测首页可达性
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:80/ || exit 1

# 使用官方镜像默认入口（会先执行 envsubst 渲染模板再启动 nginx）
CMD ["nginx", "-g", "daemon off;"]
