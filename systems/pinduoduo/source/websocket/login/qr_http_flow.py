"""独立 HTTP 扫码实验链路；未接生产，不启动浏览器。

正常动态参数提供器与身份核验器必须由调用方明确提供。没有提供器就不发送请求，
不读取旧抓包、不自动切换到浏览器。不要将替身测试通过描述为平台登录已跑通。
"""
import base64
from dataclasses import dataclass, field
from io import BytesIO
from http.cookies import SimpleCookie
from urllib.parse import quote, unquote

import httpx
import qrcode

from login.qr_protocol import QrProtocol, Reply, ProtocolError


@dataclass(repr=False)
class HttpScanResult:
    state: str
    shop_id: str = ''
    message: str = ''
    cookies: dict = field(default_factory=dict, repr=False)


class HttpQrFlow:
    """每实例新建 CookieJar，正常取码/轮询，确认后再独立核验身份。

    http_transport 供测试注入 MockTransport；默认 HTTP 客户端仅在 generate/poll 时发请求。
    headers_provider 不得读取历史票据/签名，identity_verifier 必须进行独立身份核验。
    captcha_token 只能由用户本人完成图形验证码后提供；本类不生成、不猜测、不绕过验证码。
    本类不保存数据库；核验成功的结果由有归属校验的调用方负责保存。
    """
    def __init__(self, *, headers_provider, fingerprint, identity_verifier,
                 expected_shop_id=None, http_transport=None,
                 captcha_token=None, allow_missing_anti_content=False):
        if not callable(identity_verifier):
            raise ProtocolError('缺少独立身份核验器')
        # captcha_token 是用户本人过完图形验证码后得到的**已解码** verifyAuthToken；
        # 本类只按页面的同一方式转存为 Cookie，不生成、不猜测、不绕过验证码。
        if captcha_token is not None:
            if (not isinstance(captcha_token, str) or not captcha_token
                    or len(captcha_token) > 4096
                    or any(c in captcha_token for c in '\r\n\0')):
                raise ProtocolError('验证码令牌格式不正确')
        navigator = fingerprint.get('navigator') if isinstance(fingerprint, dict) else None
        navigator = navigator if isinstance(navigator, dict) else {}
        supplied_ua = navigator.get('userAgent')
        legacy_ua = navigator.get('ua')
        if supplied_ua and legacy_ua and supplied_ua != legacy_ua:
            raise ProtocolError('环境资料中的 User-Agent 不一致')
        self._user_agent = supplied_ua or legacy_ua
        if self._user_agent is not None and not isinstance(self._user_agent, str):
            raise ProtocolError('环境资料中的 User-Agent 格式异常')
        self._client = httpx.Client(
            base_url='https://mms.pinduoduo.com', timeout=15,
            follow_redirects=False, trust_env=False, transport=http_transport,
            headers={'User-Agent': self._user_agent} if self._user_agent else None)
        if captcha_token is not None:
            # 页面由 captchaCallback 用 document.cookie 写入（非 HttpOnly，10 分钟），
            # 值经 encodeURIComponent；这里按同一编码写入，_request 再从 Cookie 头解码。
            self._client.cookies.set('msfe-pc-cookie-captcha-token',
                                     quote(captcha_token, safe=''),
                                     domain='mms.pinduoduo.com', path='/')
        self._verifier = identity_verifier
        self._closed = False
        self._started = False
        self._initialized = False
        try:
            self._protocol = QrProtocol(
                transport=self._request, headers_provider=headers_provider,
                fingerprint=fingerprint, expected_shop_id=expected_shop_id,
                allow_missing_anti_content=allow_missing_anti_content)
        except Exception:
            self._client.close()
            raise

    def _request(self, method, path, headers, body):
        allowed = {'/janus/api/scan/login/qrcode', '/janus/api/scan/login/query'}
        if self._closed or method != 'POST' or path not in allowed:
            raise ProtocolError('扫码请求不在允许范围')
        # 初始及后续 Cookie 均由本实例响应维护，拒绝提供器注入其他账号登录态。
        if any(k.lower() in {'cookie', 'host', 'authorization'} for k in headers):
            raise ProtocolError('动态参数提供器不得注入账号凭据或目标地址')
        request_ua = next((v for k,v in headers.items() if k.lower() == 'user-agent'), None)
        if request_ua is not None and request_ua != self._user_agent:
            raise ProtocolError('请求头与环境资料中的 User-Agent 不一致')
        request = self._client.build_request('POST', path, headers=headers, json=body)
        # 与官方 beforeSend 一致：只使用本实例 CookieJar 中、适用于当前请求且
        # 尚未过期的验证 Cookie。build_request 已应用 domain/path/expiry 规则。
        # 不从 headers_provider 接收外部验证令牌，不把挑战响应中的待验证值当凭据。
        jar = SimpleCookie()
        jar.load(request.headers.get('cookie', ''))
        token = jar.get('msfe-pc-cookie-captcha-token')
        if 'verifyauthtoken' in request.headers:
            raise ProtocolError('验证请求头只能来自当前会话的有效 Cookie')
        if token and token.value:
            request.headers['VerifyAuthToken'] = unquote(token.value)
        response = self._client.send(request)
        return Reply(response.status_code, response.json(),
                     tuple(response.headers.get_list('set-cookie')))

    def initialize(self):
        """先访问固定官方登录页，在同一 HTTP 会话接收匿名初始化 Cookie。

        只返回 Cookie 名称，不执行页面脚本、不猜测或生成动态参数。
        重定向与不明响应一律交回研究，不自动跳转或回退到浏览器。
        """
        if self._closed or self._started:
            raise ProtocolError('只能在申请二维码之前初始化新会话')
        if self._initialized:
            return {'initialized': True, 'cookie_names': sorted(self._client.cookies.keys())}
        headers = {'User-Agent': self._user_agent} if self._user_agent else {}
        try:
            response = self._client.get('/login/', headers=headers)
            if response.status_code != 200:
                raise ValueError('unexpected initialization response')
            if 'PASS_ID' in self._client.cookies:
                raise ValueError('unexpected login credential during anonymous initialization')
        except Exception:
            self.close()
            raise ProtocolError('匿名会话初始化未通过，未继续申请二维码') from None
        self._initialized = True
        return {'initialized': True, 'cookie_names': sorted(self._client.cookies.keys())}

    def generate(self):
        """返回实际取码响应链接编码的 PNG；不是页面截图或模拟二维码。"""
        if self._closed:
            raise ProtocolError('扫码流程已关闭')
        uri = self._protocol.generate()
        self._started = True
        qr = qrcode.QRCode(box_size=6, border=4,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(uri)
        qr.make(fit=True)
        output = BytesIO()
        qr.make_image().save(output)
        return {'state': 'waiting', 'qr_image_base64': base64.b64encode(output.getvalue()).decode()}

    def poll(self):
        if self._closed:
            raise ProtocolError('扫码流程已关闭')
        confirmation = self._protocol.poll()
        if confirmation.state != 'confirmed_response_only':
            # 把协议层已按页面证据保留的自述原因（status==5 的 message）透出，
            # 不在本层丢弃；其余中间态 message 为空串。
            return HttpScanResult(state=confirmation.state, message=confirmation.message)
        try:
            # 以本次确认响应的新 PASS_ID 为准，不把 CookieJar 中旧值当作成功证据。
            cookies = {cookie.name: cookie.value for cookie in self._client.cookies.jar
                       if cookie.domain in {'mms.pinduoduo.com', '.pinduoduo.com'}
                       and cookie.path == '/'}
            cookies['PASS_ID'] = confirmation.cookie.split('=', 1)[1]
            identity = self._verifier(dict(cookies))
            if (not isinstance(identity, dict)
                    or str(identity.get('shop_id')) != confirmation.shop_id
                    or str(identity.get('user_id')) != confirmation.user_id):
                raise ProtocolError('新登录态独立身份核验未通过，未保存凭据')
            return HttpScanResult(state='verified', shop_id=confirmation.shop_id, cookies=cookies)
        except Exception:
            raise ProtocolError('新登录态独立身份核验未通过，未保存凭据') from None
        finally:
            self.close()

    def close(self):
        self._protocol.close()
        self._client.cookies.clear()
        self._client.close()
        self._closed = True
