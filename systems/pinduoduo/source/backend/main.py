"""
backend 服务启动入口（最小入口桩，规范 32）。

本文件用途：仅负责拉起 backend（HTTP API）服务，不承载任何业务装配逻辑。
真正的应用装配（创建 FastAPI 应用、CORS、挂载路由、lifespan、健康检查、
启动服务等）全部置于同目录下的 `_bootstrap.py`。

职责：
1. 先于业务导入设置 sys.path，引入项目根目录与本服务目录，使 `common`
   公共库与本服务包 `app` 均可被正常导入。
2. 显式从当前目录加载 `_bootstrap` 模块（兼容打包环境），暴露 ASGI 应用对象 `app`。
3. 作为脚本直接运行（`__main__`）时调用 `_bootstrap.run_server` 拉起服务。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# 将当前服务目录与项目根目录加入 Python 路径（必须先于任何业务导入执行）。
# - current_dir：本服务目录，便于导入本服务包 app 与 _bootstrap。
# - project_root：工作区根目录，便于通过 sys.path 共享 common 公共库。
current_dir = Path(__file__).parent
project_root = current_dir.parent
sys.path.insert(0, str(current_dir))
sys.path.insert(0, str(project_root))

# 显式从当前目录加载 _bootstrap（兼容 Nuitka / PyInstaller 等打包环境，
# 避免因工作目录不同导致的相对导入失败）。
_bootstrap_file = current_dir / "_bootstrap.py"
if _bootstrap_file.exists() and "_bootstrap" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("_bootstrap", str(_bootstrap_file))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["_bootstrap"] = _mod
    _spec.loader.exec_module(_mod)

from _bootstrap import app  # noqa: E402  # 暴露 ASGI 应用对象供 uvicorn 引用

if __name__ == "__main__":
    # 仅在作为脚本直接运行时拉起服务；装配逻辑见 _bootstrap.run_server。
    from _bootstrap import run_server

    run_server()
