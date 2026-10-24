"""
文件用途：scheduler 定时任务服务的启动入口（最小入口桩，规范 32）。

本文件仅负责：
  1. 将「项目根目录」与「当前服务目录」加入 sys.path，使服务可共享 common 公共库；
  2. 从 _bootstrap 模块加载已装配好的 FastAPI 应用对象 app；
  3. 在 __main__ 下调用 run_server 拉起 HTTP 服务（默认端口 8091）。

真正的应用装配（创建 FastAPI 应用、健康检查、lifespan、定时任务调度等）
全部放在同目录的 _bootstrap.py 中，与 backend / websocket 服务保持一致。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# 将「当前服务目录」与「项目根目录」加入 Python 路径（必须先于业务导入执行）：
# - 当前目录：用于直接 import _bootstrap、tasks 等本服务模块；
# - 项目根目录：用于通过 sys.path 共享 common 公共库（规范：common 为共享库）。
current_dir = Path(__file__).parent
project_root = current_dir.parent
sys.path.insert(0, str(current_dir))
sys.path.insert(0, str(project_root))

# 显式从当前目录加载 _bootstrap 模块（兼容打包环境，避免相对导入歧义）。
_bootstrap_file = current_dir / "_bootstrap.py"
if _bootstrap_file.exists() and "_bootstrap" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("_bootstrap", str(_bootstrap_file))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["_bootstrap"] = _mod
    _spec.loader.exec_module(_mod)

from _bootstrap import app  # noqa: E402  （须在 sys.path 设置之后导入）

if __name__ == "__main__":
    # 仅作为脚本直接运行时才拉起 HTTP 服务，导入时不产生副作用。
    from _bootstrap import run_server

    run_server()
