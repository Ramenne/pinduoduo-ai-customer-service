import test from 'node:test'
import assert from 'node:assert/strict'
import { sendOperation } from '../src/utils/send_operation.js'
function storage() { const values=new Map(); return { getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k) } }
test('unconfirmed send survives rebuild and does not replay itself',async()=>{
  const local=storage(),first=await sendOperation(1,'c','same body',local)
  const restarted=await sendOperation(1,'c','same body',local)
  assert.equal(first.key,restarted.key)
  assert.notEqual(first.key,(await sendOperation(2,'c','same body',local)).key)
  restarted.complete()
  assert.notEqual(first.key,(await sendOperation(1,'c','same body',local)).key)
})
test('storage failure stops before any send can begin',async()=>{
  await assert.rejects(sendOperation(1,'c','body',{getItem:()=>null,setItem:()=>{throw Error('storage unavailable')}}))
})
