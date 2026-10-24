import asyncio
from app.services.chat_push_hub import ChatPushHub


def test_attention_signal_checks_shop_scope_even_without_chat_subscribers(monkeypatch):
    from app.api.routes import chat_ws
    monkeypatch.setattr(chat_ws,'_authorize',lambda token,shop=None:9 if token=='allowed' and shop==1 else None)
    class Socket:
        def __init__(self):self.events=[]
        async def send_json(self,value):self.events.append(value)
    async def scenario():
        hub=ChatPushHub();allowed,foreign=Socket(),Socket()
        await hub.register_attention(allowed,'allowed');await hub.register_attention(foreign,'foreign')
        await hub.broadcast(1,{'event':'conversation_control','customer_uid':'private','message':{'reason':'private'}})
        assert allowed.events==[{'event':'attention_changed'}] and foreign.events==[]
        await hub.broadcast(2,{'event':'conversation_control'})
        assert len(allowed.events)==1
    asyncio.run(scenario())
