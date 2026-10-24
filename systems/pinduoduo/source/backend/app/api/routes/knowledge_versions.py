from typing import Literal
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.services import knowledge_version_service as service
from common.db.session import get_db

router=APIRouter(prefix='/knowledge-versions',tags=['知识版本'])

@router.get('/{kind}/{item_id}')
def history(kind:Literal['product','service'],item_id:int,page:int=Query(1,ge=1),user=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.history(db,user,kind,item_id,page)

@router.get('/{kind}/{item_id}/{version_id}')
def detail(kind:Literal['product','service'],item_id:int,version_id:int,user=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.detail(db,user,kind,item_id,version_id)

class Restore(BaseModel):
    expected_hash:str=Field(...,pattern='^[a-f0-9]{64}$')

@router.post('/{kind}/{item_id}/{version_id}/restore')
def restore(kind:Literal['product','service'],item_id:int,version_id:int,payload:Restore,user=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.restore(db,user,kind,item_id,version_id,payload.expected_hash)
