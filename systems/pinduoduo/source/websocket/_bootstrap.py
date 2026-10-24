"""
文件用途：websocket 长连接服务核心装配模块。

main.py 仅作为最小入口桩，本文件负责真正的应用装配：
1. 创建 FastAPI 应用并配置 CORS；
2. 提供占位的 /health 健康检查接口（返回项目统一响应体结构）；
3. 提供 lifespan 生命周期占位（后续在此启动拼多多连接管理、消息消费、AI 引擎等）；
4. 提供 run_server 函数供 main.py 的 __main__ 块调用拉起服务。

说明（本任务仅搭建骨架）：
- 本服务后续承载：拼多多基础请求层、连接与 WebSocket 收发、消息解析、关键词匹配、
  营业时间、过滤/黑名单、风控、自动回复决策链、消息队列、连接状态机、AI 回复引擎、
  商品卡片签名降级、转人工与卡片发送、账号密码登录（Playwright）等运行时能力；
- 此处不实现任何业务逻辑，仅保证服务可启动、健康检查可用。

约束：
- 服务地址/端口经环境变量配置，禁止写死 localhost（规范 21）；host 默认 0.0.0.0；
- 默认端口 8090，经环境变量 WEBSOCKET_PORT 读取。
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from websockets import exceptions as ws_exceptions

# websocket 服务对外路由聚合器：聚合连接断开 / 商品拉取 / 手动发送消息 / Cookie 刷新
# 等供 backend / scheduler 经服务间 HTTP 调用触发的运行时能力接口（任务 19.1）。
from routes import api_router

# 服务基础配置（后续接入 common.core.config 统一配置加载，本骨架先从环境变量读取）。
# 规范 21：禁止写死 localhost，监听地址与端口均经环境变量管理。
SERVICE_NAME = "pdd-websocket-service"
# 监听地址默认 0.0.0.0（容器/多机部署可访问），可经 WEBSOCKET_HOST 覆盖。
SERVICE_HOST = os.getenv("WEBSOCKET_HOST", "0.0.0.0")
# 服务端口默认 8090，经环境变量 WEBSOCKET_PORT 读取。
SERVICE_PORT = int(os.getenv("WEBSOCKET_PORT", "8090"))
# 业务路由统一前缀，默认 /api/v1（与 backend / scheduler 调用方约定一致）。
API_PREFIX = os.getenv("WEBSOCKET_API_PREFIX", "/api/v1")

# 北京时间时区（UTC+8，规范 17）：用于日志时间戳显示。
_BEIJING_TZ = timezone(timedelta(hours=8))


def _beijing_time_converter(timestamp: float):
    """将时间戳转换为北京时间的 struct_time（供日志 Formatter 使用，规范 17）。

    Args:
        timestamp: Unix 时间戳（秒）。

    Returns:
        北京时间对应的 time.struct_time。
    """
    return datetime.fromtimestamp(timestamp, _BEIJING_TZ).timetuple()


def _setup_logging() -> None:
    """初始化全局日志配置（禁用 debug，时间用北京时间，规范 17 / 38）。

    根因：未配置日志时，自定义 logger 默认级别为 WARNING，所有 ``logger.info``
    （含心跳、连接、启动日志）都会被丢弃，控制台只剩 uvicorn 自带日志。此处统一
    配置 root logger，级别经环境变量 ``WEBSOCKET_LOG_LEVEL`` 读取（默认 INFO）。
    """
    level_name = os.getenv("WEBSOCKET_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    # 禁用 debug：即便环境变量误配为 DEBUG 也提升为 INFO（规范 38）。
    if level < logging.INFO:
        level = logging.INFO

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 日志时间统一显示为北京时间（规范 17）。
    formatter.converter = _beijing_time_converter

    root = logging.getLogger()
    root.setLevel(level)
    # 避免重复添加 handler（reload / 多次导入场景）。
    if not any(getattr(h, "_pdd_ws_handler", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler._pdd_ws_handler = True  # 标记本服务安装的 handler，避免重复添加
        root.addHandler(handler)


# 模块加载即初始化日志，确保「python main.py」与「uvicorn main:app」两种启动方式
# 下自定义 logger 的 info 日志均可正常输出。
_setup_logging()

logger = logging.getLogger(SERVICE_NAME)


def _install_asyncio_exception_filter() -> None:
    """为运行中的事件循环安装过滤型异常处理器，压制连接关闭后的无害噪音。

    根因：底层 ``websockets`` 库的接收任务（transfer_data）在连接被对端关闭时会以
    ``ConnectionClosedError`` 结束；当我们的消息循环已转入重连流程、未再去 await
    该任务持有的 Future 时，asyncio 会在该 Future 被 GC 时打出
    「Future exception was never retrieved」并附带整段堆栈。该异常对业务无影响
    （重连逻辑已正确处理断开），但会以 ERROR + 堆栈污染日志，违反「控制台不允许
    出现报错信息」（规范 4）。

    处理：仅过滤「未被检索」的连接关闭类异常（ConnectionClosed 家族），其余异常
    一律交还默认处理器，避免掩盖真正的问题。参照 Customer-Agent 的 app.py 思路。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中的事件循环（理论上不会发生在 lifespan 内），安全跳过。
        return

    def _filtered_handler(running_loop, context: dict) -> None:
        exc = context.get("exception")
        # 仅压制连接关闭类异常的「未检索」噪音；其余交默认处理器。
        if isinstance(exc, ws_exceptions.ConnectionClosed):
            return
        running_loop.default_exception_handler(context)

    loop.set_exception_handler(_filtered_handler)
    logger.info("已安装 asyncio 异常过滤器：压制连接关闭后的无害 Future 噪音")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理。

    启动阶段：装配消息处理全链路（任务 19.2）——拼多多连接（PDDChannel 收消息）
    经 FIFO 队列触发 MessageConsumer，串联「解析 → 决策链 → 知识库/AI → 发送
    回复/商品卡片降级 → 记消息/风控日志 → 系统事件通知（经 backend）」。具体连接
    由 ``channel_pdd.connection_manager.start_channel`` 按店铺创建、启动并登记到
    ``connection_registry``（由 backend 经 HTTP 触发启停，需求 5.1 / 5.3 / 3.5）。
    关闭阶段：停止全部已登记连接并清理资源。

    说明：本服务以「按需启停店铺连接」为运行模型，连接的创建在收到 backend 的
    启动指令时进行；此处仅完成全链路装配就绪与启动/关闭日志。
    """
    logger.info("%s 启动中，监听 %s:%s", SERVICE_NAME, SERVICE_HOST, SERVICE_PORT)
    # 安装 asyncio 异常过滤器，压制 websockets 连接关闭后「未检索 Future」的无害噪音
    # （重连逻辑已正确处理断开，详见 _install_asyncio_exception_filter 文档，规范 4）。
    _install_asyncio_exception_filter()
    from login.qr_warm_browser import start_warm_browser, stop_warm_browser
    start_warm_browser()
    # 端到端链路装配收口于 channel_pdd.connection_manager（PDDChannel + MessageConsumer），
    # 由 routes.connections 等服务间接口按店铺触发启停，连接登记在 connection_registry。
    # 服务启动时自动拉起全部「已启用」店铺的连接（参照 Customer-Agent「启动所有」），
    # 使重启后无需手动逐个启动；读库 / 单店铺启动失败均被兜底，不阻断服务启动。
    try:
        from channel_pdd import connection_manager

        started = await connection_manager.start_enabled_channels()
        logger.info("%s 启动完成，已自动拉起 %d 个店铺连接", SERVICE_NAME, started)
    except Exception as exc:  # noqa: BLE001 - 自动拉起失败不阻断服务启动
        logger.error("自动拉起已启用店铺连接失败（不影响服务启动）: %s", exc)
    from agent.knowledge_curation import run as run_curation
    curation_task = asyncio.create_task(run_curation(), name='human-knowledge-curation')
    from agent.context_compression import run as run_compression
    compression_task = asyncio.create_task(run_compression(), name='context-compression')
    try:
        yield
    finally:
        compression_task.cancel()
        await asyncio.gather(compression_task, return_exceptions=True)
        curation_task.cancel()
        await asyncio.gather(curation_task, return_exceptions=True)
        await stop_warm_browser()
    logger.info("%s 关闭中，正在释放资源", SERVICE_NAME)
    # 优雅停止：断开全部已登记的店铺连接，避免悬挂任务。
    await _shutdown_all_connections()


async def _shutdown_all_connections() -> None:
    """关闭并注销全部已登记的店铺长连接（服务关闭时调用）。"""
    from channel_pdd import connection_registry

    # 复制键列表后逐个断开（disconnect 会注销，避免遍历时修改字典）。
    for owner_user_id, shop_id in list(connection_registry._CONNECTIONS.keys()):
        try:
            await connection_registry.disconnect(shop_id, owner_user_id)
        except Exception as exc:  # noqa: BLE001 - 关闭阶段不抛异常
            logger.warning("关闭店铺连接出错: shop_id=%s, %s", shop_id, exc)


# 创建 FastAPI 应用
app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)

# 配置 CORS（跨域由前端经环境变量配置的地址访问，禁止写死 localhost）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 websocket 服务对外路由聚合器，统一前缀（默认 /api/v1）：连接断开、商品拉取、
# 手动发送消息、Cookie 刷新等接口经此对外暴露，供 backend / scheduler 服务间调用
# （任务 19.1，地址经环境变量配置、禁止写死 localhost——规范 21 / 需求 25.4）。
app.include_router(api_router, prefix=API_PREFIX)


@app.get("/health")
async def health_check():
    """
    健康检查接口（占位）。

    返回项目统一响应体结构 {code, success, message, data}（规范 1-3）：
    - code: 业务码，正常为 0；
    - success: 是否成功；
    - message: 中文提示信息；
    - data: 业务数据，此处返回服务运行状态。
    后续可在 data 中补充数据库连接状态、活跃连接数等运行指标。
    """
    return {
        "code": 0,
        "success": True,
        "message": "服务运行正常",
        "data": {
            "service": SERVICE_NAME,
            "status": "running",
        },
    }


def run_server():
    """启动 ASGI 服务（供 main.py 的 __main__ 块调用）。"""
    import uvicorn

    # host 默认 0.0.0.0、端口经环境变量配置，禁止写死 localhost（规范 21）。
    uvicorn.run(
        "main:app",
        host=SERVICE_HOST,
        port=SERVICE_PORT,
        reload=False,
    )
