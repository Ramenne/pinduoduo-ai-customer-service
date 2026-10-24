<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { knowledgeApi } from '@/api'
import { FormModal } from '@/components/common'
import { showToast } from '@/utils/toast'
const props=defineProps({kind:{type:String,required:true},itemId:{type:Number,required:true}})
const emit=defineEmits(['restored'])
const open=ref(false),busy=ref(false),restoring=ref(false),error=ref(''),entries=ref([]),detail=ref(null),current=ref(null),page=ref(1),more=ref(false)
let generation=0,selection=0,disposed=false
onUnmounted(()=>{disposed=true;generation++;selection++})
const labels={create:'新增',upsert:'新增/更新',edit:'编辑',import:'批量导入',enable_disable:'启停用',delete:'删除（停用）',curation_publish:'沉淀入库',restore:'恢复旧版',baseline:'初始内容',observed_change:'发现未留痕的内容变化'}
const names={goods_id:'商品ID',goods_name:'商品名',price:'价格',price_min:'最低价',price_max:'最高价',sold_quantity:'销量',thumb_url:'缩略图',specifications:'规格',extracted_content:'知识正文',last_extracted_at:'提取时间',status:'状态',title:'标题',content:'知识正文',tags:'标签',enabled:'状态'}
const fields=values=>Object.entries(values||{}).filter(([,v])=>v!==null&&v!=='').map(([key,value])=>({key,label:names[key]||key,value:['enabled','status'].includes(key)?(value?'启用':'停用'):value}))
const changed=computed(()=>detail.value&&current.value?.hash!==detail.value.version.content_hash)
async function load(append=false){
 const g=generation;busy.value=true;error.value=''
 try{
  const p=append?page.value+1:1,data=await knowledgeApi.listKnowledgeVersions(props.kind,props.itemId,p)
  if(disposed||g!==generation)return
  entries.value=append?[...entries.value,...data.list]:data.list;page.value=p;more.value=data.has_more;current.value=data.current
 }catch(e){if(g===generation)error.value=e.message||'版本历史读取失败'}finally{if(g===generation)busy.value=false}
}
function show(){generation++;selection++;open.value=true;entries.value=[];detail.value=null;load()}
function close(){if(restoring.value)return;generation++;selection++;open.value=false;busy.value=false}
async function selectVersion(row){
 const g=generation,s=++selection;busy.value=true;error.value='';detail.value=null
 try{const data=await knowledgeApi.getKnowledgeVersion(props.kind,props.itemId,row.id);if(disposed||g!==generation||s!==selection)return;detail.value=data;current.value=data.current}
 catch(e){if(g===generation&&s===selection)error.value=e.message||'该版本读取失败'}finally{if(g===generation&&s===selection)busy.value=false}
}
async function restore(){
 if(!changed.value||!detail.value?.can_restore||restoring.value)return
 const g=generation,revision=detail.value.version.id,expected=current.value.hash
 restoring.value=true;error.value=''
 try{
  const data=await knowledgeApi.restoreKnowledgeVersion(props.kind,props.itemId,revision,expected)
  if(disposed||g!==generation)return
  current.value=data.current;showToast(`已恢复，当前为第 ${data.current.version_no} 版`,'success');emit('restored');detail.value=null;await load()
 }catch(e){if(g===generation)error.value=e.message||'恢复未确认，请重新查看当前内容后重试'}finally{if(g===generation)restoring.value=false}
}
</script>
<template>
 <button class="kv-open" type="button" @click="show">历史版本</button>
 <FormModal :model-value="open" title="知识版本历史" :show-footer="false" max-width="820px" @update:model-value="value=>{if(!value)close()}">
  <div class="kv-content">
   <p>当前：{{ current?.version_no?`第 ${current.version_no} 版`:'尚未留版本记录' }}<span v-if="!entries.length&&!busy">。首次修改时会保留当前内容作为初始版本。</span></p>
   <p class="kv-note">恢复会产生新版本，保留历史。上线前未记录的修改无法补回。</p>
   <p v-if="error" class="kv-error" role="alert">{{ error }}</p>
   <div class="kv-list"><button v-for="entry in entries" :key="entry.id" type="button" :disabled="busy||restoring" :class="{selected:detail?.version.id===entry.id}" @click="selectVersion(entry)"><strong>第 {{ entry.version_no }} 版 · {{ labels[entry.operation]||entry.operation }}</strong><small>{{ entry.actor || '历史修改者未知' }} · {{ entry.at.replace('T',' ') }}</small></button></div>
   <button v-if="more" type="button" :disabled="busy||restoring" @click="load(true)">更早版本</button>
   <p v-if="busy">读取中…</p>
   <div v-if="detail" class="kv-compare"><section><h4>当前内容</h4><dl><template v-for="field in fields(current.values)" :key="field.key"><dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></template></dl></section><section><h4>所选：第 {{ detail.version.version_no }} 版</h4><dl><template v-for="field in fields(detail.values)" :key="field.key"><dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></template></dl></section></div>
   <footer><button type="button" :disabled="restoring" @click="close">关闭</button><button v-if="detail" class="kv-restore" type="button" :disabled="!changed||!detail.can_restore||restoring||busy" @click="restore">{{ restoring?'恢复中…':!changed?'当前已是此内容':!detail.can_restore?'当前账号无恢复权限':'恢复为新版本' }}</button></footer>
  </div>
 </FormModal>
</template>
<style scoped>
.kv-open{border:0;background:transparent;color:var(--primary-color,#2563eb);padding:4px 8px;font:inherit;cursor:pointer;border-radius:5px}.kv-content{font-size:14px;color:var(--text-primary,#253047)}.kv-note{font-size:12px;color:var(--text-secondary,#64748b)}.kv-error{color:#b42318}.kv-list{max-height:210px;overflow:auto;display:grid;gap:7px;margin:12px 0}.kv-list button{text-align:left;display:grid;gap:4px;border:1px solid var(--border-color,#dce1e8);background:var(--bg-primary,#fff);color:inherit;border-radius:7px;padding:10px;cursor:pointer}.kv-list .selected{border-color:#2563eb}.kv-list small{color:var(--text-secondary,#64748b)}.kv-compare{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}.kv-compare section{padding:12px;background:var(--bg-secondary,#f5f7fa);border-radius:8px;max-height:320px;overflow:auto;min-width:0}.kv-compare h4{margin:0 0 10px}dt{font-size:12px;color:var(--text-secondary,#64748b)}dd{margin:4px 0 12px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}footer{display:flex;gap:10px;justify-content:flex-end;margin-top:16px}footer button{font:inherit;border-radius:7px;border:1px solid var(--border-color,#cbd5e1);padding:8px 14px;cursor:pointer;background:var(--bg-primary,#fff);color:inherit}footer .kv-restore{background:#2563eb;color:#fff;border-color:#2563eb}button:disabled{opacity:.5;cursor:default}@media(max-width:600px){.kv-compare{grid-template-columns:1fr}.kv-compare section{max-height:220px}}
</style>
