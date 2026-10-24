"""
文件用途：拼多多渠道核心组件包（channel_pdd.core）。

承载拼多多渠道的底层核心能力：
- base_request：拼多多基础请求层 BaseRequest（统一 post/get、重试、会话过期
  error_code=43001 自动刷新重登、anti-content 签名缺失/失效检测）；
- anti_content：anti-content 动态风控签名的缺失与失效检测（需求 26.1/26.2）；
- credential_store：店铺 Cookie/密码凭据的加载、解密与加密回写（需求 3.6/8.6）；
- connection_status：连接状态枚举 ConnectionState 与线程安全的连接状态管理器
  ConnectionStatusManager（每店铺维护 已连接/连接中/断开/重连中/错误，需求 5.7）；
- connection_state_machine：连接状态判定与状态机 ConnectionStateMachine（歧义/断开
  判定为需重连置「重连中」、达上限置「错误」并恰好一条风控日志，需求 5.4/5.5）。

本模块统一导出基础请求层、签名检测与连接状态机的常用符号，供 channel_pdd 各处复用。
"""
from channel_pdd.core.anti_content import (
    AntiContentMissingError,
    SIGNATURE_MISSING_MESSAGE,
    extract_anti_content,
    has_valid_anti_content,
    is_signature_invalid_response,
)
from channel_pdd.core.base_request import (
    SESSION_EXPIRED_ERROR_CODE,
    SESSION_EXPIRED_KEYWORD,
    BaseRequest,
)
from channel_pdd.core.connection_state_machine import (
    RECONNECT_FAIL_RISK_TYPE,
    RECONNECT_LIMIT_REASON,
    ConnectionStateMachine,
    LinkObservation,
    ReconnectPolicy,
    classify_link_observation,
)
from channel_pdd.core.connection_status import (
    ConnectionState,
    ConnectionStatus,
    ConnectionStatusManager,
)

__all__ = [
    "BaseRequest",
    "SESSION_EXPIRED_ERROR_CODE",
    "SESSION_EXPIRED_KEYWORD",
    "AntiContentMissingError",
    "SIGNATURE_MISSING_MESSAGE",
    "extract_anti_content",
    "has_valid_anti_content",
    "is_signature_invalid_response",
    "ConnectionState",
    "ConnectionStatus",
    "ConnectionStatusManager",
    "ConnectionStateMachine",
    "LinkObservation",
    "ReconnectPolicy",
    "classify_link_observation",
    "RECONNECT_FAIL_RISK_TYPE",
    "RECONNECT_LIMIT_REASON",
]
