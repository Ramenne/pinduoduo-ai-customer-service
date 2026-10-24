"""共享读取预算：单店串行、有界等待、失败退避；绝不重登或重放平台操作。"""
import threading
import time
from urllib.parse import urlsplit

READ_PATHS = frozenset(('/plateau/chat/list', '/plateau/chat/latest_conversations',
    '/latitude/goods/recommendGoods', '/glide/v2/mms/query/commit/on_shop/detail'))
_lock = threading.Lock()
_shops = {}


def guarded(url):
    parsed = urlsplit(url)
    return parsed.hostname == 'mms.pinduoduo.com' and parsed.path in READ_PATHS


def execute(owner, shop, callback):
    key = (owner, shop)
    with _lock:
        # 店铺连接数有界；不保存任何请求正文、Cookie或客户身份。
        state = _shops.setdefault(key, {'lock': threading.Lock(), 'next': 0, 'failures': 0})
    if not state['lock'].acquire(timeout=2):
        return {'success': False, 'error_msg': '店铺正在读取，请稍后重试'}
    try:
        wait = state['next'] - time.monotonic()
        if wait > 3:
            return {'success': False, 'error_msg': '平台读取已退避，请稍后再试'}
        if wait > 0:
            time.sleep(wait)
        result, status = callback()
        from channel_pdd.core.anti_content import is_signature_invalid_response
        rejected = (status in (401, 403, 429) or is_signature_invalid_response(result)
            or str((result or {}).get('error_code') or (result or {}).get('errorCode')) == '43001')
        message = str((result or {}).get('errorMsg') or (result or {}).get('error_msg') or '')
        rejected = rejected or any(word in message for word in ('验证码', '验证后', '频繁', '频率', '滑块', '人机验证'))
        if rejected:
            state['failures'] += 1
            delay = 300
        elif not result or result.get('success') is not True:
            state['failures'] = min(5, state['failures'] + 1)
            delay = min(120, 5 * 2 ** state['failures'])
        else:
            state['failures'] = 0
            delay = 2
        state['next'] = time.monotonic() + delay
        return result
    finally:
        state['lock'].release()
