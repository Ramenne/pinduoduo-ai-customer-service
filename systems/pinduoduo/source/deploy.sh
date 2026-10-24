#!/usr/bin/env bash
# ==========================================
# 拼多多自动回复系统 - 一键部署脚本（deploy.sh）
# ------------------------------------------
# 用途：完整部署流程（规范 46-47）：
#       1) 删除本项目已存在的容器与镜像（仅本项目，避免误伤其它项目；保留数据卷以遵守「禁止删除数据」底线）；
#       2) 基于 docker-compose.yml 构建新镜像；
#       3) 启动新容器并等待健康检查。
# 规范：地址 / 配置一律经 .env（环境变量）管理，禁止写死 localhost（规范 21 / 需求 25.3-25.4）；
#       数据卷（MySQL / Redis 数据）默认保留，不做物理删除（规范 11）。
# 用法：bash deploy.sh           # 交互确认后执行部署
#       bash deploy.sh -y        # 跳过确认，直接部署（适合 CI / 自动化）
# ==========================================

set -euo pipefail

# ---- 终端彩色输出（无 TTY 时自动降级为无色）----
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; NC=''
fi

# ---- 解析参数：-y / --yes 跳过安全确认 ----
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        *) ;;
    esac
done

# ---- 路径与文件定位 ----
WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$WORK_DIR/docker-compose.yml"
ENV_FILE="$WORK_DIR/.env"
ENV_EXAMPLE="$WORK_DIR/.env.example"

echo "=========================================="
echo "  拼多多自动回复系统 - 一键部署"
echo "=========================================="

# ---- 校验 Docker 与 Compose 是否就绪 ----
if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}错误: 未检测到 Docker，请先安装 Docker。${NC}"
    echo "安装教程: https://docs.docker.com/get-docker/"
    exit 1
fi

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

# ---- 准备 .env：不存在则从 .env.example 复制 ----
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        echo -e "${YELLOW}[提示] 未找到 .env，已从 .env.example 自动生成默认配置。${NC}"
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        echo -e "${YELLOW}[提示] 如需修改端口 / 密码等配置，请编辑 $ENV_FILE 后重新运行本脚本。${NC}"
    else
        echo -e "${RED}错误: 未找到 .env 与 .env.example，无法读取部署配置。${NC}"
        exit 1
    fi
fi

# ---- 读取项目名（用于精确清理本项目资源；默认与目录约定一致）----
PROJECT_NAME="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ENV_FILE" 2>/dev/null | head -n1 | cut -d '=' -f2 | tr -d '\r' || true)"
PROJECT_NAME="${PROJECT_NAME:-pdd-auto-reply}"
export COMPOSE_PROJECT_NAME="$PROJECT_NAME"

# Compose 命令统一携带编排文件与环境变量文件
DC_CMD="$DC -f $COMPOSE_FILE --env-file $ENV_FILE"

echo -e "${CYAN}[信息] Docker:   $(docker --version)${NC}"
echo -e "${CYAN}[信息] Compose:  $DC${NC}"
echo -e "${CYAN}[信息] 项目名称: $PROJECT_NAME${NC}"
echo -e "${CYAN}[信息] 项目目录: $WORK_DIR${NC}"

# ---- 安全确认（删除容器 / 镜像属于不可逆操作，需用户确认；-y 可跳过）----
if [ "$ASSUME_YES" -ne 1 ]; then
    echo ""
    echo -e "${YELLOW}本操作将删除本项目（$PROJECT_NAME）已存在的容器与镜像，并重新构建启动。${NC}"
    echo -e "${YELLOW}数据卷（MySQL / Redis 数据）将被保留，不会删除。${NC}"
    read -r -p "确认继续？[y/N] " confirm
    case "$confirm" in
        y|Y|yes|YES) ;;
        *) echo -e "${CYAN}已取消。${NC}"; exit 0 ;;
    esac
fi

# ========== 步骤 1/3：删除本项目已存在的容器与镜像 ==========
echo ""
echo -e "${YELLOW}步骤 1/3: 删除本项目已存在的容器与镜像（保留数据卷）...${NC}"
# down 默认不带 -v，因此数据卷会保留（遵守「禁止删除数据」底线）。
# --rmi local 仅删除由本 compose 构建的本地镜像，不影响其它项目镜像。
$DC_CMD down --rmi local --remove-orphans 2>/dev/null || true
echo -e "${GREEN}✓ 旧容器与镜像已清理（数据卷已保留）。${NC}"

# ========== 步骤 2/3：构建新镜像 ==========
echo ""
echo -e "${YELLOW}步骤 2/3: 构建新镜像...${NC}"
$DC_CMD build --pull
echo -e "${GREEN}✓ 新镜像构建完成。${NC}"

# ========== 步骤 3/3：启动新容器 ==========
echo ""
echo -e "${YELLOW}步骤 3/3: 启动新容器...${NC}"
$DC_CMD up -d
echo -e "${GREEN}✓ 新容器已启动。${NC}"

# ---- 等待服务起步并展示状态 ----
echo ""
echo -e "${CYAN}[信息] 等待服务启动（约 15 秒）...${NC}"
sleep 15
$DC_CMD ps

# ---- 读取端口用于提示访问地址（地址来自 .env，禁止写死 localhost）----
read_port() {
    local key="$1" default="$2" val
    val="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -n1 | cut -d '=' -f2 | tr -d '\r' || true)"
    echo "${val:-$default}"
}
FRONTEND_PORT="$(read_port FRONTEND_HOST_PORT 80)"
BACKEND_WEB_PORT="$(read_port BACKEND_WEB_PORT 8089)"
WEBSOCKET_PORT="$(read_port WEBSOCKET_PORT 8090)"
SCHEDULER_PORT="$(read_port SCHEDULER_PORT 8091)"

echo ""
echo -e "${GREEN}=========================================="
echo "  部署完成！"
echo "==========================================${NC}"
echo "服务访问地址（请将「服务器IP」替换为实际部署机 IP）："
echo "  前端:      http://服务器IP:${FRONTEND_PORT}"
echo "  Backend:   http://服务器IP:${BACKEND_WEB_PORT}"
echo "  WebSocket: http://服务器IP:${WEBSOCKET_PORT}"
echo "  Scheduler: http://服务器IP:${SCHEDULER_PORT}"
echo ""
echo "常用命令："
echo "  查看日志: $DC_CMD logs -f"
echo "  停止服务: $DC_CMD down"
echo "  重启服务: $DC_CMD restart"
echo "  更新版本: bash update.sh"
