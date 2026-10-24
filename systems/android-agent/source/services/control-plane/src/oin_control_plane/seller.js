'use strict';
const root=document.getElementById('seller'), api=root.dataset.api, csrf=root.dataset.csrf;
const el=id=>document.getElementById(id), notice=(s,bad=false)=>{el('notice').textContent=s;el('notice').className=bad?'error':'ok';if(el('task-feedback')){el('task-feedback').textContent=s;el('task-feedback').className=bad?'error':'ok';}};
async function request(path,body){const r=await fetch(api+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok){const error=Error(typeof d.error==='string'?d.error:JSON.stringify(d.error));error.status=r.status;throw error;}return d;}
const button=(label,action)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.addEventListener('click',()=>perform(action));return b;};
async function perform(action){try{await action();}catch(e){notice(e.message,true);}}
function steps(){return JSON.parse(el('steps').value||'[]');}
function writeSteps(s){el('steps').value=JSON.stringify(s,null,2);}
function readStep(){return {task_type:'observe',parameters:{},effect:'read',expect:[]};}
writeSteps([{task_type:'action',parameters:{action:'launch_app',package:'com.taobao.idlefish'},effect:'navigation'},readStep()]);
function push(s){const all=steps();all.push(s);writeSteps(all);}
async function phone(type,parameters={}){
 const d=await request('/tasks',{task_type:type,parameters,ttl_seconds:60,idempotency_key:'seller-ui-'+crypto.randomUUID()});
 for(let i=0;i<30;i++){await new Promise(r=>setTimeout(r,2000));const t=(await request('/tasks/'+d.task.id)).task;if(t.status==='succeeded')return t.result;if(!['queued','dispatched','running'].includes(t.status))throw Error('手机任务未成功：'+t.status+(t.error?.code?'（'+t.error.code+'）':''));}
 throw Error('手机任务仍等待回执；请查看设备任务，勿重复不可逆操作。');
}
let phoneInspectionBusy=false;
async function inspectPhone(open){
 if(phoneInspectionBusy)return;
 phoneInspectionBusy=true;el('launch').disabled=true;el('observe').disabled=true;
 try{
  if(open){notice('正在打开闲鱼，随后自动读取…');await phone('action',{action:'launch_app',package:'com.taobao.idlefish'});}
  await readCurrentPage();
 }finally{phoneInspectionBusy=false;el('launch').disabled=false;el('observe').disabled=false;}
}
el('launch').addEventListener('click',()=>perform(()=>inspectPhone(true)));
el('observe').addEventListener('click',()=>perform(()=>inspectPhone(false)));
el('phone-back').addEventListener('click',()=>perform(async()=>{
 if(phoneInspectionBusy)return;
 phoneInspectionBusy=true;el('launch').disabled=true;el('observe').disabled=true;
 try{const snapshot=await phone('observe');
  if(snapshot?.package!=='com.taobao.idlefish')throw Error('不是闲鱼前台，未执行返回');
  await phone('action',{action:'back'});await readCurrentPage();
 }finally{phoneInspectionBusy=false;el('launch').disabled=false;el('observe').disabled=false;}
}));
async function readCurrentPage(){
 notice('读取手机页面…');const snapshot=await phone('observe');el('nodes').replaceChildren();
 if(!snapshot)throw Error('手机未返回有效页面');
 if(snapshot.package!=='com.taobao.idlefish')throw Error('手机当前前台是 '+(snapshot.package||'未知窗口')+'，不是闲鱼；未录入流程');
 if(snapshot.truncated)throw Error('闲鱼页面读取被截断（'+(snapshot.truncation_reason||'未知原因')+'），不能作为完整校验');
 const nodes=snapshot.nodes||[];
 const pricePanel=nodes.some(n=>n.visible_to_user===true&&n.description==='定价\n基础软件服务费\n成交额 (含运费) *0.6%');
 for(const n of nodes){if(n.password||n.visible_to_user!==true)continue;const kind=['resource_id','description','text'].find(k=>n[k]&&nodes.filter(x=>x.visible_to_user===true&&x[k]===n[k]).length===1);if(!kind)continue;
  const selector={kind,value:n[kind]},row=document.createElement('div');row.style.borderBottom='1px solid #ddd';const text=document.createElement('p');text.textContent=(n.text||n.description||n.resource_id).slice(0,130);row.append(text);
  row.append(button('作为校验',()=>{const all=steps();let r=all.at(-1);if(!r||r.task_type!=='observe'){r=readStep();all.push(r);}if(!r.expect.some(x=>x.kind===kind&&x.value===selector.value))r.expect.push(selector);writeSteps(all);}));
  if(n.enabled)row.append(button('录入点击',()=>push({task_type:'action',parameters:{action:n.clickable?'click_unique_node':'tap_unique_node',package:'com.taobao.idlefish',selector},effect:el('commit').checked?'commit':'navigation'})));
  if(n.editable)row.append(button('录入输入',()=>{if(!el('input-value').value)throw Error('先填写输入模板');push({task_type:'action',parameters:{action:'set_node_text',package:'com.taobao.idlefish',selector},effect:'navigation'});const all=steps();all.at(-1).parameters.value=el('input-value').value;writeSteps(all);}));
  el('nodes').append(row);
  // Exploration is navigation-only: never expose a direct publish/save/send action.
  const label=n.text||n.description;
  const priceKey=pricePanel&&n.enabled&&(/^[0-9.]$/.test(label)||label==='删除');
  if(priceKey||(pricePanel&&label==='取消')||['闲鱼币','点击领币','现金签到','闲鱼','卖闲置','我的','我的，未选中状态','我的，选中状态','我发布的','相册','拍照','返回','关闭','发闲置\n自己拍图卖·啥都能换钱','价格设置','添加图片','发货方式\n包邮','描述, 描述一下宝贝的品牌型号、货品来源…'].includes(label))row.append(button(priceKey?(label==='删除'?'退格并读取':'输入并读取'):'进入并读取',async()=>{
   if(phoneInspectionBusy)return;
   phoneInspectionBusy=true;el('launch').disabled=true;el('observe').disabled=true;
   try{notice('进入页面…');await phone('action',{action:n.clickable?'click_unique_node':'tap_unique_node',package:'com.taobao.idlefish',selector});await readCurrentPage();}
   finally{phoneInspectionBusy=false;el('launch').disabled=false;el('observe').disabled=false;}
  }));
 }
 notice('已读取。配置依赖真实页面；不会自动猜按钮。');
}
el('new-read').addEventListener('click',()=>perform(()=>push(readStep())));
el('add-image').addEventListener('click',()=>perform(()=>push({task_type:'action',parameters:{action:'import_image',image_index:Number(el('image-index').value)-1},effect:'navigation'})));
el('reset-recipe').addEventListener('click',()=>writeSteps([{task_type:'action',parameters:{action:'launch_app',package:'com.taobao.idlefish'},effect:'navigation'},readStep()]));
el('save-recipe').addEventListener('click',()=>perform(async()=>{await request('/seller/recipes',{name:el('recipe-name').value,kind:el('recipe-kind').value,steps:steps()});await refresh();notice('流程已保存');}));
async function image(file){
 if(!['image/jpeg','image/png'].includes(file.type)||file.size>20*1024*1024)throw Error('图片仅支持20MB内的JPEG/PNG');
 const bitmap=await createImageBitmap(file),canvas=document.createElement('canvas');const scale=Math.min(1,1600/Math.max(bitmap.width,bitmap.height));canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
 let blob;for(let quality=.85;quality>=.25;quality-=.1){blob=await new Promise(r=>canvas.toBlob(r,'image/jpeg',quality));if(blob&&blob.size<=262144)break;}
 if(!blob||blob.size>262144)throw Error('图片压缩后仍超限，请先缩小图片');const bytes=new Uint8Array(await blob.arrayBuffer());const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(x=>x.toString(16).padStart(2,'0')).join('');let binary='';for(const b of bytes)binary+=String.fromCharCode(b);
 return {mime_type:'image/jpeg',filename:'oin-'+sha.slice(0,16)+'.jpg',sha256:sha,data_base64:btoa(binary)};
}
el('item-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const files=Array.from(el('images').files);if(files.length>9)throw Error('最多9张图片');notice('处理素材…');const images=[];for(const f of files)images.push(await image(f));await request('/seller/items',{title:el('title').value,description:el('description').value,price:el('price').value,images});await refresh();notice('商品已保存，还未发布到闲鱼');});});
let catalog, selectedSection='preheat';
function selectSection(id){selectedSection=id;renderWorkflows();}
const concise=name=>name.replace(/^(上新准备|维护准备) · /,'');
el('resume-task').addEventListener('click',()=>el('run').click());
let currentJob;
el('cancel-current').addEventListener('click',()=>perform(async()=>{
 if(!currentJob||currentJob.status!=='queued')return;
 await request('/seller/jobs/'+currentJob.id+'/reconcile',{outcome:'cancel'});await refresh();notice('排队任务已取消');
}));
function showCurrentTask(){
 const active=catalog.jobs.find(j=>['running','waiting','unknown'].includes(j.status))||catalog.jobs.find(j=>j.status==='queued');currentJob=active||catalog.jobs[0];
 const states={queued:'等待执行',running:'执行中',waiting:'正在核对',unknown:'结果待核对',succeeded:'任务成功',failed:'未完成',cancelled:'已取消'};
 el('current-state').textContent=currentJob?(states[currentJob.status]||currentJob.status):'空闲';
 el('current-state').className='status '+(currentJob?.status||'');
 el('current-name').textContent=currentJob?concise(currentJob.name||'页面任务'):'目前没有执行任务';
 el('current-step').textContent=currentJob?currentJob.current_step||'等待状态同步':'选择下方任务后开始';
 el('current-progress').max=Math.max(1,currentJob?.step_count||1);el('current-progress').value=currentJob?.step_index||0;
 el('current-count').textContent=currentJob?'共 '+(currentJob.stages?.length||1)+' 个阶段':'';el('current-phase').textContent=currentJob?.current_phase||'';el('current-reason').textContent=currentJob?.error_message||'';if(currentJob?.wait_remaining_seconds!=null)el('current-step').textContent+=' · 剩余 '+clockLabel(currentJob.wait_remaining_seconds);
 el('stage-progress').replaceChildren();for(const stage of currentJob?.stages||[]){const row=button('',()=>perform(()=>showStepDetails(currentJob.id)));row.className='stage-card '+stage.state;const name=document.createElement('strong'),state=document.createElement('span');name.textContent=stage.name;state.textContent=phaseLabel(stage.state);row.append(name,state);if(stage.summary){const summary=document.createElement('small');summary.textContent=stage.summary;row.append(summary);}el('stage-progress').append(row);}
 el('cancel-current').hidden=true;
 el('current-state').textContent=controlState(currentJob);
 const controllable=currentJob&&['queued','running','waiting'].includes(currentJob.status);
 el('pause-current').hidden=!controllable||currentJob.control_state!=='active';el('continue-current').hidden=!controllable||currentJob.control_state!=='paused';el('continue-current').textContent=currentJob?.requires_manual?'完成后继续':'继续这次任务';el('stop-current').hidden=!controllable||currentJob.control_state==='stopping';el('details-current').hidden=!currentJob;el('retry-current').hidden=!currentJob||!(catalog.builtins||[]).some(t=>t.available&&t.recipe_id===currentJob.recipe_id)||!(currentJob.control_state==='paused'||['failed','cancelled'].includes(currentJob.status));el('retry-current').textContent=currentJob?.control_state==='paused'?'重新运行':'重试';
}

const taskStates={queued:'等待执行',running:'执行中',waiting:'等待页面证实',unknown:'结果需核对',succeeded:'任务成功',failed:'未完成',cancelled:'已结束'};
function controlState(job){return job?.control_state==='active'||!job?.control_state?taskStates[job?.status]||'暂无任务':({paused:'已暂停',pausing:'正在暂停 · 等待当前步骤回执',stopping:'正在结束 · 等待当前步骤回执'}[job.control_state]);}
for(const [id,action] of [['pause-current','pause'],['continue-current','resume'],['stop-current','stop']])el(id).addEventListener('click',()=>perform(async()=>{if(!currentJob)return;const confirm_manual=action==='resume'&&currentJob.requires_manual;if(confirm_manual&&!confirm('确认你已经在闲鱼完成这项人工操作。此确认会作为人工完成记录保存。'))return;await request('/seller/jobs/'+currentJob.id+'/control',{action,...(confirm_manual?{confirm_manual:true}:{})});await refresh();notice('控制已受理：'+controlState(currentJob));}));
const phaseStates={completed:'已完成',current:'进行中',paused:'已暂停',pending:'未开始',failed:'已停止',stopped:'已结束',skipped:'无需执行'};
function phaseLabel(value){return phaseStates[value]||value;}
function clockLabel(seconds){if(seconds==null)return '';return Math.floor(seconds/60)+'分'+seconds%60+'秒';}
function receiptBlock(step){const block=document.createElement('details');block.className='receipt-block';const summary=document.createElement('summary');summary.textContent='查看操作回执';block.append(summary);
 for(const receipt of step.receipts||[]){const p=document.createElement('p');p.textContent=(taskStates[receipt.status]||receipt.status)+' · '+new Date(receipt.started_at*1000).toLocaleTimeString()+(receipt.error?' · '+receipt.error:'');block.append(p);if(receipt.evidence?.length){const evidence=document.createElement('p');evidence.className='muted';evidence.textContent='页面信息：'+receipt.evidence.slice(0,6).join('；');block.append(evidence);}}
 if(!step.receipts?.length){const p=document.createElement('p');p.textContent=step.note||'尚无操作回执';block.append(p);}return block;}
function readableStep(step){const row=document.createElement('div');row.className='readable-step';const name=document.createElement('p');name.className='step-name';name.textContent=step.name;const state=document.createElement('span');state.className='step-state '+step.state;state.textContent=phaseLabel(step.state);row.append(name,state);if(step.note){const note=document.createElement('p');note.className='step-note';note.textContent=step.note;row.append(note);}row.append(receiptBlock(step));return row;}
async function showStepDetails(id){
 const job=await request('/seller/jobs/'+id);el('record-title').textContent=job.name||catalog.jobs.find(j=>j.id===id)?.name||'任务详情';el('record-state').textContent=controlState(job)+(job.error_message?' · '+job.error_message:'');el('step-details').replaceChildren();
 for(const stage of job.stages||[]){const section=document.createElement('details');section.className='record-stage';section.open=['current','paused','failed'].includes(stage.state);const heading=document.createElement('summary');heading.textContent=stage.name+' · '+phaseLabel(stage.state);section.append(heading);const selected=job.steps.filter(v=>stage.numbers.includes(v.number));const visible=selected.filter(v=>!v.technical),technical=selected.filter(v=>v.technical);for(const step of visible)section.append(readableStep(step));if(technical.length){const extra=document.createElement('details');extra.className='technical-records';const title=document.createElement('summary');title.textContent='页面校验记录（'+technical.length+'项）';extra.append(title);for(const step of technical)extra.append(readableStep(step));section.append(extra);}el('step-details').append(section);}
 if(job.control_events?.length){const events=document.createElement('details'),heading=document.createElement('summary');heading.textContent='暂停、继续与结束记录';events.append(heading);for(const event of job.control_events){const p=document.createElement('p');p.textContent=({pause:'暂停',resume:'继续',stop:'结束'}[event.action]||event.action)+' · '+new Date(event.at*1000).toLocaleTimeString()+' · '+(event.origin==='bound_device'?'手机控制':'网页控制')+(event.outcome==='already_ended'?'（任务此前已停止）':'');events.append(p);}el('step-details').append(events);}
 if(!el('record-dialog').open)el('record-dialog').showModal();
}
el('close-record').addEventListener('click',()=>el('record-dialog').close());
el('details-current').addEventListener('click',()=>perform(()=>showStepDetails(currentJob.id)));
function renderWorkflows(){
 if(!catalog)return;
 const section=catalog.sections.find(s=>s.id===selectedSection)||catalog.sections[0];
 selectedSection=section.id;el('task-section-title').textContent=selectedSection==='publish'?'上新资料':section.name;el('publish-research').hidden=selectedSection!=='publish';el('automation-controls').hidden=selectedSection==='publish';
 el('section-summary').textContent=section.summary;el('section-progress').textContent=section.progress;
 el('task-sections').replaceChildren();
 for(const s of catalog.sections.filter(s=>s.id!=='publish')){const b=button(s.id==='publish'?'上新资料':s.name,()=>selectSection(s.id));b.setAttribute('aria-pressed',String(s.id===selectedSection));el('task-sections').append(b);}
 const previousChoice=el('workflow-choice').value;el('workflow-choice').replaceChildren();
 for(const task of catalog.builtins||[]){if(task.section!==selectedSection)continue;const option=new Option(task.name+(task.available===false?'（需更新手机）':''),'builtin:'+task.id);option.disabled=task.available===false;el('workflow-choice').append(option);}
 const builtinRecipes=new Set((catalog.builtins||[]).map(t=>t.recipe_id));

 if(!el('workflow-choice').options.length){const option=new Option('内置路径适配中，暂无可运行流程','');option.disabled=true;el('workflow-choice').append(option);}
 if(previousChoice&&Array.from(el('workflow-choice').options).some(o=>o.value===previousChoice))el('workflow-choice').value=previousChoice;
 const available=Array.from(el('workflow-choice').options).filter(o=>o.value&&!o.disabled).length;
 el('section-progress').textContent=available?'':section.progress;
 const pending=!!sessionStorage.getItem(pendingSlot);el('resume-task').hidden=!pending;el('run').textContent=pending?'核对上次任务':'开始任务';
 el('run').disabled=!sessionStorage.getItem(pendingSlot)&&!Array.from(el('workflow-choice').options).some(o=>!o.disabled);
 showWorkflowBoundary();
}
function showWorkflowBoundary(){
 const choice=el('workflow-choice').value;
 const task=(catalog?.builtins||[]).find(t=>'builtin:'+t.id===choice)||(catalog?.builtins||[]).find(t=>t.section===selectedSection);
 const recipe=catalog?.recipes.find(r=>'recipe:'+r.id===choice);
 el('plan-preview').replaceChildren();for(const phase of task?.preview||[]){const li=document.createElement('li');li.textContent=phase;el('plan-preview').append(li);}const choices=el('workflow-choice').options.length;el('workflow-label').hidden=choices<=1;el('workflow-choice').hidden=choices<=1;
 el('selected-task-name').textContent=concise(task?.name||recipe?.name||'核对上次任务');
 el('material-choice').hidden=task?!task.requires_item:!recipe;
 el('authorization').hidden=!!task||!recipe||['research','metrics'].includes(recipe.kind);
 el('workflow-boundary').textContent=(task?.available===false?'请先在手机设置中检查更新，安装最新版本。\n':'')+(task?.boundary||'执行所选已配置流程；开始前确认手机页面及目标商品。');
 const pending=!!sessionStorage.getItem(pendingSlot),blocked=currentJob&&['queued','running','waiting','unknown'].includes(currentJob.status);
 const missingMaterial=!!task?.requires_item&&!el('selected-item').value;
 el('run').disabled=!pending&&(!!blocked||missingMaterial||!Array.from(el('workflow-choice').options).some(o=>!o.disabled));el('run').textContent=pending?'核对上次任务':blocked?'先暂停或结束当前任务':task?.available===false?'请先更新手机至52版':selectedSection==='preheat'?'开始预热':'开始日常维护';
}
el('workflow-choice').addEventListener('change',showWorkflowBoundary);
el('selected-item').addEventListener('change',showWorkflowBoundary);
el('check-update').addEventListener('click',()=>perform(async()=>{
 el('check-update').disabled=true;
 try{const r=await fetch(root.dataset.updateApi,{cache:'no-store'});if(!r.ok)throw Error('更新信息暂不可用');
  const release=await r.json();await refresh();const installed=catalog.device_version;
  el('update-notice').textContent='手机上报版本：'+installed.name+'；最新发布：'+release.version_name+'。'+
    (installed.code<37?'首次请覆盖安装带更新按钮的版本，之后在手机 OIN 内检查更新。':
     release.version_code>installed.code?'请在手机 OIN 点击“检查更新→下载并更新”。':'目前无需更新。');
 }finally{el('check-update').disabled=false;}
}));
async function refresh(){
 catalog=await request('/seller');const selectedItem=el('selected-item').value,selectedRecipe=el('selected-recipe').value;
 showCurrentTask();renderWorkflows();
 el('count-items').textContent=catalog.items.length;
 el('count-active').textContent=catalog.jobs.filter(j=>['queued','running','waiting'].includes(j.status)).length;
 el('count-review').textContent=catalog.jobs.filter(j=>j.status==='unknown').length;
 el('device-version').textContent='当前手机上报版本：'+catalog.device_version.name;
 el('builtin-tasks').replaceChildren();
 for(const task of catalog.builtins||[]){const row=document.createElement('div'),hint=document.createElement('p');hint.textContent=task.boundary;
  row.append(button(task.name,async()=>{await request('/seller/builtin-jobs',{builtin_id:task.id,idempotency_key:crypto.randomUUID()});await refresh();notice('内置任务已受理；上新准备不会发布商品');}),hint);el('builtin-tasks').append(row);}
 el('items').replaceChildren();el('selected-item').replaceChildren(new Option('请选择商品素材',''));
 for(const item of catalog.items){const p=document.createElement('p');p.textContent=item.title+' · '+item.status+' · '+item.image_count+'张图';el('items').append(p);el('selected-item').append(new Option(item.title,item.id));}el('selected-item').value=selectedItem;showWorkflowBoundary();
 el('selected-recipe').replaceChildren();for(const r of catalog.recipes)el('selected-recipe').append(new Option(r.name+' · '+r.kind,r.id));if(selectedRecipe)el('selected-recipe').value=selectedRecipe;
 const states={queued:'等待执行',running:'执行中',waiting:'等待页面证实',unknown:'结果未知 · 需人工核对',succeeded:'任务成功',failed:'未完成',cancelled:'已取消'};
 const kinds={publish:'商品上新',edit:'编辑优化',brighten:'擦亮',metrics:'数据观察',reply:'回复',research:'页面任务'};
 el('jobs').replaceChildren();for(const j of catalog.jobs){const row=document.createElement('div'),p=document.createElement('p');row.className='execution-row';const recipe=catalog.recipes.find(r=>r.id===j.recipe_id);p.className='row-title';p.textContent=concise(j.name||recipe?.name||kinds[j.kind]||j.kind);const meta=document.createElement('div');meta.className='row-info';const state=document.createElement('span');state.className='status '+j.status;state.textContent=controlState(j);const progress=document.createElement('span');progress.textContent=j.current_phase||'执行过程';meta.append(state,progress);row.append(p,meta,button('查看回执',async()=>{await showStepDetails(j.id);}));
  if(j.status==='unknown'){for(const [label,outcome] of [['人工确认已完成','confirmed'],['人工证实未执行','never_left']])row.append(button(label,async()=>{if(!confirm('这是人工对账，不是重试。请先在闲鱼确认实际结果。'))return;await request('/seller/jobs/'+j.id+'/reconcile',{outcome});await refresh();}));}
  if(j.status==='queued')row.append(button('取消排队',async()=>{await request('/seller/jobs/'+j.id+'/reconcile',{outcome:'cancel'});await refresh();}));el('jobs').append(row);}
 el('schedules').replaceChildren();for(const s of catalog.schedules){const p=document.createElement('p');p.textContent='每日维护 · '+(s.enabled?'运行':'暂停')+' · 下次 '+new Date(s.next_due_at*1000).toLocaleString();if(s.enabled)p.append(button('暂停',async()=>{await request('/seller/schedules/'+s.id+'/pause',{});await refresh();}));el('schedules').append(p);}
 for(const [id,empty] of [['items','还没有商品素材，可先保存真实商品信息。'],['jobs','还没有任务。启动内置任务后，在这里查看进度。'],['schedules','暂无定时维护安排。']])if(!el(id).children.length){const p=document.createElement('p');p.className='empty';p.textContent=empty;el(id).append(p);}
}
function jobBody(){if(!el('selected-recipe').value)throw Error('先配置并选择流程');return {recipe_id:el('selected-recipe').value,item_id:el('selected-item').value||null,authorize:el('authorize').checked,idempotency_key:crypto.randomUUID(),values:el('reply').value?{reply:el('reply').value}:{}};}
el('run-research').addEventListener('click',()=>perform(async()=>{
 const slot=api+':pending-research';el('run-research').disabled=true;
 try{
  let body=JSON.parse(sessionStorage.getItem(slot)||'null');
  if(!body){const recipe=catalog.recipes.find(r=>r.id===el('selected-recipe').value);
   if(!recipe||!['research','metrics'].includes(recipe.kind))throw Error('这里只执行调研或数据读取流程');
   body={recipe_id:recipe.id,idempotency_key:crypto.randomUUID()};sessionStorage.setItem(slot,JSON.stringify(body));}
  await request('/seller/jobs',body);sessionStorage.removeItem(slot);await refresh();notice('调研已受理');
 }catch(error){if(error.status>=400&&error.status<500&&error.status!==409)sessionStorage.removeItem(slot);throw error;}
 finally{el('run-research').disabled=false;}
}));
const pendingSlot=api+':pending-task';
el('run').addEventListener('click',()=>perform(async()=>{
 el('run').disabled=true;
 try{
  let submission=JSON.parse(sessionStorage.getItem(pendingSlot)||'null');
  if(!submission){const choice=el('workflow-choice').value;
   if(choice.startsWith('builtin:')){
    const task=catalog.builtins.find(t=>t.id===choice.slice(8));
    if(!task||task.available===false)throw Error('请先更新手机应用');
    const body={builtin_id:task.id,idempotency_key:crypto.randomUUID()};
    if(task.requires_item){if(!el('selected-item').value)throw Error('请先选择真实商品素材');body.item_id=el('selected-item').value;}
    submission={path:'/seller/builtin-jobs',body};
   }
   else if(choice.startsWith('recipe:')){el('selected-recipe').value=choice.slice(7);submission={path:'/seller/jobs',body:jobBody()};}
   else throw Error('请选择已开放的任务');
   sessionStorage.setItem(pendingSlot,JSON.stringify(submission));
  }
  await request(submission.path,submission.body);sessionStorage.removeItem(pendingSlot);await refresh();notice('任务已受理，查看上方当前任务。');
 }catch(error){if(error.status>=400&&error.status<500&&error.status!==409){sessionStorage.removeItem(pendingSlot);notice('任务未受理：'+error.message+'。请调整设置后再提交。',true);}else notice('提交未确认或发生冲突：'+error.message+'。再次点击沿用原任务号核对，不另建任务。',true);}
 finally{showWorkflowBoundary();}
}));
el('schedule').addEventListener('click',()=>perform(async()=>{if(!el('authorize').checked)throw Error('请确认授权每日维护');await request('/seller/schedules',{recipe_id:el('selected-recipe').value,item_id:el('selected-item').value||null,interval_seconds:86400});await refresh();notice('每日维护已登记');}));
perform(refresh);setInterval(()=>{if(!document.hidden)perform(refresh);},10000);

el('retry-current').addEventListener('click',()=>perform(async()=>{
 if(sessionStorage.getItem(pendingSlot)){el('run').click();return;}if(!currentJob)return;const old=currentJob;const task=(catalog.builtins||[]).find(t=>t.recipe_id===old.recipe_id&&t.available&&['daily_preheat_v3','daily_maintenance_v3','daily_maintenance_v4'].includes(t.id));if(!task){notice('旧任务请重新选择新的每日任务',true);return;}
 if(['queued','running','waiting'].includes(old.status)){const stopped=await request('/seller/jobs/'+old.id+'/control',{action:'stop'});if(stopped.status!=='cancelled'){await refresh();notice('正在结束原任务，确认结束后才能重新运行');return;}}
 const submission={path:'/seller/builtin-jobs',body:{builtin_id:task.id,idempotency_key:crypto.randomUUID()}};sessionStorage.setItem(pendingSlot,JSON.stringify(submission));
 try{await request(submission.path,submission.body);sessionStorage.removeItem(pendingSlot);await refresh();notice('已重新开始，请保持手机闲鱼前台');}catch(error){if(error.status>=400&&error.status<500&&error.status!==409)sessionStorage.removeItem(pendingSlot);throw error;}
}));

el('product-data').addEventListener('click',()=>perform(async()=>{
 const data=await request('/seller/metrics');el('product-rows').replaceChildren();el('product-state').textContent=({not_collected:'运行日常维护后查看商品数据',empty_goods:'当前没有在售商品',captured:'曝光下降的商品优先显示',metrics_unavailable:'本次未读到可确认的商品数据',unconfirmed:'商品页面尚未确认'})[data.state]||'等待数据';
 for(const p of data.products||[]){const row=document.createElement('article'),title=document.createElement('h3'),amount=document.createElement('p'),advice=document.createElement('p');title.textContent=p.title;amount.textContent=(p.scope==='daily'?'今日曝光 ':p.scope==='cumulative'?'累计曝光 ':'曝光 ')+p.exposure+(p.previous_exposure==null?'':' · 昨日同时段 '+p.previous_exposure);advice.textContent=p.advice;advice.className=p.trend==='down'?'error':'muted';row.append(title,amount,advice);el('product-rows').append(row);}
 el('product-dialog').showModal();
}));
el('close-products').addEventListener('click',()=>el('product-dialog').close());
