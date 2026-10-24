"""OIN 平台账号入口；已有拼多多适配与外部闲鱼入口明确分开。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.data_scope import build_owner_condition, load_user_scope
from app.core.errors import BusinessError
from app.services import chat_workspace_service
from common.db.session import get_db
from common.models.shop_models import Shop
from common.schemas.common import error_response, success_response

router = APIRouter(prefix='/platforms', tags=['平台与账号'])


def viewer(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not permission.check(user, 'shop', 'view', session=db):
        raise BusinessError(40300, '无平台账号访问权限')
    return user


@router.get('')
def catalog(user=Depends(viewer), db: Session = Depends(get_db)):
    conditions = [Shop.merged_into_shop_pk.is_(None)]
    owner = build_owner_condition(load_user_scope(db, user.id), Shop.owner_user_id)
    if owner is not None:
        conditions.append(owner)
    count = db.scalar(select(func.count()).select_from(Shop).where(*conditions)) or 0
    return success_response(data={'list': [
        {'code': 'pdd', 'name': '拼多多', 'mode': 'integrated',
         'account_count': count, 'accounts_path': '/shops?platform=pdd',
         'chat_path': '/online-chat', 'execution': 'server',
         'login_methods': ['qr', 'password', 'cookie']},
        {'code': 'xianyu', 'name': '闲鱼', 'mode': 'external',
         'account_count': None, 'external_url': 'https://xy.example.invalid/',
         'login_methods': []},
    ]})


@router.get('/{platform_code}/accounts')
def accounts(platform_code: str, page: int = Query(1, ge=1),
             page_size: int = Query(20, ge=1, le=100),
             q: str = Query('', max_length=128),
             status: Optional[int] = Query(None, ge=0, le=1),
             ai_enabled: Optional[bool] = None, connected: Optional[bool] = None,
             user=Depends(viewer), db: Session = Depends(get_db)):
    # 尚未接入的入口绝不回落到拼多多查询，避免跨平台账号身份混淆。
    if platform_code != 'pdd':
        return error_response(40400, '该平台账号尚未接入 OIN，请使用其独立后台')
    result = chat_workspace_service.list_shops(
        db, user, page=page, page_size=page_size, q=q.strip(), status=status,
        ai_enabled=ai_enabled, connected=connected)
    if result.success:
        result.data['platform_code'] = platform_code
        for account in result.data['list']:
            account['platform_code'] = platform_code
            account['account_ref'] = f'pdd:{account["id"]}'
    return result
