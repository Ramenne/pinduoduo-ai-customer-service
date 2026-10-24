#!/usr/bin/env bash
# ==========================================
# 拼多多自动回复系统 - 一键构建脚本（build.sh）
# ------------------------------------------
# 用途：基于 docker-compose.yml 构建本项目全部服务镜像（backend / websocket / scheduler / frontend）。
#       仅构建镜像，不启动容器；如需「删除旧容器镜像→构建→启动」请使用 deploy.sh。
# 规范：地址 / 配置一律经 .env（环境变量）管理，禁止写死 localhost（规范 21 / 需求 25.3-25.4）。
# 用法：bash build.sh            # 构建全部服务镜像
#       bash build.sh backend   # 仅构建指定服务镜像
# ==========================================

set -euo pipefail

# ---- 终端彩色输出（无 TTY 时自动降级为无色）----
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; NC=''
fi

# ---- 路径与文件定位（以脚本所在目录为项目根，避免依赖调用方当前目录）----
WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$WORK_DIR/docker-compose.yml"
ENV_FILE="$WORK_DIR/.env"
ENV_EXAMPLE="$WORK_DIR/.env.example"

echo "=========================================="
echo "  拼多多自动回复系统 - 一键构建"
echo "=========================================="

# ---- 校验 Docker 与 Compose 是否就绪 ----
if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}错误: 未检测到 Docker，请先安装 Docker。${NC}"
    echo "安装教程: https://docs.docker.com/get-docker/"
    exit 1
fi

# 兼容 docker compose（插件版）与 docker-compose（独立版）两种形态
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    echo -e "${RED}错误: 未检测到 Docker Compose，请先安装。${NC}"
    exit 1
fi

# ---- 校验编排文件存在 ----
if [ ! -f "$COMPOSE_FILE" ]; then
    echo -e "${RED}错误: 未找到 docker-compose.yml（应位于 $COMPOSE_FILE）。${NC}"
    exit 1
fi

# ---- 准备 .env：不存在则从 .env.example 复制，保证地址 / 配置经环境变量管理 ----
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        echo -e "${YELLOW}[提示] 未找到 .env，已从 .env.example 自动生成，请按需修改后再次执行。${NC}"
        cp "$ENV_EXAMPLE" "$ENV_FILE"
    else
        echo -e "${RED}错误: 未找到 .env 与 .env.example，无法读取构建配置。${NC}"
        exit 1
    fi
fi

# Compose 命令统一携带编排文件与环境变量文件
DC_CMD="$DC -f $COMPOSE_FILE --env-file $ENV_FILE"

echo -e "${CYAN}[信息] Docker:  $(docker --version)${NC}"
echo -e "${CYAN}[信息] Compose: $DC${NC}"
echo -e "${CYAN}[信息] 项目目录: $WORK_DIR${NC}"

# ---- 执行构建（可选指定单个服务名作为第一个参数）----
TARGET_SERVICE="${1:-}"
echo ""
if [ -n "$TARGET_SERVICE" ]; then
    echo -e "${YELLOW}开始构建服务镜像: ${TARGET_SERVICE} ...${NC}"
    $DC_CMD build --pull "$TARGET_SERVICE"
else
    echo -e "${YELLOW}开始构建全部服务镜像（backend / websocket / scheduler / frontend）...${NC}"
    $DC_CMD build --pull
fi

echo ""
echo -e "${GREEN}✓ 镜像构建完成。${NC}"
echo -e "${CYAN}[提示] 启动服务请执行: bash deploy.sh${NC}"
