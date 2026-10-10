'use strict';
// Local workerd/R2/HTTPS regression proof; every credential and byte is a fixture.
// First run: npm --prefix web/cloudflare ci && npm --prefix web/cloudflare run build
const {Miniflare,Log,LogLevel,convertV4MiniflareOptions}=require('../cloudflare/node_modules/miniflare');
const https=require('node:https'),crypto=require('node:crypto'),assert=require('node:assert/strict'),fs=require('node:fs');
const path=require('node:path'),os=require('node:os'),{test}=require('node:test');
const {createCloudflareBridge}=require('../server/cloudflare-bridge.cjs');
const webRoot=path.resolve(__dirname,'..');
const bundlePath=path.join(webRoot,'cloudflare','.cloudflare','output','v0','workers','default','bundle','worker.js');
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
const options={modules:true,scriptPath:bundlePath,compatibilityDate:'2026-10-10',compatibilityFlags:['nodejs_compat'],
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
  assert.ok(fs.existsSync(bundlePath),'Run npm --prefix web/cloudflare run build before runtime fixtures.');
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
    const report={schemaVersion:1,scope:'tiny local workerd/R2/HTTPS fixtures only',checks,passed:checks.length,bulkSourcesRead:false,accountAccessed:false,deploymentPerformed:false,
      workerSha256:digest(fs.readFileSync(path.join(webRoot,'cloudflare','worker.mjs'))),unitTestSha256:digest(fs.readFileSync(path.join(__dirname,'cloudflare-worker.cjs'))),runtimeTestSha256:digest(fs.readFileSync(__filename)),bundleSha256:digest(fs.readFileSync(bundlePath)),fixtureAudioBytes:audio.length,fixtureTextBytes:text.length};
    const reportPath=path.join(fs.mkdtempSync(path.join(os.tmpdir(),'lumen-cloudflare-runtime-')),'report.json');
    fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n',{mode:0o600,flag:'wx'});context.diagnostic(JSON.stringify({passed:checks.length,report:reportPath}));
  }finally{await mf.dispose();}
});
