"""独立 HTTP 链路契约测试：MockTransport 不发网络请求，凭据均为假值。"""
import base64
import json

import httpx
import pytest

from login.qr_http_flow import HttpQrFlow
from login.qr_protocol import CaptchaRequired, ProtocolError


def flow(handler, verifier=None, headers=None, expected='123', **kwargs):
    return HttpQrFlow(
        headers_provider=headers or (lambda: {'Anti-Content': 'dummy-test-only'}),
        fingerprint={'navigator': {'ua': 'test-only'}},
        identity_verifier=verifier or (lambda c: {'shop_id':'123', 'user_id':'456'}),
        expected_shop_id=expected, http_transport=httpx.MockTransport(handler), **kwargs)


def qr_response():
    return httpx.Response(200, json={'success':True,'result':{
        'uri':'https://w.url.cn/s/test?type=1&data=newTicket$dummy-only'}})


def confirmation(cookie=True):
    headers=[('set-cookie','PASS_ID=dummy-new; Path=/; Secure; HttpOnly')]
    if cookie:
        headers.append(('set-cookie','api_uid=dummy-anonymous; Path=/; Secure'))
    return httpx.Response(200,headers=headers if cookie else [],json={
        'success':True,'result':{'status':3,'userInfoVO':{'mallId':123,'id':456}}})


def test_http_qr_to_verified_identity_preserves_response_cookies():
    requests=[]
    identities=[]
    def handler(request):
        requests.append(request)
        return qr_response() if len(requests)==1 else confirmation()
    def verify(cookies):
        identities.append(cookies)
        return {'shop_id':123,'user_id':456}
    obj=flow(handler,verify)
    image=obj.generate()
    assert base64.b64decode(image['qr_image_base64']).startswith(b'\x89PNG\r\n\x1a\n')
    result=obj.poll()
    assert result.state=='verified' and result.shop_id=='123'
    assert identities==[{'PASS_ID':'dummy-new','api_uid':'dummy-anonymous'}]
    assert json.loads(requests[1].content)['data']=='newTicket$dummy-only'
    assert 'cookie' not in requests[0].headers
    assert not obj._client.cookies
    with pytest.raises(ProtocolError):obj.poll()


def test_rejects_identity_mismatch_and_clears_credentials():
    calls=[]
    def handler(request):
        calls.append(request)
        return qr_response() if len(calls)==1 else confirmation()
    obj=flow(handler,lambda c:{'shop_id':'other','user_id':'456'})
    obj.generate()
    with pytest.raises(ProtocolError):obj.poll()
    assert obj._closed and not obj._client.cookies


def test_missing_new_pass_id_cannot_reach_identity_verifier():
    calls=[]
    def handler(request):
        calls.append(request)
        return qr_response() if len(calls)==1 else confirmation(False)
    obj=flow(handler,lambda c:pytest.fail('must not verify old cookies'))
    try:
        obj.generate()
        with pytest.raises(ProtocolError):obj.poll()
    finally:obj.close()


def test_provider_missing_stops_before_network():
    obj=flow(lambda r:pytest.fail('must not send request'),headers=lambda:{})
    try:
        with pytest.raises(ProtocolError):obj.generate()
    finally:obj.close()


def test_platform_challenge_is_not_replaced_with_browser():
    obj=flow(lambda r:httpx.Response(200,json={'success':False,'errorCode':54001,
        'result':{'verifyAuthToken':'dummy-only'}}))
    try:
        with pytest.raises(CaptchaRequired):obj.generate()
    finally:obj.close()


def test_provider_cannot_import_old_account_cookie():
    obj=flow(lambda r:pytest.fail('must not send request'),headers=lambda:{
        'Anti-Content':'dummy','Cookie':'PASS_ID=dummy-old'})
    try:
        with pytest.raises(ProtocolError):obj.generate()
    finally:obj.close()


def test_verification_header_uses_same_session_cookie_and_tracks_expiration():
    requests=[]
    def handler(request):
        requests.append(request)
        if request.url.path=='/login/':
            return httpx.Response(200, headers={'set-cookie':
                'msfe-pc-cookie-captcha-token=dummy%2Bverified; Path=/; Secure; Max-Age=600'})
        if request.url.path.endswith('/qrcode'):
            response=qr_response()
            response.headers['set-cookie']=(
                'msfe-pc-cookie-captcha-token=; Path=/; Secure; Max-Age=0')
            return response
        return confirmation()
    obj=flow(handler)
    obj.initialize(); obj.generate()
    assert requests[1].headers['VerifyAuthToken']=='dummy+verified'
    assert obj.poll().state=='verified'
    assert 'VerifyAuthToken' not in requests[2].headers


def test_expired_verification_cookie_does_not_create_header():
    requests=[]
    def handler(request):
        requests.append(request)
        if request.url.path=='/login/':
            return httpx.Response(200, headers={'set-cookie':
                'msfe-pc-cookie-captcha-token=dummy-expired; Path=/; Secure; Max-Age=0'})
        return qr_response()
    obj=flow(handler)
    try:
        obj.initialize(); obj.generate()
        assert 'VerifyAuthToken' not in requests[1].headers
    finally:obj.close()


def test_provider_cannot_supply_verification_header_without_current_cookie():
    obj=flow(lambda r:pytest.fail('must not send request'), headers=lambda:{
        'Anti-Content':'dummy', 'VerifyAuthToken':'dummy-external'})
    try:
        with pytest.raises(ProtocolError):obj.generate()
    finally:obj.close()


def test_anonymous_initialization_and_qr_share_one_cookie_jar():
    requests=[]
    def handler(request):
        requests.append(request)
        if request.url.path=='/login/':
            return httpx.Response(200,headers={'set-cookie':'api_uid=dummy-anon; Path=/; Secure'},text='test page')
        return qr_response() if request.url.path.endswith('/qrcode') else confirmation()
    obj=flow(handler)
    assert obj.initialize()=={'initialized':True,'cookie_names':['api_uid']}
    obj.initialize()
    assert len(requests)==1  # 重复初始化幂等，避免重置同轮会话。
    obj.generate()
    assert requests[1].headers['cookie']=='api_uid=dummy-anon'
    assert requests[0].headers['user-agent']=='test-only'
    with pytest.raises(ProtocolError):obj.initialize()
    assert obj.poll().state=='verified'


@pytest.mark.parametrize('response',[
    httpx.Response(302,headers={'location':'https://other.example/'}),
    httpx.Response(403),
    httpx.Response(200,headers={'set-cookie':'PASS_ID=dummy-old; Path=/; Secure'}),
])
def test_bootstrap_rejects_redirect_errors_and_preexisting_login(response):
    calls=[]
    def handler(request):
        calls.append(request)
        return response
    obj=flow(handler)
    with pytest.raises(ProtocolError):obj.initialize()
    assert len(calls)==1 and obj._closed
    assert not obj._client.cookies


@pytest.mark.parametrize('field',['ua','userAgent'])
def test_real_user_agent_is_consistent_across_bootstrap_generate_and_poll(field):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path=='/login/':
            return httpx.Response(200,text='local fixture')
        return qr_response() if request.url.path.endswith('/qrcode') else confirmation()
    obj=HttpQrFlow(headers_provider=lambda:{'Anti-Content':'dummy-only'},
        fingerprint={'navigator':{field:'actual-test-UA'}},
        identity_verifier=lambda c:{'shop_id':'123','user_id':'456'},
        http_transport=httpx.MockTransport(handler))
    obj.initialize();obj.generate();assert obj.poll().state=='verified'
    assert len(calls)==3
    assert all(request.headers['user-agent']=='actual-test-UA' for request in calls)


def test_conflicting_user_agent_stops_without_network():
    with pytest.raises(ProtocolError):
        HttpQrFlow(headers_provider=lambda:{},fingerprint={'navigator':{'ua':'one','userAgent':'two'}},
                   identity_verifier=lambda c:{},http_transport=httpx.MockTransport(
                       lambda r:pytest.fail('must not send')))
    obj=flow(lambda r:pytest.fail('must not send'),headers=lambda:{
        'Anti-Content':'dummy-only','User-Agent':'different-from-fingerprint'})
    try:
        with pytest.raises(ProtocolError):obj.generate()
    finally:obj.close()


def test_provider_cannot_change_user_agent_after_bootstrap_when_fingerprint_omits_it():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,text='local bootstrap fixture')
    obj=HttpQrFlow(headers_provider=lambda:{'Anti-Content':'dummy-only','User-Agent':'late-UA'},
        fingerprint={'navigator':{}}, identity_verifier=lambda c:{},
        http_transport=httpx.MockTransport(handler))
    try:
        obj.initialize()
        with pytest.raises(ProtocolError):obj.generate()
        assert len(calls)==1 and calls[0].url.path=='/login/'
    finally:obj.close()


# ---------------------------------------------------------------------------
# 2026-10-19 研发顺序第3步：用户本人过完验证码后的 token 转存（不生成、不绕过）
# ---------------------------------------------------------------------------
def test_user_supplied_captcha_token_is_stored_encoded_and_sent_decoded():
    seen=[]
    def handler(request):
        seen.append(request)
        return qr_response()
    obj=flow(handler, captcha_token='solved+test/token')
    try:
        obj.generate()
        assert seen[0].headers['VerifyAuthToken']=='solved+test/token'
        assert 'solved%2Btest%2Ftoken' in seen[0].headers['cookie']
    finally:obj.close()


def test_captcha_token_special_characters_roundtrip():
    seen=[]
    def handler(request):
        seen.append(request)
        return qr_response()
    token='a+b/c= d&e'
    obj=flow(handler, captcha_token=token)
    try:
        obj.generate()
        assert seen[0].headers['VerifyAuthToken']==token
    finally:obj.close()


@pytest.mark.parametrize('bad',['','a\r\nb','a\x00b','x'*5000,1,b'x'])
def test_invalid_captcha_token_stops_before_network(bad):
    with pytest.raises(ProtocolError):
        flow(lambda r:pytest.fail('must not send'), captcha_token=bad)


def test_captcha_token_is_not_exposed_in_repr():
    obj=flow(lambda r:qr_response(), captcha_token='secret-test-token')
    try:
        assert 'secret-test-token' not in repr(obj)
    finally:obj.close()


def test_missing_anti_content_control_experiment_is_opt_in():
    seen=[]
    def handler(request):
        seen.append(request)
        return qr_response()
    obj=flow(handler, headers=lambda:{}, allow_missing_anti_content=True)
    try:
        obj.generate()
        assert 'anti-content' not in seen[0].headers
    finally:obj.close()


def test_platform_failure_message_is_surfaced_not_dropped():
    """status==5 的平台自述原因由协议层保留，HTTP 层不得丢弃。"""
    def handler(request):
        if request.url.path.endswith('/qrcode'):
            return qr_response()
        return httpx.Response(200,json={'success':True,'result':{
            'status':5,'message':'dummy-platform-reason'}})
    obj=flow(handler)
    try:
        obj.generate()
        result=obj.poll()
        assert result.state=='platform_declared_failure'
        assert result.message=='dummy-platform-reason'
    finally:obj.close()


def test_waiting_result_carries_empty_message():
    def handler(request):
        if request.url.path.endswith('/qrcode'):
            return qr_response()
        return httpx.Response(200,json={'success':True,'result':{'status':1}})
    obj=flow(handler)
    try:
        obj.generate()
        result=obj.poll()
        assert result.state=='waiting_observed' and result.message==''
    finally:obj.close()
