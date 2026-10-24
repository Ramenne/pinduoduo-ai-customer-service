# -*- coding: utf-8 -*-
"""
common.utils 工具子包
=====================
本文件为 common 公共库 utils 子包的初始化文件。

utils 子包提供供各服务复用的通用工具，后续任务中包含：
- time_utils：北京时间（Asia/Shanghai, UTC+8）获取、UTC↔北京时间互转与格式化
- security：密码哈希存储与校验、JWT 签发 / 校验 / 失效（不返回明文 / 哈希）
- pagination：分页响应结构 {list, total, page, page_size}（默认 page_size=20，
  校验可选值 10/20/50/100）
- 日志、分词等通用工具

具体实现在后续任务（2.2 / 2.4 / 2.15）中完成。
"""
