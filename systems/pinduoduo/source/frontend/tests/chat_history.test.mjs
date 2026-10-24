import test from 'node:test'
import assert from 'node:assert/strict'
import {normalizeMessage,mergeMessages,mergeConversations,messageTime,mediaUrl} from '../src/utils/chat_history.js'
const msg=(id,content='你好')=>({msg_id:id,content,direction:'out',msg_at:'2026-10-19T12:00:00'})
test('历史加载与实时推送重合时去重，真实相同文本保留',()=>{assert.equal(mergeMessages([msg('1')],[msg('1'),msg('2')]).length,2)})
test('只对已确认占位做一对一对账，保留未确认发送',()=>{const placeholder={...msg(null),client_id:'x',status:'sent'};assert.equal(mergeMessages([placeholder],[msg('1')]).length,1);assert.equal(mergeMessages([{...placeholder,status:'sending'}],[msg('1')]).length,2);assert.equal(mergeMessages([placeholder,{...placeholder,client_id:'y'}],[msg('1')]).length,2)})
test('旧HTTP摘要不覆盖较新的实时摘要',()=>{const latest={customer_uid:'c',last_content:'new',last_msg_at:'2026-10-19T12:00:00'};assert.equal(mergeConversations([latest],[{...latest,last_content:'old',last_msg_at:'2026-10-19T11:00:00'}])[0].last_content,'new')})
test('北京时间在不同时区客户端一致，图片不接受脚本URL',()=>{assert.equal(messageTime(msg('1')),Date.parse('2026-10-19T04:00:00Z'));assert.equal(mediaUrl({msg_type:'image',content:'javascript:alert(1)'}),'');assert.equal(mediaUrl({msg_type:'image',content:'{"url":"https://img.example/a.png"}'}),'https://img.example/a.png')})

test('平台扩展消息使用中性提示，客户原文不被误改',()=>{const notice=normalizeMessage({msg_type:'system_status',content:'不支持的消息类型: 41'});assert.equal(notice.platform_notice,true);assert.doesNotMatch(notice.content,/不支持/);const text=normalizeMessage({msg_type:'text',content:'不支持的消息类型: 41'});assert.equal(text.content,'不支持的消息类型: 41');assert.equal(text.platform_notice,false)})

test('草稿只因实际更新的会话消息失效，旧历史和平台通知不误报', async()=>{
  const { draftInvalidatedBy, isPlatformNotice } = await import('../src/utils/chat_history.js')
  const boundary={id:10,direction:'in',msg_type:'text',msg_at:'2026-10-22T12:00:00'}
  const draft={history_boundary_id:10}
  const old={...boundary,id:20,msg_at:'2026-10-22T11:00:00'}
  for(const kind of ['system_status','auth','mall_system_msg']) {
    const notice={...boundary,id:30,msg_type:kind,msg_at:'2026-10-22T13:00:00'}
    assert.equal(isPlatformNotice(notice),true)
    assert.equal(draftInvalidatedBy(draft,[boundary,old,notice]),false)
  }
  assert.equal(draftInvalidatedBy(draft,[boundary,old,{...boundary,id:31}]),true)
  assert.equal(draftInvalidatedBy(draft,[boundary,{...boundary,id:32,direction:'out',msg_at:'2026-10-22T13:00:00'}]),true)
  assert.equal(draftInvalidatedBy(draft,[old]),false)
})
