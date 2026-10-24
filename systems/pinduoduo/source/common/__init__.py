# -*- coding: utf-8 -*-
"""
common 公共库包
==================
本文件为 common 公共库的包初始化文件。

common 是「拼多多自动回复」系统的公共库（非独立服务），被 backend、
websocket、scheduler 等各服务通过 sys.path 共享复用，统一提供：
- models：数据表结构模型
- db：数据库连接池、失败重试、会话管理、启动自检迁移
- schemas：统一响应体、分页、共享 DTO 与脱敏序列化
- services：跨服务复用的业务逻辑（数据字典、知识检索、服务间 HTTP 客户端等）
- utils：北京时间、安全（密码哈希 / JWT）、日志、分词等工具
- core：配置加载（环境变量优先，禁止写死 localhost）

公共库不包含服务启动入口（无 main.py）。
"""
