'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {createCloudflareBridge}=require('../server/cloudflare-bridge.cjs');
const {createPrivateReader,sha256}=require('../server/private-access.cjs');
const origin='https://lumen.example',worker='https://lumen-media.example.workers.dev';
const env={LUMEN_APP_ORIGIN:origin,LUMEN_WORKER_ORIGIN:worker,LUMEN_BRIDGE_KEY:Buffer.alloc(32,8).toString('base64url')};
const previousWorker='https://lumen-retained.example.workers.dev',previousRelease='lumen-private-192-v4';
const retainedEnv={...env,LUMEN_PREVIOUS_RELEASE_ID:previousRelease,LUMEN_PREVIOUS_WORKER_ORIGIN:previousWorker};
test('retained release GET and HEAD preserve current bridge authentication and request conditions',async()=>{
  for(const method of ['GET','HEAD']) {
    let call;
    const bridge=createCloudflareBridge({env:retainedEnv,fetch:async(url,options)=>{
      call={url,options};return new Response(method==='HEAD'?null:'{"retained":true}',{headers:{'Content-Type':'application/json'}});
    }});
    const requestPath='/api/assets/'+previousRelease+'/tracks/chapter-001/reader-autonoe.'+'a'.repeat(64)+'.json';
    const result=await bridge(new Request(origin+requestPath,{method,headers:{Cookie:'__Host-lumen_session=fixture',
      Range:'bytes=2-4','If-Range':'"fixture"','If-None-Match':'"reader"','X-Worker-Origin':'https://untrusted.example'}}));
    assert.equal(result.status,200);assert.equal(call.url,previousWorker+'/bridge');
    assert.equal(call.options.headers['X-Lumen-Bridge-Key'],env.LUMEN_BRIDGE_KEY);
    assert.equal(call.options.redirect,'manual');
    const envelope=JSON.parse(method==='HEAD'?Buffer.from(call.options.headers['X-Lumen-Request'],'base64url').toString():call.options.body);
    assert.equal(envelope.url,origin+requestPath);assert.equal(envelope.method,method);
    assert.equal(envelope.headers.cookie,'__Host-lumen_session=fixture');assert.equal(envelope.headers.range,'bytes=2-4');
    assert.equal(envelope.headers['if-range'],'"fixture"');assert.equal(envelope.headers['if-none-match'],'"reader"');
    assert.equal(envelope.headers['x-worker-origin'],undefined);
    if(method==='HEAD')assert.equal(result.body,null);
  }
});
test('current control and asset requests and near previous prefixes keep the current Worker',async()=>{
  const paths=['/api/book','/api/session','/api/assets/lumen-illustrated-192-v1/images/painting.webp',
    '/api/assets/'+previousRelease,'/api/assets/'+previousRelease+'0/reader.json',
    '/api/assets/'+previousRelease+'-extra/reader.json','/api/assets/lumen-private-192-v%34/reader.json',
    '/api/assets/'+previousRelease+'%2Freader.json'];
  for(const requestPath of paths) {
    const bridge=createCloudflareBridge({env:retainedEnv,fetch:async(url)=>{
      assert.equal(url,worker+'/bridge');return new Response('{}',{headers:{'Content-Type':'application/json'}});
    }});
    assert.equal((await bridge(new Request(origin+requestPath))).status,200,requestPath);
  }
});
test('retained audio GET and HEAD accept only their selected Worker media redirects',async()=>{
  for(const method of ['GET','HEAD']) {
    const bridge=createCloudflareBridge({env:retainedEnv,fetch:async(url,options)=>{
      assert.equal(url,previousWorker+'/bridge');assert.equal(options.redirect,'manual');
      return new Response(null,{status:307,headers:{Location:previousWorker+'/media/audio.mp3?grant=fixture'}});
    }});
    const result=await bridge(new Request(origin+'/api/assets/'+previousRelease+'/audio/charon/chapter-001.mp3',{method}));
    assert.equal(result.status,307);assert.equal(result.body,null);
    assert.equal(new URL(result.headers.get('location')).origin,previousWorker);
  }
  for(const [requestPath,target] of [['/api/assets/'+previousRelease+'/audio.mp3',worker],
    ['/api/assets/lumen-illustrated-192-v1/audio.mp3',previousWorker]]) {
    const bridge=createCloudflareBridge({env:retainedEnv,fetch:async()=>new Response(null,{status:307,headers:{Location:target+'/media/audio.mp3?grant=fixture'}})});
    assert.equal((await bridge(new Request(origin+requestPath))).status,503);
  }
});
test('invalid or incomplete retained-release configuration never contacts a Worker',async()=>{
  let calls=0;
  const configurations=[{...env,LUMEN_PREVIOUS_RELEASE_ID:previousRelease},{...env,LUMEN_PREVIOUS_WORKER_ORIGIN:previousWorker},
    ...['',null,'../other','old/release','UPPER','x'.repeat(129)].map(id=>({...retainedEnv,LUMEN_PREVIOUS_RELEASE_ID:id})),
    ...['http://unsafe.example',previousWorker+'/',previousWorker+'/path','https://user:pass@unsafe.example',
      previousWorker+'?origin=other',previousWorker+'#other'].map(value=>({...retainedEnv,LUMEN_PREVIOUS_WORKER_ORIGIN:value}))];
  for(const config of configurations) {
    const bridge=createCloudflareBridge({env:config,fetch:async()=>{calls++;throw Error('must not fetch');}});
    assert.equal((await bridge(new Request(origin+'/api/book'))).status,503);
  }
  assert.equal(calls,0);
});
test('retained routes enforce the existing origin query and method checks before forwarding',async()=>{
  let calls=0;const bridge=createCloudflareBridge({env:retainedEnv,fetch:async()=>{calls++;throw Error('must not fetch');}});
  const retained='/api/assets/'+previousRelease+'/reader.json';
  for(const request of [new Request('https://foreign.example'+retained),
    new Request(origin+retained,{headers:{Origin:'https://foreign.example'}}),
    new Request(origin+retained,{headers:{Host:'foreign.example'}}),
    new Request(origin+retained,{headers:{'Sec-Fetch-Site':'cross-site'}}),
    new Request(origin+retained+'?worker=https://foreign.example'),new Request(origin+retained,{method:'POST'})]) {
    assert.ok((await bridge(request)).status>=400);
  }
  assert.equal(calls,0);
});
test('selected retained Worker configuration is captured before the upstream await',async()=>{
  const mutable={...retainedEnv};
  const bridge=createCloudflareBridge({env:mutable,fetch:async(url,options)=>{
    assert.equal(url,previousWorker+'/bridge');assert.equal(options.headers['X-Lumen-Bridge-Key'],env.LUMEN_BRIDGE_KEY);
    mutable.LUMEN_PREVIOUS_WORKER_ORIGIN='https://changed.example';mutable.LUMEN_BRIDGE_KEY='changed';
    return new Response(null,{status:307,headers:{Location:previousWorker+'/media/audio.mp3?grant=fixture'}});
  }});
  assert.equal((await bridge(new Request(origin+'/api/assets/'+previousRelease+'/audio.mp3'))).status,307);
});
test('bridge preserves login cookies and forwards only trusted request context',async()=>{
  let call;
  const bridge=createCloudflareBridge({env,fetch:async(url,options)=>{call={url,options};return new Response('{"authenticated":true}',{headers:{'Content-Type':'application/json','Cache-Control':'private, no-store','Set-Cookie':'__Host-lumen_session=fixture; Secure; HttpOnly; Path=/'}});}});
  const response=await bridge(new Request(origin+'/api/session',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json','X-Forged-Secret':'untrusted'},body:'{"invite":"fixture"}'}));
  assert.equal(response.status,200);assert.ok(response.headers.get('set-cookie').includes('HttpOnly'));
  assert.equal(call.url,worker+'/bridge');assert.equal(call.options.redirect,'manual');
  assert.equal(call.options.headers['X-Lumen-Bridge-Key'],env.LUMEN_BRIDGE_KEY);
  const payload=JSON.parse(call.options.body);assert.equal(payload.url,origin+'/api/session');assert.equal(payload.body,'{"invite":"fixture"}');
  assert.equal(payload.headers.origin,origin);assert.equal(payload.headers['x-forged-secret'],undefined);
  assert.ok(!call.options.body.includes(env.LUMEN_BRIDGE_KEY));
});
test('bridge relays media redirects without downloading media or following locations',async()=>{
  const bridge=createCloudflareBridge({env,fetch:async(url,options)=>{
    assert.equal(options.redirect,'manual');return new Response(null,{status:307,headers:{Location:worker+'/media/fixture?grant=fixture','Cache-Control':'private, no-store','Referrer-Policy':'no-referrer'}});
  }});
  const response=await bridge(new Request(origin+'/api/assets/fixture',{headers:{Range:'bytes=2-4'}}));
  assert.equal(response.status,307);assert.equal(response.body,null);assert.ok(response.headers.get('location').startsWith(worker+'/media/'));
});
test('bridge preserves authorized JSON HEAD and 304 responses without bodies',async()=>{
  for(const status of [200,304]) {
    const bridge=createCloudflareBridge({env,fetch:async()=>new Response(null,{status,headers:{'Content-Type':'application/json','Cache-Control':'private, no-store',...(status===200?{'Content-Length':'123'}:{})}})});
    const response=await bridge(new Request(origin+'/api/book',{method:'HEAD'}));
    assert.equal(response.status,status);assert.equal(response.body,null);
  }
});
test('bridge rejects foreign redirects, streamed audio and oversized upstream JSON',async()=>{
  for(const result of [new Response(null,{status:307,headers:{Location:'https://evil.example/media/private'}}),
    new Response('audio',{headers:{'Content-Type':'audio/mpeg'}}),new Response('x'.repeat(2*1024*1024+1),{headers:{'Content-Type':'application/json'}})]) {
    const bridge=createCloudflareBridge({env,fetch:async()=>result});
    assert.equal((await bridge(new Request(origin+'/api/book'))).status,503);
  }
});
test('decoded upstream JSON has exact length and no stale compression headers',async()=>{
  const http=require('node:http'),zlib=require('node:zlib'),bytes=Buffer.from('{"authenticated":true}');
  let encoding;
  const server=http.createServer((req,res)=>{
    encoding=req.headers['accept-encoding'];
    const compressed=zlib.gzipSync(bytes);res.writeHead(200,{'Content-Type':'application/json','Content-Encoding':'gzip','Content-Length':String(compressed.length)});res.end(compressed);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const bridge=createCloudflareBridge({env,fetch:(_,options)=>fetch('http://127.0.0.1:'+server.address().port,options)});
    const result=await bridge(new Request(origin+'/api/session'));
    assert.equal(encoding,'identity');
    assert.equal(result.status,200);assert.equal(result.headers.get('content-encoding'),null);
    assert.equal(result.headers.get('content-length'),String(bytes.length));assert.equal(await result.text(),bytes.toString());
  }finally{await new Promise(resolve=>server.close(resolve));}
});
test('original HEAD uses HTTP HEAD with a bounded envelope header',async()=>{
  let call;
  const bridge=createCloudflareBridge({env,fetch:async(url,options)=>{call={url,options};return new Response(null,{headers:{'Content-Type':'application/json','Content-Length':'123'}});}});
  const result=await bridge(new Request(origin+'/api/book',{method:'HEAD',headers:{Cookie:'__Host-lumen_session=fixture'}}));
  assert.equal(result.status,200);assert.equal(call.options.method,'HEAD');assert.equal(call.options.body,undefined);
  const envelope=JSON.parse(Buffer.from(call.options.headers['X-Lumen-Request'],'base64url'));
  assert.equal(envelope.method,'HEAD');assert.equal(envelope.url,origin+'/api/book');assert.equal(envelope.headers.cookie,'__Host-lumen_session=fixture');
});
test('bad request origins, methods, queries and oversized login bodies never contact Cloudflare',async()=>{
  let reads=0;const bridge=createCloudflareBridge({env,fetch:async()=>{reads++;throw Error('must not contact');}});
  for(const request of [new Request('https://evil.example/api/book'),new Request(origin+'/api/book',{headers:{Host:'evil.example'}}),
    new Request(origin+'/api/book',{headers:{Origin:'https://evil.example'}}),new Request(origin+'/api/book',{headers:{'Sec-Fetch-Site':'cross-site'}}),
    new Request(origin+'/api/book?extra=1'),new Request(origin+'/unrelated'),new Request(origin+'/api/book',{method:'PUT'}),
    new Request(origin+'/api/book',{method:'HEAD',headers:{Cookie:'x'.repeat(13000)}}),
    new Request(origin+'/api/session',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:'x'.repeat(4097)})]) {
    assert.ok((await bridge(request)).status>=400);
  }
  assert.equal(reads,0);
});
test('bridge trusts the configured Vercel preview origin and conceals upstream failures',async()=>{
  const preview='https://lumen-preview.vercel.app',previewEnv={...env,VERCEL_ENV:'preview',VERCEL_URL:'lumen-preview.vercel.app'};
  const bridge=createCloudflareBridge({env:previewEnv,fetch:async(url,options)=>{
    assert.equal(JSON.parse(options.body).url,preview+'/api/book');throw Error('private credentials');
  }});
  const response=await bridge(new Request(preview+'/api/book'));assert.equal(response.status,503);assert.ok(!(await response.text()).includes('credentials'));
  for(const badEnv of [{...env,LUMEN_WORKER_ORIGIN:'http://unsafe.example'},{...env,LUMEN_WORKER_ORIGIN:worker+'/path'},
    {...env,LUMEN_WORKER_ORIGIN:'https://user:password@unsafe.example'},{...env,LUMEN_BRIDGE_KEY:'short'}]) {
    assert.equal((await createCloudflareBridge({env:badEnv,fetch:()=>{throw Error('must not call');}})(new Request(origin+'/api/book'))).status,503);
  }
});
test('authenticated asset hook receives verified claims and skips blob reads, including HEAD and conditions',async()=>{
  const now=1800000000,token=Buffer.alloc(32,7).toString('base64url'),bytes=Buffer.from('audio'),hash=sha256(bytes);
  const path='audio.'+hash+'.mp3',index={schemaVersion:1,releaseId:'fixture',appOrigin:origin,manifestPath:'book.'+hash+'.json',assets:[
    {path,blobPath:'lumen/'+path,sha256:hash,bytes:bytes.length,contentType:'audio/mpeg'},
    {path:'book.'+hash+'.json',blobPath:'lumen/book.'+hash+'.json',sha256:hash,bytes:bytes.length,contentType:'application/json'}]};
  const config={LUMEN_APP_ORIGIN:origin,LUMEN_SESSION_SECRET:Buffer.alloc(32,9).toString('base64url'),LUMEN_INVITES_JSON:JSON.stringify([{id:'fixture',tokenHash:sha256(token),version:1,expiresAt:now+3600,revoked:false}])};
  let hooks=0,blobs=0;
  const reader=createPrivateReader({env:config,now:()=>now*1000,loadIndex:async()=>index,getBlob:async()=>{blobs++;throw Error('unexpected');},assetResponse:async({claims,asset})=>{
    hooks++;assert.equal(claims.i,'fixture');assert.equal(claims.exp,now+3600);assert.equal(asset.path,path);
    return new Response(null,{status:307,headers:{Location:worker+'/media/fixture'}});
  }});
  const login=await reader(new Request(origin+'/api/session',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({invite:token})}));
  const cookie=login.headers.get('set-cookie').split(';')[0];
  for(const [method,extra] of [['GET',{}],['HEAD',{}],['GET',{'If-None-Match':'*'}],['GET',{Range:'bytes=0-1'}]]) {
    assert.equal((await reader(new Request(origin+'/api/assets/'+path,{method,headers:{Cookie:cookie,...extra}}))).status,307);
  }
  assert.equal(hooks,4);assert.equal(blobs,0);
  assert.equal((await reader(new Request(origin+'/api/assets/'+path))).status,401);assert.equal(hooks,4);
});
