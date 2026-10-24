"""所有数据均为测试假值；传输为函数替身，不启动浏览器、不访问平台。"""
import json

import pytest
from login.qr_protocol import (CaptchaRequired, LoginRestricted, QrProtocol, Reply,
                               ProtocolError)


def make(replies, expected=None, headers=None, allow_missing=False):
    calls=[]
    def transport(method,path,h,b):
        calls.append((method,path,h,b))
        return replies.pop(0)
    obj=QrProtocol(transport=transport, headers_provider=headers or (lambda:{'Anti-Content':'test-only'}),
                   fingerprint={'navigator':{'ua':'test-environment'}},expected_shop_id=expected,
                   allow_missing_anti_content=allow_missing)
    return obj,calls


def generated(ticket='test-ticket'):
    return Reply(200,{'success':True,'result':{'uri':'https://w.url.cn/s/test?type=1&data=newTicket$'+ticket}})


def confirmed(cookie=True,shop='123'):
    return Reply(200,{'success':True,'result':{'status':3,'token':None,
        'userInfoVO':{'id':456,'mallId':shop}}},
        ('PASS_ID=test-only-value; Max-Age=864000; Path=/; Secure; HttpOnly',) if cookie else ())


def test_matched_ticket_and_fresh_headers():
    count=[]
    def headers(): count.append(1); return {'Anti-Content':'test-only-'+str(len(count))}
    obj,calls=make([generated(),Reply(200,{'success':True,'result':{'status':1}}),confirmed()],headers=headers)
    obj.generate()
    assert obj.poll().state=='waiting_observed'
    result=obj.poll()
    assert result.state=='confirmed_response_only' and result.shop_id=='123'
    assert result.cookie=='PASS_ID=test-only-value'
    assert set(calls[0][3])=={'fingerprint'}
    assert calls[1][3]['data']=='newTicket$test-ticket'
    assert len(count)==3 and calls[0][2]['anti-content']!=calls[1][2]['anti-content']
    assert 'test-only-value' not in repr(result)
    with pytest.raises(ProtocolError):obj.poll()


def test_missing_dynamic_parameters_stops_before_transport():
    obj,calls=make([generated()],headers=lambda:{})
    with pytest.raises(ProtocolError):obj.generate()
    assert not calls


def test_confirmation_without_new_cookie_is_not_success():
    obj,_=make([generated(),confirmed(False)])
    obj.generate()
    with pytest.raises(ProtocolError):obj.poll()


def test_wrong_shop_stops_and_closes():
    obj,_=make([generated(),confirmed()],expected='different-shop')
    obj.generate()
    with pytest.raises(ProtocolError):obj.poll()
    with pytest.raises(ProtocolError):obj.poll()


def test_unknown_status_not_invented():
    obj,_=make([generated(),Reply(200,{'success':True,'result':{'status':2}})])
    obj.generate()
    assert obj.poll().state=='unmapped_status'


def test_separate_rounds_never_share_ticket():
    a,ca=make([generated('a'),Reply(200,{'success':True,'result':{'status':1}})])
    b,cb=make([generated('b'),Reply(200,{'success':True,'result':{'status':1}})])
    a.generate();b.generate();a.poll();b.poll()
    assert ca[1][3]['data']!=cb[1][3]['data']


def test_conflicting_cookie_rejected():
    r=confirmed();r.set_cookie+=('PASS_ID=other-test-value; Path=/',)
    obj,_=make([generated(),r]);obj.generate()
    with pytest.raises(ProtocolError):obj.poll()


# ---------------------------------------------------------------------------
# 2026-10-16 真实抓包补充：54001 图形验证码形态与 Anti-Content 逐请求刷新
# ---------------------------------------------------------------------------
def captcha_reply():
    return Reply(200, {'success': False, 'errorCode': 54001,
                       'errorMsg': '需要验证图形验证码',
                       'result': {'verifyAuthToken': 'test-only-token'}})


def test_captcha_gate_is_distinguishable_from_business_failure():
    """54001+verifyAuthToken 必须是可单独识别的形态，不能混进"业务失败"或"超时"。"""
    obj, _ = make([captcha_reply()])
    with pytest.raises(ProtocolError) as e:
        obj.generate()
    assert isinstance(e.value, CaptchaRequired)
    assert '绕过' in str(e.value)


def test_captcha_error_message_does_not_leak_token():
    """异常文案里不得出现 verifyAuthToken 值。"""
    obj, _ = make([captcha_reply()])
    with pytest.raises(ProtocolError) as e:
        obj.generate()
    assert 'test-only-token' not in str(e.value)


def test_error_code_54001_without_verify_token_is_plain_failure():
    """只有错误码而没有 verifyAuthToken 时不得猜成验证码，仍走通用失败。"""
    obj, _ = make([Reply(200, {'success': False, 'errorCode': 54001,
                               'errorMsg': 'x', 'result': {}})])
    with pytest.raises(ProtocolError) as e:
        obj.generate()
    assert not isinstance(e.value, CaptchaRequired)


def test_anti_content_is_counted_per_request_and_reuse_is_visible():
    """真实流量里 Anti-Content 每请求都不同；提供器复用必须能在 stats 里看到。"""
    reused = make([generated(), Reply(200, {'success': True, 'result': {'status': 1}}),
                   confirmed()], headers=lambda: {'Anti-Content': 'same-every-time'})
    obj, _ = reused
    obj.generate(); obj.poll(); obj.poll()
    assert obj.stats['requests'] == 3
    assert obj.stats['anti_content_distinct'] == 1
    assert obj.stats['anti_content_reused'] == 2


def test_fresh_provider_shows_no_reuse():
    count = []
    def headers():
        count.append(1)
        return {'Anti-Content': 'fresh-' + str(len(count))}
    obj, _ = make([generated(), confirmed()], headers=headers)
    obj.generate(); obj.poll()
    assert obj.stats['anti_content_reused'] == 0
    assert obj.stats['anti_content_distinct'] == 2


def test_stats_never_contain_anti_content_values():
    obj, _ = make([generated(), confirmed()])
    obj.generate(); obj.poll()
    assert 'test-only' not in json.dumps(obj.stats)


# ---------------------------------------------------------------------------
# 2026-10-19 页面静态资源核查补充：status==5 的平台自述失败；确认态限登分支
# ---------------------------------------------------------------------------
def test_platform_status_5_is_distinguishable_failure():
    """页面把 status==5 单独当作平台自述失败（带 message），不得混进未观察状态。"""
    obj, _ = make([generated(), Reply(200, {'success': True, 'result': {
        'status': 5, 'message': '系统繁忙，请通过账密登录'}})])
    obj.generate()
    result = obj.poll()
    assert result.state == 'platform_declared_failure'
    assert result.shop_id == '' and result.cookie == ''


def test_platform_status_5_tolerates_missing_message():
    obj, _ = make([generated(), Reply(200, {'success': True, 'result': {'status': 5}})])
    obj.generate()
    assert obj.poll().state == 'platform_declared_failure'


def test_confirmed_but_login_restricted_is_not_delivered_as_success():
    """status==3 只代表手机已确认；loginLimitStatus==2（限登）要手机实人认证，
    绝不能当作成功交付凭据。"""
    restricted = Reply(200, {'success': True, 'result': {
        'status': 3, 'loginLimitStatus': 2, 'maskMobile': '138****0000',
        'identityVerifyURL': 'https://example.invalid/identity?token=test-only-value',
        'userInfoVO': {'id': 456, 'mallId': 123}}},
        ('PASS_ID=test-only-value; Path=/; Secure; HttpOnly',))
    obj, _ = make([generated(), restricted])
    obj.generate()
    with pytest.raises(ProtocolError) as e:
        obj.poll()
    assert isinstance(e.value, LoginRestricted)
    assert 'test-only-value' not in str(e.value)
    with pytest.raises(ProtocolError):
        obj.poll()  # 已经关闭，不能再取


def test_confirmed_with_null_login_limit_status_still_succeeds():
    """回归：确认态里 loginLimitStatus 为 null（本机实测形态）时仍按正常确认处理。"""
    obj, _ = make([generated(), Reply(200, {'success': True, 'result': {
        'status': 3, 'loginLimitStatus': None, 'identityVerifyURL': None,
        'userInfoVO': {'id': 456, 'mallId': 123}}},
        ('PASS_ID=test-only-value; Path=/; Secure; HttpOnly',))])
    obj.generate()
    assert obj.poll().state == 'confirmed_response_only'


# ---------------------------------------------------------------------------
# 2026-10-19 研发顺序第3步：Anti-Content 必要性未证，允许显式开启"无该头"对照
# ---------------------------------------------------------------------------
def test_missing_anti_content_only_allowed_by_explicit_opt_in():
    """默认仍 fail-closed；只有显式开启才允许不发 Anti-Content，并记入 stats。"""
    obj, calls = make([generated()], headers=lambda: {}, allow_missing=True)
    obj.generate()
    assert 'anti-content' not in calls[0][2]
    assert obj.stats['anti_content_missing'] == 1
    assert obj.stats['anti_content_distinct'] == 0


def test_missing_anti_content_flag_rejects_non_boolean():
    with pytest.raises(ProtocolError):
        QrProtocol(transport=lambda *a: None, headers_provider=lambda: {},
                   fingerprint={'navigator': {}}, allow_missing_anti_content=1)

