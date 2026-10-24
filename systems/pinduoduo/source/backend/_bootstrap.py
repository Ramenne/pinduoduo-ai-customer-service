"""
backend 服务核心装配模块（应用装配集中于此，main.py 仅为最小入口桩）。

本文件用途：装配 backend（HTTP API）服务，包括：
1. 创建 FastAPI 应用；
2. 配置 CORS 跨域；
3. 提供占位健康检查接口 /health（返回统一响应体 {code, success, message, data}）；
4. 定义应用生命周期 lifespan：在启动流程中调用 common 的 SchemaMigrator
   完成建表/补字段/补字典启动自检（需求 24.5、规范 14）。
   注意：**仅 backend 服务执行迁移自检**；websocket / scheduler 服务启动时
   只做数据库连接检查、不调用迁移器，以避免多服务并发迁移冲突；
5. 提供 run_server 启动函数（host/port 经环境变量配置，禁止写死 localhost，规范 21）。

说明：本任务仅搭建服务骨架与启动自检，不实现任何业务逻辑；路由挂载、鉴权、
各业务接口均由后续任务在 app/ 子包内实现后再于此挂载。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

# 仅 backend 负责建表迁移自检：导入 common 的迁移器入口与字典初始化注册钩子。
# - init_database：执行建表 / 补字段 / 补字典的启动自检（带连接失败重试）。
# - register_dict_initializer：注册字典初始化钩子，使迁移 seed 阶段回调补字典。
# - register_dict_initial_data：common 字典服务提供的字典初始数据幂等登记函数。
# - get_engine：获取进程内单例引擎，用于启动时的数据库连接检查。
from common.db.init_database import init_database, register_dict_initializer
from common.db.session import get_engine
from common.utils.access_log import AccessTokenFilter
from common.services.dict_service import register_dict_initial_data
# 导入初始管理员种子模块：导入即注册启动自检钩子（库中无用户时幂等创建超级管理员）。
import common.services.admin_seed  # noqa: F401

# 业务路由聚合与业务异常处理器（鉴权/业务失败恒返回 HTTP 200 + 统一响应体）。
from app.api.router import api_router
from app.services import product_jobs
from app.core.errors import register_exception_handlers

# 模块级日志记录器（统一 info/warning/error，禁用 debug —— 规范 38）。
logger = logging.getLogger(__name__)
for _logger_name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_logger_name).addFilter(AccessTokenFilter())

# 服务默认配置（均可经环境变量覆盖，禁止写死 localhost，规范 21）。
# - BACKEND_WEB_HOST：监听地址，默认 0.0.0.0（容器内对外可达）。
# - BACKEND_WEB_PORT：监听端口，默认 8089。
# - BACKEND_WEB_CORS_ORIGINS：允许跨域来源，逗号分隔，默认 *（开发期）。
# - BACKEND_WEB_API_PREFIX：业务路由统一前缀，默认 /api/v1。
DEFAULT_SERVICE_HOST = os.getenv("BACKEND_WEB_HOST", "0.0.0.0")
DEFAULT_SERVICE_PORT = int(os.getenv("BACKEND_WEB_PORT", "8089"))
API_PREFIX = os.getenv("BACKEND_WEB_API_PREFIX", "/api/v1")
SERVICE_NAME = "pdd-auto-reply-backend"
SERVICE_VERSION = "0.1.22"


def _build_response(code: int, success: bool, message: str, data=None) -> dict:
    """构造统一响应体 {code, success, message, data}（规范 1-3）。

    全项目对外返回结构保持一致，便于前端统一处理；失败时 success=false、
    message 为中文错误信息、data=null。
    """
    return {"code": code, "success": success, "message": message, "data": data}


def _check_database_connection() -> bool:
    """启动时数据库连接检查：执行一次轻量 ``SELECT 1`` 验证可达性。

    参照 xianyu backend-web 的 check_database_connection 思路：连接成功返回
    True；连接失败仅记录错误日志并返回 False，由调用方决定是否中断启动。
    连接信息全部来自 common 配置（规范 21，禁止写死 localhost）。

    Returns:
        bool：数据库可连接返回 True，否则 False。
    """
    try:
        engine = get_engine()
        with engine.connect() as connection:
            # 参数化无关的常量探活语句，仅验证连接可用性
            connection.execute(text("SELECT 1"))
        logger.info("数据库连接检查通过")
        return True
    except SQLAlchemyError as exc:
        # 连接失败记录错误日志（不抛出，保留启动流程的可观测性）
        logger.error("数据库连接检查失败：%s", exc)
        return False


def _run_startup_migration() -> None:
    """执行 backend 启动自检迁移：注册字典钩子并完成建表 / 补字段 / 补字典。

    仅 backend 服务调用本流程；websocket / scheduler 仅连接数据库、不迁移，
    避免多服务并发迁移冲突（见 common.db.init_database 模块说明）。

    步骤：
    1. 注册字典初始化钩子（幂等注册），使迁移 seed 阶段回调
       ``register_dict_initial_data`` 补齐枚举字典与初始数据；
    2. 调用 ``init_database()``（内部为 SchemaMigrator + 连接失败重试）完成
       建表 + 补字段 + 补字典，全过程只增不改不删、幂等（需求 24.5）。
    """
    # 注册字典初始化钩子：迁移末尾 seed 阶段会回调它补齐字典（幂等注册）
    register_dict_initializer(register_dict_initial_data)
    # 执行建表 / 补字段 / 补字典自检，并记录本次变更概要
    result = init_database()
    logger.info(
        "backend 启动自检迁移完成：新建表 %d 张，补齐字段 %d 个，补充初始数据 %d 条",
        len(result.created_tables),
        len(result.added_columns),
        result.seeded_rows,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动执行建表迁移自检，关闭释放资源。

    启动阶段（仅 backend 执行迁移）：
    1. 先做数据库连接检查；连接失败记录日志（不静默吞掉），便于排障；
    2. 调用 common 迁移器完成建表 / 补字段 / 补字典启动自检（需求 24.5）。

    其余服务（websocket / scheduler）启动时仅连接数据库、不在此执行迁移，
    以避免多服务并发迁移冲突。

    关闭阶段：占位释放数据库连接池等资源（后续按需补充 HTTP 客户端等）。
    """
    # 启动：先连接检查，再执行迁移自检（仅 backend 迁移）
    _check_database_connection()
    _run_startup_migration()
    product_jobs.start()
    try:
        yield
    finally:
        product_jobs.stop()
    # 关闭：释放进程内引擎连接池，归还底层连接
    try:
        get_engine().dispose()
        logger.info("已释放数据库连接池资源")
    except SQLAlchemyError as exc:
        logger.error("释放数据库连接池资源失败：%s", exc)


# 创建 FastAPI 应用，绑定生命周期管理。
app = FastAPI(
    title=SERVICE_NAME,
    version=SERVICE_VERSION,
    lifespan=lifespan,
)

# 配置 CORS 跨域：来源经环境变量配置，开发期默认放开。
_cors_origins = [
    origin.strip()
    for origin in os.getenv("BACKEND_WEB_CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册业务异常处理器：鉴权 / 业务失败以异常中断时，统一转为
# 「HTTP 200 + {code, success, message, data}」失败响应体（规范 1）。
register_exception_handlers(app)

# 挂载业务路由聚合器，统一前缀（默认 /api/v1）：认证接口（登录/登出）等
# 经此对外暴露，后续任务并入更多业务路由（用户/店铺/规则/日志等）。
app.include_router(api_router, prefix=API_PREFIX)


@app.get("/health")
async def health_check() -> dict:
    """健康检查接口（占位）。

    返回统一响应体结构，供容器编排 healthcheck 与负载均衡探活使用；
    后续任务可在此追加数据库连接状态等运行信息。
    """
    return _build_response(
        code=0,
        success=True,
        message="服务运行正常",
        data={
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "status": "running",
        },
    )


def run_server() -> None:
    """启动 HTTP 服务（供 main.py 的 __main__ 块调用）。

    监听地址与端口均经环境变量配置，禁止写死 localhost（规范 21）；
    以模块路径字符串 "main:app" 方式引用应用，便于 uvicorn 加载。
    """
    import uvicorn

    uvicorn.run(
        "main:app",
        host=DEFAULT_SERVICE_HOST,
        port=DEFAULT_SERVICE_PORT,
        reload=False,
    )
