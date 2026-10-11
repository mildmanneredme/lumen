'use strict';
// Local workerd/R2/HTTPS regression proof; every credential and byte is a fixture.
// Uses the already installed Cloudflare test dependencies and current source.
// The fixture bundle stays in memory; no CLI account/configuration is accessed.
const fixtureRequire=require('node:module').createRequire(require('node:path').resolve(__dirname,'../cloudflare/package.json'));
const {Miniflare,Log,LogLevel,convertV4MiniflareOptions}=fixtureRequire('miniflare');
const https=require('node:https'),crypto=require('node:crypto'),assert=require('node:assert/strict'),fs=require('node:fs');
const path=require('node:path'),os=require('node:os'),{test}=require('node:test');
const {createCloudflareBridge}=require('../server/cloudflare-bridge.cjs');
const webRoot=path.resolve(__dirname,'..');
async function fixtureBundle(contents) {
  const result=await fixtureRequire('esbuild').build({stdin:{contents,resolveDir:webRoot},bundle:true,write:false,format:'esm',platform:'neutral',target:'es2022',
    plugins:[{name:'fixture-node-builtins',setup(build){
      build.onResolve({filter:/^node:/},args=>args.kind==='require-call'?{path:args.path,namespace:'fixture-node-require'}:{path:args.path,external:true});
      build.onLoad({filter:/.*/,namespace:'fixture-node-require'},args=>({contents:'import value from '+JSON.stringify(args.path)+';module.exports=value;',loader:'js'}));
    }}]});
  return result.outputFiles[0].text;
}
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const origin='https://lumen-fixture.example',code='acorn-blend-choir-drift-eager-flock';
const audio=Buffer.from('0123456789abcdefghijklmnopqrstuvwxyz'),text=Buffer.from('{"bookId":"tiny-fixture"}\n');
const audioPath=`audio/autonoe/chapter-001.${digest(audio)}.mp3`,manifestPath=`tracks/chapter-000/book-manifest.${digest(text)}.json`;
const assets=[[audioPath,audio,'audio/mpeg'],[manifestPath,text,'application/json']].map(([path,bytes,contentType])=>({path,blobPath:`lumen/release-one/${path}`,bytes:bytes.length,sha256:digest(bytes),contentType}));
const index={schemaVersion:1,releaseId:'release-one',appOrigin:origin,manifestPath,assets};
const indexBytes=Buffer.from(JSON.stringify(index)),indexHash=digest(indexBytes),indexPath=`lumen/releases/release-one/server-index.${indexHash}.json`;
const env={LUMEN_APP_ORIGIN:origin,LUMEN_WORKER_ORIGIN:'https://media-fixture.example',
  LUMEN_SESSION_SECRET:Buffer.alloc(32,9).toString('base64url'),LUMEN_BRIDGE_KEY:Buffer.alloc(32,10).toString('base64url'),LUMEN_MEDIA_SECRET:Buffer.alloc(32,11).toString('base64url'),
  LUMEN_INVITES_JSON:JSON.stringify([{id:'fixture-readers',tokenHash:digest(code),version:1,expiresAt:2_100_000_000,revoked:false}]),
  LUMEN_RELEASE_INDEX_PATH:indexPath,LUMEN_RELEASE_INDEX_SHA256:indexHash};
const options={modules:true,cf:false,compatibilityDate:'2026-10-10',compatibilityFlags:['nodejs_compat'],
  host:'127.0.0.1',port:0,https:true,bindings:env,r2Buckets:{LUMEN_R2:'tiny-fixture-bucket'},log:new Log(LogLevel.NONE)};
const checks=[];
const check=(label,condition)=>{assert.ok(condition,label);checks.push(label);};
function raw(url,init={}) {
  assert.equal(new URL(url).hostname,'127.0.0.1');
  return new Promise((resolve,reject)=>{
    const req=https.request(url,{method:init.method||'GET',headers:init.headers||{},rejectUnauthorized:false},res=>{
      const chunks=[];res.on('data',chunk=>chunks.push(Buffer.from(chunk)));res.on('error',reject);
      res.on('end',()=>resolve({status:res.statusCode,headers:new Headers(Object.entries(res.headers).map(([name,value])=>[name,Array.isArray(value)?value.join(', '):value])),bytes:Buffer.concat(chunks)}));
    });req.on('error',reject);req.setTimeout(8000,()=>req.destroy(Error('Fixture HTTP timeout')));if(init.body!==undefined)req.write(init.body);req.end();
  });
}
test('actual workerd private bridge and R2 media preserve auth, native lengths and range bytes',{timeout:30000},async(context)=>{
  const fixtureRoot=fs.mkdtempSync(path.join(os.tmpdir(),'lumen-private-reader-runtime-'));
  const script=await fixtureBundle('export {default} from '+JSON.stringify(path.join(webRoot,'cloudflare/worker.mjs'))+';');
  options.script=script;options.rootPath=fixtureRoot;
  const mf=new Miniflare(convertV4MiniflareOptions(options));
  try {
    const ready=await mf.ready;env.LUMEN_WORKER_ORIGIN=ready.origin;options.port=Number(ready.port);options.bindings={...env};await mf.setOptions(convertV4MiniflareOptions(options));await mf.ready;
    const bucket=await mf.getR2Bucket('LUMEN_R2');
    const put=async(key,bytes,type)=>bucket.put(key,bytes,{sha256:Buffer.from(digest(bytes),'hex'),httpMetadata:{contentType:type,cacheControl:'private, no-store'},customMetadata:{sha256:digest(bytes)}});
    await put(assets[0].blobPath,audio,'audio/mpeg');await put(assets[1].blobPath,text,'application/json');await put(indexPath,indexBytes,'application/json');
    const object=await bucket.get(assets[0].blobPath,{range:{offset:2,length:5}});
    check('native R2 range reports full size and exact offset/length',object.size===audio.length&&object.range.offset===2&&object.range.length===5);
    check('native R2 returns the pinned custom SHA and HTTP type',object.customMetadata.sha256===digest(audio)&&object.httpMetadata.contentType==='audio/mpeg');
    check('native R2 range bytes match',Buffer.from(await object.arrayBuffer()).equals(audio.subarray(2,7)));
    await assert.rejects(()=>bucket.put('checksum-mismatch',audio,{sha256:Buffer.alloc(32)}));checks.push('native R2 rejects wrong upload checksum');
    const bridgeFetch=async(url,init)=>{const result=await raw(url,init);return new Response(init.method==='HEAD'||[204,304].includes(result.status)?null:result.bytes,{status:result.status,headers:result.headers});};
    const bridge=createCloudflareBridge({env:{LUMEN_APP_ORIGIN:origin,LUMEN_WORKER_ORIGIN:env.LUMEN_WORKER_ORIGIN,LUMEN_BRIDGE_KEY:env.LUMEN_BRIDGE_KEY},fetch:bridgeFetch});
    const request=(path,method='GET',headers={},body)=>new Request(origin+path,{method,headers,...(body!==undefined?{body}:{})});
    const guest=await bridge(request('/api/book'));check('guest cannot read JSON through real bridge HTTP',guest.status===401);
    const login=await bridge(request('/api/session','POST',{Origin:origin,'Content-Type':'application/json'},JSON.stringify({invite:code})));
    check('CJS crypto/session login succeeds in actual workerd',login.status===200&&(await login.json()).authenticated===true);
    const setCookie=login.headers.get('set-cookie'),cookie=setCookie.split(';')[0];
    check('Vercel bridge retains Secure HttpOnly SameSite device cookie',setCookie.includes('Secure')&&setCookie.includes('HttpOnly')&&setCookie.includes('SameSite=Lax'));
    const book=await bridge(request('/api/book','GET',{Cookie:cookie}));const bookBytes=Buffer.from(await book.arrayBuffer());
    if(book.status!==200){const value=await bucket.get(indexPath);console.log(JSON.stringify({diagnostic:'fixture-only index metadata',status:book.status,size:value.size,httpMetadata:value.httpMetadata,customMetadata:value.customMetadata,range:value.range,bodyLength:(await value.arrayBuffer()).byteLength,body:bookBytes.toString()}));}
    check('private JSON bytes pass across real HTTP unchanged',book.status===200&&bookBytes.equals(text));
    const head=await bridge(request('/api/book','HEAD',{Cookie:cookie}));check('original HEAD transport retains length without truncation',head.status===200&&head.headers.get('content-length')===String(text.length)&&(await head.arrayBuffer()).byteLength===0);
    const conditional=await bridge(request('/api/book','GET',{Cookie:cookie,'If-None-Match':head.headers.get('etag')}));check('conditional JSON uses bodyless 304',conditional.status===304&&(await conditional.arrayBuffer()).byteLength===0);
    const redirect=await bridge(request('/api/assets/'+audioPath,'GET',{Cookie:cookie,Range:'bytes=2-6'}));
    check('Vercel returns only no-store 307 for native media',redirect.status===307&&(await redirect.arrayBuffer()).byteLength===0&&redirect.headers.get('cache-control')==='private, no-store');
    const url=redirect.headers.get('location');check('redirect stays scoped to configured Worker and canonical audio path',new URL(url).origin===env.LUMEN_WORKER_ORIGIN&&new URL(url).pathname==='/media/'+audioPath);
    for(const [range,start,end]of[['bytes=2-6',2,6],['bytes=20-',20,35],['bytes=-5',31,35]]) {
      const response=await raw(url,{headers:{Range:range,Origin:origin}});
      if(response.status!==206||response.headers.get('content-length')!==String(end-start+1))console.log(JSON.stringify({diagnostic:'fixture-only range reply',status:response.status,headers:Object.fromEntries(response.headers),bytes:response.bytes.toString()}));
      check('real HTTP '+range+' exact 206',response.status===206&&response.headers.get('content-range')===`bytes ${start}-${end}/36`&&response.headers.get('content-length')===String(end-start+1)&&response.bytes.equals(audio.subarray(start,end+1)));
      check('real HTTP '+range+' private CORS/CORP',response.headers.get('access-control-allow-origin')===origin&&response.headers.get('cross-origin-resource-policy')==='cross-origin'&&response.headers.get('cache-control')==='private, no-store');
    }
    const mediaHead=await raw(url,{method:'HEAD',headers:{Origin:'null'}});check('direct media HEAD is bodyless and allows valid null-origin grant',mediaHead.status===200&&mediaHead.bytes.length===0&&mediaHead.headers.get('content-length')==='36');
    check('redirect-tainted null Origin receives matching CORS',mediaHead.headers.get('access-control-allow-origin')==='null');
    const preflight=await raw(url,{method:'OPTIONS',headers:{Origin:origin,'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'Range, If-Range'}});
    check('real preflight is bodyless and grants only exact origin GET/HEAD',preflight.status===204&&preflight.bytes.length===0&&preflight.headers.get('access-control-allow-origin')===origin&&preflight.headers.get('access-control-allow-methods')==='GET, HEAD');
    const full=await raw(url);check('full direct audio keeps native fixed length and bytes without buffering',full.status===200&&full.headers.get('content-length')==='36'&&full.headers.get('content-range')===null&&full.bytes.equals(audio));
    const badRange=await raw(url,{headers:{Range:'bytes=99-'}});check('real HTTP invalid range retains 416 metadata',badRange.status===416&&badRange.headers.get('content-range')==='bytes */36');
    const foreign=await raw(url,{headers:{Origin:'https://evil.example'}});check('foreign browser Origin is rejected',foreign.status===403);
    const invite=JSON.parse(env.LUMEN_INVITES_JSON)[0];invite.revoked=true;env.LUMEN_INVITES_JSON=JSON.stringify([invite]);options.bindings={...env};await mf.setOptions(convertV4MiniflareOptions(options));await mf.ready;
    const denied=await raw(url,{headers:{Range:'bytes=2-6'}});check('sole Worker policy update rejects previously minted range grant',denied.status===401);
    const deniedHead=await raw(url,{method:'HEAD'});check('revocation also denies a bodyless media HEAD',deniedHead.status===401&&deniedHead.bytes.length===0);
    const session=await bridge(request('/api/session','GET',{Cookie:cookie}));check('same policy update also rejects same-origin device session',(await session.json()).authenticated===false);
    const customCode='hidden-horizons';invite.revoked=false;invite.tokenHash=digest(customCode);invite.version++;
    env.LUMEN_INVITES_JSON=JSON.stringify([invite]);options.bindings={...env};await mf.setOptions(convertV4MiniflareOptions(options));await mf.ready;
    const oldCode=await bridge(request('/api/session','POST',{Origin:origin,'Content-Type':'application/json'},JSON.stringify({invite:code})));
    check('rotated policy rejects the previous six-word login',oldCode.status===401);
    const oldSession=await bridge(request('/api/session','GET',{Cookie:cookie}));
    check('same-grant version rotation rejects the previous cookie',(await oldSession.json()).authenticated===false);
    check('same-grant version rotation rejects the previous signed media grant',(await raw(url,{headers:{Range:'bytes=2-6'}})).status===401);
    const customSession=await bridge(request('/api/session','POST',{Origin:origin,'Content-Type':'application/json'},JSON.stringify({invite:'  HIDDEN — HORIZONS  '})));
    check('actual workerd normalizes and opens a custom two-word code',customSession.status===200&&(await customSession.json()).authenticated===true);
    const customCookie=customSession.headers.get('set-cookie').split(';')[0];
    const customBook=await bridge(request('/api/book','GET',{Cookie:customCookie}));
    check('fresh custom-code cookie reads unchanged private JSON',customBook.status===200&&Buffer.from(await customBook.arrayBuffer()).equals(text));
    const customRedirect=await bridge(request('/api/assets/'+audioPath,'GET',{Cookie:customCookie}));
    check('fresh custom-code cookie mints a new signed media grant',customRedirect.status===307);
    const customRange=await raw(customRedirect.headers.get('location'),{headers:{Range:'bytes=2-6',Origin:origin}});
    check('fresh custom-code media grant preserves native ranged bytes',customRange.status===206&&customRange.bytes.equals(audio.subarray(2,7)));
    const report={schemaVersion:1,scope:'tiny local workerd/R2/HTTPS fixtures only',checks,passed:checks.length,bulkSourcesRead:false,accountAccessed:false,deploymentPerformed:false,
      workerSha256:digest(fs.readFileSync(path.join(webRoot,'cloudflare','worker.mjs'))),unitTestSha256:digest(fs.readFileSync(path.join(__dirname,'cloudflare-worker.cjs'))),runtimeTestSha256:digest(fs.readFileSync(__filename)),bundleSha256:digest(Buffer.from(script)),bundleSource:'current source compiled in memory',fixtureAudioBytes:audio.length,fixtureTextBytes:text.length};
    const reportPath=path.join(fs.mkdtempSync(path.join(os.tmpdir(),'lumen-cloudflare-runtime-')),'report.json');
    fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n',{mode:0o600,flag:'wx'});context.diagnostic(JSON.stringify({passed:checks.length,report:reportPath}));
  }finally{await mf.dispose();fs.rmSync(fixtureRoot,{recursive:true,force:true});}
});


test('actual workerd native protector preserves fixed length and rejects wrong length, aborts and cancellations',{timeout:30000},async(context)=>{
  const source=`import {createNativeStreamProtector} from ${JSON.stringify(path.join(webRoot,'cloudflare/worker.mjs'))};
  const protect=createNativeStreamProtector();
  export default {async fetch(request){
    const mode=new URL(request.url).pathname.slice(1);let cancelled=0,cursor=0;
    if(mode.startsWith('writer-')){
      const fixed=new FixedLengthStream(8),writer=fixed.writable.getWriter(),reader=fixed.readable.getReader();
      let readRejected=false,writeRejected=false,received=[];
      const read=async()=>{try{for(;;){const row=await reader.read();if(row.done)break;received.push(...row.value);}}catch(_){readRejected=true;}finally{reader.releaseLock();}};
      const reading=read();
      try{await writer.write(new Uint8Array(mode==='writer-short'?[0,1,2,3,4,5,6]:[0,1,2,3,4,5,6,7]));
        if(mode==='writer-long')await writer.write(new Uint8Array([8]));await writer.close();}catch(_){writeRejected=true;}finally{writer.releaseLock();}
      await reading;return Response.json({readRejected,writeRejected,received});
    }
    const total=mode==='short'?7:['long','legacy-long','overflow','oversized'].includes(mode)?9:8;
    const body=new ReadableStream({pull(controller){if(cursor===total){controller.close();return;}
      if(mode==='oversized'){controller.enqueue(new Uint8Array([0,1,2,3,4,5,6,7,8]));cursor=9;}
      else controller.enqueue(new Uint8Array([cursor++]));},cancel(){cancelled++;}});
    const abort=new AbortController();let guarded;
    if(mode==='legacy-long'){
      let count=0;const checked=body.pipeThrough(new TransformStream({transform(chunk,controller){count+=chunk.byteLength;if(count>8)throw Error('length');controller.enqueue(chunk);},flush(){if(count!==8)throw Error('length');}}));
      const oldFixed=new FixedLengthStream(8);checked.pipeTo(oldFixed.writable).catch(()=>{});guarded=oldFixed.readable;
    }else guarded=protect(body,8,{signal:abort.signal});
    if(mode==='overflow'){
      const reader=guarded.getReader();let rejected=false,received=[];
      try{for(;;){const row=await reader.read();if(row.done)break;received.push(...row.value);}}catch(_){rejected=true;}finally{reader.releaseLock();}
      return Response.json({rejected,received});
    }
    if(['abort','cancel','slow'].includes(mode)){
      const reader=guarded.getReader();let received=[];received.push(...(await reader.read()).value);
      if(mode==='abort'){abort.abort();let rejected=false;try{for(;;){const row=await reader.read();if(row.done)break;}}catch(_){rejected=true;}reader.releaseLock();await new Promise(resolve=>setTimeout(resolve,0));return Response.json({cancelled,rejected});}
      if(mode==='cancel'){await reader.cancel();reader.releaseLock();await new Promise(resolve=>setTimeout(resolve,0));return Response.json({cancelled});}
      for(;;){await new Promise(resolve=>setTimeout(resolve,1));const row=await reader.read();if(row.done)break;received.push(...row.value);}reader.releaseLock();return Response.json({received});
    }
    return new Response(guarded,{headers:{'Content-Type':'application/octet-stream'}});
  }};`;
  // Compile the actual exported guard with native built-in module interop.
  const script=await fixtureBundle(source);
  const fixtureRoot=fs.mkdtempSync(path.join(os.tmpdir(),'lumen-native-stream-runtime-'));
  const mf=new Miniflare(convertV4MiniflareOptions({rootPath:fixtureRoot,cf:false,modules:true,script,compatibilityDate:'2026-10-10',compatibilityFlags:['nodejs_compat'],host:'127.0.0.1',port:0,log:new Log(LogLevel.NONE)}));
  try{
    await mf.ready;const full=await mf.dispatchFetch('https://fixture.example/full');assert.equal(full.status,200);assert.equal(full.headers.get('content-length'),'8');assert.deepEqual(Buffer.from(await full.arrayBuffer()),Buffer.from([0,1,2,3,4,5,6,7]));
    for(const mode of ['short','oversized']){
      let rejected=false,result;try{const response=await mf.dispatchFetch('https://fixture.example/'+mode);result={status:response.status,bytes:Buffer.from(await response.arrayBuffer())};}catch(_){rejected=true;}
      // workerd may reject the stream or replace an error before first byte
      // with HTTP 500; neither may yield a successful truncated response.
      assert.ok(rejected||result.status>=500,'Native '+mode+' length violation must fail, not finish HTTP 200');
    }
    const nativeLong=await mf.dispatchFetch('https://fixture.example/long'),legacyLong=await mf.dispatchFetch('https://fixture.example/legacy-long');
    assert.equal(nativeLong.status,200);assert.equal(legacyLong.status,200);
    assert.deepEqual(Buffer.from(await nativeLong.arrayBuffer()),Buffer.from([0,1,2,3,4,5,6,7]));
    assert.deepEqual(Buffer.from(await legacyLong.arrayBuffer()),Buffer.from([0,1,2,3,4,5,6,7]));
    // Neither pipeline can retract a Content-Length body after its exact eight
    // bytes reached HTTP. Direct native consumption must still reject the ninth
    // byte; no extra byte may pass through direct or HTTP consumption. Native
    // errors can surface on the readable rather than the write/pipe promise.
    const overflow=await(await mf.dispatchFetch('https://fixture.example/overflow')).json();
    assert.equal(overflow.rejected,true);assert.deepEqual(overflow.received,[0,1,2,3,4,5,6,7]);
    for(const mode of ['writer-short','writer-long']){
      const writer=await(await mf.dispatchFetch('https://fixture.example/'+mode)).json();
      assert.equal(typeof writer.writeRejected,'boolean');assert.equal(writer.readRejected,true);
      assert.deepEqual(writer.received,mode==='writer-short'?[0,1,2,3,4,5,6]:[0,1,2,3,4,5,6,7]);
    }
    assert.deepEqual(await(await mf.dispatchFetch('https://fixture.example/slow')).json(),{received:[0,1,2,3,4,5,6,7]});
    assert.deepEqual(await(await mf.dispatchFetch('https://fixture.example/abort')).json(),{cancelled:1,rejected:true});
    assert.deepEqual(await(await mf.dispatchFetch('https://fixture.example/cancel')).json(),{cancelled:1});
    context.diagnostic(JSON.stringify({scope:'tiny actual workerd native length guard only',checks:11,fixtureBytes:8,accountAccessed:false,bulkSourcesRead:false}));
  }finally{await mf.dispose();fs.rmSync(fixtureRoot,{recursive:true,force:true});}
});
