"""Human task phases and step records shared by native and web controls."""
import json
from .security import now_seconds

def step_title(step):
    if step.get('name'):return step['name']
    if step['task_type']=='wait':return '浏览停留 '+str(step['parameters']['seconds'])+' 秒'
    if step['task_type']=='route_home':return '关闭可确认的干扰页面，进入首页'
    if step['task_type']=='manual':return '需要本人处理'
    if step['task_type']=='content_browse':return '浏览商品详情'
    if step['task_type']=='browse_item':return '阅读推荐商品详情'
    p=step.get('parameters',{});action=p.get('action');selector=p.get('selector',{}).get('value','')
    if step['task_type']=='observe':
        targets='、'.join(v['value'].replace('\n',' / ') for v in step.get('expect',[]))
        return '确认页面：'+targets if targets else '保存当前页面信息'
    return {'launch_app':'打开闲鱼','back':'返回上一页','scroll_page':'浏览下一页','set_node_text':'填写内容','set_description_text':'填写商品描述','import_image':'导入商品图片'}.get(action,'打开 '+selector.replace('\n',' / '))

ERROR_MESSAGES={'device_connection_interrupted':'连接中断，进度已保留；回到手机后可继续','xianyu_not_foreground':'读取时闲鱼不在前台','home_route_failed':'返回首页的操作未完成','home_route_not_confirmed':'未能稳定进入首页','browse_page_not_confirmed':'页面未能确认，浏览计时已暂停','browse_connection_unavailable':'手机连接中断，浏览计时已暂停','system_overlay_open':'通知栏或系统页面打开，后续操作已暂停','action_result_not_proven':'该操作未取得成功回执','manual_step_required':'等待本人处理','page_evidence_not_confirmed':'目标页面尚未确认','device_unavailable_or_upgrade_required':'手机连接或应用版本未就绪'}
ERROR_MESSAGES.update({'item_detail_not_confirmed':'商品详情尚未读到，稍后可继续', 'browse_no_readable_items':'暂时没有找到可阅读商品，可重试', 'browse_action_interrupted':'浏览暂时中断，可继续', 'free_brighten_not_available':'普通一键擦亮入口暂不可用，未点击付费擦亮',
    'brighten_result_unknown':'擦亮已派发，结果未确认；不会重复执行',
    'maintenance_page_interrupted':'页面或连接中断，可返回闲鱼后继续',
    'maintenance_action_failed':'翻页未完成，可返回闲鱼后继续',
    'seller_list_not_confirmed':'尚未进入在售商品列表',
    'seller_count_not_confirmed':'在售商品数量未能读取',
    'seller_count_changed':'在售商品数量发生变化，请重新采集',
    'seller_collection_incomplete':'商品数据未读全，可继续或重新采集'})

ERROR_MESSAGES.update({
    'screen_off':'手机已息屏，亮屏解锁后可继续',
    'device_locked':'手机已锁定，解锁后可继续',
    'accessibility_unavailable':'无障碍服务暂不可用，请到设置检查后继续',
    'accessibility_not_connected':'无障碍服务未连接，请到设置检查后继续',
    'favorite_result_not_confirmed':'收藏结果未确认，可继续核对；不会重复点击',
    'favorite_counter_unavailable':'尚未读到收藏人数，稍后可继续',
    'purchase_page_not_confirmed':'未能稳定找到购买入口，可重试',
    'purchase_no_affordable_candidate':'浏览已完成，暂未找到0.10元以内的合适商品',
    'purchase_total_over_budget':'含运费总价超过0.10元，已停下，未下单',
    'purchase_checkout_not_confirmed':'购买页面需要本人处理，未下单',
})

def present_job(row):
    plan=json.loads(row['steps_json']);outcomes=json.loads(row['outcomes_json']);index=row['step_index'];status=row['status'];mode=row['control_state']
    steps=[];stages=[]
    for i,step in enumerate(plan):
        state='completed' if i<index else ('paused' if mode=='paused' else 'failed' if status in {'failed','unknown'} else 'stopped' if status=='cancelled' else 'current') if i==index else 'pending'
        record=outcomes.get(str(i),{})
        if record.get('state')=='skipped':state='skipped'
        phase=step.get('phase','执行过程')
        entry={'number':i+1,'name':step_title(step),'phase':phase,'state':state,'technical':step['task_type']=='observe',**{k:v for k,v in record.items() if k not in {'state','parameters','recovery_parameters'}}}
        steps.append(entry)
        stage=next((v for v in stages if v['name']==phase),None)
        if stage is None:stage={'name':phase,'numbers':[]};stages.append(stage)
        stage['numbers'].append(i+1)
    for stage in stages:
        values=[steps[n-1] for n in stage['numbers']];states=[v['state'] for v in values]
        stage['state']=next((s for s in ['paused','failed','stopped','current'] if s in states),'completed' if all(s in {'completed','skipped'} for s in states) else 'pending')
        stage['summary']=next((v.get('note','') for v in reversed(values) if v.get('note')), '')
    current=steps[index] if index<len(steps) else None
    wait=outcomes.get(str(index),{});remaining=wait.get('remaining_seconds')
    if remaining is None and wait.get('duration_seconds') is not None:remaining=max(0,wait['started_at']+wait['duration_seconds']-now_seconds())
    message=ERROR_MESSAGES.get(row['error_code'],'') if row['error_code'] else ''
    if row['error_code'] and not message:message='操作受阻，请查看这一步的回执'
    return {k:row[k] for k in row.keys() if k not in {'steps_json','outcomes_json'}}|{'step_count':len(plan),'steps':steps,'stages':stages,'current_phase':current['phase'] if current else stages[-1]['name'] if stages else '执行过程','current_step':'等待手机执行' if status=='queued' else current['name'] if current and status not in {'failed','cancelled','succeeded'} else '已完成' if status=='succeeded' else '已停止' if status in {'failed','cancelled'} else '等待手机执行','requires_manual':index<len(plan) and plan[index]['task_type']=='manual','wait_remaining_seconds':remaining,'error_message':message,'control_events':outcomes.get('_control_events',[])}
