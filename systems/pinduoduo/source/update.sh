#!/usr/bin/env bash
# ==========================================
# 拼多多自动回复系统 - 一键更新脚本（update.sh）
# ------------------------------------------
# 用途：滚动更新已部署的本项目服务（规范 46-47）：
#       1) 可选拉取最新代码（git pull，仅当处于 git 仓库且未禁用时）；
#       2) 拉取基础镜像并重新构建本项目镜像；
#       3) 以滚动方式重建并启动容器（逐服务重建，尽量减少停机），并清理悬空镜像。
#       与 deploy.sh 的区别：update.sh 保留并复用现有容器/数据，做平滑更新，不强制删除全部镜像。
# 规范：地址 / 配置一律经 .env（环境变量）管理，禁止写死 localhost（规范 21 / 需求 25.3-25.4）；
#       数据卷默认保留，不做物理删除（规范 11）。
# 用法：bash update.sh             # 交互确认后滚动更新
#       bash update.sh -y          # 跳过确认
#       bash update.sh --no-git    # 不执行 git pull，仅重建镜像并更新
# ==========================================

set -euo pipefail

# ---- 终端彩色输出（无 TTY 时自动降级为无色）----
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; NC=''
fi

# ---- 解析参数 ----
ASSUME_YES=0
DO_GIT=1
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        --no-git) DO_GIT=0 ;;
        *) ;;
    esac
done

# ---- 路径与文件定位 ----
WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$WORK_DIR/docker-compose.yml"
ENV_FILE="$WORK_DIR/.env"
ENV_EXAMPLE="$WORK_DIR/.env.example"

echo "=========================================="
echo "  拼多多自动回复系统 - 一键更新"
echo "=========================================="

# ---- 校验 Docker 与 Compose 是否就绪 ----
if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}错误: 未检测到 Docker，请先安装 Docker。${NC}"
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
    else
        echo -e "${RED}错误: 未找到 .env 与 .env.example，无法读取更新配置。${NC}"
        exit 1
    fi
fi

# ---- 读取项目名 ----
PROJECT_NAME="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ENV_FILE" 2>/dev/null | head -n1 | cut -d '=' -f2 | tr -d '\r' || true)"
PROJECT_NAME="${PROJECT_NAME:-pdd-auto-reply}"
export COMPOSE_PROJECT_NAME="$PROJECT_NAME"

DC_CMD="$DC -f $COMPOSE_FILE --env-file $ENV_FILE"

echo -e "${CYAN}[信息] 项目名称: $PROJECT_NAME${NC}"
echo -e "${CYAN}[信息] 项目目录: $WORK_DIR${NC}"

# ---- 安全确认 ----
if [ "$ASSUME_YES" -ne 1 ]; then
    echo ""
    echo -e "${YELLOW}本操作将更新本项目（$PROJECT_NAME）：重建镜像并滚动重启容器。${NC}"
    echo -e "${YELLOW}数据卷（MySQL / Redis 数据）将被保留，不会删除。${NC}"
    read -r -p "确认继续？[y/N] " confirm
    case "$confirm" in
        y|Y|yes|YES) ;;
        *) echo -e "${CYAN}已取消。${NC}"; exit 0 ;;
    esac
fi

# ========== 步骤 1/4：可选拉取最新代码 ==========
echo ""
if [ "$DO_GIT" -eq 1 ] && [ -d "$WORK_DIR/.git" ] && command -v git >/dev/null 2>&1; then
    echo -e "${YELLOW}步骤 1/4: 拉取最新代码（git pull）...${NC}"
    git -C "$WORK_DIR" pull --ff-only || echo -e "${YELLOW}[提示] git pull 未成功（可能存在本地改动），将基于当前代码继续更新。${NC}"
    echo -e "${GREEN}✓ 代码已更新。${NC}"
else
    echo -e "${CYAN}步骤 1/4: 跳过 git pull（非 git 仓库或已通过 --no-git 禁用）。${NC}"
fi

# ========== 步骤 2/4：拉取基础镜像并重建本项目镜像 ==========
echo ""
echo -e "${YELLOW}步骤 2/4: 拉取基础镜像并重新构建本项目镜像...${NC}"
$DC_CMD build --pull
echo -e "${GREEN}✓ 镜像重建完成。${NC}"

# ========== 步骤 3/4：滚动重建并启动容器 ==========
echo ""
echo -e "${YELLOW}步骤 3/4: 滚动更新容器（仅重建有变更的服务）...${NC}"
# --no-deps + 逐服务可实现更平滑滚动；此处用 up -d 让 compose 仅重建变更服务，依赖顺序由 depends_on 保证。
$DC_CMD up -d --remove-orphans
echo -e "${GREEN}✓ 容器已滚动更新。${NC}"

# ========== 步骤 4/4：清理悬空镜像（释放磁盘，仅删无标签悬空镜像，安全）==========
echo ""
echo -e "${YELLOW}步骤 4/4: 清理更新后产生的悬空镜像...${NC}"
docker image prune -f >/dev/null 2>&1 || true
echo -e "${GREEN}✓ 悬空镜像已清理。${NC}"

# ---- 展示状态 ----
echo ""
echo -e "${CYAN}[信息] 等待服务就绪（约 10 秒）...${NC}"
sleep 10
$DC_CMD ps

echo ""
echo -e "${GREEN}=========================================="
echo "  更新完成！"
echo "==========================================${NC}"
echo "常用命令："
echo "  查看日志: $DC_CMD logs -f"
echo "  停止服务: $DC_CMD down"
