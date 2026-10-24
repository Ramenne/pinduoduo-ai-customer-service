<script setup>
import { ref,onMounted } from 'vue'
import { chatApi } from '@/api'
const shops=ref([]),pk=ref(null),stats=ref(null),experiences=ref([]),summaries=ref([]),before=ref(null),summaryBefore=ref(null)
const busy=ref(false),error=ref(''),editing=ref(null),versions=ref([]),saving=ref(false)
const labels={pending:'等待处理',running:'处理中',ready:'摘要可用',failed:'失败，原文保留',stored:'原文已归档',
  not_sent:'未发送',processing:'处理中',superseded:'已被新消息替代',failed_send:'发送失败',candidate:'待审核',published:'已启用',revoked:'已撤回',cancelled:'已取消',accepted:'发送成功',confirmed:'平台已确认',
  uncertain:'发送待核',review:'待人工核对',dispatching:'发送中',done:'已处理',human:'人工接管',auto:'自动',retry:'等待重试',backfill:'历史补齐'}
const name=x=>labels[x]||x
async function load() {
  if(!pk.value)return
  busy.value=true;error.value=''
  try {
    const [s,e,a]=await Promise.all([chatApi.harnessOperations(pk.value),chatApi.harnessExperiences({shop_pk:pk.value}),chatApi.harnessSummaries({shop_pk:pk.value})])
    stats.value=s;experiences.value=e.items;before.value=e.before;summaries.value=a.items;summaryBefore.value=a.before
  }catch(e){error.value=e.message}finally{busy.value=false}
}
async function more(type) {
  try {
    const data=type==='experience'?await chatApi.harnessExperiences({shop_pk:pk.value,before:before.value}):await chatApi.harnessSummaries({shop_pk:pk.value,before:summaryBefore.value})
    if(type==='experience'){experiences.value.push(...data.items);before.value=data.before}
    else {summaries.value.push(...data.items);summaryBefore.value=data.before}
  }catch(e){error.value=e.message}
}
async function edit(row) {
  editing.value={...row};versions.value=[];error.value=''
  try {versions.value=await chatApi.experienceVersions(pk.value,row.id)}catch(e){error.value=e.message}
}
async function review(action) {
  const e=editing.value;saving.value=true;error.value=''
  try {
    await chatApi.reviewExperience({shop_pk:pk.value,ident:e.id,expected_version:e.version_no,action,
      title:e.title,guidance:e.guidance,trigger_terms:e.trigger_terms,validation_note:e.validation_note})
    editing.value=null;await load()
  }catch(e){error.value=e.message}finally{saving.value=false}
}
function restore(version) {
  // Restoring is an explicit new reviewed version; never mutate historical records.
  for(const key of ['title','guidance','trigger_terms','validation_note'])editing.value[key]=version[key]
}
async function retry(row) { try {await chatApi.retrySummary(pk.value,row.id);await load()}catch(e){error.value=e.message} }
onMounted(async()=>{try{const data=await chatApi.workspaceShops({page_size:100});shops.value=data.list;pk.value=shops.value[0]?.id;await load()}catch(e){error.value=e.message}})
</script>
<template>
  <main class="ho-page"><header><div><h1>运行状态与经验</h1><p>查看本地接收、发送及归档状态。经验经本店审核启用后，才参与对应客服场景。</p></div><div><select v-model="pk" aria-label="选择店铺" @change="editing=null;load()"><option v-for="s in shops" :key="s.id" :value="s.id">{{s.shop_name||s.shop_id}} · {{s.connected?'通道在线':'通道未连接'}}</option></select><button :disabled="busy" @click="load">{{busy?'读取中…':'刷新本地状态'}}</button></div></header>
    <p v-if="error" class="ho-error" role="alert">{{error}}</p>
    <div v-if="stats" class="ho-stats"><section v-for="(group,key) in stats" :key="key"><h2>{{({inbox:'消息接收',delivery:'发送记录',control:'会话归属',compression:'历史压缩',experience:'运行经验'})[key]}}</h2><p v-for="(n,state) in group" :key="state">{{name(state)}} <strong>{{n}}</strong></p><p v-if="!Object.keys(group).length">暂无记录</p></section></div>
    <section class="ho-section"><h2>本店运行经验</h2><p>从原 AI 协作中的纠正和程序异常形成候选。未审核候选不影响回答。启用需要店铺设置和客服发送权限；保存修订会先回到候选状态。</p><p v-if="!experiences.length">暂无经验候选</p><article v-for="e in experiences" :key="e.id"><div><strong>{{e.title}}</strong><span>{{name(e.state)}} · v{{e.version_no}}</span></div><p>{{e.observation}}</p><p v-if="e.guidance">处理指引：{{e.guidance}}</p><button @click="edit(e)">审核与版本</button></article><button v-if="before" @click="more('experience')">更早经验</button></section>
    <section class="ho-section"><h2>归档与摘要</h2><p>摘要用于历史导航，原始记录始终保留。失败不会删原文，也不会触发顾客回复。</p><p v-if="!summaries.length">尚未达到压缩条件</p><article v-for="s in summaries" :key="s.id"><strong>会话 {{s.customer_uid}}</strong><span>{{name(s.state)}} · 尝试 {{s.attempts}} 次</span><p v-if="s.error">{{s.error}}</p><details v-if="s.summary"><summary>查看有来源摘录</summary><p v-for="e in s.summary.excerpts" :key="e.id">{{e.quote}}</p></details><button v-if="s.state==='failed'" @click="retry(s)">重试本地摘要</button></article><button v-if="summaryBefore" @click="more('summary')">更早归档</button></section>
    <div v-if="editing" class="ho-mask" @click.self="editing=null"><section class="ho-dialog" role="dialog" aria-modal="true" aria-label="审核运行经验"><header><h2>审核与版本 · v{{editing.version_no}}</h2><button @click="editing=null">关闭</button></header><p v-if="error" class="ho-error">{{error}}</p><label>标题<input v-model="editing.title" maxlength="200"></label><label>适用关键词（逗号分隔）<input v-model="editing.trigger_terms" maxlength="400"></label><label>内部处理指引<textarea v-model="editing.guidance" maxlength="2000" rows="5"></textarea></label><label>验证场景与结果<textarea v-model="editing.validation_note" maxlength="1000" rows="3"></textarea></label><div class="ho-actions"><button :disabled="saving" @click="review('save')">保存为候选</button><button :disabled="saving" @click="review('publish')">审核并启用</button><button :disabled="saving" @click="review('revoke')">撤回</button></div><h3>历史版本</h3><article v-for="v in versions" :key="v.version_no"><strong>v{{v.version_no}} · {{name(v.state)}}</strong><p>{{v.guidance}}</p><button @click="restore(v)">沿用此版内容，再审核</button></article></section></div>
  </main>
</template>
<style scoped>
.ho-page{max-width:1180px;margin:auto;padding:24px;color:#20364b}.ho-page header{display:flex;gap:20px;justify-content:space-between;align-items:center;flex-wrap:wrap}.ho-page h1{font-size:24px;margin:0}.ho-page h2{font-size:18px}.ho-page p{line-height:1.65}.ho-page button,.ho-page select,.ho-page input,.ho-page textarea{border:1px solid #bacbda;border-radius:7px;padding:9px;background:#fff;color:#20364b}.ho-page button{cursor:pointer;margin:4px}.ho-page button:disabled{opacity:.5;cursor:default}.ho-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin:20px 0}.ho-stats section,.ho-section{background:#fff;border:1px solid #dce5ed;border-radius:10px;padding:18px}.ho-stats p{display:flex;justify-content:space-between}.ho-section{margin-top:18px}.ho-page article{border-top:1px solid #e0e7ee;padding:16px 0}.ho-page article span{margin-left:14px;color:#52687b}.ho-mask{position:fixed;inset:0;z-index:1000;display:grid;place-items:center;padding:20px;background:#1a2c4388}.ho-dialog{background:#fff;border-radius:12px;width:min(720px,95vw);max-height:90vh;overflow:auto;padding:24px}.ho-dialog label{display:grid;gap:6px;margin:16px 0}.ho-dialog textarea{resize:vertical}.ho-error{color:#b42336;background:#fff0f2;padding:12px}.ho-actions{display:flex;gap:8px;flex-wrap:wrap}@media(max-width:600px){.ho-page{padding:12px}.ho-stats{grid-template-columns:1fr 1fr}}
</style>
