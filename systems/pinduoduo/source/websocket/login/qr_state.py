# -*- coding: utf-8 -*-
"""
login.qr_state —— 扫码登录状态枚举与页面状态判据
================================================
本文件用途：把「拼多多登录页当前处于什么状态」的判定逻辑从浏览器流程中拆出来，
使其可被单元测试以纯数据方式覆盖（不依赖 Playwright、不触网），同时给上层提供
统一、可区分、可对外呈现的终态集合。

为什么需要单独拆出来（本轮实测教训）：
- 参照实现与本项目既有的账号密码登录，都把「需要人工验证」和「超时」混成一个
  失败：出滑块时只会一路等到超时，前端只能显示"登录超时"，从而误导用户以为
  密码错了。本模块把二者拆成不同终态。
- 判定 **以结构信号为主、文案为辅**：滑块用其容器节点识别，成功用凭据 + 接口
  核验识别；文案只用于「已扫码待确认」这类中间态与「二维码失效」的辅助提示。
  实测已证明：商家后台首页文案本身含「风控 / 防控」等词，若先判验证关键词、
  后判是否离开登录页，会把一次成功登录误判成需要人工验证。

实现约束（开发规范）：导入置顶（51）、中文注释（37）、文件名用下划线（40）、
纯逻辑无副作用（便于属性测试）、日志禁用 debug（38）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("login.qr_state")

# 商家后台域：Cookie 读取与判定的作用域（只取该 URL 实际会携带的 Cookie）。
MMS_COOKIES_URL: str = "https://mms.pinduoduo.com/"

# 登录态承载 Cookie 名。实测确认其为 HttpOnly，且是商家后台接口的必要凭据。
PASS_ID_COOKIE: str = "PASS_ID"


class QrOutcome:
    """扫码会话状态常量。

    中间态：``waiting`` / ``scanned`` / ``verification_required``；
    终态：``confirmed`` / ``claimed`` / ``expired`` / ``cancelled`` / ``failed``。

    ``verification_required`` 被刻意归为**可恢复的中间态**而不是终态：平台要求人工
    验证时，只要那个浏览器对用户可见，用户本人完成验证后流程可以继续走到成功。
    把它做成终态就等于宣布"扫码不可用"，而事实只是"需要用户做一次人工操作"。
    到截止时间仍未通过时，流程会以 ``verification_required`` 作为最终结果返回，
    从而给出准确原因，而不是笼统的"超时"。
    """

    WAITING: str = "waiting"
    SCANNED: str = "scanned"
    VERIFICATION_REQUIRED: str = "verification_required"
    CONFIRMED: str = "confirmed"
    CLAIMED: str = "claimed"
    EXPIRED: str = "expired"
    CANCELLED: str = "cancelled"
    FAILED: str = "failed"

    @classmethod
    def terminal(cls) -> frozenset:
        """终态集合：进入这些状态后不再轮询、并应释放浏览器资源。"""
        return frozenset(
            {cls.CONFIRMED, cls.CLAIMED, cls.EXPIRED, cls.CANCELLED, cls.FAILED}
        )

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        """判断给定状态是否为终态。"""
        return state in cls.terminal()

    @classmethod
    def needs_user_action(cls, state: str) -> bool:
        """是否需要用户本人采取行动（目前只有人工验证一种）。"""
        return state == cls.VERIFICATION_REQUIRED


# 滑块拼图验证的结构特征（实测形态：容器 + 背景图 + 滑块块）。
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    ".captcha-wrapper",
    ".captcha-wrapper__slider",
    ".slider-img-bg",
    ".slider-item",
)

# 在页面 JS 中一次性完成结构探测，减少跨进程往返。
_PAGE_PROBE_SCRIPT: str = """
(selectors) => {
  let captchaNodes = 0;
  for (const sel of selectors) {
    try { captchaNodes += document.querySelectorAll(sel).length; } catch (e) { /* 忽略非法选择器 */ }
  }
  const text = document.body ? document.body.innerText : '';
  return {
    captchaNodes: captchaNodes,
    onLoginPage: location.pathname.indexOf('/login') >= 0,
    leftLoginPage: location.pathname.indexOf('/login') < 0,
    scannedText: /已扫码|扫码成功|请在手机上确认|扫描成功/.test(text),
    expiredText: /二维码已失效|二维码已过期|点击刷新|刷新二维码/.test(text)
  };
}
"""


@dataclass
class PageProbe:
    """页面状态探测结果（纯数据，便于单测断言）。"""

    captcha_nodes: int = 0
    on_login_page: bool = True
    left_login_page: bool = False
    scanned_text: bool = False
    expired_text: bool = False

    @property
    def needs_verification(self) -> bool:
        """需要人工验证：仅当 **仍在登录页** 且出现滑块结构时才成立。

        加上「仍在登录页」这个前置条件，是为了避免登录成功跳到首页后，
        被首页文案里的「风控 / 防控」等词误判成需要验证。
        """
        return self.captcha_nodes > 0 and self.on_login_page

    @property
    def scanned_hint(self) -> bool:
        """已扫码待确认的提示信号（非终态，仅用于展示）。"""
        return self.scanned_text and self.on_login_page

    @property
    def expired_hint(self) -> bool:
        """二维码失效的文案信号（辅助信号，非唯一判据）。"""
        return self.expired_text and self.on_login_page

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "PageProbe":
        """由页面探测脚本返回值构造（缺字段按默认值处理，不抛异常）。"""
        raw = raw or {}
        return cls(
            captcha_nodes=int(raw.get("captchaNodes") or 0),
            on_login_page=bool(raw.get("onLoginPage", True)),
            left_login_page=bool(raw.get("leftLoginPage", False)),
            scanned_text=bool(raw.get("scannedText", False)),
            expired_text=bool(raw.get("expiredText", False)),
        )


@dataclass
class QrBrowserResult:
    """浏览器扫码流程的返回值（共享词汇表，与页面状态判据放在一起）。

    放在本模块而不是 ``qr_browser`` 里，是为了让会话注册表 **不必为了取一个结果
    类型而把 playwright 与 channel_pdd 整条导入链拖进来** —— 注册表因此可以在
    没有浏览器依赖的环境里被单测覆盖。
    """

    outcome: str = QrOutcome.FAILED
    message: str = ""
    qr_png_b64: Optional[str] = None
    qr_rotation: int = 0
    identity: Optional[Dict[str, Any]] = None
    cookies: Optional[Dict[str, str]] = field(default=None, repr=False)
    elapsed_seconds: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


async def classify_page(page: Any) -> PageProbe:
    """对当前页面做一次结构 + 文案探测。

    Args:
        page: Playwright 页面对象（或任何提供 ``evaluate`` 的替身，便于测试）。

    Returns:
        ``PageProbe``；探测失败返回「无结构信号」的空结果，不抛异常。
    """
    try:
        raw = await page.evaluate(_PAGE_PROBE_SCRIPT, list(_CAPTCHA_SELECTORS))
    except Exception as exc:  # noqa: BLE001 - 页面重绘期间的瞬时失败可容忍
        logger.warning("页面状态探测失败，按无信号处理：%s", exc)
        return PageProbe()
    return PageProbe.from_raw(raw)


__all__ = [
    "MMS_COOKIES_URL",
    "PASS_ID_COOKIE",
    "PageProbe",
    "QrBrowserResult",
    "QrOutcome",
    "classify_page",
]
