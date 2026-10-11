'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {Readable}=require('node:stream'),crypto=require('node:crypto');
const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
const {createR2WorkerAdapter,releasePrefix,releaseIdFromPrefix,createReleaseStoragePolicy}=require('../scripts/r2-worker-adapter.cjs');
const {uploadR2Release}=require('../scripts/upload_r2_release.cjs');
const workerPromise=import('../cloudflare/upload-worker.mjs');
const ORIGIN='https://upload.example',PREFIX='lumen/releases/lumen-private-192-v4/',SECRET='u'.repeat(43);
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const key=PREFIX+'assets/book-manifest.'+digest('fixture')+'.json';
const bytes=Buffer.from('{"fixture":"private"}\n');
function nativeFixture() {
  const objects=new Map(),calls=[];
  function metadata(objectKey,item) {
    return {key:objectKey,size:item.bytes.length,etag:'etag-'+digest(item.bytes),version:item.version,
      httpMetadata:{...item.httpMetadata},customMetadata:{...item.customMetadata}};
  }
  const bucket={
    async head(objectKey){calls.push(['head',objectKey]);const item=objects.get(objectKey);return item?metadata(objectKey,item):null;},
    async get(objectKey,options={}){
      calls.push(['get',objectKey,options]);const item=objects.get(objectKey);if(!item)return null;
      const info=metadata(objectKey,item);
      if(options.onlyIf?.etagMatches!==info.etag)return info;
      const range=options.range,body=range?item.bytes.subarray(range.offset,range.offset+range.length):item.bytes;
      return {...info,range:{offset:range?range.offset:0,length:body.length},body:new Response(body).body};
    },
    async put(objectKey,body,options){
      calls.push(['put',objectKey,options]);assert.equal(options.onlyIf.get('If-None-Match'),'*');
      if(objects.has(objectKey))return null;
      const chunks=[];for await(const chunk of body)chunks.push(Buffer.from(chunk));
      const value=Buffer.concat(chunks);if(digest(value)!==options.sha256)throw Error('Fixture checksum mismatch');
      const item={bytes:value,version:'v1',httpMetadata:{...options.httpMetadata},customMetadata:{...options.customMetadata}};
      objects.set(objectKey,item);return metadata(objectKey,item);
    }
  };
  const env={LUMEN_R2:bucket,LUMEN_UPLOAD_SECRET:SECRET,LUMEN_UPLOAD_PREFIX:PREFIX};
  return {objects,calls,bucket,env,seed(objectKey=key,value=bytes,type='application/json'){
    objects.set(objectKey,{bytes:value,version:'v1',httpMetadata:{contentType:type,cacheControl:'private, no-store'},customMetadata:{sha256:digest(value)}});
  }};
}
function request(objectKey=key,options={}) {
  const headers=new Headers(options.headers);if(!headers.has('Authorization'))headers.set('Authorization','Bearer '+SECRET);
  return new Request(ORIGIN+'/'+objectKey,{...options,headers,...(options.body?{duplex:'half'}:{})});
}
function putHeaders(value=bytes,type='application/json') {
  return {'If-None-Match':'*','Content-Length':String(value.length),'Content-Type':type,'X-Lumen-Sha256':digest(value)};
}
function adapterOptions(){return {onlyIf:new Headers({'If-None-Match':'*'}),contentLength:bytes.length,sha256:digest(bytes),
  httpMetadata:{contentType:'application/json',cacheControl:'private, no-store'},customMetadata:{sha256:digest(bytes)}};}
function bridge(fixture,requests=[]){return async(url,init)=>{requests.push({url,init});return (await workerPromise).default.fetch(new Request(url,init),fixture.env);};}
function metadata(response){return JSON.parse(Buffer.from(response.headers.get('X-Lumen-R2-Metadata'),'base64url').toString());}

test('Worker authenticates before bucket metadata or upload-body consumption',async()=>{
  const f=nativeFixture(),worker=(await workerPromise).default;let reads=0;
  const body=new ReadableStream({pull(controller){reads++;controller.enqueue(bytes);controller.close();}},{highWaterMark:0});
  const response=await worker.fetch(request(key,{method:'PUT',headers:{...putHeaders(),Authorization:'Bearer wrong'},body}),f.env);
  assert.equal(response.status,401);assert.equal(reads,0);assert.equal(f.calls.length,0);
  const missing=await worker.fetch(new Request(ORIGIN+'/'+key),f.env);assert.equal(missing.status,401);assert.equal(f.calls.length,0);
});
test('Worker rejects foreign prefixes, encoded traversal, queries and unsupported methods before R2',async()=>{
  const f=nativeFixture(),worker=(await workerPromise).default;
  for(const objectKey of ['lumen/releases/foreign/assets/a.json',PREFIX+'assets/%2foutside.json',PREFIX+'assets/%252e%252e/secret.json',key+'?secret=ignored'])
    assert.ok((await worker.fetch(request(objectKey),f.env)).status>=400);
  for(const method of ['POST','DELETE','PATCH','OPTIONS'])assert.equal((await worker.fetch(request(key,{method}),f.env)).status,405);
  assert.equal(f.calls.length,0);
});
test('Worker fail-closed configuration does not access R2',async()=>{
  const f=nativeFixture(),worker=(await workerPromise).default;
  for(const env of [{...f.env,LUMEN_UPLOAD_SECRET:'short'},{...f.env,LUMEN_UPLOAD_PREFIX:'lumen/releases/../foreign/'},{...f.env,LUMEN_R2:null},
    {...f.env,LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON:'not json'},{...f.env,LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON:'{}'},
    {...f.env,LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON:JSON.stringify(['lumen-private-192-v4'])}])
    assert.equal((await worker.fetch(request(),env)).status,503);
  assert.equal(f.calls.length,0);
});
test('Worker conditional upload publishes checked native metadata and never overwrites',async()=>{
  const f=nativeFixture(),worker=(await workerPromise).default;
  const response=await worker.fetch(request(key,{method:'PUT',headers:putHeaders(),body:bytes}),f.env);
  assert.equal(response.status,201);assert.equal(metadata(response).size,bytes.length);assert.equal(metadata(response).customMetadata.sha256,digest(bytes));
  assert.equal(response.headers.get('Cache-Control'),'private, no-store');assert.equal(response.headers.get('Content-Encoding'),null);
  const again=await worker.fetch(request(key,{method:'PUT',headers:putHeaders(),body:bytes}),f.env);
  assert.equal(again.status,412);assert.deepEqual(f.objects.get(key).bytes,bytes);assert.equal(f.calls.filter(c=>c[0]==='head').length,0);
});
test('Worker validates conditional headers, SHA, length, MIME and encoding before reading body',async()=>{
  const f=nativeFixture(),worker=(await workerPromise).default;
  const changes=[{'If-None-Match':'other'},{'X-Lumen-Sha256':'bad'},{'Content-Length':'0'},{'Content-Length':'1.5'},
    {'Content-Type':'text/plain'},{'Content-Type':'audio/mpeg'},{'Content-Encoding':'gzip'}];
  for(const change of changes){const response=await worker.fetch(request(key,{method:'PUT',headers:{...putHeaders(),...change},body:bytes}),f.env);assert.equal(response.status,400);}
  assert.equal(f.calls.length,0);
});
test('Worker streaming length or SHA mismatch leaves no committed R2 object',async()=>{
  const worker=(await workerPromise).default;
  for(const change of [{'Content-Length':String(bytes.length-1)},{'Content-Length':String(bytes.length+1)},{'X-Lumen-Sha256':'0'.repeat(64)}]){
    const f=nativeFixture();assert.equal((await worker.fetch(request(key,{method:'PUT',headers:{...putHeaders(),...change},body:bytes}),f.env)).status,503);
    assert.equal(f.objects.size,0);
  }
});
test('Worker uses the runtime known-length stream and rejects its short or long bodies before commit',async()=>{
  const previous=globalThis.FixedLengthStream,expected=[];
  globalThis.FixedLengthStream=class extends TransformStream{
    constructor(length){let count=0;expected.push(length);super({
      transform(chunk,controller){count+=chunk.byteLength;if(count>length)throw Error('Fixture fixed-length overflow');controller.enqueue(chunk);},
      flush(){if(count!==length)throw Error('Fixture fixed-length underflow');}
    });}
  };
  try{
    const worker=(await workerPromise).default;
    for(const length of [bytes.length,bytes.length-1,bytes.length+1]){
      const f=nativeFixture(),response=await worker.fetch(request(key,{method:'PUT',headers:{...putHeaders(),'Content-Length':String(length)},body:bytes}),f.env);
      assert.equal(response.status,length===bytes.length?201:503);assert.equal(f.objects.size,length===bytes.length?1:0);
    }
    assert.deepEqual(expected,[bytes.length,bytes.length-1,bytes.length+1]);
  }finally{if(previous===undefined)delete globalThis.FixedLengthStream;else globalThis.FixedLengthStream=previous;}
});
test('Worker returns full-size HEAD and pinned full or exact closed-range GET metadata',async()=>{
  const f=nativeFixture();f.seed();const worker=(await workerPromise).default,etag=(await f.bucket.head(key)).etag;
  const head=await worker.fetch(request(key,{method:'HEAD'}),f.env);assert.equal(head.status,200);assert.equal(head.body,null);assert.equal(metadata(head).size,bytes.length);
  const full=await worker.fetch(request(key,{headers:{'If-Match':'"'+etag+'"'}}),f.env);
  assert.equal(full.status,200);assert.deepEqual(Buffer.from(await full.arrayBuffer()),bytes);assert.equal(metadata(full).range,undefined);
  const range=await worker.fetch(request(key,{headers:{'If-Match':'"'+etag+'"',Range:'bytes=2-6'}}),f.env);
  assert.equal(range.status,206);assert.equal(range.headers.get('Content-Range'),`bytes 2-6/${bytes.length}`);
  assert.deepEqual(metadata(range).range,{offset:2,length:5});assert.equal(metadata(range).size,bytes.length);
  assert.deepEqual(Buffer.from(await range.arrayBuffer()),bytes.subarray(2,7));
});
test('Worker normalizes native exact-full ranges but rejects incorrect full-range metadata',async()=>{
  const f=nativeFixture();f.seed();const worker=(await workerPromise).default,etag=(await f.bucket.head(key)).etag;
  const native=await f.bucket.get(key,{onlyIf:{etagMatches:etag}});assert.deepEqual(native.range,{offset:0,length:bytes.length});await native.body.cancel();
  const full=await worker.fetch(request(key,{headers:{'If-Match':'"'+etag+'"'}}),f.env);
  assert.equal(full.status,200);assert.equal(metadata(full).range,undefined);assert.deepEqual(Buffer.from(await full.arrayBuffer()),bytes);
  const original=f.bucket.get;
  for(const range of [{offset:1,length:bytes.length},{offset:0,length:bytes.length-1}]){
    f.bucket.get=async(...args)=>({...await original(...args),range});
    assert.equal((await worker.fetch(request(key,{headers:{'If-Match':'"'+etag+'"'}}),f.env)).status,503);
  }
});
test('Worker missing objects and conditional reads have native R2 outcomes',async()=>{
  const f=nativeFixture(),worker=(await workerPromise).default;
  assert.equal((await worker.fetch(request(key,{method:'HEAD'}),f.env)).status,404);
  assert.equal((await worker.fetch(request(key,{headers:{'If-Match':'"missing"'}}),f.env)).status,404);
  f.seed();const response=await worker.fetch(request(key,{headers:{'If-Match':'"missing"'}}),f.env);
  assert.equal(response.status,412);assert.equal(response.body,null);assert.equal(metadata(response).size,bytes.length);
});
test('Worker rejects unpinned, multiple, suffix, open-ended and noncanonical ranges',async()=>{
  const f=nativeFixture();f.seed();const worker=(await workerPromise).default;
  for(const headers of [{},{'If-Match':'*'},{'If-Match':'"x", "y"'},{'If-Match':'"x"',Range:'bytes=0-'},
    {'If-Match':'"x"',Range:'bytes=-1'},{'If-Match':'"x"',Range:'bytes=01-2'},{'If-Match':'"x"',Range:'bytes=2-1'},
    {'If-Match':'"x"',Range:'bytes=0-1,3-4'}])assert.equal((await worker.fetch(request(key,{headers}),f.env)).status,400);
  assert.equal(f.calls.length,0);
});
test('Adapter validates trusted configuration without fetch or secret disclosure',()=>{
  for(const workerOrigin of ['http://upload.example','https://user:pass@upload.example','https://upload.example/path','https://upload.example?x=1'])
    assert.throws(()=>createR2WorkerAdapter({workerOrigin,secret:SECRET,fetch:()=>assert.fail('fetch')}));
  assert.throws(()=>createR2WorkerAdapter({workerOrigin:ORIGIN,secret:'short',fetch:()=>assert.fail('fetch')}));
});
test('Adapter transports only header secrets, pins context and preserves native metadata and streams',async()=>{
  const f=nativeFixture(),requests=[],settings={workerOrigin:ORIGIN,secret:SECRET,fetch:bridge(f,requests)};
  const adapter=createR2WorkerAdapter(settings);settings.secret='changed';settings.workerOrigin='https://foreign.example';settings.fetch=()=>assert.fail('mutated fetch');
  const created=await adapter.put(key,Readable.from([bytes]),adapterOptions());assert.equal(created.size,bytes.length);
  assert.equal(created.customMetadata.sha256,digest(bytes));assert.equal((await adapter.head(key)).etag,created.etag);
  const result=await adapter.get(key,{onlyIf:{etagMatches:created.etag},range:{offset:2,length:5}});
  assert.equal(result.size,bytes.length);assert.deepEqual(result.range,{offset:2,length:5});assert.deepEqual(Buffer.from(await new Response(result.body).arrayBuffer()),bytes.subarray(2,7));
  for(const record of requests){assert.ok(!record.url.includes(SECRET));assert.equal(record.init.headers.get('Authorization'),'Bearer '+SECRET);
    assert.equal(record.init.headers.get('Accept-Encoding'),'identity');assert.equal(record.init.redirect,'error');}
  assert.equal(requests[0].init.duplex,'half');assert.equal(requests[0].init.headers.get('Content-Length'),String(bytes.length));
});
test('Adapter uses null for missing objects and conditional-create collisions',async()=>{
  const f=nativeFixture(),adapter=createR2WorkerAdapter({workerOrigin:ORIGIN,secret:SECRET,fetch:bridge(f)});
  assert.equal(await adapter.head(key),null);assert.equal(await adapter.get(key,{onlyIf:{etagMatches:'missing'}}),null);
  await adapter.put(key,bytes,adapterOptions());assert.equal(await adapter.put(key,bytes,adapterOptions()),null);
});
test('Worker and adapter preserve the native opaque version without assuming an encoding',async()=>{
  const f=nativeFixture();f.seed();f.objects.get(key).version='opaque/+version=é';
  const adapter=createR2WorkerAdapter({workerOrigin:ORIGIN,secret:SECRET,fetch:bridge(f)});
  assert.equal((await adapter.head(key)).version,'opaque/+version=é');
});
test('Adapter rejects invalid key and option contracts before fetch',async()=>{
  const calls=[],adapter=createR2WorkerAdapter({workerOrigin:ORIGIN,secret:SECRET,fetch:(...args)=>{calls.push(args);assert.fail('fetch');}});
  for(const objectKey of ['../secret',PREFIX+'assets/%2f.json','lumen/releases/foreign/a.json'])await assert.rejects(adapter.head(objectKey));
  for(const options of [{},{onlyIf:{etagMatches:'*'}},{onlyIf:{etagMatches:'x'},range:{offset:0,length:0}},{onlyIf:{etagMatches:'x'},range:{offset:0.5,length:1}}])await assert.rejects(adapter.get(key,options));
  for(const options of [{...adapterOptions(),sha256:'bad'},{...adapterOptions(),contentLength:bytes.length+1},{...adapterOptions(),onlyIf:new Headers()},
    {...adapterOptions(),httpMetadata:{contentType:'text/plain'}},{...adapterOptions(),customMetadata:{sha256:'0'.repeat(64)}}])await assert.rejects(adapter.put(key,bytes,options));
  assert.equal(calls.length,0);
});
test('Adapter rejects malformed or encoded metadata and compressed transport bodies',async()=>{
  const f=nativeFixture();f.seed();const original=bridge(f);
  for(const mode of ['missing','invalid','foreign-key','bad-size','bad-sha','gzip','length','etag','range']){
    const adapter=createR2WorkerAdapter({workerOrigin:ORIGIN,secret:SECRET,fetch:async(...args)=>{
      const response=await original(...args),headers=new Headers(response.headers);
      if(mode==='missing')headers.delete('X-Lumen-R2-Metadata');else if(mode==='invalid')headers.set('X-Lumen-R2-Metadata','%%');
      else if(['foreign-key','bad-size','bad-sha','range'].includes(mode)){
        const data=metadata(response);if(mode==='foreign-key')data.key=PREFIX+'other.json';if(mode==='bad-size')data.size=0;
        if(mode==='bad-sha')data.customMetadata.sha256='bad';if(mode==='range')data.range={offset:1,length:bytes.length};
        headers.set('X-Lumen-R2-Metadata',Buffer.from(JSON.stringify(data)).toString('base64url'));
      }else if(mode==='gzip')headers.set('Content-Encoding','gzip');else if(mode==='length')headers.set('Content-Length','1');else if(mode==='etag')headers.set('ETag','"foreign"');
      return new Response(response.body,{status:response.status,headers});
    }});
    await assert.rejects(adapter.get(key,{onlyIf:{etagMatches:'etag-'+digest(bytes)}}));
  }
});
test('Adapter and upload engine integrate full hashes, every range and index-last publication offline',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'lumen-r2-bridge-'));try{
    const f=nativeFixture(),requests=[],adapter=createR2WorkerAdapter({workerOrigin:ORIGIN,secret:SECRET,fetch:bridge(f,requests)}),assets=[];
    for(const [i,body] of [Buffer.from('{"bookId":"lumen"}\n'),Buffer.from(Array.from({length:3507},(_,n)=>n%251))].entries()){
      const hash=digest(body),logical=i===0?`book-manifest.${hash}.json`:`tracks/chapter-001/audio.${hash}.mp3`,sourcePath=path.join(root,'source-'+i);
      await fs.writeFile(sourcePath,body);const width=Math.min(1024,body.length),starts=[0,Math.floor((body.length-width)/2),body.length-width];
      assets.push({url:'https://lumen.example/api/assets/'+logical,sourcePath,sha256:hash,bytes:body.length,contentType:i?'audio/mpeg':'application/json',immutable:true,
        samples:starts.map(start=>({start,end:start+width-1,sha256:digest(body.subarray(start,start+width))}))});
    }
    const inventory={schemaVersion:1,releaseId:'lumen-private-192-v4',accessModel:'private',appOrigin:'https://lumen.example',mediaOrigins:['https://lumen.example'],assets};
    const result=await uploadR2Release({root,inventory,manifestURL:assets[0].url,r2:adapter,maxUploadBytes:2_000_000,verifyByteBudget:4_000_000});
    assert.equal(result.status,'uploaded-private-r2');assert.equal(result.rangeReads,6);assert.equal(result.assets.length,2);assert.equal(f.objects.size,3);
    assert.ok(requests.filter(r=>r.init.method==='PUT').at(-1).url.includes('server-index.'));
    const resumed=await uploadR2Release({root,inventory,manifestURL:assets[0].url,r2:adapter,maxUploadBytes:2_000_000,verifyByteBudget:4_000_000});assert.equal(resumed.uploadedBytes,0);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});
test('shared release policy derives exact write prefixes and tightly scoped previous audio reads',()=>{
  const readOnlyReleaseIds=['lumen-private-192-v4'],policy=createReleaseStoragePolicy({releaseId:'illustrated-v5',readOnlyReleaseIds});
  const prefix=releasePrefix('illustrated-v5'),audioKey=PREFIX+'assets/tracks/audio.'+digest(bytes)+'.mp3';
  assert.equal(releaseIdFromPrefix(prefix),'illustrated-v5');assert.equal(policy.prefix,prefix);
  assert.equal(policy.canWrite(prefix+'assets/new.'+digest(bytes)+'.webp'),true);
  assert.equal(policy.canRead(audioKey),true);assert.equal(policy.canWrite(audioKey),false);
  for(const foreign of [key,PREFIX+'assets/art.'+digest(bytes)+'.webp',PREFIX+'server-index.'+digest(bytes)+'.json',
    PREFIX+'assets/audio.mp3',PREFIX+'assets/%2f/audio.'+digest(bytes)+'.mp3',
    'lumen/releases/not-allowed/assets/audio.'+digest(bytes)+'.mp3']){
    assert.equal(policy.canRead(foreign),false);assert.equal(policy.canWrite(foreign),false);
  }
  readOnlyReleaseIds.push('not-allowed');assert.equal(policy.canRead('lumen/releases/not-allowed/assets/audio.'+digest(bytes)+'.mp3'),false);
  assert.ok(Object.isFrozen(policy)&&Object.isFrozen(policy.readOnlyReleaseIds));
  for(const releaseId of ['',null,'UPPER','../other','with.dot','with_slash/x','a'.repeat(129)])assert.throws(()=>releasePrefix(releaseId));
  for(const prefix of ['lumen/releases/a','lumen/releases/a//','lumen/releases/a/assets/','lumen/releases/%61/','elsewhere/a/'])assert.throws(()=>releaseIdFromPrefix(prefix));
  for(const ids of [null,{},['illustrated-v5'],['prior','prior'],['../prior'],Array.from({length:9},(_,i)=>'prior-'+i)])
    assert.throws(()=>createReleaseStoragePolicy({releaseId:'illustrated-v5',readOnlyReleaseIds:ids}));
});
test('Worker supports a new exact release prefix while older audio is read-only and expressly allowlisted',async()=>{
  const f=nativeFixture(),worker=(await workerPromise).default,releaseId='illustrated-v5',prefix=releasePrefix(releaseId);
  f.env.LUMEN_UPLOAD_PREFIX=prefix;f.env.LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON=JSON.stringify(['lumen-private-192-v4']);
  const oldKey=PREFIX+'assets/audio.'+digest(bytes)+'.mp3',newKey=prefix+'assets/book.'+digest(bytes)+'.json';
  f.seed(oldKey,bytes,'audio/mpeg');
  const uploaded=await worker.fetch(request(newKey,{method:'PUT',headers:putHeaders(),body:bytes}),f.env);assert.equal(uploaded.status,201);
  const head=await worker.fetch(request(oldKey,{method:'HEAD'}),f.env);assert.equal(head.status,200);
  const audio=await worker.fetch(request(oldKey,{headers:{'If-Match':head.headers.get('ETag'),Range:'bytes=2-6'}}),f.env);
  assert.equal(audio.status,206);assert.deepEqual(Buffer.from(await audio.arrayBuffer()),bytes.subarray(2,7));
  const count=f.calls.length;
  for(const objectKey of [key,PREFIX+'assets/art.'+digest(bytes)+'.webp',PREFIX+'server-index.'+digest(bytes)+'.json',
    'lumen/releases/not-allowed/assets/audio.'+digest(bytes)+'.mp3'])assert.equal((await worker.fetch(request(objectKey,{method:'HEAD'}),f.env)).status,400);
  assert.equal((await worker.fetch(request(oldKey,{method:'PUT',headers:putHeaders(bytes,'audio/mpeg'),body:bytes}),f.env)).status,400);
  assert.equal(f.calls.length,count);assert.deepEqual(f.objects.get(oldKey).bytes,bytes);
  const defaultDenied=await worker.fetch(request(oldKey,{method:'HEAD'}),{...f.env,LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON:undefined});
  assert.equal(defaultDenied.status,400);assert.equal(f.calls.length,count);
});
test('Adapter binds the new prefix and older audio read scope before fetch or body consumption',async()=>{
  const f=nativeFixture(),requests=[],releaseId='illustrated-v5',readOnlyReleaseIds=['lumen-private-192-v4'];
  f.env.LUMEN_UPLOAD_PREFIX=releasePrefix(releaseId);f.env.LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON=JSON.stringify(readOnlyReleaseIds);
  const oldKey=PREFIX+'assets/audio.'+digest(bytes)+'.mp3',newKey=releasePrefix(releaseId)+'assets/book.'+digest(bytes)+'.json';
  f.seed(oldKey,bytes,'audio/mpeg');
  const adapter=createR2WorkerAdapter({workerOrigin:ORIGIN,secret:SECRET,releaseId,readOnlyReleaseIds,fetch:bridge(f,requests)});
  readOnlyReleaseIds.push('foreign');await adapter.put(newKey,bytes,adapterOptions());
  const head=await adapter.head(oldKey),audio=await adapter.get(oldKey,{onlyIf:{etagMatches:head.etag}});
  assert.deepEqual(Buffer.from(await new Response(audio.body).arrayBuffer()),bytes);
  const count=requests.length;let consumed=0;
  const forbidden=Readable.from((async function*(){consumed++;yield bytes;})());
  await assert.rejects(adapter.put(oldKey,forbidden,{...adapterOptions(),httpMetadata:{contentType:'audio/mpeg',cacheControl:'private, no-store'}}));
  assert.equal(consumed,0);assert.equal(requests.length,count);forbidden.destroy();
  for(const old of [key,'lumen/releases/foreign/assets/audio.'+digest(bytes)+'.mp3'])await assert.rejects(adapter.head(old));
  assert.equal(requests.length,count);
});
test('new-release Worker and adapter reuse old audio with complete independent proofs and index-last writes',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'lumen-r2-reuse-bridge-'));try{
    const f=nativeFixture(),requests=[],releaseId='illustrated-v5',readOnlyReleaseIds=['lumen-private-192-v4'],assets=[];
    f.env.LUMEN_UPLOAD_PREFIX=releasePrefix(releaseId);f.env.LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON=JSON.stringify(readOnlyReleaseIds);
    const adapter=createR2WorkerAdapter({workerOrigin:ORIGIN,secret:SECRET,releaseId,readOnlyReleaseIds,fetch:bridge(f,requests)});
    const sources=[Buffer.from('{"bookId":"lumen"}\n'),Buffer.from(Array.from({length:3507},(_,n)=>n%251)),Buffer.from('tiny painting bytes')];
    for(const [i,body] of sources.entries()){
      const hash=digest(body),extension=['json','mp3','webp'][i],type=['application/json','audio/mpeg','image/webp'][i];
      const logical=(i===0?'book-manifest':i===1?'tracks/chapter-001/audio':'images/painting')+'.'+hash+'.'+extension,sourcePath=path.join(root,'source-'+i);
      await fs.writeFile(sourcePath,body);const width=Math.min(1024,body.length),starts=[0,Math.floor((body.length-width)/2),body.length-width];
      assets.push({url:'https://lumen.example/api/assets/'+logical,sourcePath,sha256:hash,bytes:body.length,contentType:type,immutable:true,
        samples:starts.map(start=>({start,end:start+width-1,sha256:digest(body.subarray(start,start+width))}))});
    }
    const oldKey=PREFIX+'assets/tracks/chapter-001/audio.'+assets[1].sha256+'.mp3';f.seed(oldKey,sources[1],'audio/mpeg');
    const inventory={schemaVersion:1,releaseId,accessModel:'private',appOrigin:'https://lumen.example',mediaOrigins:['https://lumen.example'],assets};
    const result=await uploadR2Release({root,inventory,manifestURL:assets[0].url,r2:adapter,reuseAudio:[{url:assets[1].url,objectKey:oldKey}],
      maxUploadBytes:2_000_000,verifyByteBudget:4_000_000});
    assert.equal(result.reusedAssetCount,1);assert.equal(result.reusedBytes,sources[1].length);assert.equal(result.rangeReads,9);
    assert.deepEqual(result.readOnlyReleaseIds,readOnlyReleaseIds);assert.equal(result.assets[1].objectKey,oldKey);
    const writes=requests.filter(row=>row.init.method==='PUT');assert.equal(writes.length,3);
    assert.ok(writes.every(row=>new URL(row.url).pathname.slice(1).startsWith(releasePrefix(releaseId))));
    assert.ok(writes.at(-1).url.includes('server-index.'));assert.equal(f.objects.get(oldKey).version,'v1');
    assert.equal(requests.filter(row=>row.init.method==='GET'&&new URL(row.url).pathname==='/'+oldKey).length,4);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});
