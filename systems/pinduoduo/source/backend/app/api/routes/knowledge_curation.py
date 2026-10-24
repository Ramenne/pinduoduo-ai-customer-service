"""人工沉淀候选：预览与一次确认入库，写入既有商品/客服知识。"""
from typing import Literal
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.services import knowledge_curation_service as service
from common.db.session import get_db

router = APIRouter(prefix='/knowledge-curation', tags=['知识沉淀'])


@router.get('/tasks')
def tasks(shop_pk:int, page:int=Query(1,ge=1),user=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.list_tasks(db,user,shop_pk,page)


@router.get('/products')
def products(shop_pk:int,q:str=Query('',max_length=100),user=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.products(db,user,shop_pk,q)


@router.get('/tasks/{task_id}/candidates/{index}/preview')
def preview(task_id:int,index:int,category:Literal['product','service'],title:str=Query(...,min_length=1,max_length=100),
            goods_id:str=Query('',max_length=128),user=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.preview(db,user,task_id,index,category,title,goods_id)


class Review(BaseModel):
    decision:Literal['publish','reject']='publish'
    candidate_token:str=Field(...,pattern='^[a-f0-9]{64}$')
    category:Literal['product','service']
    title:str=Field(...,min_length=1,max_length=100)
    content:str=Field(...,min_length=1,max_length=4000)
    goods_id:str=Field('',max_length=128)
    target_hash:str=Field(...,pattern='^[a-f0-9]{64}$')
    mode:Literal['create','append','replace']='create'


@router.post('/tasks/{task_id}/candidates/{index}/review')
def review(task_id:int,index:int,payload:Review,user=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.review(db,user,task_id,index,payload.model_dump())
