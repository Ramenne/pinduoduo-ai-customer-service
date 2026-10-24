"""本人短信验证映射：仅替身与 about:blank 静态夹具，不访问平台。"""
import asyncio
from types import SimpleNamespace
import pytest
from login import password_verification as module


class Locator:
    def __init__(self, events, label, enabled=True):
        self.events, self.label, self.enabled = events, label, enabled
    def filter(self, **_kw): return self
    async def count(self): return 1
    async def is_enabled(self): return self.enabled
    async def fill(self, value, **_kw): self.events.append(('fill', value))
    async def click(self, **_kw): self.events.append(('click', self.label))


def harness(monkeypatch, *, active=True, kind='sms', cooldown=0, expire_after_fill=False):
    events=[]
    field,confirm,resend=[Locator(events,name) for name in ('field','confirm','resend')]
    page=SimpleNamespace(url='https://mms.pinduoduo.com/login/', locator=lambda _:field,
                        get_by_role=lambda *_a,**_k:confirm, get_by_text=lambda *_a,**_k:resend)
    async def allowed(session_id, owner):
        if session_id!='fixture' or owner!=7 or not active or (expire_after_fill and events):return None
        return page
    async def inspect(_page):
        return {'type':kind,'can_resend':cooldown==0,'cooldown_seconds':cooldown}
    monkeypatch.setattr(module.qr_view, '_allowed', allowed)
    monkeypatch.setattr(module, 'inspect', inspect)
    monkeypatch.setattr(module.qr_view, '_LOCKS', {'fixture':asyncio.Lock()})
    module.forget('fixture')
    return events


def test_submit_targets_sms_field_before_confirmation(monkeypatch):
    events=harness(monkeypatch)
    ok,message=asyncio.run(module.perform('fixture',7,'submit_sms','123456'))
    assert ok and '等待' in message
    assert events==[('fill','123456'),('click','confirm')]


@pytest.mark.parametrize('options,owner,code',[
    ({},8,'123456'), ({'active':False},7,'123456'), ({'kind':'captcha'},7,'123456'),
    ({},7,''), ({},7,'12'), ({},7,'123456789'), ({},7,'12ab56')])
def test_invalid_or_other_owner_never_fills(monkeypatch,options,owner,code):
    events=harness(monkeypatch,**options)
    ok,_=asyncio.run(module.perform('fixture',owner,'submit_sms',code))
    assert not ok and events==[]


def test_expiry_between_fill_and_click_prevents_submit(monkeypatch):
    events=harness(monkeypatch,expire_after_fill=True)
    ok,_=asyncio.run(module.perform('fixture',7,'submit_sms','123456'))
    assert not ok and events==[('fill','123456')]


def test_resend_respects_platform_cooldown_and_duplicate_clicks(monkeypatch):
    events=harness(monkeypatch,cooldown=19)
    ok,_=asyncio.run(module.perform('fixture',7,'resend_sms'))
    assert not ok and events==[]
    events=harness(monkeypatch)
    async def scenario():
        assert (await module.perform('fixture',7,'resend_sms'))[0]
        assert not (await module.perform('fixture',7,'resend_sms'))[0]
        assert events==[('click','resend')]
        module.forget('fixture')
    asyncio.run(scenario())


def test_blank_fixture_sms_probe_masks_phone_and_prioritizes_captcha():
    from playwright.async_api import async_playwright
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            try:
                page=await browser.new_page()
                await page.set_content('<input disabled value="195****8667"><input placeholder="请输入短信验证码" value="123456"><button>确认</button><button disabled>重新发送</button><span>还剩19秒</span>')
                assert page.url=='about:blank'
                result=await module.inspect(page)
                assert result['type']=='sms' and result['masked_mobile']=='195****8667'
                assert result['cooldown_seconds']==19 and not result['can_resend']
                assert '123456' not in str(result)
                await page.set_content('<input disabled value="195****8667"><input placeholder="请输入短信验证码"><button>确认</button><button>重新发送</button>')
                assert (await module.inspect(page))['can_resend']
                assert await page.get_by_role('button',name='确认',exact=True).filter(visible=True).count()==1
                assert await page.get_by_text(module.RESEND_TEXT).filter(visible=True).count()==1
                await page.set_content('<div class="captcha-wrapper">fixture captcha</div><input placeholder="请输入短信验证码">')
                assert await module.inspect(page)=={'type':'captcha'}
            finally:
                await browser.close()
    asyncio.run(scenario())
