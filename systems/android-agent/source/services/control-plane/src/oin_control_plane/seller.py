"""Persisted seller recipes over the existing durable Android task channel.

No platform selectors are guessed here: recipes use exact nodes observed on the
bound phone. A dispatched click is never treated as a confirmed publication.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from .protocol import XIANYU_PACKAGE, validate_task_request, bounded_input_text
from .security import SecurityError, canonical_json, now_seconds, safe_integer, safe_text
from .store import ConflictError, NotFoundError, TaskCapacityError, UnsupportedDeviceTaskError
from .seller_workflows import BUILTINS, SECTIONS, recipe_section
from .seller_page import confirmed_selling, already_brightened

KINDS = {"publish", "edit", "brighten", "metrics", "reply", "research"}
MUTATIONS = {"publish", "edit", "brighten", "reply"}
DESCRIPTION_NO_WRITE_ERRORS={
    'oin_input_method_not_active','description_requires_task_worker','description_input_not_ready',
    'description_input_budget_exhausted','invalid_description_text','description_readback_unavailable',
    'description_input_changed','description_editor_not_empty','description_page_not_confirmed',
    'screen_off','device_locked','accessibility_unavailable','system_ui_foreground',
    'xianyu_not_foreground','execution_environment_unknown',
    'bridge_paused',
}
log = logging.getLogger("oin.seller")


def expected_node(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {"kind", "value"} or raw["kind"] not in {"text", "description", "resource_id"}:
        raise SecurityError("invalid_expected_node")
    return {"kind": raw["kind"], "value": bounded_input_text(raw["value"], 500)}


def validate_steps(raw: Any, kind: str, *, template: bool = False) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 60 or not all(isinstance(s, dict) for s in raw):
        raise SecurityError("invalid_workflow_steps")
    steps = []
    for index, s in enumerate(raw):
        if set(s) - {"task_type", "parameters", "effect", "expect", "name", "when", "unless", "phase"}:
            raise SecurityError("unknown_step_fields")
        task_type, effect = s.get("task_type"), s.get("effect", "read")
        if task_type not in {"action", "observe", "wait", "browse_item", "route_home", "manual", "report", "brighten_all", "scan_goods", "content_browse"} or effect not in {"read", "navigation", "commit"}:
            raise SecurityError("invalid_workflow_effect")
        params = s.get("parameters", {})
        if task_type=='wait':
            if set(params)-{'seconds','browse_motion'} or 'seconds' not in params or 'browse_motion' in params and type(params['browse_motion']) is not bool: raise SecurityError('invalid_wait_step')
            params={'seconds':safe_integer(params['seconds'],minimum=1,maximum=180,field='seconds'),**({'browse_motion':params['browse_motion']} if 'browse_motion' in params else {})}
        elif task_type=='content_browse':
            if kind not in {'research','metrics'} or set(params)-{'search_browse'} not in ({'seconds'},{'seconds','favorites'},{'seconds','favorites','purchase_handoff'}) or effect!=('commit' if params.get('favorites') else 'navigation'):raise SecurityError('invalid_content_browse_step')
            if 'search_browse' in params and params['search_browse'] is not True:raise SecurityError('invalid_search_browse')
            if 'purchase_handoff' in params and params['purchase_handoff'] is not True:raise SecurityError('invalid_purchase_handoff')
            params={**({'search_browse':True} if params.get('search_browse') else {}),'seconds':safe_integer(params['seconds'],minimum=60,maximum=1200,field='seconds'),**({'purchase_handoff':True} if params.get('purchase_handoff') else {}),**({'favorites':safe_integer(params['favorites'],minimum=2,maximum=2,field='favorites')} if 'favorites' in params else {})}
        elif task_type in {'brighten_all','scan_goods'}:
            if kind!='metrics' or params or effect!=('commit' if task_type=='brighten_all' else 'read') or 'when' in s or 'unless' in s:
                raise SecurityError('invalid_seller_maintenance_step')
        elif task_type in {'browse_item','route_home','manual','report'}:
            if params or effect!=('read' if task_type=='report' else 'navigation'):raise SecurityError('invalid_browse_step')
        elif template and isinstance(params, dict) and params.get("action") == "import_image":
            if set(params) != {"action", "image_index"} or type(params["image_index"]) is not int or not 0 <= params["image_index"] < 9:
                raise SecurityError("invalid_image_template")
        else:
            _, params, _ = validate_task_request(task_type, params, 60)
        if task_type == "action":
            if params.get("action") not in {"launch_app", "click_unique_node", "tap_unique_node", "set_node_text", "set_description_text", "import_image", "scroll_page", "back"}:
                raise SecurityError("unsafe_workflow_action")
            if params["action"] in {"click_unique_node", "tap_unique_node"} and params.get("selector",{}).get("value") in {"现金签到","闲鱼币","做任务","去做任务","领现金"}:
                raise SecurityError("reward_game_entry_disabled")
            if params["action"] in {"click_unique_node", "tap_unique_node"} and effect not in {"navigation", "commit"}:
                raise SecurityError("click_effect_required")
        elif task_type in {"observe","wait"} and effect != "read":
            raise SecurityError("observe_is_read_only")
        expect = s.get("expect", [])
        if not isinstance(expect, list) or len(expect) > 12 or (expect and task_type != "observe"):
            raise SecurityError("invalid_expected_nodes")
        if effect == "commit" and task_type not in {'brighten_all','content_browse'} and (index+1 >= len(raw) or raw[index+1].get("task_type") != "observe" or len(raw[index+1].get("expect", [])) < 2):
            raise SecurityError("commit_requires_page_and_item_readback")
        if effect=='commit' and ('when' in s or 'unless' in s):raise SecurityError('optional_commit_not_supported')
        step={"task_type": task_type, "parameters": params, "effect": effect, "expect": [expected_node(v) for v in expect]}
        if 'phase' in s:step['phase']=safe_text(s['phase'],maximum=60,field='phase',allow_empty=False)
        if 'name' in s:step['name']=safe_text(s['name'],maximum=160,field='step_name',allow_empty=False)
        if 'when' in s:step['when']=expected_node(s['when'])
        if 'unless' in s:step['unless']=expected_node(s['unless'])
        steps.append(step)
    anchor_index = 1 if steps[0]["parameters"].get("action") == "launch_app" else 0
    if len(steps) <= anchor_index or steps[anchor_index]["task_type"] != "observe" or not steps[anchor_index]["expect"]:
        raise SecurityError("observed_start_anchor_required")
    if kind in MUTATIONS and (not any(s["effect"] == "commit" for s in steps) or steps[-1]["task_type"] != "observe" or len(steps[-1]["expect"]) < 2):
        raise SecurityError("mutation_requires_confirmed_terminal")
    if kind not in MUTATIONS and any(s["effect"] == "commit" and s['task_type'] not in {'brighten_all','content_browse'} for s in steps):
        raise SecurityError("read_recipe_cannot_commit")
    return steps



def wait_remaining(record, checkpoint=None):
    duration=max(0,record.get('duration_seconds',0))
    started=record.get('started_at',now_seconds())
    checked=record.get('last_verified_at',started) if checkpoint is None else checkpoint
    return max(0,min(duration,started+duration-max(started,checked)))


def matches(result: Any, expected: list[dict[str, str]]) -> bool:
    if not isinstance(result, dict) or result.get("package") != XIANYU_PACKAGE or result.get("truncated") or not isinstance(result.get("nodes"), list):
        return False
    for anchor in expected:
        if anchor == {'kind':'description','value':'在卖'} and confirmed_selling(result):
            continue
        if sum(1 for n in result["nodes"] if isinstance(n, dict) and n.get("visible_to_user") is True and not n.get("password") and n.get(anchor["kind"]) == anchor["value"]) != 1:
            return False
    return True


from .task_presentation import step_title, present_job


def interruption_selector(snapshot):
    if not matches(snapshot,[]):return None
    nodes=[n for n in snapshot['nodes'] if isinstance(n,dict) and n.get('visible_to_user') and n.get('enabled') and not n.get('password') and not n.get('editable')]
    labels=[str(n.get('text') or n.get('description') or '') for n in nodes]
    advertisement=any(any(word in label for word in ['广告','开屏','推广','红包弹窗']) for label in labels)
    editing=any('发布, 发布' in label for label in labels)
    for value in ['关闭广告','跳过广告','跳过','关闭','×']:
        if value in {'关闭','×'} and (not advertisement or editing):continue
        for kind in ['description','text']:
            matched=[n for n in nodes if n.get(kind)==value]
            if len(matched)==1 and sum(n.get(kind)==value for n in snapshot['nodes'] if n.get('visible_to_user'))==1:
                return {'kind':kind,'value':value}
    return None


class SellerStore:
    def __init__(self, database, phones):
        self.db, self.phones = database, phones

    def require_device(self, owner: str, device: str):
        d = self.phones.get_device(owner, device)
        if d is None or d["binding_state"] != "bound":
            raise NotFoundError("device_not_found")
        return d

    def item(self, owner: str, device: str, body: dict) -> dict:
        self.require_device(owner, device)
        if set(body) - {"title", "description", "price", "images", "platform_item_id"}:
            raise SecurityError("unknown_item_fields")
        value = {"title": safe_text(body.get("title"), maximum=120, field="title", allow_empty=False),
                 "description": bounded_input_text(body.get("description")),
                 "price": safe_text(body.get("price"), maximum=16, field="price", allow_empty=False),
                 "platform_item_id": safe_text(body.get("platform_item_id", ""), maximum=120, field="platform_item_id"), "images": []}
        if not re.fullmatch(r"\d{1,7}(?:\.\d{1,2})?", value["price"]) or float(value["price"]) <= 0:
            raise SecurityError("invalid_price")
        images = body.get("images", [])
        if not isinstance(images, list) or len(images) > 9:
            raise SecurityError("invalid_images")
        for image in images:
            if not isinstance(image, dict): raise SecurityError("invalid_image")
            _, p, _ = validate_task_request("action", {"action": "import_image", "package": XIANYU_PACKAGE, **image}, 60)
            value["images"].append({k:v for k,v in p.items() if k not in {"action", "package"}})
        ident, now = str(uuid.uuid4()), now_seconds()
        with self.db.transaction(immediate=True) as c:
            c.execute("INSERT INTO seller_items VALUES(?,?,?,?,?,'draft',?,?)", (ident,owner,device,value["title"],canonical_json(value),now,now))
        return {"id":ident,"title":value["title"],"image_count":len(images),"status":"draft"}

    def recipe(self, owner: str, device: str, body: dict) -> dict:
        self.require_device(owner,device)
        if set(body) != {"name", "kind", "steps"} or body["kind"] not in KINDS:
            raise SecurityError("invalid_recipe")
        name = safe_text(body["name"],maximum=100,field="name",allow_empty=False)
        steps = validate_steps(body["steps"],body["kind"],template=True)
        ident = str(uuid.uuid4())
        with self.db.transaction(immediate=True) as c:
            c.execute("INSERT INTO seller_recipes VALUES(?,?,?,?,?,?,?)",(ident,owner,device,name,body["kind"],canonical_json(steps),now_seconds()))
        return {"id":ident,"name":name,"kind":body["kind"],"steps":steps}

    def builtin_job(self, owner: str, device: str, body: dict) -> dict:
        current=self.require_device(owner,device)
        if not {'builtin_id','idempotency_key'} <= set(body) or set(body)-{'builtin_id','idempotency_key','item_id'} or body.get('builtin_id') not in BUILTINS:
            raise SecurityError('invalid_builtin_workflow')
        plan=BUILTINS[body['builtin_id']]
        if current['app_version_code'] < plan.get('min_agent',35):
            raise ConflictError('seller_agent_upgrade_required')
        if plan.get('requires_item') and not body.get('item_id'):
            raise SecurityError('workflow_requires_item')
        if not plan.get('requires_item') and body.get('item_id'):
            raise SecurityError('navigation_does_not_use_item')
        steps=validate_steps(plan['steps'],plan['kind'],template=True)
        ident=self.builtin_recipe_id(owner,device,body['builtin_id'])
        with self.db.transaction(immediate=True) as c:
            c.execute('INSERT INTO seller_recipes VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                      (ident,owner,device,plan['name'],plan['kind'],canonical_json(steps),now_seconds()))
        return self.job(owner,device,{'recipe_id':ident,'idempotency_key':body['idempotency_key'],
            **({'authorize':True} if any(s['effect']=='commit' for s in steps) else {}),
            **({'item_id':body['item_id']} if body.get('item_id') else {})})

    @staticmethod
    def builtin_recipe_id(owner,device,builtin):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"oin-seller:{owner}:{device}:{builtin}"))

    @staticmethod
    def expand(steps, item, extra):
        values = {k:v for k,v in item.items() if k != "images"} | extra
        def replace(v):
            if isinstance(v,dict): return {k:replace(x) for k,x in v.items()}
            if isinstance(v,list): return [replace(x) for x in v]
            if isinstance(v,str):
                def sub(m):
                    if not isinstance(values.get(m[1]),str): raise SecurityError("missing_template_value")
                    return values[m[1]]
                return re.sub(r"\{\{([a-z_]+)\}\}",sub,v)
            return v
        steps = replace(steps)
        for s in steps:
            if s["parameters"].get("action") == "import_image":
                try: image = item["images"][s["parameters"]["image_index"]]
                except (KeyError,IndexError) as exc: raise SecurityError("missing_image") from exc
                s["parameters"] = {"action":"import_image","package":XIANYU_PACKAGE,**image}
        return steps

    def job(self, owner: str, device: str, body: dict) -> dict:
        self.require_device(owner,device)
        if set(body) - {"recipe_id","item_id","values","authorize","idempotency_key","due_at"}:
            raise SecurityError("unknown_job_fields")
        key = safe_text(body.get("idempotency_key"),maximum=128,field="idempotency_key",allow_empty=False)
        if len(key) < 8: raise SecurityError("idempotency_key_too_short")
        with self.db.transaction(immediate=True) as c:
            recipe = c.execute("SELECT * FROM seller_recipes WHERE id=? AND account_id=? AND device_id=?",(body.get("recipe_id"),owner,device)).fetchone()
            if not recipe: raise NotFoundError("recipe_not_found")
            if recipe["kind"] in MUTATIONS and body.get("authorize") is not True:
                raise SecurityError("explicit_action_authorization_required")
            item = {}
            if body.get("item_id"):
                r = c.execute("SELECT data_json FROM seller_items WHERE id=? AND account_id=? AND device_id=?",(body["item_id"],owner,device)).fetchone()
                if not r: raise NotFoundError("item_not_found")
                item = json.loads(r["data_json"])
            if recipe["kind"] == "publish" and not item.get("images"):
                raise SecurityError("publish_requires_images")
            extra = body.get("values",{})
            if not isinstance(extra,dict) or set(extra) - {"reply","target","title","description","price"}:
                raise SecurityError("invalid_job_values")
            extra={k:bounded_input_text(v) for k,v in extra.items()}
            steps=validate_steps(self.expand(json.loads(recipe["steps_json"]),item,extra),recipe["kind"])
            if any(s['effect']=='commit' for s in steps) and body.get('authorize') is not True:
                raise SecurityError('explicit_action_authorization_required')
            old=c.execute("SELECT * FROM seller_jobs WHERE account_id=? AND idempotency_key=?",(owner,key)).fetchone()
            if old:
                if old["device_id"] != device or old["recipe_id"] != recipe["id"] or old["item_id"] != body.get("item_id") or old["steps_json"] != canonical_json(steps):
                    raise ConflictError("idempotency_conflict")
                return self.public_job(old)
            if c.execute("SELECT count(*) AS n FROM seller_jobs WHERE device_id=? AND status IN ('queued','running','waiting','unknown')",(device,)).fetchone()["n"] >= 20:
                raise ConflictError("seller_job_capacity")
            ident,now=str(uuid.uuid4()),now_seconds()
            due=safe_integer(body.get("due_at",now),minimum=now-60,maximum=now+30*86400,field="due_at")
            c.execute("INSERT INTO seller_jobs(id,account_id,device_id,item_id,recipe_id,kind,steps_json,status,idempotency_key,due_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',?,?,?,?)",
                      (ident,owner,device,body.get("item_id"),recipe["id"],recipe["kind"],canonical_json(steps),key,due,now,now))
            return self.public_job(c.execute("SELECT * FROM seller_jobs WHERE id=?",(ident,)).fetchone())

    @staticmethod
    def public_job(row):
        return present_job(row)

    def inventory(self, owner, device):
        current=self.require_device(owner,device)
        with self.db.connect() as c:
            data={name:c.execute(f"SELECT * FROM seller_{name} WHERE account_id=? AND device_id=? ORDER BY created_at DESC LIMIT 100",(owner,device)).fetchall() for name in ["items","recipes","jobs","schedules"]}
        names={r['id']:r['name'] for r in data['recipes']}
        names.update({self.builtin_recipe_id(owner,device,k):v['name'] for k,v in BUILTINS.items()})
        return {"sections":SECTIONS,"device_version":{'name':current['app_version'],'code':current['app_version_code']},
                "builtins":[{'id':k,'name':v['name'],'boundary':v['boundary'],
                             'recipe_id':self.builtin_recipe_id(owner,device,k),
                             'available':current['app_version_code']>=v.get('min_agent',35),
                             'requires_item':bool(v.get('requires_item')),'preview':list(dict.fromkeys(step.get('phase','执行过程') for step in v['steps'])),'section':v['section']} for k,v in BUILTINS.items() if k in ({'daily_preheat_v5','daily_maintenance_v5'} if current['app_version_code']>=63 else {'daily_preheat_v5'} if current['app_version_code']>=58 else {'daily_preheat_v4'} if current['app_version_code']>=57 else {'daily_preheat_v3','daily_maintenance_v4'})],
                "items":[{"id":r["id"],"title":r["title"],"status":r["status"],"image_count":len(json.loads(r["data_json"])["images"])} for r in data["items"]],
                "recipes":[{k:r[k] for k in r.keys() if k not in {"steps_json","outcomes_json"}}|{"steps":json.loads(r["steps_json"]),"section":next((v["section"] for k,v in BUILTINS.items() if r["id"]==self.builtin_recipe_id(owner,device,k)),recipe_section(r["kind"])),"hidden_builtin":r["id"] in {self.builtin_recipe_id(owner,device,k) for k in BUILTINS}} for r in data["recipes"]],
                "jobs":[self.public_job(r)|{"name":names.get(r['recipe_id'],'页面任务')} for r in data["jobs"]],"schedules":[dict(r) for r in data["schedules"]]}

    def product_metrics(self, owner, device):
        self.require_device(owner,device)
        from .product_metrics import trends
        with self.db.connect() as c:
            rows=c.execute("SELECT id,outcomes_json FROM seller_jobs WHERE account_id=? AND device_id=? AND kind='metrics' ORDER BY created_at DESC LIMIT 200",(owner,device)).fetchall()
        snapshots=[]
        for row in rows:
            for outcome in json.loads(row['outcomes_json']).values():
                if isinstance(outcome,dict) and isinstance(outcome.get('product_metrics'),dict):snapshots.append({**outcome['product_metrics'],'job_id':row['id']})
        latest=max(snapshots,key=lambda s:s['captured_at']) if snapshots else None
        # A confirmed empty seller list must not resurrect old goods as current.
        keys={p['key'] for p in latest.get('products',[])} if latest else set()
        products=[row for row in trends(snapshots) if row['key'] in keys]
        return {'state':latest['state'] if latest else 'not_collected','captured_at':latest['captured_at'] if latest else None,'products':products}

    def inspect(self, owner, device, ident):
        self.require_device(owner,device)
        with self.db.connect() as c:
            r=c.execute("SELECT * FROM seller_jobs WHERE id=? AND account_id=? AND device_id=?",(ident,owner,device)).fetchone()
        if not r: raise NotFoundError("job_not_found")
        value=self.public_job(r)
        with self.db.connect() as c:
            recipe=c.execute('SELECT name FROM seller_recipes WHERE id=? AND account_id=? AND device_id=?',(r['recipe_id'],owner,device)).fetchone()
        value['name']=recipe['name'] if recipe else '手机任务'
        with self.db.connect() as c:
            receipts=c.execute("SELECT id,idempotency_key,status,issued_at,result_received_at,error_code,result_json FROM device_tasks WHERE device_id=? AND requested_by_account_id=? AND idempotency_key LIKE ? ORDER BY issued_at,id",(device,owner,'seller:'+ident+':%')).fetchall()
        steps=json.loads(r['steps_json'])
        for key,outcome in json.loads(r['outcomes_json']).items():
            if key.isdigit() and int(key)<len(value['steps']):value['steps'][int(key)].update({k:v for k,v in outcome.items() if k!='state'})
        for receipt in receipts:
            parts=receipt['idempotency_key'].split(':'); index=int(parts[2])
            if index>=len(value['steps']): continue
            result=json.loads(receipt['result_json']) if receipt['result_json'] else {}
            evidence=[]
            if isinstance(result,dict):
                for node in result.get('nodes',[]):
                    if isinstance(node,dict) and node.get('visible_to_user') and not node.get('password'):
                        text=node.get('text') or node.get('description')
                        if text: evidence.append(str(text)[:160])
                        if len(evidence)>=12: break
            value['steps'][index].setdefault('receipts',[]).append({'task_id':receipt['id'],'status':receipt['status'],'started_at':receipt['issued_at'],'finished_at':receipt['result_received_at'],'error':receipt['error_code'],'evidence':evidence,'success':result.get('success') if isinstance(result,dict) else None})
        value['task']=self.phones.get_task(owner,device,r['task_id']) if r['task_id'] else None
        return value

    def last_observation(self, job, evidence=False):
        with self.db.connect() as c:
            rows=c.execute("SELECT id,idempotency_key,result_json,result_received_at FROM device_tasks WHERE device_id=? AND requested_by_account_id=? AND task_type='observe' AND status='succeeded' AND idempotency_key LIKE ?",(job['device_id'],job['account_id'],'seller:'+job['id']+':%')).fetchall()
        valid=[r for r in rows if int(r['idempotency_key'].split(':')[2])<job['step_index']]
        if not valid:return None
        latest=max(valid,key=lambda r:tuple(int(v) for v in r['idempotency_key'].split(':')[2:]))
        snapshot=json.loads(latest['result_json']) if latest['result_json'] else None
        return {'snapshot':snapshot,'captured_at':latest['result_received_at'],'task_id':latest['id']} if evidence else snapshot

    def record_outcome(self, job, outcome):
        values=json.loads(job['outcomes_json']);values[str(job['step_index'])]=outcome
        self.change(job['id'],outcomes_json=canonical_json(values),**({'step_index':job['step_index']+1,'probe_attempt':0,'task_id':None} if outcome.get('state') in {'completed','skipped'} else {}))

    def read_job(self, ident):
        with self.db.connect() as c:
            r=c.execute("SELECT * FROM seller_jobs WHERE id=?",(ident,)).fetchone()
        return dict(r) if r else None

    def control(self, owner, device, ident, action, *, confirm_manual=False, origin="controller"):
        self.require_device(owner,device)
        if not isinstance(action,str) or action not in {'pause','resume','stop'}: raise SecurityError('invalid_task_control')
        with self.db.transaction(immediate=True) as c:
            r=c.execute("SELECT * FROM seller_jobs WHERE id=? AND account_id=? AND device_id=?",(ident,owner,device)).fetchone()
            if not r: raise NotFoundError('job_not_found')
            if action=='stop' and r['status'] in {'succeeded','failed','cancelled'}:
                outcomes=json.loads(r['outcomes_json']);events=outcomes.setdefault('_control_events',[]);events.append({'action':action,'at':now_seconds(),'origin':origin,'outcome':'already_ended'});outcomes['_control_events']=events[-100:]
                c.execute('UPDATE seller_jobs SET outcomes_json=? WHERE id=?',(canonical_json(outcomes),ident));return self.public_job({**dict(r),'outcomes_json':canonical_json(outcomes)})
            if r['status'] not in {'queued','running','waiting'}:raise ConflictError('task_is_finished_or_unknown')
            state=r['control_state']; status=r['status']
            if action=='pause':
                if state in {'paused','pausing'}:return self.public_job(r)
                if state not in {'active','paused','pausing'}: raise ConflictError('task_is_stopping')
                state='pausing' if r['task_id'] else 'paused'
            elif action=='resume':
                if state!='paused': raise ConflictError('task_not_paused')
                if r['step_index']<len(json.loads(r['steps_json'])) and json.loads(r['steps_json'])[r['step_index']]['task_type']=='manual' and confirm_manual is not True:raise ConflictError('manual_completion_confirmation_required')
                state='active'
            else:
                state='stopping' if r['task_id'] else 'active'
                if not r['task_id']: status='cancelled'
            outcomes=json.loads(r['outcomes_json'])
            if action=='pause' and state in {'paused','pausing'} and str(r['step_index']) in outcomes:
                record=outcomes[str(r['step_index'])]
                if record.get('state')=='current' and record.get('duration_seconds'):
                    record['remaining_seconds']=wait_remaining(record,now_seconds())
                    c.execute("UPDATE seller_jobs SET outcomes_json=? WHERE id=?",(canonical_json(outcomes),ident))
            if action=='resume' and r['step_index']<len(json.loads(r['steps_json'])) and json.loads(r['steps_json'])[r['step_index']]['task_type']=='manual':
                outcomes[str(r['step_index'])]={'state':'completed','note':'本人通过继续按钮确认已手动完成','finished_at':now_seconds()}
                c.execute("UPDATE seller_jobs SET step_index=step_index+1,outcomes_json=? WHERE id=?",(canonical_json(outcomes),ident))
            if action=='resume' and r['error_code'] in {'purchase_no_affordable_candidate','purchase_page_not_confirmed','purchase_total_over_budget','purchase_checkout_not_confirmed'}:
                record=outcomes.get(str(r['step_index']),{})
                if record.get('phase','').startswith('purchase_'):
                    from .purchase_flow import retry_search
                    retry_search(record)
            if action=='resume':c.execute("UPDATE seller_jobs SET due_at=?,error_code=NULL WHERE id=?",(now_seconds(),ident))
            events=outcomes.setdefault('_control_events',[]);events.append({'action':action,'at':now_seconds(),'origin':origin,'state':state,'status':status});outcomes['_control_events']=events[-100:]
            c.execute('UPDATE seller_jobs SET outcomes_json=? WHERE id=?',(canonical_json(outcomes),ident))
            c.execute("UPDATE seller_jobs SET control_state=?,status=?,updated_at=? WHERE id=?",(state,status,now_seconds(),ident))
        return self.public_job(self.read_job(ident))

    def reconcile(self, owner, device, ident, body):
        self.require_device(owner,device)
        if set(body) != {"outcome"} or body["outcome"] not in {"confirmed","never_left","cancel"}:
            raise SecurityError("explicit_reconciliation_required")
        with self.db.transaction(immediate=True) as c:
            r=c.execute("SELECT * FROM seller_jobs WHERE id=? AND account_id=? AND device_id=?",(ident,owner,device)).fetchone()
            if not r: raise NotFoundError("job_not_found")
            if body["outcome"] == "cancel" and r["status"] in {"queued","unknown"}: state="cancelled"
            elif r["status"] == "unknown" and body["outcome"] in {"confirmed","never_left"}: state="succeeded" if body["outcome"] == "confirmed" else "failed"
            else: raise ConflictError("job_not_reconcilable")
            if body['outcome']=='cancel' and r['status']=='unknown':
                outcomes=json.loads(r['outcomes_json']);events=outcomes.setdefault('_control_events',[])
                events.append({'action':'stop','at':now_seconds(),'origin':'explicit_reconciliation','prior_error':r['error_code'],'effect_outcome':'unknown'})
                outcomes['_control_events']=events[-100:]
                c.execute('UPDATE seller_jobs SET outcomes_json=? WHERE id=?',(canonical_json(outcomes),ident))
            changed=c.execute("UPDATE seller_jobs SET status=?,error_code=?,updated_at=? WHERE id=? AND status=?",(state,"manual_"+body["outcome"],now_seconds(),ident,r["status"])).rowcount
            if not changed: raise ConflictError("job_state_changed")
            if state == "succeeded" and r["kind"] == "publish":
                c.execute("UPDATE seller_items SET status='published',updated_at=? WHERE id=? AND account_id=?",(now_seconds(),r["item_id"],owner))
        return {"id":ident,"status":state}

    def schedule(self, owner, device, body):
        self.require_device(owner,device)
        if set(body) - {"recipe_id","item_id","interval_seconds","first_due_at"}: raise SecurityError("invalid_schedule")
        interval=safe_integer(body.get("interval_seconds",86400),minimum=86400,maximum=604800,field="interval_seconds")
        now=now_seconds()
        due=safe_integer(body.get("first_due_at",now),minimum=now,maximum=now+604800,field="first_due_at")
        with self.db.transaction(immediate=True) as c:
            r=c.execute("SELECT kind FROM seller_recipes WHERE id=? AND account_id=? AND device_id=?",(body.get("recipe_id"),owner,device)).fetchone()
            if not r or r["kind"] not in {"brighten","metrics"}: raise SecurityError("only_daily_maintenance_can_repeat")
            if body.get("item_id") and not c.execute("SELECT id FROM seller_items WHERE id=? AND account_id=? AND device_id=?",(body["item_id"],owner,device)).fetchone(): raise NotFoundError("item_not_found")
            ident=str(uuid.uuid4())
            c.execute("INSERT INTO seller_schedules VALUES(?,?,?,?,?,?,?,1,?)",(ident,owner,device,body.get("item_id"),body["recipe_id"],interval,due,now))
        return {"id":ident,"next_due_at":due,"interval_seconds":interval}

    def pause_schedule(self, owner, device, ident):
        with self.db.transaction(immediate=True) as c:
            if not c.execute("UPDATE seller_schedules SET enabled=0 WHERE id=? AND account_id=? AND device_id=?",(ident,owner,device)).rowcount: raise NotFoundError("schedule_not_found")

    def due_schedules(self):
        now=now_seconds()
        with self.db.connect() as c:
            rows=c.execute("SELECT * FROM seller_schedules WHERE enabled=1 AND next_due_at<=? ORDER BY next_due_at LIMIT 20",(now,)).fetchall()
        for r in rows:
            try: self.job(r["account_id"],r["device_id"],{"recipe_id":r["recipe_id"],"item_id":r["item_id"],"authorize":True,"idempotency_key":f"schedule:{r['id']}:{r['next_due_at']}"})
            except (SecurityError,ConflictError,NotFoundError): continue
            due=r["next_due_at"]+((now-r["next_due_at"])//r["interval_seconds"]+1)*r["interval_seconds"]
            with self.db.transaction(immediate=True) as c:
                c.execute("UPDATE seller_schedules SET next_due_at=? WHERE id=? AND next_due_at=?",(due,r["id"],r["next_due_at"]))

    def runnable(self):
        now=now_seconds()
        with self.db.connect() as c:
            rows=c.execute("""SELECT j.* FROM seller_jobs j WHERE j.status IN ('queued','running','waiting') AND j.control_state!='paused' AND j.due_at<=?
                AND NOT EXISTS(SELECT 1 FROM seller_jobs u WHERE u.device_id=j.device_id AND u.status='unknown')
                AND NOT EXISTS(SELECT 1 FROM seller_jobs p WHERE p.device_id=j.device_id AND p.status IN ('queued','running','waiting') AND (p.due_at<=? OR p.status IN ('running','waiting'))
                 AND (p.created_at<j.created_at OR (p.created_at=j.created_at AND p.id<j.id))) ORDER BY j.due_at,j.created_at,j.id LIMIT 20""",(now,now)).fetchall()
        return [dict(r) for r in rows]

    def claim(self, ident):
        with self.db.transaction(immediate=True) as c:
            return c.execute("UPDATE seller_jobs SET status='running',updated_at=? WHERE id=? AND status='queued'",(now_seconds(),ident)).rowcount == 1

    def change(self, ident, **fields):
        if set(fields)-{"status","step_index","probe_attempt","task_id","error_code","control_state","outcomes_json","due_at"}: raise ValueError("invalid_update")
        if fields.get('status') in {'succeeded','failed','cancelled','unknown'}:fields['control_state']='active'
        fields["updated_at"]=now_seconds()
        with self.db.transaction(immediate=True) as c:
            c.execute("UPDATE seller_jobs SET "+",".join(k+"=?" for k in fields)+" WHERE id=? AND status IN ('queued','running','waiting')",(*fields.values(),ident))

    @staticmethod
    def committed(job):
        outcomes=json.loads(job['outcomes_json'])
        return any(s['effect']=='commit' and (s['task_type']!='brighten_all' or
            outcomes.get(str(i),{}).get('mutation_attempted') and not outcomes.get(str(i),{}).get('confirmed'))
            for i,s in enumerate(json.loads(job['steps_json'])[:job['step_index']+1]))

    def finish(self, job):
        with self.db.transaction(immediate=True) as c:
            c.execute("UPDATE seller_jobs SET status='succeeded',error_code=NULL,updated_at=? WHERE id=? AND status IN ('running','waiting')",(now_seconds(),job["id"]))
            if job["kind"] == "publish":
                c.execute("UPDATE seller_items SET status='published',updated_at=? WHERE id=? AND account_id=?",(now_seconds(),job["item_id"],job["account_id"]))


class SellerEngine:
    def __init__(self, ledger, io, dispatch, connected):
        self.ledger,self.io,self.dispatch,self.connected=ledger,io,dispatch,connected
        self.locks=[asyncio.Lock() for _ in range(64)]

    async def tick(self):
        await self.io.call(self.ledger.due_schedules)
        for job in await self.io.call(self.ledger.runnable):
            try: await self.advance(job)
            except (OSError,RuntimeError): log.warning("seller_dependency_unavailable job=%s",job["id"])

    async def control(self, owner, device, ident, action, *, confirm_manual=False, origin="controller"):
        async with self.locks[hash(ident)%len(self.locks)]:
            return await self.io.call(self.ledger.control,owner,device,ident,action,confirm_manual=confirm_manual,origin=origin)

    async def advance(self, job):
        async with self.locks[hash(job['id'])%len(self.locks)]:
            fresh=await self.io.call(self.ledger.read_job,job['id'])
            if not fresh or fresh['status'] not in {'queued','running','waiting'} or fresh['control_state']=='paused': return
            await self._advance(fresh)

    async def _advance(self, job):
        if job["status"] == "queued" and not await self.io.call(self.ledger.claim,job["id"]):
            return
        steps=json.loads(job["steps_json"])
        if job["step_index"] >= len(steps):
            await self.io.call(self.ledger.finish,job);return
        s=steps[job["step_index"]]
        owner,device=job["account_id"],job["device_id"]
        if s['task_type']=='content_browse':
            from .seller_browse import advance
            await advance(self,job,s);return
        if s['task_type'] in {'brighten_all','scan_goods'}:
            from .seller_maintenance import advance
            await advance(self,job,s);return
        if not job['task_id'] and ('unless' in s or s['task_type']=='manual' and 'when' in s):
            snapshot=await self.io.call(self.ledger.last_observation,job)
            if not matches(snapshot,[]):
                await self.io.call(self.ledger.change,job['id'],status='failed',error_code='complete_page_required');return
            absent='when' in s and not matches(snapshot,[s['when']]);present='unless' in s and matches(snapshot,[s['unless']])
            if absent or present:
                await self.io.call(self.ledger.record_outcome,job,{'state':'skipped','note':'当前无在售商品，无需擦亮；请准备上新' if present else '当前不需要此动作'});return
        if s['task_type']=='report':
            snapshot=await self.io.call(self.ledger.last_observation,job)
            note='已保存今日逐步记录。请复核商品的曝光、浏览、想要和咨询；需要更换标题、图片、价格的商品由本人处理。上新资料在独立整理区保存。'
            if isinstance(snapshot,dict) and any('你还没有在卖的宝贝' in str(n.get('description','')) for n in snapshot.get('nodes',[])):note='当前没有在售商品：今日无商品可擦亮，提醒准备上新。'
            metrics={}
            if s.get('name') in {'整理卖家数据，提醒人工优化与上新','整理卖家数据,提醒人工优化与上新'}:
                from .product_metrics import extract
                evidence=await self.io.call(self.ledger.last_observation,job,True)
                if evidence and evidence['captured_at'] is not None:metrics={'product_metrics':{**extract(evidence['snapshot'],evidence['captured_at']),'source_task_id':evidence['task_id']}}
            await self.io.call(self.ledger.record_outcome,job,{'state':'completed','note':note,'finished_at':now_seconds(),**metrics});return
        if s['task_type']=='manual':
            if s.get('name','').startswith('完成今日擦亮'):
                snapshot=await self.io.call(self.ledger.last_observation,job)
                if already_brightened(snapshot):
                    await self.io.call(self.ledger.record_outcome,job,{'state':'skipped','note':'闲鱼明确显示今日已擦亮；未重复擦亮，未进入超强擦亮','finished_at':now_seconds()});return
            await self.io.call(self.ledger.record_outcome,job,{'state':'current','note':s.get('name','需要本人处理')})
            await self.io.call(self.ledger.change,job['id'],control_state='paused',error_code='manual_step_required');return
        if s['task_type']=='route_home':
            if job['probe_attempt']>=12:
                await self.io.call(self.ledger.change,job['id'],status='failed',error_code='home_route_not_confirmed');return
            record=json.loads(job['outcomes_json']).get(str(job['step_index']),{})
            if not job['task_id']:
                if job['control_state'] in {'pausing','stopping'}:
                    await self.io.call(self.ledger.change,job['id'],status='cancelled' if job['control_state']=='stopping' else 'running',control_state='active' if job['control_state']=='stopping' else 'paused');return
                if not await self.connected(device):
                    await self.io.call(self.ledger.change,job['id'],control_state='paused',error_code='device_connection_interrupted');return
                parameters=record.get('parameters',{})
                task,_=await self.io.call(self.ledger.phones.create_task,owner,device,'action' if parameters else 'observe',f"seller:{job['id']}:{job['step_index']}:{job['probe_attempt']}",parameters,60)
                await self.io.call(self.ledger.change,job['id'],task_id=task['id'])
                await self.dispatch(task);return
            task=await self.io.call(self.ledger.phones.get_task,owner,device,job['task_id'])
            if task['status'] in {'queued','dispatched','running'}:return
            if task['status']!='succeeded':
                await self.io.call(self.ledger.record_outcome,job,{'state':'current','parameters':{},'note':'当前操作未确认，恢复后重新读取页面'})
                if job['control_state']=='stopping':
                    await self.io.call(self.ledger.change,job['id'],status='cancelled',task_id=None);return
                await self.io.call(self.ledger.change,job['id'],control_state='paused',task_id=None,probe_attempt=job['probe_attempt']+1,error_code=(task.get('error') or {}).get('code') or 'home_route_'+task['status']);return
            result=task['result'];next_params={}
            if task['task_type']=='observe':
                if not matches(result,[]):
                    if not isinstance(result,dict) or result.get('package')!=XIANYU_PACKAGE:
                        await self.io.call(self.ledger.change,job['id'],control_state='paused',task_id=None,probe_attempt=job['probe_attempt']+1,error_code='system_overlay_open' if isinstance(result,dict) and result.get('package')=='com.android.systemui' else 'xianyu_not_foreground');return
                elif interruption_selector(result):next_params={'action':'tap_unique_node','package':XIANYU_PACKAGE,'selector':interruption_selector(result)}
                elif matches(result,[{'kind':'text','value':'推荐'},{'kind':'text','value':'我的'}]):
                    await self.io.call(self.ledger.record_outcome,job,{'state':'completed','note':'已核对首页推荐与底部导航','finished_at':now_seconds()})
                    if job['control_state']=='pausing':await self.io.call(self.ledger.change,job['id'],control_state='paused')
                    if job['control_state']=='stopping':await self.io.call(self.ledger.change,job['id'],status='cancelled')
                    return
                elif matches(result,[{'kind':'text','value':'闲鱼'},{'kind':'text','value':'我的'}]):next_params={'action':'tap_unique_node','package':XIANYU_PACKAGE,'selector':{'kind':'text','value':'闲鱼'}}
                else:next_params={'action':'back'}
            elif not isinstance(result,dict) or result.get('success') is not True:
                await self.io.call(self.ledger.change,job['id'],status='failed',error_code='home_route_action_not_proven');return
            await self.io.call(self.ledger.record_outcome,job,{'state':'current','parameters':next_params})
            await self.io.call(self.ledger.change,job['id'],task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused' if job['control_state']=='pausing' else 'active',**({'status':'cancelled'} if job['control_state']=='stopping' else {}));return
        if s['task_type']=='wait':
            record=json.loads(job['outcomes_json']).get(str(job['step_index']))
            if not record:
                record={'state':'current','started_at':now_seconds(),'duration_seconds':s['parameters']['seconds']}
                await self.io.call(self.ledger.record_outcome,job,record)
            if record.get('remaining_seconds') is not None and job['control_state']=='active' and not record.get('recovery_parameters'):
                record['started_at']=now_seconds();record['duration_seconds']=min(record.pop('remaining_seconds'),record.get('duration_seconds',s['parameters']['seconds']),s['parameters']['seconds']);record['last_verified_at']=record['started_at'];record['last_moved_at']=record['started_at']
                await self.io.call(self.ledger.record_outcome,job,record)
            if job['task_id']:
                task=await self.io.call(self.ledger.phones.get_task,owner,device,job['task_id'])
                if task and task['status'] in {'queued','dispatched','running'}:return
                if job['control_state']=='stopping':
                    await self.io.call(self.ledger.change,job['id'],status='cancelled',task_id=None);return
                if task and task['task_type']=='action' and (record.get('recovery_parameters') or record.get('movement_parameters')):
                    if task['status']=='succeeded' and isinstance(task.get('result'),dict) and task['result'].get('success') is True:
                        if record.get('movement_parameters'):
                            record.pop('movement_parameters');record['motion_count']=record.get('motion_count',0)+1;record['last_moved_at']=now_seconds();record['note']='已浏览页面，重新确认闲鱼后继续计时'
                        else:
                            record.pop('recovery_parameters');record['note']='弹窗已关闭，重新确认浏览页后继续计时'
                        await self.io.call(self.ledger.record_outcome,job,record)
                        await self.io.call(self.ledger.change,job['id'],task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused' if job['control_state']=='pausing' else 'active');return
                selector=interruption_selector(task.get('result')) if task and task['status']=='succeeded' and task['task_type']=='observe' else None
                if selector and record.get('close_attempts',0)<2 and job['control_state']=='active':
                    record['remaining_seconds']=wait_remaining(record)
                    record['recovery_parameters']={'action':'tap_unique_node','package':XIANYU_PACKAGE,'selector':selector};record['close_attempts']=record.get('close_attempts',0)+1;record['note']='浏览计时暂停，先关闭可确认的干扰弹窗'
                    await self.io.call(self.ledger.record_outcome,job,record)
                    await self.io.call(self.ledger.change,job['id'],task_id=None,probe_attempt=job['probe_attempt']+1);return
                verified=task and task['status']=='succeeded' and not selector and matches(task.get('result'),[])
                if not verified and job['control_state']=='active' and task and task['status']=='succeeded' and isinstance(task.get('result'),dict) and task['result'].get('package')==XIANYU_PACKAGE and task['result'].get('truncated') and record.get('read_retries',0)<3:
                    record['remaining_seconds']=wait_remaining(record);record['read_retries']=record.get('read_retries',0)+1;record['note']='页面仍在加载，稍后重新读取'
                    await self.io.call(self.ledger.record_outcome,job,record)
                    await self.io.call(self.ledger.change,job['id'],task_id=None,probe_attempt=job['probe_attempt']+1,due_at=now_seconds()+3);return
                if not verified or job['control_state']=='pausing':
                    if task and task['task_type']=='action':record.pop('recovery_parameters',None);record.pop('movement_parameters',None)
                    if 'remaining_seconds' not in record:record['remaining_seconds']=wait_remaining(record)
                    record['note']='计时已暂停；继续前需保持手机亮屏、解锁和闲鱼前台'
                    await self.io.call(self.ledger.record_outcome,job,record)
                    await self.io.call(self.ledger.change,job['id'],control_state='paused',task_id=None,probe_attempt=job['probe_attempt']+1,error_code=None if verified else ((task.get('error') or {}).get('code') if task else None) or 'browse_page_not_confirmed');return
                record['read_retries']=0;record['last_verified_at']=now_seconds();await self.io.call(self.ledger.record_outcome,job,record)
                if now_seconds()>=record['started_at']+record['duration_seconds']:
                    await self.io.call(self.ledger.record_outcome,job,{'state':'completed','started_at':record['started_at'],'finished_at':now_seconds(),'note':'计划停留已结束，期间每10秒核对闲鱼前台；手机回执可回看'});return
                if s['parameters'].get('browse_motion') and now_seconds()-record.get('last_moved_at',record['started_at'])>=15:
                    record['movement_parameters']={'action':'scroll_page','package':XIANYU_PACKAGE,'direction':'up' if record.get('motion_count',0)%3==2 else 'down'}
                    await self.io.call(self.ledger.record_outcome,job,record)
                    await self.io.call(self.ledger.change,job['id'],task_id=None,probe_attempt=job['probe_attempt']+1,due_at=now_seconds());return
                await self.io.call(self.ledger.change,job['id'],task_id=None,probe_attempt=job['probe_attempt']+1,due_at=min(record['started_at']+record['duration_seconds'],now_seconds()+10));return
            if job['control_state']=='pausing':
                await self.io.call(self.ledger.change,job['id'],control_state='paused');return
            if not await self.connected(device):
                record['remaining_seconds']=wait_remaining(record)
                await self.io.call(self.ledger.record_outcome,job,record)
                await self.io.call(self.ledger.change,job['id'],control_state='paused',error_code='browse_connection_unavailable');return
            try:task,_=await self.io.call(self.ledger.phones.create_task,owner,device,'action' if record.get('recovery_parameters') or record.get('movement_parameters') else 'observe',f"seller:{job['id']}:{job['step_index']}:{job['probe_attempt']}",record.get('recovery_parameters') or record.get('movement_parameters',{}),60)
            except TaskCapacityError:return
            await self.io.call(self.ledger.change,job['id'],task_id=task['id'])
            await self.dispatch(task);return
        if not job['task_id'] and ('when' in s or s['task_type']=='browse_item'):
            snapshot=await self.io.call(self.ledger.last_observation,job)
            if not isinstance(snapshot,dict) or snapshot.get('package')!=XIANYU_PACKAGE or snapshot.get('truncated'):
                await self.io.call(self.ledger.change,job['id'],status='failed',error_code='complete_page_required');return
            if 'when' in s and not matches(snapshot,[s['when']]):
                await self.io.call(self.ledger.record_outcome,job,{'state':'skipped','note':'当前页面没有 '+s['when']['value']+'，无需执行'});return
            if s['task_type']=='browse_item':
                candidates=[n for n in snapshot.get('nodes',[]) if n.get('visible_to_user') and not n.get('password') and n.get('description','').startswith('pic')]
                if not candidates:
                    await self.io.call(self.ledger.record_outcome,job,{'state':'skipped','note':'当前推荐页无可确认的商品卡片'});return
                selector={'kind':'description','value':candidates[0]['description']}
                s={**s,'task_type':'action','parameters':{'action':'tap_unique_node','package':XIANYU_PACKAGE,'selector':selector}}
        elif s['task_type']=='browse_item':
            s={**s,'task_type':'action'}
        recovery=json.loads(job['outcomes_json']).get(str(job['step_index']),{}).get('recovery_parameters') if s['task_type']=='observe' else None
        if recovery:s={**s,'task_type':'action','parameters':recovery,'effect':'navigation'}
        if job["task_id"]:
            task=await self.io.call(self.ledger.phones.get_task,owner,device,job["task_id"])
        else:
            if job['control_state'] in {'pausing','stopping'}:
                await self.io.call(self.ledger.change,job['id'],status='cancelled' if job['control_state']=='stopping' else 'running',control_state='active' if job['control_state']=='stopping' else 'paused');return
            if not await self.connected(device):
                await self.io.call(self.ledger.change,job['id'],control_state='paused',error_code='device_connection_interrupted');return
            try:
                task,_=await self.io.call(self.ledger.phones.create_task,owner,device,s["task_type"],f"seller:{job['id']}:{job['step_index']}:{job['probe_attempt']}",s["parameters"],60)
            except TaskCapacityError: return
            except (NotFoundError,UnsupportedDeviceTaskError):
                await self.io.call(self.ledger.change,job["id"],status="unknown" if self.ledger.committed(job) else "failed",error_code="device_unavailable_or_upgrade_required");return
            # Same durable task key on restart closes insertion->attachment crash.
            await self.io.call(self.ledger.change,job["id"],status="running",task_id=task["id"])
            await self.dispatch(task)
        if not task:
            await self.io.call(self.ledger.change,job["id"],status="unknown",error_code="task_missing");return
        if task["status"] in {"queued","dispatched","running"}: return
        if task["status"] != "succeeded":
            if job['control_state']=='stopping':
                uncertain=self.ledger.committed(job) or s['parameters'].get('action')=='set_description_text' and (task.get('error') or {}).get('code') not in DESCRIPTION_NO_WRITE_ERRORS
                await self.io.call(self.ledger.change,job['id'],status='unknown' if uncertain else 'cancelled',control_state='active',error_code='stopped_with_unconfirmed_result' if uncertain else 'stopped_by_user');return
            if (task.get('error') or {}).get('code')=='bridge_paused' or (task.get('error') or {}).get('code') in {'screen_off','device_locked','xianyu_not_foreground','system_ui_foreground','accessibility_unavailable'} and not self.ledger.committed(job) and s['parameters'].get('action')!='set_description_text':
                await self.io.call(self.ledger.change,job['id'],status='cancelled' if job['control_state']=='stopping' else 'running',control_state='active' if job['control_state']=='stopping' else 'paused',task_id=None,probe_attempt=job['probe_attempt']+1);return
            input_unknown=(s['parameters'].get('action')=='set_description_text' and
                not (task['status'] in {'failed','rejected'} and (task.get('error') or {}).get('code') in DESCRIPTION_NO_WRITE_ERRORS))
            await self.io.call(self.ledger.change,job["id"],status="unknown" if self.ledger.committed(job) or input_unknown else "failed",error_code="phone_task_"+task["status"]);return
        result=task["result"]
        if job['control_state']=='stopping':
            await self.io.call(self.ledger.change,job['id'],status='unknown' if self.ledger.committed(job) else 'cancelled',task_id=None,control_state='active',step_index=job['step_index']+1);return
        if recovery:
            if not isinstance(result,dict) or result.get('success') is not True:
                await self.io.call(self.ledger.record_outcome,job,{'state':'current','note':'未确认关闭弹窗，恢复后重新读取页面'})
                await self.io.call(self.ledger.change,job['id'],control_state='paused',task_id=None,probe_attempt=job['probe_attempt']+1,error_code='interruption_close_not_confirmed');return
            await self.io.call(self.ledger.record_outcome,job,{'state':'current','note':'干扰页面已关闭，正在重新确认目标页面'})
            await self.io.call(self.ledger.change,job['id'],task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused' if job['control_state']=='pausing' else 'active');return
        if s['task_type']=='observe' and isinstance(result,dict) and result.get('package')!=XIANYU_PACKAGE and not self.ledger.committed(job):
            await self.io.call(self.ledger.change,job['id'],control_state='paused',task_id=None,probe_attempt=job['probe_attempt']+1,error_code='system_overlay_open' if result.get('package')=='com.android.systemui' else 'xianyu_not_foreground');return
        if s['task_type']=='observe' and job['probe_attempt']<7 and interruption_selector(result):
            await self.io.call(self.ledger.record_outcome,job,{'state':'current','note':'发现可确认的广告/弹窗，先关闭再核对页面','recovery_parameters':{'action':'tap_unique_node','package':XIANYU_PACKAGE,'selector':interruption_selector(result)}})
            await self.io.call(self.ledger.change,job['id'],task_id=None,probe_attempt=job['probe_attempt']+1);return
        if s["task_type"] == "observe" and not matches(result,s["expect"]):
            if job["probe_attempt"] < 7:
                await self.io.call(self.ledger.change,job["id"],status="waiting",task_id=None,probe_attempt=job["probe_attempt"]+1,control_state="paused" if job["control_state"]=="pausing" else "active")
            else:
                await self.io.call(self.ledger.change,job["id"],status="unknown" if self.ledger.committed(job) else "failed",error_code="page_evidence_not_confirmed")
            return
        if s["task_type"] == "action" and (not isinstance(result,dict) or result.get("success") is not True):
            await self.io.call(self.ledger.change,job["id"],status="unknown" if self.ledger.committed(job) or s['parameters'].get('action')=='set_description_text' else "failed",error_code="action_result_not_proven");return
        if s['parameters'].get('action') == 'set_description_text' and result.get('verified') is not True:
            await self.io.call(self.ledger.change,job['id'],status='unknown',error_code='description_result_not_verified');return
        await self.io.call(self.ledger.change,job["id"],status="running",step_index=job["step_index"]+1,task_id=None,probe_attempt=0,control_state="paused" if job["control_state"]=="pausing" else "active")

    async def run(self):
        while True:
            try: await self.tick()
            except asyncio.CancelledError: raise
            except Exception: log.warning("seller_tick_unavailable")
            await asyncio.sleep(2)
