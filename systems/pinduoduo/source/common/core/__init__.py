# -*- coding: utf-8 -*-
"""
common.core 核心配置子包
========================
本文件为 common 公共库 core 子包的初始化文件。

core 子包负责系统核心配置加载：
- config：从环境变量读取 MySQL/Redis 连接、JWT 密钥、各服务地址
  （WEBSOCKET_SERVICE_URL / BACKEND_WEB_SERVICE_URL / SCHEDULER_SERVICE_URL）、
  代理、Playwright（BROWSER_HEADLESS / MAX_CAPTCHA_CONCURRENT）、各服务端口、
  日志级别、静态目录、时区等配置，未提供时回退合理默认值。

约束：禁止在代码中写死 localhost，统一经环境变量管理（开发规范 21、需求 25.4）。
对外暴露 Settings、get_settings、reload_settings，供各服务统一获取配置。
"""
from common.core.config import Settings, get_settings, reload_settings

__all__ = ["Settings", "get_settings", "reload_settings"]
