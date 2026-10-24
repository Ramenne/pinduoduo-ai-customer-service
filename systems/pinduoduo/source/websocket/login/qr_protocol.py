"""扫码协议的离线可测适配层；尚未连接生产路由。

只编码用户提供记录与 2026-10-16 真实浏览器抓包已证实的字段。不生成/伪造设备指纹或
Anti-Content，不自动获取 Cookie，不持有浏览器、不发网络请求。
传输和动态请求头由调用方显式注入。

本轮实测补充（见 handoffs/.../result-protocol/evidence/）：
1. 页面请求携带 Anti-Content，采样值有变化。尚无有效参数的受控对照，不能据此证明
   两个接口必须携带它、参数不可复用，或生成它必须持续运行完整浏览器。
2. 报告中的缺失/占位参数探测返回 HTTP 200 +
   ``success=false, errorCode=54001, errorMsg=需要验证图形验证码`` 且
   ``result.verifyAuthToken`` 存在。这一形态与"票据已过期""用户取消"都不同，
   必须单独区分，故本模块用 CaptchaRequired 抛出。
3. 真实页面的 query 请求还携带一个名为 ``ETag`` 的**请求头**（同一轮内取值恒定，
   长度 32）。其语义未经验证，本模块不猜测、不生成，仅允许由提供器透传。

2026-10-19 只读核查官方登录页静态资源（bundle）后的补充结论，均为"读源码/读接口
schema"，不是平台登录已跑通：

4. ``/janus/api/scan/login/query`` 的响应 schema（页面自带）为
   ``result: {status, token, windowsAppShopToken, loginLimitStatus,
   loginLimitEffectiveTime, userInfoVO{mallId,id,username}, maskMobile,
   identityVerifyURL, message}``，``required=["status"]``；其中 ``status`` 的说明是
   "提供 result 时必提供该信息，**其他字段只在手机端扫码并确认后提供**"。
   因此 ``identityVerifyURL`` / ``loginLimitStatus`` / ``maskMobile`` / ``message``
   都是**手机扫码确认之后**才可能出现，本模块据此把它们视为"确认态"字段，
   而不是取码阶段的字段。
5. 页面代码把 ``status === 5`` 单独当作**平台自述的失败**处理
   （``message`` 作为原因，缺省文案"系统繁忙，请通过账密登录"），与 1/2/3 明确区分；
   故本模块新增可辨状态 ``platform_declared_failure``，不并入"未观察状态"。
6. 页面在确认成功后先调用 ``loginSuccessHandle(token, result)``，再执行
   ``limitLoginCheck(result)``：**当且仅当 ``loginLimitStatus === 2``（限登）时**，
   弹窗展示由 ``identityVerifyURL`` 渲染的二维码（文案"如需继续使用，请微信扫码认证
   账号"），要用户用手机微信扫码完成实人认证。也就是说：``status === 3`` 只代表
   "手机已确认"，**不必然等于"拿到可正常使用的登录态"**。本模块据此在确认分支中
   显式检查该状态，命中即抛 ``LoginRestricted``，绝不当作成功交付凭据。
7. 动态参数来源得到解释（不是新证据）：``Anti-Content`` 与 ``ETag`` 均由拼多多自己的
   风控 SDK（登录页动态加载的 chunk，``app: "mms-login"``）在页面运行时产出，
   ``ETag`` 取值缓存于 ``localStorage["MMS-LOGIN-FP"]``；``fingerprint`` 请求体则只是
   浏览器环境只读数据。本模块仍不生成、不伪造这些值。
"""
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit, parse_qs


class ProtocolError(ValueError):
    """固定文案，避免异常携带登录凭据。"""


class TransportError(ProtocolError):
    """传输层失败（网络/超时/连接中断）。

    单列为子类，让调用方能把"可以重试的传输失败"与"协议/业务失败"分开处理，
    避免把网络抖动误报成登录失败。
    """


class CaptchaRequired(ProtocolError):
    """平台要求先完成图形验证码。需要用户本人处理，本模块不尝试绕过。"""


class LoginRestricted(ProtocolError):
    """手机已确认，但平台把该账号判为限登（``loginLimitStatus == 2``）。

    页面证据：确认后弹窗要求用**手机微信**扫描 ``identityVerifyURL`` 完成实人认证。
    本模块不代替用户认证，也不把这种状态当作"登录成功"交付凭据。
    """


# 实测观察到的"需要人工图形验证码"响应形态。
CAPTCHA_ERROR_CODE = 54001

# 页面代码把确认态里 loginLimitStatus==2 视为"限登"，需实人认证（微信扫码）。
LOGIN_RESTRICTED_STATUS = 2

# 平台自述失败态：页面把 status==5 当作"系统繁忙/由平台给出原因"的失败。
PLATFORM_FAILURE_STATUS = 5

@dataclass(repr=False)
class Reply:
    status_code: int
    body: dict
    set_cookie: tuple = ()


@dataclass(repr=False)
class Confirmation:
    state: str
    shop_id: str = ''
    user_id: str = ''
    message: str = ''
    cookie: str = field(default='', repr=False)


class QrProtocol:
    """每实例仅处理一轮扫码。transport 接受 method/path/headers/body，返回 Reply。

    headers_provider 每次请求单独调用。它必须提供正常授权流程取得的新鲜参数；
    本模块不会把抓包中的一次性字符串当作生成器。调用者还须自行验证初始 Cookie 来源。
    """
    def __init__(self, *, transport, headers_provider, fingerprint, expected_shop_id=None,
                 allow_missing_anti_content=False):
        if not callable(transport) or not callable(headers_provider):
            raise ProtocolError('缺少显式传输或动态参数提供器')
        if not isinstance(fingerprint, dict) or not isinstance(fingerprint.get('navigator'), dict):
            raise ProtocolError('缺少真实会话的浏览器环境资料')
        if type(allow_missing_anti_content) is not bool:
            raise ProtocolError('Anti-Content 缺失开关必须是显式布尔值')
        self._transport = transport
        self._headers_provider = headers_provider
        self._fingerprint = deepcopy(fingerprint)
        self._expected_shop = str(expected_shop_id) if expected_shop_id else None
        self._ticket = None
        self._done = False
        self._last_anti = None
        # 证据边界：页面在取码/轮询时都携带 Anti-Content，但尚未证明它是必要条件
        # （带真实 Anti-Content 的页面请求同样被 54001 拦下）。故默认仍 fail-closed，
        # 只允许调用方显式开启"无 Anti-Content"对照实验。
        self._allow_missing_anti = allow_missing_anti_content
        # 只比较相邻请求的摘要；变化/重复均不能单独证明参数有效或无效。
        # distinct 是历史字段名，实际计数为相邻值发生变化的次数，并非全局去重数。
        self.stats = {'anti_content_distinct': 0, 'anti_content_reused': 0,
                      'anti_content_missing': 0, 'requests': 0}

    def _call(self, path, body):
        supplied = self._headers_provider()
        if not isinstance(supplied, dict):
            raise ProtocolError('动态请求头不可用')
        headers = {str(k).lower():v for k,v in supplied.items()}
        anti = headers.get('anti-content')
        if not isinstance(anti, str) or not anti.strip():
            if not self._allow_missing_anti:
                raise ProtocolError('尚未接入动态 Anti-Content 提供器；未发送请求')
            headers.pop('anti-content', None)
            self.stats['anti_content_missing'] += 1
        else:
            self._note_anti(anti)
        self.stats['requests'] += 1
        # 保守保留已观察字段，不主张 Anti-Content 已被证明必需。
        headers['content-type'] = 'application/json'
        try:
            reply = self._transport('POST', path, headers, deepcopy(body))
        except Exception:
            raise TransportError('扫码传输失败；未保存任何凭据') from None
        if not isinstance(reply, Reply) or reply.status_code != 200:
            raise ProtocolError('扫码接口未返回预期 HTTP 响应')
        if not isinstance(reply.body, dict):
            raise ProtocolError('扫码响应结构与已观察协议不符')
        # 实测形态：HTTP 200 但 success=false + errorCode 54001 + verifyAuthToken。
        # 这是"要先过图形验证码"，不是业务失败，也不是超时，必须单独可辨。
        result = reply.body.get('result')
        if (reply.body.get('success') is not True
                and reply.body.get('errorCode') == CAPTCHA_ERROR_CODE
                and isinstance(result, dict) and result.get('verifyAuthToken')):
            raise CaptchaRequired('平台要求图形验证码，需用户本人完成；本模块不尝试绕过')
        if reply.body.get('success') is not True:
            raise ProtocolError('扫码接口未确认业务成功；需单独核实原因')
        if not isinstance(result, dict):
            raise ProtocolError('扫码响应结构与已观察协议不符')
        return reply, result

    def _note_anti(self, value):
        """只记录 Anti-Content 的哈希前缀，用于事后判断提供器是否在做复用。"""
        digest = sha256(str(value).encode()).hexdigest()[:16]
        if digest == self._last_anti:
            self.stats['anti_content_reused'] += 1
        else:
            self.stats['anti_content_distinct'] += 1
        self._last_anti = digest

    def adopt_ticket(self, ticket):
        """接收浏览器侧 mint 的票据，供本实例继续轮询。

        只接受与已观察格式一致的 ``newTicket$…``；不生成票据，也不读取历史票据。
        取码必须由调用方在浏览器里按正常流程完成，本方法只接管后续 HTTP 轮询。
        """
        if self._ticket is not None or self._done:
            raise ProtocolError('本实例已申请二维码，请新建独立会话')
        if (not isinstance(ticket, str) or not ticket.startswith('newTicket$')
                or len(ticket) > 4096 or any(c in ticket for c in '\r\n\0')):
            raise ProtocolError('扫码票据格式不正确')
        self._ticket = ticket

    def generate(self):
        if self._ticket is not None or self._done:
            raise ProtocolError('本实例已申请二维码，请新建独立会话')
        _, result = self._call('/janus/api/scan/login/qrcode', {'fingerprint':self._fingerprint})
        uri = result.get('uri')
        if not isinstance(uri, str) or len(uri) > 4096:
            raise ProtocolError('二维码链接格式不正确')
        url = urlsplit(uri)
        if url.scheme != 'https' or url.hostname != 'w.url.cn':
            raise ProtocolError('二维码链接与已观察来源不符')
        tickets = parse_qs(url.query).get('data', [])
        if len(tickets) != 1 or not tickets[0].startswith('newTicket$'):
            raise ProtocolError('二维码响应没有唯一的扫码票据')
        self._ticket = tickets[0]
        return uri

    def poll(self):
        if self._ticket is None or self._done:
            raise ProtocolError('扫码会话未开始或已经结束')
        reply, result = self._call('/janus/api/scan/login/query', {
            'data':self._ticket, 'fingerprint':self._fingerprint,
        })
        status = result.get('status')
        if type(status) is int and status == 1:
            return Confirmation(state='waiting_observed')
        if type(status) is int and status == PLATFORM_FAILURE_STATUS:
            # 页面代码把 5 当作平台自述的失败并展示 message；这不是"未观察状态"，
            # 但也不等于凭据已失效，故单独可辨，且不触发任何凭据处理。
            raw = result.get('message')
            text = raw.strip()[:200] if isinstance(raw, str) else ''
            return Confirmation(state='platform_declared_failure', message=text)
        if type(status) is not int or status != 3:
            # 未观察的状态不能猜作已扫码、取消或过期，更不能自动当作成功。
            return Confirmation(state='unmapped_status')
        info = result.get('userInfoVO')
        if not isinstance(info, dict) or not info.get('mallId') or not info.get('id'):
            raise ProtocolError('确认响应缺少可核对的店铺与用户身份')
        shop_id, user_id = str(info['mallId']), str(info['id'])
        if self._expected_shop and shop_id != self._expected_shop:
            self.close()
            raise ProtocolError('扫码店铺与目标店铺不一致，拒绝交付凭据')
        # 页面证据：确认成功后先 loginSuccessHandle，再 limitLoginCheck；后者仅当
        # loginLimitStatus==2 时要求用户用手机微信扫描 identityVerifyURL 完成实人认证。
        # 故 status==3 只代表"手机已确认"，不能直接当作可用登录态交付。
        if result.get('loginLimitStatus') == LOGIN_RESTRICTED_STATUS:
            self.close()
            raise LoginRestricted(
                '平台判定该账号限登，需用户本人用手机完成实人认证；未交付凭据')
        # 必须来自本次确认响应，绝不把旧 CookieJar 里的 PASS_ID 当作新登录结果。
        values = []
        try:
            for header in reply.set_cookie:
                jar = SimpleCookie(); jar.load(header)
                if 'PASS_ID' in jar:
                    value = jar['PASS_ID'].value
                    if value and value not in values:
                        values.append(value)
        except (CookieError, TypeError):
            raise ProtocolError('确认响应 Cookie 格式异常') from None
        if len(values) != 1:
            raise ProtocolError('确认响应未下发唯一 PASS_ID，不能判定取得登录态')
        self.close()
        return Confirmation(state='confirmed_response_only', shop_id=shop_id, user_id=user_id,
                            cookie='PASS_ID='+values[0])

    def close(self):
        self._done = True
        self._ticket = None
        self._fingerprint = {}
