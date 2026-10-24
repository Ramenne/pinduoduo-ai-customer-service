import json
import uuid

from conftest import account,enroll,sign
from oin_control_plane.seller_native import native_message
from oin_control_plane.security import now_seconds


def proof(device,key,command='inventory',payload=None,stamp=None):
    stamp=now_seconds() if stamp is None else stamp
    nonce=str(uuid.uuid4());text=json.dumps(payload or {},ensure_ascii=False)
    return {'command':command,'issued_at':stamp,'nonce':nonce,'payload_text':text,
            'signature':sign(key,native_message(device,command,stamp,nonce,text))}


def test_native_message_matches_android_byte_contract():
    assert native_message('device','inventory',123,'nonce','{}')=='oin-native-seller-v1\ndevice\ninventory\n123\nnonce\n44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'


def test_bound_device_native_access_without_web_login_and_replay_is_same_job(client,app):
    owner=account(client,'native-seller');d=enroll(client,owner['token']);device=d['device']['id'];key=d['private_key']
    with app.state.database.transaction(immediate=True) as c:
        c.execute('UPDATE devices SET app_version_code=52 WHERE id=?',(device,))
    path=f'/api/v1/devices/{device}/seller-native'
    response=client.post(path,json=proof(device,key))
    assert response.status_code==200,response.text
    assert 'steps' not in str(response.json())
    assert [s['id'] for s in response.json()['sections']]==['publish','preheat','maintain']
    groups={v['id']:v['section'] for v in response.json()['builtins']}
    assert groups=={'daily_preheat_v3':'preheat','daily_maintenance_v4':'maintain'}
    assert all(value['available'] for value in response.json()['builtins'])
    assert set(groups.values()) <= {'publish','preheat','maintain'}
    payload={'builtin_id':'daily_preheat_v3','idempotency_key':'native-one-request'}
    request=proof(device,key,'start',payload)
    first=client.post(path,json=request)
    assert first.status_code==201,first.text
    assert client.post(path,json=request).json()['id']==first.json()['id']
    assert client.post(path,json=proof(device,key,'start',payload)).json()['id']==first.json()['id']
    with app.state.database.connect() as c:
        assert c.execute('SELECT count(*) AS n FROM seller_jobs WHERE device_id=?',(device,)).fetchone()['n']==1
    inventory=client.post(path,json=proof(device,key)).json()
    assert inventory['recipes']==[]  # Builtin's persisted recipe must not become a duplicate custom choice.
    assert any(v['recipe_id']==first.json()['recipe_id'] for v in inventory['builtins'])


def test_proof_is_scoped_to_device_command_exact_body_time_and_revocation(client,app):
    a=account(client,'native-a');d=enroll(client,a['token']);device=d['device']['id'];key=d['private_key']
    other=account(client,'native-b');b=enroll(client,other['token'])
    path=f'/api/v1/devices/{device}/seller-native'
    assert client.post(path,json=proof(device,b['private_key'])).status_code==401
    request=proof(device,key);request['payload_text']='{"owner":"other"}'
    assert client.post(path,json=request).status_code==401
    request=proof(device,key);request['command']='start'
    assert client.post(path,json=request).status_code==401
    assert client.post(path,json=proof(device,key,stamp=now_seconds()-121)).status_code==422
    assert client.post(f"/api/v1/devices/{b['device']['id']}/seller-native",json=proof(device,key)).status_code==401
    assert client.post(path,json=proof(device,key,'start',{'account_id':a['account']['id']})).status_code==422
    with app.state.database.transaction(immediate=True) as c:
        c.execute("UPDATE devices SET binding_state='revoked',revoked_at=? WHERE id=?",(now_seconds(),device))
    assert client.post(path,json=proof(device,key)).status_code==401


def test_native_cancel_only_own_queued_job_and_stage_is_explicit(client,app):
    a=account(client,'control-a');d=enroll(client,a['token']);device=d['device']['id'];key=d['private_key']
    b=account(client,'control-b');other=enroll(client,b['token'])
    with app.state.database.transaction(immediate=True) as c:
        c.execute('UPDATE devices SET app_version_code=46 WHERE id=?',(device,))
    path=f'/api/v1/devices/{device}/seller-native'
    job=client.post(path,json=proof(device,key,'start',{'builtin_id':'open_publish_form_v1','idempotency_key':'control-queued'})).json()
    inventory=client.post(path,json=proof(device,key)).json()
    assert any(j['id']==job['id'] for j in inventory['jobs']), {'job':job,'jobs':inventory['jobs']}
    entry=next(j for j in inventory['jobs'] if j['id']==job['id'])
    assert entry['current_step']=='等待手机执行' and entry['step_count']==6
    assert entry['name']=='上新准备 · 打开发布表单'
    otherpath=f"/api/v1/devices/{other['device']['id']}/seller-native"
    assert client.post(otherpath,json=proof(other['device']['id'],other['private_key'],'cancel',{'job_id':job['id']})).status_code==404
    assert client.post(path,json=proof(device,key,'cancel',{'job_id':job['id'],'outcome':'confirmed'})).status_code==422
    with app.state.database.transaction(immediate=True) as c:
        c.execute("UPDATE seller_jobs SET status='running' WHERE id=?",(job['id'],))
    assert client.post(path,json=proof(device,key,'cancel',{'job_id':job['id']})).status_code==409
    with app.state.database.transaction(immediate=True) as c:
        c.execute("UPDATE seller_jobs SET status='queued' WHERE id=?",(job['id'],))
    result=client.post(path,json=proof(device,key,'cancel',{'job_id':job['id']}))
    assert result.status_code==200 and result.json()['status']=='cancelled'
    assert client.post(path,json=proof(device,key,'cancel',{'job_id':job['id']})).status_code==409


def test_signed_product_metrics_read_does_not_accept_cross_device_or_parameters(client,app):
    owner=account(client,'native-metrics');d=enroll(client,owner['token']);device=d['device']['id'];key=d['private_key']
    other=account(client,'native-metrics-other');foreign=enroll(client,other['token'])
    path=f'/api/v1/devices/{device}/seller-native'
    response=client.post(path,json=proof(device,key,'metrics',{}))
    assert response.status_code==200 and response.json()=={'state':'not_collected','captured_at':None,'products':[]}
    assert client.post(path,json=proof(device,foreign['private_key'],'metrics',{})).status_code==401
    assert client.post(path,json=proof(device,key,'metrics',{'device_id':foreign['device']['id']})).status_code==422


def test_current_phone_catalog_has_both_daily_actions(client,app):
    from oin_control_plane.seller_workflows import BUILTINS
    from oin_control_plane.seller import validate_steps
    owner=account(client,'two-daily-actions');d=enroll(client,owner['token']);device=d['device']['id'];key=d['private_key']
    with app.state.database.transaction(immediate=True) as c:
        c.execute('UPDATE devices SET app_version_code=64 WHERE id=?',(device,))
    path=f'/api/v1/devices/{device}/seller-native'
    inventory=client.post(path,json=proof(device,key)).json()
    assert {p['id'] for p in inventory['builtins']}=={'daily_preheat_v5','daily_maintenance_v5'}
    plan=BUILTINS['daily_maintenance_v5'];steps=validate_steps(plan['steps'],plan['kind'],template=True)
    assert sum(s['task_type']=='brighten_all' for s in steps)==1
    assert steps[-1]['task_type']=='content_browse' and steps[-1]['parameters']=={'seconds':600,'favorites':2,'purchase_handoff':True,'search_browse':True}
    assert not any(s['task_type'] in {'manual','wait'} for s in steps)
    accepted=client.post(path,json=proof(device,key,'start',{'builtin_id':'daily_maintenance_v5','idempotency_key':'maintenance-real-plan'}))
    assert accepted.status_code==201,accepted.text
