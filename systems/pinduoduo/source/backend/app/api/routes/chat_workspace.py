"""历史工作台 API；每个入口同时校验功能权限与店铺归属。"""
from typing import Optional
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core import permission
from app.core.errors import BusinessError
from app.services import chat_workspace_service as service
from common.db.session import get_db
from common.schemas.common import error_response

router=APIRouter(prefix='/chat/workspace',tags=['客服工作台'])

def viewer(user=Depends(get_current_user),db:Session=Depends(get_db)):
    if not permission.check(user,'chat','view',session=db):
        raise BusinessError(40300,'无访问权限')
    return user

def sender(user=Depends(viewer),db:Session=Depends(get_db)):
    if not permission.check(user,'chat','send',session=db):
        raise BusinessError(40300,'无发送权限')
    return user


@router.get('/deliveries')
def deliveries(shop_pk: int, customer_uid: str = Query(..., min_length=1, max_length=128),
               user=Depends(viewer), db: Session=Depends(get_db)):
    from app.services import chat_delivery_service
    return chat_delivery_service.deliveries(db, user, shop_pk, customer_uid)


class DeliveryResolution(BaseModel):
    shop_pk: int
    customer_uid: str = Field(..., min_length=1, max_length=128)
    delivery_id: int
    outcome: str
    note: str = Field(..., min_length=4, max_length=500)


@router.post('/deliveries/resolve')
def resolve_delivery(payload: DeliveryResolution, user=Depends(sender), db: Session=Depends(get_db)):
    from app.services import chat_delivery_service
    return chat_delivery_service.resolve(db, user, **payload.model_dump())

def shop_viewer(user=Depends(get_current_user),db:Session=Depends(get_db)):
    if not permission.check(user,'shop','view',session=db): raise BusinessError(40300,'无店铺访问权限')
    return user

@router.get('/shop-overview')
def shop_overview(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),q:str=Query('',max_length=128),status:Optional[int]=Query(None,ge=0,le=1),ai_enabled:Optional[bool]=None,connected:Optional[bool]=None,user=Depends(shop_viewer),db:Session=Depends(get_db)):
    return service.list_shops(db,user,page=page,page_size=page_size,q=q.strip(),status=status,ai_enabled=ai_enabled,connected=connected)

@router.get('/shops')
def shops(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),q:str=Query('',max_length=128),status:Optional[int]=Query(None,ge=0,le=1),ai_enabled:Optional[bool]=None,connected:Optional[bool]=None,user=Depends(viewer),db:Session=Depends(get_db)):
    return service.list_shops(db,user,page=page,page_size=page_size,q=q.strip(),status=status,ai_enabled=ai_enabled,connected=connected)

@router.get('/conversations')
def conversations(shop_pk:int,page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),q:str=Query('',max_length=200),start_time:Optional[str]=None,end_time:Optional[str]=None,user=Depends(viewer),db:Session=Depends(get_db)):
    return service.conversations(db,user,shop_pk=shop_pk,page=page,page_size=page_size,q=q.strip(),start_time=start_time,end_time=end_time)


@router.get('/attention')
def attention(user=Depends(viewer),db:Session=Depends(get_db)):
    return service.attention(db,user)

@router.get('/messages')
def messages(shop_pk:int,customer_uid:str=Query(...,min_length=1,max_length=128),before:Optional[str]=Query(None,max_length=256),after_id:Optional[int]=Query(None,ge=0),limit:int=Query(50,ge=1,le=100),q:str=Query('',max_length=200),start_time:Optional[str]=None,end_time:Optional[str]=None,user=Depends(viewer),db:Session=Depends(get_db)):
    return service.messages(db,user,shop_pk=shop_pk,customer_uid=customer_uid,before=before,after_id=after_id,limit=limit,q=q.strip(),start_time=start_time,end_time=end_time)

class SyncRequest(BaseModel):
    shop_pk:int
    customer_uid:str=Field('',max_length=128)
    cursor:Optional[str]=Field(None,max_length=128)

@router.post('/sync')
def sync(payload:SyncRequest,user=Depends(viewer),db:Session=Depends(get_db)):
    return service.sync_page(db,user,**payload.model_dump())

@router.post('/read')
def read(payload:SyncRequest,user=Depends(viewer),db:Session=Depends(get_db)):
    return service.read_conversation(db,user,payload.shop_pk,payload.customer_uid)

@router.get('/phrases')
def phrases(shop_pk:int,user=Depends(viewer),db:Session=Depends(get_db)):
    return service.phrases(db,user,shop_pk)

class PhraseRequest(BaseModel):
    shop_pk:int
    title:str=Field(...,min_length=1,max_length=64)
    content:str=Field(...,min_length=1,max_length=2000)
    shared:bool=False
    phrase_id:Optional[int]=None
    enabled:bool=True

@router.post('/phrases')
def save_phrase(payload:PhraseRequest,user=Depends(sender),db:Session=Depends(get_db)):
    if not payload.title.strip() or not payload.content.strip():return error_response(40000,'短语标题与内容不能为空')
    return service.save_phrase(db,user,**payload.model_dump())


class AiModeRequest(BaseModel):
    shop_pk: int
    enabled: bool

@router.post('/ai-mode')
def ai_mode(payload: AiModeRequest, user=Depends(sender), db:Session=Depends(get_db)):
    from app.services import chat_ai_service
    if not permission.check(user, 'shop', 'update', session=db):
        return error_response(40300, '无店铺设置权限')
    return chat_ai_service.set_mode(db, user, payload.shop_pk, payload.enabled)

@router.post('/ai-draft')
def ai_draft(payload: SyncRequest, background_tasks: BackgroundTasks, user=Depends(sender), db:Session=Depends(get_db)):
    from app.services import chat_ai_service
    if not payload.customer_uid:
        return error_response(40000, '请先选择客户会话')
    result = chat_ai_service.draft(db, user, payload.shop_pk, payload.customer_uid)
    if result.success:
        broadcast_ai_state(payload, background_tasks)
    return result


class ConversationControlRequest(BaseModel):
    shop_pk: int
    customer_uid: str = Field(..., min_length=1, max_length=128)
    mode: str
    expected_revision: int = Field(..., ge=0)


@router.get('/conversation-control')
def conversation_control(shop_pk: int, customer_uid: str = Query(..., min_length=1, max_length=128),
                         user=Depends(viewer), db: Session = Depends(get_db)):
    from app.services import chat_ai_service
    return chat_ai_service.conversation_control(db, user, shop_pk, customer_uid)


@router.post('/conversation-control')
def set_conversation_control(payload: ConversationControlRequest, background_tasks: BackgroundTasks, user=Depends(sender),
                             db: Session = Depends(get_db)):
    from app.services import chat_ai_service
    result = chat_ai_service.conversation_control(db, user, **payload.model_dump())
    if result.success:
        db.commit()
        from app.services.chat_push_hub import get_chat_push_hub
        background_tasks.add_task(get_chat_push_hub().broadcast, payload.shop_pk, {
            'event': 'conversation_control', 'shop_pk': payload.shop_pk,
            'customer_uid': payload.customer_uid, 'message': result.data})
    return result


@router.get('/ai-workspace')
def ai_workspace(shop_pk:int,customer_uid:str=Query(...,min_length=1,max_length=128),
                 user=Depends(viewer),db:Session=Depends(get_db)):
    from app.services import chat_ai_service
    return chat_ai_service.ai_workspace(db,user,shop_pk,customer_uid)


class AiSupplementRequest(BaseModel):
    shop_pk:int
    customer_uid:str=Field(...,min_length=1,max_length=128)
    text:str=Field(...,min_length=1,max_length=4000)
    expected_revision:int=Field(...,ge=0)
    kind:Literal['fact','instruction']='fact'
    answer_id:Optional[str]=Field(None,min_length=36,max_length=36)


@router.post('/ai-supplement')
def ai_supplement(payload:AiSupplementRequest,background_tasks:BackgroundTasks,
                  user=Depends(sender),db:Session=Depends(get_db)):
    from app.services import chat_ai_service
    result=chat_ai_service.ai_workspace(db,user,payload.shop_pk,payload.customer_uid,
        text=payload.text,expected_revision=payload.expected_revision,kind=payload.kind,answer_id=payload.answer_id)
    if result.success:
        db.commit()
        from app.services.chat_push_hub import get_chat_push_hub
        background_tasks.add_task(get_chat_push_hub().broadcast,payload.shop_pk,{
            'event':'conversation_control','shop_pk':payload.shop_pk,
            'customer_uid':payload.customer_uid,'message':result.data['control']})
    return result


class AiAdviceRequest(SyncRequest):
    instruction_id: Optional[str] = Field(None,min_length=36,max_length=36)


@router.post('/ai-advice')
def ai_advice(payload: AiAdviceRequest, background_tasks: BackgroundTasks, user=Depends(sender), db:Session=Depends(get_db)):
    from app.services import chat_ai_service
    result = chat_ai_service.draft(db, user, payload.shop_pk, payload.customer_uid, purpose='advice',instruction_id=payload.instruction_id)
    if result.success:
        broadcast_ai_state(payload, background_tasks)
    return result


def broadcast_ai_state(payload, background_tasks):
    from common.services.conversation_control import read_committed
    from app.services.chat_push_hub import get_chat_push_hub
    state = read_committed(payload.shop_pk, payload.customer_uid)
    background_tasks.add_task(get_chat_push_hub().broadcast, payload.shop_pk, {
        'event':'conversation_control', 'shop_pk':payload.shop_pk,
        'customer_uid':payload.customer_uid, 'message':state})


@router.get('/human-history')
def human_history(shop_pk:int,customer_uid:str=Query(...,min_length=1,max_length=128),before:Optional[int]=Query(None,gt=0),user=Depends(viewer),db:Session=Depends(get_db)):
    from app.services import harness_operations as ops
    return ops.archive_page(db,user,shop_pk,customer_uid,'human',before)


@router.get('/answer-history')
def answer_history(shop_pk:int,customer_uid:str=Query(...,min_length=1,max_length=128),before:Optional[int]=Query(None,gt=0),user=Depends(viewer),db:Session=Depends(get_db)):
    from app.services import harness_operations as ops
    return ops.archive_page(db,user,shop_pk,customer_uid,'answer',before)


def operator_admin(user=Depends(sender),db:Session=Depends(get_db)):
    if not permission.check(user,'shop','update',session=db):raise BusinessError(40300,'无店铺设置权限')
    return user


@router.get('/operations')
def operations(shop_pk:int,user=Depends(viewer),db:Session=Depends(get_db)):
    from app.services import harness_operations as ops
    return ops.overview(db,user,shop_pk)


@router.get('/experiences')
def experiences(shop_pk:int,before:Optional[int]=Query(None,gt=0),user=Depends(viewer),db:Session=Depends(get_db)):
    from app.services import harness_operations as ops
    return ops.experiences(db,user,shop_pk,before)


@router.get('/experience-versions')
def experience_versions(shop_pk:int,ident:int=Query(...,gt=0),user=Depends(viewer),db:Session=Depends(get_db)):
    from app.services import harness_operations as ops
    return ops.versions(db,user,shop_pk,ident)


class ExperienceReview(BaseModel):
    shop_pk:int
    ident:int=Field(...,gt=0)
    expected_version:int=Field(...,gt=0)
    action:Literal['save','publish','revoke']
    title:str=Field(...,min_length=1,max_length=200)
    guidance:str=Field('',max_length=2000)
    trigger_terms:str=Field('',max_length=400)
    validation_note:str=Field('',max_length=1000)


@router.post('/experience-review')
def experience_review(payload:ExperienceReview,user=Depends(operator_admin),db:Session=Depends(get_db)):
    from app.services import harness_operations as ops
    args=payload.model_dump();pk=args.pop('shop_pk')
    return ops.review(db,user,pk,**args)


@router.get('/summaries')
def summaries(shop_pk:int,before:Optional[int]=Query(None,gt=0),user=Depends(viewer),db:Session=Depends(get_db)):
    from app.services import harness_operations as ops
    return ops.summaries(db,user,shop_pk,before)


class SummaryRetry(BaseModel):
    shop_pk:int
    ident:int=Field(...,gt=0)


@router.post('/summary-retry')
def summary_retry(payload:SummaryRetry,user=Depends(operator_admin),db:Session=Depends(get_db)):
    from app.services import harness_operations as ops
    return ops.retry(db,user,payload.shop_pk,payload.ident)
