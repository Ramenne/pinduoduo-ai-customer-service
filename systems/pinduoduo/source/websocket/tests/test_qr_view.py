import asyncio
from datetime import timedelta
from types import SimpleNamespace
from login import qr_view, qr_session_store

class Page:
    url = 'https://mms.pinduoduo.com/login/'
    viewport_size = {'width':100,'height':100}
    def __init__(self):
        self.events=[]
        self.mouse=self
        self.keyboard=self
    async def screenshot(self, **kw): return b'fake-jpeg'
    async def move(self,x,y): self.events.append(('move',x,y))
    async def down(self): self.events.append(('down',))
    async def up(self): self.events.append(('up',))
    async def press(self,k): self.events.append(('key',k))
    async def wheel(self,x,y): self.events.append(('wheel',x,y))

def setup(monkeypatch):
    page=Page()
    session=SimpleNamespace(expires_at=qr_session_store.now_beijing_naive()+timedelta(minutes=1))
    monkeypatch.setattr(qr_session_store,'_SESSIONS',{'s':session})
    async def view(s,owner):
        return {'state':getattr(session,'test_state','waiting'),'terminal':False} if s=='s' and owner==1 else None
    monkeypatch.setattr(qr_session_store,'get_session',view)
    return page,session

def test_owner_only_and_direct_manual_inputs(monkeypatch):
    page,_=setup(monkeypatch)
    async def run():
        await qr_view.attach('s',page)
        assert await qr_view.frame('s',2) is None
        assert not await qr_view.input_event('s',2,{'kind':'down','x':.2,'y':.3})
        assert (await qr_view.frame('s',1))['width']==100
        for kind in ['down','move','up']:
            assert await qr_view.input_event('s',1,{'kind':kind,'x':.5,'y':.4})
        assert page.events[-1]==('up',)
        await qr_view.detach('s')
        assert await qr_view.frame('s',1) is None
    asyncio.run(run())

def test_wheel_forwards_position_and_deltas_with_access_checks(monkeypatch):
    page,session=setup(monkeypatch)
    event={'kind':'wheel','x':.5,'y':.25,'delta_x':12,'delta_y':320}
    async def run():
        await qr_view.attach('s',page)
        try:
            assert not await qr_view.input_event('s',2,event)
            assert page.events==[]
            assert await qr_view.input_event('s',1,event)
            assert page.events==[('move',49.5,24.75),('wheel',12,320)]
            page.events.clear()
            for bad in [None,True,float('nan'),float('inf'),2001,-2001,'100']:
                assert not await qr_view.input_event('s',1,{**event,'delta_y':bad})
            assert not await qr_view.input_event('s',1,{**event,'x':2})
            assert page.events==[]
            session.test_state='confirmed'
            assert not await qr_view.input_event('s',1,event)
            session.test_state='waiting'
            page.url='https://other.example/'
            assert not await qr_view.input_event('s',1,event)
            assert page.events==[]
        finally:
            await qr_view.detach('s')
    asyncio.run(run())

def test_expiry_navigation_and_invalid_input(monkeypatch):
    page,session=setup(monkeypatch)
    async def run():
        await qr_view.attach('s',page)
        for event in [{'kind':'move','x':2,'y':0},{'kind':'move','x':float('nan'),'y':0},{'kind':'script'},{'kind':'key','key':'Control+L'}]:
            assert not await qr_view.input_event('s',1,event)
        assert page.events==[]
        session.test_state='confirmed'
        assert await qr_view.frame('s',1) is None
        session.test_state='scanned'
        page.url='https://mms.pinduoduo.com/home'
        assert (await qr_view.frame('s',1))['width']==100
        page.url='https://other.example/login/'
        assert await qr_view.frame('s',1) is None
        page.url='https://mms.pinduoduo.com/login/'
        session.expires_at=qr_session_store.now_beijing_naive()-timedelta(seconds=1)
        assert await qr_view.frame('s',1) is None
        await qr_view.detach('s')
    asyncio.run(run())


def test_native_viewport_and_user_text_have_owner_and_expiry_checks(monkeypatch):
    page,session=setup(monkeypatch)
    page.viewport_size=None
    async def evaluate(_script): return {'width':1440, 'height':813}
    async def insert_text(text): page.events.append(('text',text))
    page.evaluate=evaluate
    page.insert_text=insert_text
    async def run():
        await qr_view.attach('s',page)
        try:
            assert (await qr_view.frame('s',1))['height']==813
            assert await qr_view.input_event('s',1,{'kind':'down','x':.5,'y':.5})
            assert ('move',719.5,406) in page.events
            assert not await qr_view.input_event('s',2,{'kind':'text','text':'123456'})
            assert await qr_view.input_event('s',1,{'kind':'text','text':'123456'})
            assert page.events[-1]==('text','123456')
            for value in [None,'','x'*257,'x\ny']:
                assert not await qr_view.input_event('s',1,{'kind':'text','text':value})
            session.test_state='confirmed'
            assert not await qr_view.input_event('s',1,{'kind':'text','text':'123456'})
        finally:
            await qr_view.detach('s')
    asyncio.run(run())
