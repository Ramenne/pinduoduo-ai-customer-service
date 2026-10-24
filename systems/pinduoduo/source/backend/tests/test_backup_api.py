# -*- coding: utf-8 -*-
"""
backend.tests.test_backup_api —— 数据库备份导出与导入恢复接口单元测试
====================================================================
本文件用途：对 backend「数据库备份导出与导入恢复」接口（任务 8.3）进行单元测试，
覆盖需求 21.16 / 21.17 的核心验收场景：

- 导出备份（需求 21.16 前半）：管理员可下载 JSON 备份附件，内容含版本与各表数据；
- 导入恢复合并语义（需求 21.16 后半）：按主键 upsert，新增插入、已存在更新，且
  不在备份中的现有记录原样保留（不破坏现有数据完整性）；
- 备份文件校验（需求 21.16）：非法文件不写入任何数据并返回中文提示；
- 权限控制（需求 21.17）：非管理员导出返回 403、导入返回 success=false「无访问权限」。

测试方案：pytest + FastAPI TestClient + SQLite 内存库（夹具见 conftest.py）。
导入接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message, data}
表达；导出接口为文件附件下载（二进制流）。
"""
from __future__ import annotations

import json

from app.core.business_codes import CODE_BACKUP_INVALID, CODE_FORBIDDEN
from common.db.repository import Repository
from common.models.user_models import SysUser
from common.utils.security import hash_password

# 业务路由统一前缀（与 _bootstrap.API_PREFIX 默认值一致）。
API_PREFIX = "/api/v1"
LOGIN_URL = f"{API_PREFIX}/login"
EXPORT_URL = f"{API_PREFIX}/settings/backup/export"
IMPORT_URL = f"{API_PREFIX}/settings/backup/import"


def _login(client, username: str, password: str) -> str:
    """登录并返回访问令牌字符串。"""
    resp = client.post(LOGIN_URL, json={"username": username, "password": password})
    return resp.json()["data"]["token"]


def _auth(token: str) -> dict:
    """构造带 Bearer 令牌的鉴权请求头。"""
    return {"Authorization": f"Bearer {token}"}


def test_export_backup_returns_json_attachment_for_admin(client, test_users):
    """导出备份：管理员获得 JSON 附件，载荷含版本号与 sys_user 表数据（需求 21.16）。"""
    admin = test_users["admin"]
    token = _login(client, admin["username"], admin["password"])

    resp = client.get(EXPORT_URL, headers=_auth(token))

    assert resp.status_code == 200
    # 附件下载头存在且文件名以备份前缀命名。
    disposition = resp.headers.get("content-disposition", "")
    assert "attachment" in disposition
    assert "pdd_backup_" in disposition

    payload = json.loads(resp.content.decode("utf-8"))
    assert payload["version"] == "1.0"
    assert "pdd_sys_user" in payload["tables"]
    # 预置的管理员账号应出现在备份中。
    usernames = [row["username"] for row in payload["tables"]["pdd_sys_user"]]
    assert admin["username"] in usernames


def test_export_backup_forbidden_for_non_admin(client, test_users):
    """非管理员导出备份：返回 HTTP 403（需求 21.17）。"""
    guest = test_users["guest"]
    token = _login(client, guest["username"], guest["password"])

    resp = client.get(EXPORT_URL, headers=_auth(token))

    assert resp.status_code == 403


def test_import_backup_merges_without_deleting_existing(client, test_users, db_session):
    """导入恢复合并语义：更新已存在 + 插入新行，且现有数据原样保留（需求 21.16）。"""
    admin = test_users["admin"]
    token = _login(client, admin["username"], admin["password"])

    # 先导出当前备份作为基线。
    export_resp = client.get(EXPORT_URL, headers=_auth(token))
    payload = json.loads(export_resp.content.decode("utf-8"))

    # 构造一份「仅含 pdd_sys_user 表」的备份：更新管理员用户名 + 新增一个用户。
    admin_row = next(
        row for row in payload["tables"]["pdd_sys_user"] if row["id"] == admin["id"]
    )
    admin_row["username"] = "admin_renamed"
    new_user_id = 999001
    new_row = dict(admin_row)
    new_row["id"] = new_user_id
    new_row["username"] = "restored_user"
    new_row["password_hash"] = hash_password("restored-password-123")

    restore_payload = {
        "version": payload["version"],
        "generated_at": payload["generated_at"],
        "tables": {"pdd_sys_user": [admin_row, new_row]},
    }
    content = json.dumps(restore_payload, ensure_ascii=False).encode("utf-8")

    resp = client.post(
        IMPORT_URL,
        headers=_auth(token),
        files={"file": ("backup.json", content, "application/json")},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is True
    assert body["data"]["inserted"] == 1
    assert body["data"]["updated"] == 1

    # 管理员用户名被更新。
    repo = Repository(SysUser, db_session)
    updated_admin = repo.get(admin["id"])
    db_session.refresh(updated_admin)
    assert updated_admin.username == "admin_renamed"

    # 新用户被插入。
    inserted = repo.get(new_user_id)
    assert inserted is not None
    assert inserted.username == "restored_user"

    # 未出现在备份中的现有用户（guest）原样保留，未被删除（需求 21.16 完整性）。
    guest = test_users["guest"]
    assert repo.get(guest["id"]) is not None


def test_import_backup_rejects_invalid_file_without_writing(client, test_users):
    """导入非法文件：返回 success=false 中文提示，不写入任何数据（需求 21.16）。"""
    admin = test_users["admin"]
    token = _login(client, admin["username"], admin["password"])

    content = b"this is not a valid backup json"
    resp = client.post(
        IMPORT_URL,
        headers=_auth(token),
        files={"file": ("bad.json", content, "application/json")},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_BACKUP_INVALID
    assert body["data"] is None


def test_import_backup_rejects_unknown_table(client, test_users):
    """导入含未知表的备份：校验失败并返回中文提示（需求 21.16）。"""
    admin = test_users["admin"]
    token = _login(client, admin["username"], admin["password"])

    restore_payload = {
        "version": "1.0",
        "generated_at": "2024-01-01T00:00:00",
        "tables": {"not_a_real_table": [{"id": 1}]},
    }
    content = json.dumps(restore_payload, ensure_ascii=False).encode("utf-8")
    resp = client.post(
        IMPORT_URL,
        headers=_auth(token),
        files={"file": ("backup.json", content, "application/json")},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_BACKUP_INVALID


def test_import_backup_forbidden_for_non_admin(client, test_users):
    """非管理员导入备份：返回 success=false、message「无访问权限」（需求 21.17）。"""
    guest = test_users["guest"]
    token = _login(client, guest["username"], guest["password"])

    content = json.dumps({"version": "1.0", "tables": {}}).encode("utf-8")
    resp = client.post(
        IMPORT_URL,
        headers=_auth(token),
        files={"file": ("backup.json", content, "application/json")},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["success"] is False
    assert body["code"] == CODE_FORBIDDEN
