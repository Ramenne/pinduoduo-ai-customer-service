"""
文件用途：账号密码登录包（login）。

承载基于 Playwright 的拼多多账号密码登录能力（任务 10.2 实现）：
- playwright_login：Playwright 启动 Chromium 进行账号密码登录（含人工验证等待）、
  使用已保存用户数据目录无头刷新 Cookie；并发与无头经环境变量
  BROWSER_HEADLESS / MAX_CAPTCHA_CONCURRENT 控制。
- cookie_import：手动粘贴 Cookie 文本的解析与格式校验（需求 4.3 / 4.4，纯逻辑）。

具体的店铺 / 用户信息抓取、登录态标记、Token 获取等编排逻辑位于
channel_pdd.pdd_login，本包仅聚焦浏览器交互与 Cookie 文本解析。
"""
from login.cookie_import import (
    COOKIE_FORMAT_INVALID_MESSAGE,
    parse_cookie_text,
    validate_cookie_text,
)
from login.playwright_login import login_with_password, refresh_with_user_data

__all__ = [
    "login_with_password",
    "refresh_with_user_data",
    "parse_cookie_text",
    "validate_cookie_text",
    "COOKIE_FORMAT_INVALID_MESSAGE",
]
