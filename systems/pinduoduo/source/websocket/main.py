"""
文件用途：websocket 长连接服务启动入口（最小入口桩，规范 32）。

本文件仅负责拉起 websocket 服务，不承载任何业务装配逻辑；
真正的应用装配（创建 FastAPI 应用、健康检查、lifespan、连接管理等）
全部放在同目录下的 _bootstrap.py（规范 32 在多服务架构下的落地方式）。

启动方式：
    python main.py            # 直接运行，调用 _bootstrap.run_server 拉起服务
    uvicorn main:app          # 以 ASGI 方式由外部进程拉起
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# 将当前服务目录与项目根目录加入 Python 路径（必须先于业务导入）：
# - 当前目录用于导入本服务内部模块（channel_pdd/engine/agent/login 等）；
# - 项目根目录用于引用 common 公共库（被各服务通过 sys.path 共享）。
current_dir = Path(__file__).parent
project_root = current_dir.parent
sys.path.insert(0, str(current_dir))
sys.path.insert(0, str(project_root))

# 显式从当前目录加载 _bootstrap（兼容打包环境下的模块定位）。
_bootstrap_file = current_dir / "_bootstrap.py"
if _bootstrap_file.exists() and "_bootstrap" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("_bootstrap", str(_bootstrap_file))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["_bootstrap"] = _mod
    _spec.loader.exec_module(_mod)

from _bootstrap import app  # noqa: E402  应用对象供 `uvicorn main:app` 使用

if __name__ == "__main__":
    # 直接运行时拉起服务，装配逻辑全部位于 _bootstrap.py
    from _bootstrap import run_server

    run_server()
