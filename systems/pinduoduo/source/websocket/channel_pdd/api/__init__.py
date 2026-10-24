"""
文件用途：拼多多 HTTP 接口封装包（channel_pdd.api）。

承载拼多多商家后台各 HTTP 接口的封装：
- get_user_info：登录用户信息查询（id/username/mall_id，需求 4.1/4.3）；
- get_shop_info：店铺信息查询（mallId/mallName/mallLogo，需求 4.1/4.3）；
- get_token：建立 WebSocket 连接所需 Token 获取（需求 4.8）；
- send_message：发送文本/图片/商品卡片、客服列表、会话转移转人工（需求 16.x）；
- 后续任务补充：商品列表/详情等。

各接口均复用 channel_pdd.core.base_request.BaseRequest（统一请求/重试/会话过期
自动重登/anti-content 签名检测）。
"""
from channel_pdd.api.get_chat_history import GetChatHistory
from channel_pdd.api.get_conversations import GetConversations
from channel_pdd.api.get_shop_info import GetShopInfo
from channel_pdd.api.get_token import GetToken
from channel_pdd.api.get_user_info import GetUserInfo
from channel_pdd.api.send_message import SendMessage

__all__ = [
    "GetShopInfo",
    "GetUserInfo",
    "GetToken",
    "SendMessage",
    "GetConversations",
    "GetChatHistory",
]
