# -*- coding: utf-8 -*-
"""
common.services 公共业务子包
============================
本文件为 common 公共库 services 子包的初始化文件。

services 子包用于实现可被各服务复用的跨服务业务逻辑，包含：
- 数据字典服务 sys_dict（按类型查询中文文案、登记枚举初始数据）——已实现
  （dict_service.py + dict_seed_data.py，任务 2.14）
- 知识检索 kb.search（限定店铺、客服知识仅启用、jieba 分词、按 goods_id/标签筛选）
  ——已实现（kb_service.py，任务 6.9）
- 服务间 HTTP 客户端封装（地址经环境变量配置，禁止写死 localhost）

服务间 HTTP 客户端在后续任务（19.1）中完成。
"""
