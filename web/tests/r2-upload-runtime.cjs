'use strict';
// Local workerd/R2 proof for the compiled temporary uploader. No account access.
// Build first: npm --prefix web/cloudflare run build -- --mode upload
const {Miniflare,Log,LogLevel,convertV4MiniflareOptions}=require('../cloudflare/node_modules/miniflare');
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {createR2WorkerAdapter,releasePrefix}=require('../scripts/r2-worker-adapter.cjs');
const {makePlan,uploadR2Release}=require('../scripts/upload_r2_release.cjs');
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const bundle=path.resolve(__dirname,'../cloudflare/.cloudflare/output/v0/workers/default/bundle/upload-worker.js');
test('actual workerd uploader keeps writes in the new release and independently verifies reused R2 audio',{timeout:30000},async(context)=>{
  await fs.access(bundle);
  const releaseId='illustration-runtime',previous='lumen-private-192-v4',secret='u'.repeat(43),origin='https://upload-fixture.example';
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'lumen-r2-runtime-')),checks=[];
  const check=(name,condition)=>{assert.ok(condition,name);checks.push(name);};
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:bundle,compatibilityDate:'2026-10-10',compatibilityFlags:['nodejs_compat'],
    host:'127.0.0.1',port:0,bindings:{LUMEN_UPLOAD_SECRET:secret,LUMEN_UPLOAD_PREFIX:releasePrefix(releaseId),
      LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON:JSON.stringify([previous])},r2Buckets:{LUMEN_R2:'fixture-only'},log:new Log(LogLevel.NONE)}));
  try{
    const bucket=await mf.getR2Bucket('LUMEN_R2'),assets=[],sources=[Buffer.from('{"bookId":"lumen"}\n'),
      Buffer.from(Array.from({length:3507},(_,i)=>i%251)),Buffer.from('fixture painting')];
    for(const [i,body] of sources.entries()){
      const hash=digest(body),type=['application/json','audio/mpeg','image/webp'][i],extension=['json','mp3','webp'][i];
      const logical=['book-manifest','tracks/chapter-001/audio','images/painting'][i]+'.'+hash+'.'+extension,sourcePath=path.join(root,'source-'+i);
      await fs.writeFile(sourcePath,body);const width=Math.min(1024,body.length),starts=[0,Math.floor((body.length-width)/2),body.length-width];
      assets.push({url:'https://lumen-fixture.example/api/assets/'+logical,sourcePath,sha256:hash,bytes:body.length,contentType:type,immutable:true,
        samples:starts.map(start=>({start,end:start+width-1,sha256:digest(body.subarray(start,start+width))}))});
    }
    const oldKey=releasePrefix(previous)+'assets/tracks/chapter-001/audio.'+assets[1].sha256+'.mp3';
    await bucket.put(oldKey,sources[1],{sha256:Buffer.from(assets[1].sha256,'hex'),httpMetadata:{contentType:'audio/mpeg',cacheControl:'private, no-store'},
      customMetadata:{sha256:assets[1].sha256}});
    const originalVersion=(await bucket.head(oldKey)).version,requests=[];
    const adapter=createR2WorkerAdapter({workerOrigin:origin,secret,releaseId,readOnlyReleaseIds:[previous],fetch:async(url,init)=>{
      requests.push({key:new URL(url).pathname.slice(1),method:init.method});return mf.dispatchFetch(url,init);
    }});
    const inventory={schemaVersion:1,releaseId,accessModel:'private',appOrigin:'https://lumen-fixture.example',mediaOrigins:['https://lumen-fixture.example'],assets};
    const reuseAudio=[{url:assets[1].url,objectKey:oldKey}],plan=makePlan(inventory,assets[0].url,{reuseAudio});
    const result=await uploadR2Release({root,inventory,manifestURL:assets[0].url,r2:adapter,reuseAudio,maxUploadBytes:plan.uploadBytes,verifyByteBudget:plan.verificationBytes});
    check('compiled shared CommonJS policy and streaming upload run in actual workerd',result.status==='uploaded-private-r2');
    check('old audio receives full native R2 read and all three exact ranges',requests.filter(row=>row.key===oldKey&&row.method==='GET').length===4&&result.rangeReads===9);
    check('only new JSON, painting and final index are written',requests.filter(row=>row.method==='PUT').length===3&&
      requests.filter(row=>row.method==='PUT').every(row=>row.key.startsWith(releasePrefix(releaseId))));
    check('index is the final write after byte and metadata proofs',requests.filter(row=>row.method==='PUT').at(-1).key===result.indexPath);
    check('previous audio is retained with its original native opaque version',(await bucket.head(oldKey)).version===originalVersion);
    check('verification byte budget includes reused full audio while upload excludes it',result.uploadedBytes===plan.uploadBytes&&
      result.reusedBytes===sources[1].length&&result.verificationBytes===plan.verificationBytes);
    const index=await bucket.get(result.indexPath);
    check('native R2 index bytes have the exact published SHA',digest(Buffer.from(await index.arrayBuffer()))===result.indexSha256);
    const headers={Authorization:'Bearer '+secret,'If-None-Match':'*','Content-Type':'audio/mpeg','Content-Length':String(sources[1].length),'X-Lumen-Sha256':assets[1].sha256};
    check('actual Worker rejects writes to allowlisted previous audio',(await mf.dispatchFetch(origin+'/'+oldKey,{method:'PUT',headers,body:sources[1]})).status===400);
    for(const key of [releasePrefix(previous)+'assets/book.'+assets[0].sha256+'.json',releasePrefix(previous)+'assets/art.'+assets[2].sha256+'.webp',
      'lumen/releases/foreign/assets/audio.'+assets[1].sha256+'.mp3']){
      check('actual Worker denies non-audio or unlisted old object reads',(await mf.dispatchFetch(origin+'/'+key,{method:'HEAD',headers:{Authorization:'Bearer '+secret}})).status===400);
    }
    check('actual Worker denies a guest before exposing private audio',(await mf.dispatchFetch(origin+'/'+oldKey,{method:'HEAD'})).status===401);
    const newAsset=result.assets[0];
    const repeated=await mf.dispatchFetch(origin+'/'+newAsset.objectKey,{method:'PUT',headers:{...headers,'Content-Type':'application/json',
      'Content-Length':String(sources[0].length),'X-Lumen-Sha256':assets[0].sha256},body:sources[0]});
    check('actual conditional write cannot overwrite an existing new object',repeated.status===412);
    context.diagnostic(JSON.stringify({passed:checks.length,scope:'tiny local workerd/R2 only',accountAccessed:false,deploymentPerformed:false}));
  }finally{await mf.dispose();await fs.rm(root,{recursive:true,force:true});}
});
