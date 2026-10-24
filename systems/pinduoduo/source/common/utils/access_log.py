"""浏览器WS需要查询参数鉴权；访问日志不保留查询中的令牌。"""
import logging
import re

_TOKENS = re.compile(r'([?&](?:token|access_token)=)[^&#\s"]+', re.IGNORECASE)


class AccessTokenFilter(logging.Filter):
    def filter(self, record):
        def clean(value):
            return _TOKENS.sub(r'\1[redacted]', value) if isinstance(value, str) else value
        record.msg = clean(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(clean(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: clean(value) for key, value in record.args.items()}
        return True
