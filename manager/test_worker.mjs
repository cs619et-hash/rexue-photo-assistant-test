import assert from 'node:assert/strict';
import worker from './rexue-line-worker.mjs';
let queried=false;
const db={batch:async()=>[],prepare(sql){ return {bind(t){assert(Number.isSafeInteger(t));return this},async all(){ queried=true;assert(sql.includes('expires_at > ?'));assert(sql.includes('revoked = 0'));return {results:[]}} }; }};
const env={EXE_API_TOKEN:'test-token',DB:db};
const req=(token)=>new Request('https://example.test/api/messages',{headers:token?{Authorization:'Bearer '+token}:{}});
assert.equal((await worker.fetch(req(),env)).status,401);
assert.equal((await worker.fetch(req('incorrect'),env)).status,401);
assert.equal(queried,false);
const result=await worker.fetch(req('test-token'),env);
assert.equal(result.status,200);assert.deepEqual(await result.json(),{version:'1.3.1',messages:[]});
assert.equal((await worker.fetch(req('test-token'),{EXE_API_TOKEN:'test-token'})).status,503);
console.log('Worker: auth denial, authorized read, expiry filtering, missing DB passed');

const uid = 'U' + '1'.repeat(32);
const namedDB = {batch:async()=>[],prepare(){return {bind(){return this},all:async()=>({results:[{message_key:'m1',sender_id:uid,text_content:'hello'}]})}}};
const getNames = async(token) => (await worker.fetch(req('test-token'),{...env,DB:namedDB,LINE_CHANNEL_ACCESS_TOKEN:token})).json();
let calls = 0;
globalThis.fetch = async(url,opts)=>{
 calls++; assert.equal(url,'https://api.line.me/v2/bot/profile/'+uid);
 assert.equal(opts.headers.Authorization,'Bearer profile-test');
 return new Response(JSON.stringify({userId:uid,displayName:'測試家長'}));
};
assert.equal((await getNames('')).messages[0].name_status,'尚未設定姓名讀取授權');
assert.equal(calls,0);
assert.equal((await getNames('profile-test')).messages[0].display_name,'測試家長');
assert.equal((await getNames('profile-test')).messages[0].display_name,'測試家長');
assert.equal(calls,1);
console.log('Profile authorization, name enrichment, cache passed');

for (const [status,expected] of [[401,'姓名讀取授權未通過'],[404,'LINE 未提供此用戶名稱'],[429,'LINE 查詢頻率受限，稍後重試'],[500,'LINE 姓名查詢 HTTP 500']]) {
 const id='U'+String(status).padStart(32,'0');
 const d={batch:async()=>[],prepare(){return {bind(){return this},all:async()=>({results:[{sender_id:id,text_content:'x'}]})}}};
 globalThis.fetch=async(url,opts)=>{assert.equal(opts.redirect,'manual');return new Response('error',{status})};
 const res=await worker.fetch(req('test-token'),{...env,DB:d,LINE_CHANNEL_ACCESS_TOKEN:'profile-test'});
 assert.equal((await res.json()).messages[0].name_status,expected);
}
console.log('Profile HTTP diagnostics passed');
