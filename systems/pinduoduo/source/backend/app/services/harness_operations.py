"""Local operational state and shop-scoped experience lifecycle."""
import json
from sqlalchemy import select,func
from common.models.harness_archive_models import ContextArchive as Archive,HarnessExperience as Experience
from common.models.message_delivery_models import MessageInbox,MessageDelivery
from common.models.conversation_control_models import ConversationControl
from common.models.shop_models import Shop
from common.services import harness_experience,context_archive
from common.schemas.common import success_response,error_response
from app.core.data_scope import ensure_shop_in_scope


def scoped(session,user,pk):
    return ensure_shop_in_scope(session,pk,user.id)


def overview(session,user,pk):
    denied=scoped(session,user,pk)
    if denied is not None:return denied
    shop=session.get(Shop,pk)
    owner=shop.owner_user_id
    def counts(model,field,extra=()):
        return dict(session.execute(select(field,func.count()).where(model.shop_pk==pk,*extra).group_by(field)).all())
    return success_response(data={'inbox':counts(MessageInbox,MessageInbox.state),
        'delivery':counts(MessageDelivery,MessageDelivery.state),
        'control':counts(ConversationControl,ConversationControl.mode),
        'compression':counts(Archive,Archive.state,(Archive.owner_user_id==owner,Archive.kind.in_(['state_segment','human_batch','human_segment']))),
        'experience':counts(Experience,Experience.state,(Experience.owner_user_id==owner,))})


def experiences(session,user,pk,before=None):
    denied=scoped(session,user,pk)
    if denied is not None:return denied
    shop=session.get(Shop,pk)
    query=select(Experience).where(Experience.shop_pk==pk,Experience.owner_user_id==shop.owner_user_id)
    if before:query=query.where(Experience.id<before)
    rows=list(session.scalars(query.order_by(Experience.id.desc()).limit(21)))
    return success_response(data={'items':[harness_experience.view(r) for r in rows[:20]],'before':rows[19].id if len(rows)>20 else None})


def review(session,user,pk,**payload):
    denied=scoped(session,user,pk)
    if denied is not None:return denied
    try:
        with session.begin_nested():result=harness_experience.review(session,pk,actor_id=user.id,**payload)
        session.commit();return success_response(data=result)
    except ValueError as exc:return error_response(40900,str(exc))


def versions(session,user,pk,ident):
    denied=scoped(session,user,pk)
    if denied is not None:return denied
    _,conditions=context_archive.scope(session,pk)
    rows=session.scalars(select(Archive).where(*conditions,Archive.kind=='experience_version',
        Archive.stable_id.startswith(str(ident)+':',autoescape=True)).order_by(Archive.id.desc()).limit(40))
    return success_response(data=[json.loads(r.payload_json) for r in rows])


def archive_page(session,user,pk,uid,kind,before=None):
    denied=scoped(session,user,pk)
    if denied is not None:return denied
    fn=context_archive.history_page if kind=='human' else context_archive.trace_page
    return success_response(data=fn(session,pk,uid,before=before))


def summaries(session,user,pk,before=None):
    denied=scoped(session,user,pk)
    if denied is not None:return denied
    _,conditions=context_archive.scope(session,pk)
    query=select(Archive).where(*conditions,Archive.kind.in_(['state_segment','human_batch','human_segment']))
    if before:query=query.where(Archive.id<before)
    rows=list(session.scalars(query.order_by(Archive.id.desc()).limit(21)))
    return success_response(data={'items':[{'id':r.id,'customer_uid':r.customer_uid,'state':r.state,
        'attempts':r.attempts,'error':r.error,'source_hash':r.source_hash,'summary':json.loads(r.summary_json) if r.summary_json else None} for r in rows[:20]],
        'before':rows[19].id if len(rows)>20 else None})


def retry(session,user,pk,ident):
    denied=scoped(session,user,pk)
    if denied is not None:return denied
    _,conditions=context_archive.scope(session,pk)
    row=session.scalar(select(Archive).where(*conditions,Archive.id==ident,
        Archive.kind.in_(['state_segment','human_batch','human_segment'])).with_for_update().execution_options(populate_existing=True))
    if not row or row.state!='failed':return error_response(40900,'只能重试已失败的摘要任务')
    row.state='pending';row.token=None;row.attempts=0;row.due_at=0;row.error=None
    session.commit();return success_response(data={'id':row.id,'state':row.state})
