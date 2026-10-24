# -*- coding: utf-8 -*-
"""
backend.app.api.routes.backup —— 数据库备份导出与导入恢复接口路由
================================================================
本文件用途：提供 backend 服务的「数据库备份导出与导入恢复」REST 接口（任务
8.3），满足需求 21.16：

- ``GET  /settings/backup/export``  导出数据库备份：生成备份文件（JSON）并以
  附件形式返回供管理员下载（需求 21.16 前半）。
- ``POST /settings/backup/import``  导入备份并恢复：校验上传的备份文件，在不
  破坏现有数据完整性的前提下按主键合并恢复（需求 21.16 后半）。

权限控制（需求 21.17）：备份导出 / 导入为管理员专属功能。所有接口先经统一权限
模块 ``permission`` 装配当前用户授权上下文并判断 ``is_admin``；非管理员一律拒绝
访问（导入返回 ``success=false``、``message``「无访问权限」的统一响应体；导出
返回 HTTP 403，避免把权限失败混入二进制附件流）。

接口约定（开发规范 1-3）：
- 导入接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message,
  data} 表达；导出接口为文件附件下载（二进制流），属统一响应体的合理例外。
- 业务逻辑委托 app.services.backup_service，路由层仅负责入参解析、鉴权依赖、
  权限判断与文件流封装；数据库会话经 common.db.session.get_db 注入。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_BACKUP_INVALID, CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import backup_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 备份路由：标签「数据库备份」便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["数据库备份"])


@router.get(
    "/settings/backup/export",
    summary="导出数据库备份（附件下载）",
)
def export_backup(
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """导出数据库备份文件供下载（需求 21.16）。仅管理员可访问（需求 21.17）。

    成功时返回 JSON 备份文件附件；非管理员返回 HTTP 403（避免把权限失败响应
    混入二进制下载流）。
    """
    context = permission.load_auth_context(current_user, db)
    if not context.is_admin:
        # 文件下载接口的权限失败以 403 表达，前端据此提示无访问权限（需求 21.17）。
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MSG_FORBIDDEN)

    filename, content = backup_service.generate_backup(db)
    # 文件名经 RFC 5987 编码，兼容中文 / 特殊字符（当前为 ASCII，作通用兜底）。
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": disposition},
    )


@router.post(
    "/settings/backup/import",
    response_model=ApiResponse,
    summary="导入备份并恢复",
)
async def import_backup(
    file: UploadFile = File(..., description="待恢复的备份文件（JSON）"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """校验并恢复上传的备份文件（需求 21.16）。仅管理员可访问（需求 21.17）。

    在不破坏现有数据完整性的前提下按主键合并恢复（插入或更新，绝不删除）；
    文件校验不通过时不写入任何数据并返回中文提示。
    """
    context = permission.load_auth_context(current_user, db)
    if not context.is_admin:
        return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)

    raw_bytes = await file.read()
    if not raw_bytes:
        return error_response(CODE_BACKUP_INVALID, "备份文件为空，无法恢复")
    return backup_service.restore_backup(db, raw_bytes)


__all__ = ["router"]
