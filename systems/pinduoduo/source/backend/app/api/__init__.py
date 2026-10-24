"""backend API 层包。

本包用途：承载对外 REST 路由（routes/ 子包）与请求级依赖（deps.py）。
后续任务在此聚合各业务路由为统一的 api_router，并由 _bootstrap.py 挂载。
"""
