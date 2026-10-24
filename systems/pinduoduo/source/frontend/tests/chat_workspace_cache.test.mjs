import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive, computed, isReactive } from 'vue'
import { createWorkspaceCache, shouldAssessConversation } from '../src/utils/chat_workspace_cache.js'

test('first-created shop and conversation remain reactive for input and buttons', () => {
 const {shopCache,threadCache}=createWorkspaceCache(reactive({}))
 assert.equal(isReactive(shopCache(1)),true)
 const thread=computed(()=>threadCache(1,'c'))
 assert.equal(isReactive(thread.value),true)
 thread.value.view='ai'
 const input=computed({get:()=>thread.value.view==='ai'?thread.value.aiInput:thread.value.draft,
  set:value=>{if(thread.value.view==='ai')thread.value.aiInput=value;else thread.value.draft=value}})
 const enabled=computed(()=>Boolean(input.value.trim()))
 assert.equal(enabled.value,false)
 input.value='材质是纯棉'
 assert.equal(enabled.value,true);assert.equal(input.value,'材质是纯棉')
 thread.value.view='customer'
 assert.equal(input.value,'');assert.equal(enabled.value,false)
 input.value='对客草稿'
 thread.value.view='ai';assert.equal(input.value,'材质是纯棉')
 thread.value.view='customer';assert.equal(input.value,'对客草稿')
})

test('live updates use the same scoped reactive conversation', () => {
 const {threadCache}=createWorkspaceCache(reactive({}))
 const first=threadCache(1,'a'),count=computed(()=>first.control?.attention_count||0)
 assert.equal(count.value,0)
 threadCache(1,'a').control={revision:2,attention_count:3}
 assert.equal(count.value,3)
 assert.equal(threadCache(2,'a').control,null)
 assert.equal(threadCache(1,'b').control,null)
})

test('opening finished history never retakes a conversation or starts AI', () => {
 assert.equal(shouldAssessConversation({control:{mode:'auto',attention_count:0},assessed:false},true),false)
 assert.equal(shouldAssessConversation({control:{mode:'human',attention_count:1},assessed:false},true),true)
 assert.equal(shouldAssessConversation({control:{mode:'human',attention_count:1},assessed:true},true),false)
})

test('saving human input cannot start a competing automatic assessment',()=>{
 assert.equal(shouldAssessConversation({control:{mode:'human',attention_count:1},assessed:false,supplementBusy:true},true),false)
})
