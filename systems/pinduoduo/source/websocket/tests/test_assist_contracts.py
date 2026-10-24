import pytest
from agent.assist_contracts import parse_draft, parse_advice

def test_customer_reply_is_separate_from_internal_notes():
    reply,note=parse_draft('{"customer_reply":"您好，材质是纯棉。","operator_note":"内部核对库存"}')
    assert reply=='您好，材质是纯棉。' and '库存' not in reply and note=='内部核对库存'

@pytest.mark.parametrize('text',['您好，纯棉。\n内部提醒：请核对库存','[]','{"customer_reply":"","operator_note":"内部内容"}'])
def test_unstructured_output_never_becomes_customer_draft(text):
    with pytest.raises(ValueError):parse_draft(text)

def test_advice_requires_explicit_pending_and_resolved_lists():
    message,requests,resolved,withdrawn=parse_advice('{"message":"还缺规格","requests":[{"id":null,"question":"规格是什么？"}],"resolved_request_ids":[]}')
    assert len(requests)==1 and resolved==[] and withdrawn==[]
    with pytest.raises(ValueError):parse_advice('{"message":"都好了"}')


def test_json_mode_is_protocol_opt_in_for_internal_calls():
    import asyncio
    from types import SimpleNamespace
    from agent.llm_client import LLMClient
    calls=[]
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}',tool_calls=[]))])
    async def run():
        for enabled in (False, True):
            client=LLMClient('fixture','https://fixture.invalid','fixture',0,10,json_mode=enabled)
            client._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            await client.chat([{'role':'user','content':'JSON'}])
    asyncio.run(run())
    assert 'response_format' not in calls[0]
    assert calls[1]['response_format']=={'type':'json_object'}


@pytest.mark.parametrize('reply',['我的知识库里没有这款商品的保存方法。','知识库未收录该商品规格。','模型调用失败，请稍后重试。'])
def test_internal_diagnostics_never_become_customer_draft(reply):
    import json
    with pytest.raises(ValueError,match='内部处理说明'):
        parse_draft(json.dumps({'customer_reply':reply,'operator_note':''}))


def test_business_terms_are_allowed_and_internal_note_can_explain_missing_knowledge():
    import json
    reply,note=parse_draft(json.dumps({'customer_reply':'这个细节我先确认一下，确认后回复您。',
        'operator_note':'知识库没有该商品保存方法，请人工核实。'}))
    assert reply=='这个细节我先确认一下，确认后回复您。' and '知识库' in note
    reply,_=parse_draft(json.dumps({'customer_reply':'这款数据库教程是电子版。','operator_note':''}))
    assert '数据库教程' in reply
