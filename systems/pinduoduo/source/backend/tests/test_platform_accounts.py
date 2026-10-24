"""平台边界、账号归属及旧登录入口的必要回归。"""
from common.models.shop_models import Shop
from common.models.user_models import SysPermission, SysRolePermission
from sqlalchemy import select
from .test_chat_workspace import env
from .test_chat_api import chat_env, _login_token, _auth_header


def grant_shop_view(session, seed):
    permission = session.scalar(select(SysPermission).where(
        SysPermission.resource_key == 'shop', SysPermission.action == 'view'))
    if permission is None:
        permission = SysPermission(resource_key='shop', action='view')
        session.add(permission); session.flush()
    session.add(SysRolePermission(role_id=seed['role_manager_id'], permission_id=permission.id))
    session.flush()


def test_catalog_counts_only_visible_unmerged_accounts(client, db_session, env, test_users, seed_permissions):
    grant_shop_view(db_session, seed_permissions)
    data, user = env
    db_session.add(Shop(shop_id='merged', shop_name='旧重复记录', owner_user_id=user.id,
                        merged_into_shop_pk=data['shop_a_pk']))
    db_session.flush()
    person = test_users['manager']
    headers = _auth_header(_login_token(client, person['username'], person['password']))
    body = client.get('/api/v1/platforms', headers=headers).json()
    assert body['success']
    pdd, xy = body['data']['list']
    assert pdd['code'] == 'pdd' and pdd['account_count'] == 1
    assert xy['mode'] == 'external' and xy['account_count'] is None
    assert xy['external_url'] == 'https://xy.example.invalid/'


def test_account_namespace_and_unknown_platform_never_fall_back(client, db_session, env, test_users, seed_permissions, monkeypatch):
    grant_shop_view(db_session, seed_permissions)
    from app.services import chat_workspace_service
    data, _ = env
    person = test_users['manager']
    headers = _auth_header(_login_token(client, person['username'], person['password']))
    body = client.get('/api/v1/platforms/pdd/accounts', headers=headers).json()
    assert body['success'] and body['data']['total'] == 1
    item = body['data']['list'][0]
    assert item['id'] == data['shop_a_pk']
    assert item['platform_code'] == 'pdd' and item['account_ref'] == f'pdd:{item["id"]}'
    assert not {'password', 'password_enc', 'cookies', 'cookies_enc'} & item.keys()
    monkeypatch.setattr(chat_workspace_service, 'list_shops', lambda *a, **k: (_ for _ in ()).throw(AssertionError('wrong adapter')))
    for platform in ['xianyu', 'unknown']:
        body = client.get(f'/api/v1/platforms/{platform}/accounts', headers=headers).json()
        assert not body['success'] and body['code'] == 40400


def test_catalog_requires_login_and_shop_permission(client, db_session, env, test_users):
    assert not client.get('/api/v1/platforms').json()['success']
    person = test_users['guest']
    headers = _auth_header(_login_token(client, person['username'], person['password']))
    for path in ['/api/v1/platforms', '/api/v1/platforms/pdd/accounts']:
        body = client.get(path, headers=headers).json()
        assert not body['success'] and body['code'] == 40300
