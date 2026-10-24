"""
文件用途：scheduler 定时任务服务的应用装配与核心启动逻辑。

本文件负责（main.py 仅为最小入口桩）：
  1. 读取服务运行配置（端口/监听地址，均经环境变量管理，禁止写死 localhost，规范 21）；
  2. 创建 FastAPI 应用；
  3. 提供 /health 健康检查接口（返回项目统一响应体结构 {code, success, message, data}，规范 3）；
  4. 通过 lifespan 启动 / 停止定时任务调度器 SchedulerService（按 scheduled_task
     配置调度 Cookie 刷新、商品同步、文件日志清理等任务，任务 15.1）；
  5. 提供 run_server 供 main.py 的 __main__ 块调用以拉起 HTTP 服务。

注意：本服务仅「连接」数据库读取任务配置与写执行日志，不执行建表迁移自检
（建表迁移由 backend 的 lifespan 统一执行，其余服务仅连接）。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tasks import SchedulerService

# 模块级日志记录器（禁用 debug 级别 —— 规范 38）。
logger = logging.getLogger("scheduler.bootstrap")

# 服务元信息
PROJECT_NAME = "拼多多自动回复系统 - scheduler 定时任务服务"

# 服务监听配置（一律经环境变量管理，禁止写死 localhost，规范 21）：
# - SCHEDULER_HOST：监听地址，默认 0.0.0.0（容器内对外可达）；
# - SCHEDULER_PORT：监听端口，默认 8091。
SERVICE_HOST = os.getenv("SCHEDULER_HOST", "0.0.0.0")
SERVICE_PORT = int(os.getenv("SCHEDULER_PORT", "8091"))


def _success_response(message: str, data: object = None) -> dict:
    """构造项目统一成功响应体 {code, success, message, data}（规范 3）。"""
    return {"code": 0, "success": True, "message": message, "data": data}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理（启动/关闭钩子）。

    启动阶段：创建并启动定时任务调度器 SchedulerService，按 scheduled_task
    配置注册并触发 Cookie 刷新、商品同步、文件日志清理等任务（任务 15.1）。
    关闭阶段：停止调度器并释放资源。

    调度器启动失败不阻断服务启动（仅记录错误），保证 /health 等接口仍可用。
    """
    # —— 启动阶段：创建并启动定时任务调度器（仅连接数据库，不做建表迁移）——
    scheduler_service = SchedulerService()
    try:
        registered = scheduler_service.start()
        logger.info("scheduler 服务启动完成，注册定时任务 %d 个", registered)
    except Exception as exc:  # noqa: BLE001 —— 调度器启动失败不阻断服务可用性
        logger.error("定时任务调度器启动失败：%s", exc)
    # 将调度器实例挂到应用状态，便于关闭阶段停止。
    app.state.scheduler_service = scheduler_service
    yield
    # —— 关闭阶段：停止调度器并清理资源 ——
    try:
        scheduler_service.shutdown()
    except Exception as exc:  # noqa: BLE001 —— 关闭失败仅记录，不影响进程退出
        logger.error("定时任务调度器停止失败：%s", exc)


# 创建 FastAPI 应用（装配逻辑集中于本文件，main.py 仅负责拉起）。
app = FastAPI(title=PROJECT_NAME, lifespan=lifespan)


@app.get("/health")
async def health_check() -> dict:
    """
    健康检查接口（占位）。

    返回项目统一响应体结构，供容器编排 healthcheck 与服务探活使用。
    后续可在 data 中补充数据库连接状态、调度器运行状态等信息。
    """
    # 读取调度器运行状态与已注册任务数（若已启动）。
    scheduler_service = getattr(app.state, "scheduler_service", None)
    scheduler_running = bool(scheduler_service and scheduler_service._started)
    return _success_response(
        message="服务运行正常",
        data={
            "service": "scheduler",
            "status": "running",
            "scheduler_running": scheduler_running,
        },
    )


def run_server() -> None:
    """启动 HTTP 服务（供 main.py 的 __main__ 块调用）。"""
    import uvicorn

    # host/port 均来自环境变量，禁止写死 localhost（规范 21）。
    uvicorn.run(
        "main:app",
        host=SERVICE_HOST,
        port=SERVICE_PORT,
        reload=False,
    )
