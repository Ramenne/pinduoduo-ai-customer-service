import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
const file = new URL('../src/components/shop_settings/SmsVerificationForm.vue', import.meta.url)
const script = fs.readFileSync(file,'utf8').match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*\n/gm,'')
const calls=[]
const props={sessionId:'owned-fixture',verification:{cooldown_seconds:19,can_resend:false}}
const context={ref:v=>({value:v}),defineProps:()=>props,watch:(get,fn)=>fn(get()),onUnmounted:()=>{},setInterval:()=>1,clearInterval:()=>{},shopApi:{submitLoginVerification:async (id,payload)=>calls.push({id,...payload})}}
vm.createContext(context)
vm.runInContext(script+'\nglobalThis.h={act,code,error,message,remaining,busy}',context)
const h=context.h
h.code.value='abc'
await h.act('submit_sms')
assert.equal(calls.length,0)
h.code.value='123456'
await h.act('submit_sms')
assert.equal(calls.length,1)
assert.equal(calls[0].id,'owned-fixture')
assert.equal(calls[0].action,'submit_sms')
assert.equal(calls[0].code,'123456')
assert.equal(h.code.value,'')
assert.match(h.message.value,/等待/)
await h.act('resend_sms')
assert.equal(calls.length,1)
h.remaining.value=0
props.verification.can_resend=true
await h.act('resend_sms')
assert.equal(calls.length,2)
assert.equal(calls[1].action,'resend_sms')
assert.equal('code' in calls[1],false)
console.log('SMS mapping form passed: validation, session binding, explicit submit, no premature success, resend cooldown')
