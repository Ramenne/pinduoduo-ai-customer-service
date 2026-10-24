from __future__ import annotations

import json
from importlib.resources import files

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .pages import layout, _e
from .security import SecurityError
from .store import ConflictError, NotFoundError


def install_seller_routes(app, ledger, io, web_context, csrf_valid, json_body, settings):
    from .seller_native import install_native_seller
    install_native_seller(app,ledger,io,json_body)
    async def context(request, *, write=False):
        ctx=await web_context(request,ledger.phones,io)
        if ctx is None: return None,JSONResponse({"error":"controller_auth_required"},status_code=401)
        if write and not csrf_valid(request,ctx): return None,JSONResponse({"error":"csrf_failed"},status_code=403)
        return ctx,None

    @app.get('/seller.js')
    async def script():
        return Response(files('oin_control_plane').joinpath('seller.js').read_text(),media_type='application/javascript')

    @app.get('/seller.css')
    async def style():
        return Response(files('oin_control_plane').joinpath('seller.css').read_text(),media_type='text/css')

    @app.get('/oin-brand.svg')
    async def brand():
        return Response(files('oin_control_plane').joinpath('oin-brand.svg').read_text(),media_type='image/svg+xml')

    @app.get('/devices/{device_id}/seller')
    async def page(request: Request,device_id: str):
        ctx,error=await context(request)
        if error: return RedirectResponse(settings.browser_path('/login'),status_code=303)
        if await io.call(ledger.phones.get_device,ctx.account['id'],device_id) is None:
            return JSONResponse({'error':'not_found'},status_code=404)
        body=files('oin_control_plane').joinpath('seller.html').read_text()
        body=body.replace('@@API@@',_e(settings.browser_path('/api/v1/devices/'+device_id)))
        body=body.replace('@@CSRF@@',_e(ctx.web_session.csrf_token))
        body=body.replace('@@UPDATE_API@@',_e(settings.browser_path('/api/v1/app-update')))
        body=body.replace('@@BRAND@@',_e(settings.browser_path('/oin-brand.svg')))
        body=f'<link rel="stylesheet" href="{_e(settings.browser_path("/seller.css"))}">'+body
        body+=f'<script src="{_e(settings.browser_path("/seller.js"))}" defer></script>'
        response=HTMLResponse(layout(settings,'闲鱼上新与维护',body,username=ctx.account['username'],csrf=ctx.web_session.csrf_token))
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.get('/api/v1/devices/{device_id}/seller')
    async def inventory(request: Request,device_id: str):
        ctx,error=await context(request)
        if error: return error
        try: return JSONResponse(await io.call(ledger.inventory,ctx.account['id'],device_id))
        except NotFoundError: return JSONResponse({'error':'not_found'},status_code=404)

    @app.get('/api/v1/devices/{device_id}/seller/metrics')
    async def metrics(request: Request,device_id: str):
        ctx,error=await context(request)
        if error:return error
        try:return JSONResponse(await io.call(ledger.product_metrics,ctx.account['id'],device_id))
        except NotFoundError:return JSONResponse({'error':'not_found'},status_code=404)

    @app.get('/api/v1/devices/{device_id}/seller/jobs/{job_id}')
    async def inspect(request: Request,device_id: str,job_id: str):
        ctx,error=await context(request)
        if error: return error
        try: return JSONResponse(await io.call(ledger.inspect,ctx.account['id'],device_id,job_id))
        except NotFoundError: return JSONResponse({'error':'not_found'},status_code=404)

    @app.post('/api/v1/devices/{device_id}/seller/{resource}')
    async def create(request: Request,device_id: str,resource: str):
        ctx,error=await context(request,write=True)
        if error: return error
        methods={'items':ledger.item,'recipes':ledger.recipe,'jobs':ledger.job,'schedules':ledger.schedule,'builtin-jobs':ledger.builtin_job}
        if resource not in methods: return JSONResponse({'error':'not_found'},status_code=404)
        try:
            body=await json_body(request,maximum=4*1024*1024 if resource=='items' else 512*1024)
            result=await io.call(methods[resource],ctx.account['id'],device_id,body)
            return JSONResponse(result,status_code=201)
        except SecurityError as exc: return JSONResponse({'error':str(exc)},status_code=422)
        except ConflictError as exc: return JSONResponse({'error':str(exc)},status_code=409)
        except NotFoundError as exc: return JSONResponse({'error':str(exc)},status_code=404)

    @app.post('/api/v1/devices/{device_id}/seller/jobs/{job_id}/control')
    async def task_control(request: Request,device_id: str,job_id: str):
        ctx,error=await context(request,write=True)
        if error: return error
        try:
            body=await json_body(request,maximum=1024)
            if set(body) not in ({'action'},{'action','confirm_manual'}): raise SecurityError('invalid_control')
            return JSONResponse(await app.state.seller_engine.control(ctx.account['id'],device_id,job_id,body['action'],confirm_manual=body.get('confirm_manual',False),origin='web_controller'))
        except SecurityError as exc: return JSONResponse({'error':str(exc)},status_code=422)
        except ConflictError as exc: return JSONResponse({'error':str(exc)},status_code=409)
        except NotFoundError: return JSONResponse({'error':'not_found'},status_code=404)

    @app.post('/api/v1/devices/{device_id}/seller/jobs/{job_id}/reconcile')
    async def reconcile(request: Request,device_id: str,job_id: str):
        ctx,error=await context(request,write=True)
        if error: return error
        try:
            body=await json_body(request,maximum=1024)
            return JSONResponse(await io.call(ledger.reconcile,ctx.account['id'],device_id,job_id,body))
        except (SecurityError,ConflictError) as exc: return JSONResponse({'error':str(exc)},status_code=409)
        except NotFoundError: return JSONResponse({'error':'not_found'},status_code=404)

    @app.post('/api/v1/devices/{device_id}/seller/schedules/{schedule_id}/pause')
    async def pause(request: Request,device_id: str,schedule_id: str):
        ctx,error=await context(request,write=True)
        if error: return error
        try: await io.call(ledger.pause_schedule,ctx.account['id'],device_id,schedule_id)
        except NotFoundError: return JSONResponse({'error':'not_found'},status_code=404)
        return JSONResponse({'state':'paused'})
