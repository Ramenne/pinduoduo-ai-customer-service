import test from 'node:test'
import assert from 'node:assert/strict'
import { createLatestLoad } from '../src/utils/latest_load.js'
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
test('older shop response cannot overwrite newer shop or its loading state',async()=>{
 let rows=[],busy=false;const loader=createLatestLoad(v=>busy=v),a=deferred(),b=deferred()
 const one=loader.run(()=>a.promise,v=>rows=v),two=loader.run(()=>b.promise,v=>rows=v)
 a.resolve(['shop A']);await one;assert.deepEqual(rows,[]);assert.equal(busy,true)
 b.resolve(['shop B']);await two;assert.deepEqual(rows,['shop B']);assert.equal(busy,false)
})
test('leaving page suppresses both late success and late failure',async()=>{
 const loader=createLatestLoad(()=>{});let mutations=0;const a=deferred(),b=deferred()
 const first=loader.run(()=>a.promise,()=>mutations++);loader.invalidate();a.resolve('late');await first
 const second=loader.run(()=>b.promise,()=>mutations++);loader.invalidate();b.reject(new Error('late failure'));await second
 assert.equal(mutations,0)
})
test('current failure clears loading and next retry succeeds',async()=>{
 let busy=false,result;const loader=createLatestLoad(v=>busy=v)
 await assert.rejects(loader.run(()=>Promise.reject(new Error('offline')),()=>{}),/offline/)
 assert.equal(busy,false)
 await loader.run(()=>Promise.resolve(['retry']),v=>result=v)
 assert.deepEqual(result,['retry']);assert.equal(busy,false)
})
