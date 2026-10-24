"""Bound-device-only native task access, not an account/session login bypass."""
import hashlib
import re

from fastapi import Request
from fastapi.responses import JSONResponse

from .security import SecurityError, now_seconds, verify_ecdsa_signature
from .store import ConflictError, NotFoundError


def native_message(device, command, issued_at, nonce, payload_text):
    return '\n'.join(['oin-native-seller-v1',device,command,str(issued_at),nonce,
                      hashlib.sha256(payload_text.encode('utf-8')).hexdigest()])


def install_native_seller(app, ledger, io, json_body):
    @app.post('/api/v1/devices/{device_id}/seller-native')
    async def native(request: Request, device_id: str):
        try:
            body=await json_body(request,maximum=8192)
            if set(body)!={'command','issued_at','nonce','payload_text','signature'}:
                raise SecurityError('invalid_native_request')
            command,stamp,nonce,text=body['command'],body['issued_at'],body['nonce'],body['payload_text']
            if (command not in {'inventory','start','cancel','inspect','control','metrics'} or type(stamp) is not int or abs(now_seconds()-stamp)>120 or
                    not isinstance(nonce,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}',nonce) or
                    not isinstance(text,str) or len(text.encode('utf-8'))>4096 or
                    not isinstance(body['signature'],str) or len(body['signature'])>512):
                raise SecurityError('invalid_native_request')
            credential=await io.call(ledger.phones.get_device_credential,device_id)
            if not credential or not verify_ecdsa_signature(credential['public_key_der'],
                    native_message(device_id,command,stamp,nonce,text),body['signature']):
                return JSONResponse({'error':'device_proof_invalid'},status_code=401)
            # Exact bytes are signed. JSON is decoded only after authenticating the device.
            import json
            payload=json.loads(text)
            if not isinstance(payload,dict): raise SecurityError('invalid_native_payload')
            owner=credential['owner_account_id']
            if command=='metrics':
                if payload:raise SecurityError('metrics_has_no_parameters')
                return JSONResponse(await io.call(ledger.product_metrics,owner,device_id),headers={'Cache-Control':'no-store'})
            if command=='inventory':
                if payload: raise SecurityError('inventory_has_no_parameters')
                value=await io.call(ledger.inventory,owner,device_id)
                builtin_recipes={v['recipe_id'] for v in value['builtins']}
                return JSONResponse({'sections':value['sections'],'builtins':value['builtins'],'items':value['items'],
                    'recipes':[],
                    'jobs':value['jobs'][:20]},headers={'Cache-Control':'no-store'})
            if command in {'inspect','control'}:
                keys={'job_id','action'} if command=='control' else {'job_id'}
                if (set(payload)!=keys and not (command=='control' and set(payload)==keys|{'confirm_manual'})) or not isinstance(payload['job_id'],str): raise SecurityError('invalid_control')
                result=await app.state.seller_engine.control(owner,device_id,payload['job_id'],payload['action'],confirm_manual=payload.get('confirm_manual',False),origin='bound_device') if command=='control' else await io.call(ledger.inspect,owner,device_id,payload['job_id'])
                if command=='inspect': result.pop('task',None)
                return JSONResponse(result)
            if command=='cancel':
                if set(payload)!={'job_id'} or not isinstance(payload['job_id'],str):
                    raise SecurityError('invalid_native_cancel')
                result=await io.call(ledger.reconcile,owner,device_id,payload['job_id'],{'outcome':'cancel'})
                return JSONResponse(result)
            # Replays carry the same persisted job identity. No endpoint takes an account/device override.
            if set(payload) in ({'builtin_id','idempotency_key'},{'builtin_id','idempotency_key','item_id'}):
                result=await io.call(ledger.builtin_job,owner,device_id,payload)
            elif set(payload)=={'recipe_id','item_id','authorize','idempotency_key'}:
                result=await io.call(ledger.job,owner,device_id,payload)
            else: raise SecurityError('invalid_native_start')
            return JSONResponse(result,status_code=201)
        except (SecurityError,ValueError,UnicodeError,TypeError):
            return JSONResponse({'error':'invalid_native_request'},status_code=422)
        except ConflictError as exc:
            return JSONResponse({'error':str(exc)},status_code=409)
        except NotFoundError:
            return JSONResponse({'error':'device_or_task_not_found'},status_code=404)
