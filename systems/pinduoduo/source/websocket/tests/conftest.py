# -*- coding: utf-8 -*-
"""
websocket 测试公共夹具与路径配置
================================
本文件用途：保证测试既能以 `channel_pdd.*` 形式导入 websocket 服务内部模块，
又能以 `common.*` 形式导入公共库。为此把「websocket 服务目录」与「仓库根目录」
均加入 sys.path（与 main.py 中的 sys.path 注入口径一致）。
"""
import os
import sys

# websocket 服务目录（本文件父目录的父目录 = tests 的父目录 = websocket/）
_WS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 仓库根目录 = websocket 目录的父目录（用于 import common.*）
_REPO_ROOT = os.path.dirname(_WS_DIR)

for _path in (_WS_DIR, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# 消费器已使用持久收件/外发账；相关单元测试显式启用隔离数据库。
import pytest

@pytest.fixture
def delivery_db(monkeypatch):
    from sqlalchemy import create_engine, BigInteger
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from common.models import Base, Shop
    from common.models.chat_workspace_models import ChatSyncCheckpoint  # 注册恢复游标表，不依赖测试导入顺序
    from common.db import session as db
    @compiles(BigInteger, 'sqlite')
    def bigint_as_integer(element, compiler, **kw):return 'INTEGER'
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False,autoflush=False)
    monkeypatch.setattr(db,'get_session_factory',lambda:factory)
    with factory.begin() as s:s.add(Shop(id=1,shop_id='shop_1',owner_user_id=9,status=1))
    monkeypatch.setattr('channel_pdd.reliable_delivery.read_baseline',lambda *a:{'ids':[],'at':0})
    yield factory
    engine.dispose()
