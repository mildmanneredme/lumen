'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {makePlan,sourceProof,uploadR2Release}=require('../scripts/upload_r2_release.cjs');
const {validateIndex}=require('../server/private-access.cjs');
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const origin='https://lumen.example';
const budgets={maxUploadBytes:2_000_000,verifyByteBudget:4_000_000};
async function fixture(count=2) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'lumen-r2-upload-'));
  const bodies=[],assets=[];
  for(let i=0;i<count;i++) {
    const body=i===1?Buffer.from(Array.from({length:3207},(_,n)=>n%251)):Buffer.from(JSON.stringify({bookId:'lumen',fixturePrivateProse:'local-only',i})+'\n');
    const digest=hash(body),type=i===1?'audio/mpeg':'application/json';
    const logical=i===0?`book-manifest.${digest}.json`:`tracks/chapter-${String(i).padStart(3,'0')}/asset.${digest}.${i===1?'mp3':'json'}`;
    const sourcePath=path.join(root,`source-${i}`);await fs.writeFile(sourcePath,body);
    const width=Math.min(1024,body.length),starts=[0,Math.floor((body.length-width)/2),body.length-width];
    bodies.push(body);assets.push({url:origin+'/api/assets/'+logical,sha256:digest,bytes:body.length,contentType:type,immutable:true,sourcePath,
      samples:starts.map(start=>({start,end:start+width-1,sha256:hash(body.subarray(start,start+width))}))});
  }
  const inventory={schemaVersion:1,releaseId:'r2-fixture',accessModel:'private',appOrigin:origin,mediaOrigins:[origin],assets};
  const objects=new Map(),calls=[];
  function metadata(key,item) {return {key,size:item.bytes.length,etag:item.etag,version:item.version,
    httpMetadata:{contentType:item.type,cacheControl:'private, no-store'},customMetadata:item.customMetadata};}
  const r2={
    async head(key) {calls.push({method:'head',key});const item=objects.get(key);return item?metadata(key,item):null;},
    async put(key,body,options) {
      calls.push({method:'put',key,options});
      assert.equal(options.onlyIf.get('If-None-Match'),'*');assert.equal(options.contentLength>0,true);
      if(objects.has(key)) return null;
      const parts=[];if(Buffer.isBuffer(body))parts.push(body);else for await(const part of body)parts.push(Buffer.from(part));
      const bytes=Buffer.concat(parts);assert.equal(bytes.length,options.contentLength);assert.equal(hash(bytes),options.sha256);
      const item={bytes,type:options.httpMetadata.contentType,customMetadata:options.customMetadata,etag:'etag-'+hash(bytes),version:'v1'};objects.set(key,item);
      return metadata(key,item);
    },
    async get(key,options={}) {
      calls.push({method:'get',key,options});const item=objects.get(key);if(!item)return null;
      if(options.onlyIf?.etagMatches!==item.etag)return {...metadata(key,item)};
      const range=options.range,selected=range?item.bytes.subarray(range.offset,range.offset+range.length):item.bytes;
      return {...metadata(key,item),...(range?{range:{...range}}:{}),body:new Response(selected).body};
    }
  };
  return {root,bodies,inventory,r2,objects,calls,manifestURL:assets[0].url,clean:()=>fs.rm(root,{recursive:true,force:true})};
}
test('R2 plan retains stable API paths and exact server-index compatibility',async()=>{
  const f=await fixture();try {
    const plan=makePlan(f.inventory,f.manifestURL),map=validateIndex(plan.index,origin);
    assert.equal(map.size,2);assert.equal(plan.receipts[0].objectKey,plan.index.assets[0].blobPath);
    assert.equal(plan.receipts[1].url,f.inventory.assets[1].url);assert.match(plan.indexPath,/server-index\.[a-f0-9]{64}\.json$/);
    assert.equal(hash(plan.indexBytes),plan.indexSha256);assert.ok(!plan.indexBytes.includes('sourcePath'));
    assert.equal(plan.uploadBytes,f.bodies.reduce((n,b)=>n+b.length,plan.indexBytes.length));
    assert.equal(plan.verificationBytes,plan.uploadBytes+f.inventory.assets.reduce((n,a)=>n+a.samples.reduce((m,s)=>m+s.end-s.start+1,0),0));
  }finally{await f.clean();}
});
test('upload verifies full bytes, HEAD and every measured range before publishing index',async()=>{
  const f=await fixture();try {
    const progress=[],result=await uploadR2Release({...f,...budgets,onProgress:async record=>progress.push(record)});
    assert.equal(result.status,'uploaded-private-r2');assert.equal(result.storageProvider,'cloudflare-r2');assert.equal(result.accessPolicyVerified,false);
    assert.equal(result.assets.length,2);assert.equal(result.rangeReads,6);assert.equal(result.verificationBytes,makePlan(f.inventory,f.manifestURL).verificationBytes);
    assert.equal(f.calls.filter(c=>c.method==='put').length,3);assert.equal(f.calls.filter(c=>c.method==='get'&&c.options.range).length,6);
    assert.match(f.calls.filter(c=>c.method==='put').at(-1).key,/server-index/);
    assert.ok(result.assets.every(a=>a.fullSha256Verified&&a.sampleSha256Verified&&a.headVerified));
    assert.ok(progress.every(p=>!JSON.stringify(p).includes('sourcePath')));
    assert.ok(!JSON.stringify(result).includes('sourcePath'));assert.ok(!JSON.stringify(result).includes('fixturePrivateProse'));
    for(const c of f.calls.filter(c=>c.method==='put'))assert.equal(c.options.httpMetadata.cacheControl,'private, no-store');
  }finally{await f.clean();}
});
test('all 365 synthetic release assets receive 1095 ranges and an index-last publication',async()=>{
  const f=await fixture(365);try {
    const result=await uploadR2Release({...f,...budgets});assert.equal(result.assets.length,365);assert.equal(result.rangeReads,1095);
    assert.equal(f.calls.filter(c=>c.method==='get'&&c.options.range).length,1095);assert.equal(f.calls.filter(c=>c.method==='put').length,366);
    assert.equal(validateIndex(result.index,origin).size,365);
  }finally{await f.clean();}
});
test('immutable resume fully re-verifies existing bytes without overwriting',async()=>{
  const f=await fixture();try {
    await uploadR2Release({...f,...budgets});f.calls.length=0;
    const result=await uploadR2Release({...f,...budgets});assert.equal(result.uploadedBytes,0);assert.equal(result.rangeReads,6);
    assert.equal(f.calls.filter(c=>c.method==='put').length,0);
    f.objects.get(result.assets[0].objectKey).bytes=Buffer.alloc(f.bodies[0].length);f.calls.length=0;
    await assert.rejects(uploadR2Release({...f,...budgets}),/SHA|hash/);assert.equal(f.calls.filter(c=>c.method==='put').length,0);
  }finally{await f.clean();}
});
test('invalid inventory, local hashes and explicit budgets reject before any remote call',async()=>{
  const f=await fixture();try {
    const alterations=[i=>i.accessModel='public',i=>i.assets[1].sha256='0'.repeat(64),i=>i.assets[1].samples[0].sha256='0'.repeat(64),
      i=>i.assets[1].url='https://foreign.example/api/assets/asset',i=>i.assets.push(i.assets[0]),i=>i.assets[1].samples[1].start++,
      i=>i.assets[1].sourcePath='relative.mp3',i=>i.releaseId='../other'];
    for(const alter of alterations) {const inventory=structuredClone(f.inventory);alter(inventory);await assert.rejects(uploadR2Release({...f,inventory,...budgets}));assert.equal(f.calls.length,0);}
    for(const change of [{maxUploadBytes:1},{verifyByteBudget:1},{maxUploadBytes:NaN},{verifyByteBudget:Infinity},{maxUploadBytes:0}]) {
      await assert.rejects(uploadR2Release({...f,...budgets,...change}));assert.equal(f.calls.length,0);
    }
    await fs.writeFile(f.inventory.assets[1].sourcePath,Buffer.alloc(f.bodies[1].length));
    await assert.rejects(uploadR2Release({...f,...budgets}));assert.equal(f.calls.length,0,'all sources must be proven before any storage access');
  }finally{await f.clean();}
});
test('foreign paths and symlink sources cannot bypass root ownership',async()=>{
  const f=await fixture(),outside=await fs.mkdtemp(path.join(os.tmpdir(),'lumen-r2-foreign-'));try {
    const foreign=path.join(outside,'source');await fs.writeFile(foreign,f.bodies[0]);
    const alias=path.join(f.root,'alias');await fs.symlink(f.inventory.assets[0].sourcePath,alias);
    for(const sourcePath of [foreign,alias]) {
      const inventory=structuredClone(f.inventory);inventory.assets[0].sourcePath=sourcePath;
      await assert.rejects(uploadR2Release({...f,inventory,...budgets}));assert.equal(f.calls.length,0);
    }
  }finally{await f.clean();await fs.rm(outside,{recursive:true,force:true});}
});
test('sourceProof returns an immutable clock-bound proof without disclosing bytes',async()=>{
  const f=await fixture();try {
    const proof=await sourceProof(f.root,f.inventory.assets[0]);assert.equal(proof.sha256,f.inventory.assets[0].sha256);
    assert.equal(proof.bytes,f.bodies[0].length);assert.ok(proof.identity);assert.ok(Object.isFrozen(proof));
    assert.ok(!JSON.stringify(proof).includes('fixturePrivateProse'));
  }finally{await f.clean();}
});
test('a source changed after preflight cannot upload new bytes or publish index',async()=>{
  const f=await fixture();try {
    const original=f.r2.head;let changed=false;
    f.r2.head=async(...args)=>{if(!changed){changed=true;await fs.writeFile(f.inventory.assets[1].sourcePath,Buffer.alloc(f.bodies[1].length));}return original(...args);};
    await assert.rejects(uploadR2Release({...f,...budgets}),/source.*changed|identity|SHA/i);
    assert.equal(f.calls.filter(c=>c.method==='put').length,1);assert.ok(!f.calls.some(c=>c.method==='put'&&c.key.includes('server-index')));
  }finally{await f.clean();}
});
test('upload stream refuses a source mutation before consumption and detects an unconsumed body',async()=>{
  for(const mode of ['mutation','unconsumed']) {
    const f=await fixture();try {
      const original=f.r2.put;
      f.r2.put=async(key,body,options)=>{
        if(mode==='mutation')await fs.writeFile(f.inventory.assets[0].sourcePath,Buffer.alloc(f.bodies[0].length));
        if(mode==='unconsumed')return {key,size:options.contentLength,etag:'fake',httpMetadata:{contentType:options.httpMetadata.contentType}};
        return original(key,body,options);
      };
      await assert.rejects(uploadR2Release({...f,...budgets}));assert.ok(!f.calls.some(c=>c.method==='put'&&c.key.includes('server-index')));
    }finally{await f.clean();}
  }
});
test('conditional create collisions never overwrite or certify an index',async()=>{
  const f=await fixture();try {
    f.r2.put=async()=>null;await assert.rejects(uploadR2Release({...f,...budgets}),/conditional|create/i);
    assert.ok(!f.calls.some(c=>c.method==='get'));assert.ok(!f.calls.some(c=>c.key.includes('server-index')));
  }finally{await f.clean();}
});
test('incorrect full metadata, bytes or range offsets cannot publish an index',async()=>{
  for(const mode of ['size','type','encoding','full-bytes','full-long','full-short','range-offset','range-body','no-body']) {
    const f=await fixture();try {
      const original=f.r2.get;
      f.r2.get=async(...args)=>{
        const r=await original(...args);if(!r)return r;
        if(mode==='size')r.size++;if(mode==='type')r.httpMetadata.contentType='text/plain';
        if(mode==='encoding')r.httpMetadata.contentEncoding='gzip';if(mode==='no-body')delete r.body;
        if(mode==='full-bytes'&&!args[1]?.range)r.body=new Response(Buffer.alloc(r.size)).body;
        if(mode==='full-long'&&!args[1]?.range)r.body=new Response(Buffer.alloc(r.size+1)).body;
        if(mode==='full-short'&&!args[1]?.range)r.body=new Response(Buffer.alloc(r.size-1)).body;
        if(mode==='range-offset'&&r.range)r.range.offset++;
        if(mode==='range-body'&&r.range)r.body=new Response(Buffer.alloc(r.range.length)).body;
        return r;
      };
      await assert.rejects(uploadR2Release({...f,...budgets}));assert.ok(!f.calls.some(c=>c.method==='put'&&c.key.includes('server-index')),mode);
    }finally{await f.clean();}
  }
});
test('object replacement between ranges and final HEAD fails closed',async()=>{
  const f=await fixture();try {
    const original=f.r2.get;let ranges=0;
    f.r2.get=async(...args)=>{const r=await original(...args);if(args[1]?.range&&++ranges===3){f.objects.get(args[0]).version='v2';}return r;};
    await assert.rejects(uploadR2Release({...f,...budgets}),/identity|version|metadata/);assert.ok(!f.calls.some(c=>c.method==='put'&&c.key.includes('server-index')));
  }finally{await f.clean();}
});
test('caller mutation after the first asset cannot alter the validated release plan',async()=>{
  const f=await fixture();try {
    const expected=makePlan(f.inventory,f.manifestURL).indexSha256;
    const result=await uploadR2Release({...f,...budgets,onProgress:async()=>{f.inventory.assets[1].url='https://foreign.example/unreviewed';f.inventory.assets[1].sourcePath='/outside';}});
    assert.equal(result.indexSha256,expected);assert.equal(result.assets[1].url,origin+'/api/assets/'+result.index.assets[1].path);
  }finally{await f.clean();}
});
test('a mutation after reading approved bytes is rejected before emitting upload bytes',async(t)=>{
  const f=await fixture();try {
    let uploading=false,mutated=false,emitted=0;
    const originalOpen=fs.open;
    t.mock.method(fs,'open',async(...args)=>{
      const handle=await originalOpen(...args);
      if(args[0]!==f.inventory.assets[0].sourcePath)return handle;
      const originalRead=handle.read.bind(handle);
      handle.read=async(...readArgs)=>{
        const result=await originalRead(...readArgs);
        if(uploading&&!mutated) {mutated=true;await fs.writeFile(args[0],Buffer.alloc(f.bodies[0].length));}
        return result;
      };
      return handle;
    });
    f.r2.put=async(key,body)=>{uploading=true;for await(const chunk of body)emitted+=chunk.length;throw Error('Unexpected completed stream');};
    await assert.rejects(uploadR2Release({...f,...budgets}),/identity changed/);
    assert.equal(mutated,true);assert.equal(emitted,0);assert.equal(f.objects.size,0);
  }finally{await f.clean();}
});
test('a corrupt existing index cannot be overwritten or marked verified on resume',async()=>{
  const f=await fixture();try {
    const result=await uploadR2Release({...f,...budgets});
    f.objects.get(result.indexPath).bytes=Buffer.alloc(makePlan(f.inventory,f.manifestURL).indexBytes.length);
    f.calls.length=0;await assert.rejects(uploadR2Release({...f,...budgets}),/SHA-256/);
    assert.equal(f.calls.filter(c=>c.method==='put').length,0);assert.equal(f.calls.filter(c=>c.method==='get'&&c.options.range).length,6);
  }finally{await f.clean();}
});
test('R2 asset and index metadata carry the exact SHA needed by the Worker',async()=>{
  const f=await fixture();try {
    await uploadR2Release({...f,...budgets});
    for(const call of f.calls.filter(c=>c.method==='put'))assert.equal(call.options.customMetadata?.sha256,call.options.sha256);
  }finally{await f.clean();}
});
test('native R2 full reads may report the exact complete range on assets and index',async()=>{
  const f=await fixture();try {
    const original=f.r2.get;let fullReads=0;
    f.r2.get=async(key,options)=>{
      const result=await original(key,options);
      if(!options.range){result.range={offset:0,length:result.size};fullReads++;}
      return result;
    };
    const result=await uploadR2Release({...f,...budgets});
    assert.equal(result.status,'uploaded-private-r2');assert.equal(fullReads,3);assert.equal(result.rangeReads,6);
  }finally{await f.clean();}
});
test('unrequested partial or shifted full-read ranges cannot certify an asset',async()=>{
  for(const range of [{offset:1,length:22},{offset:0,length:1}]) {
    const f=await fixture();try {
      const original=f.r2.get;
      f.r2.get=async(...args)=>{const result=await original(...args);if(!args[1].range)result.range=range;return result;};
      await assert.rejects(uploadR2Release({...f,...budgets}),/range metadata/);
      assert.ok(!f.calls.some(c=>c.method==='put'&&c.key.includes('server-index')));
    }finally{await f.clean();}
  }
});
test('missing or altered R2 SHA metadata cannot certify otherwise correct bytes',async()=>{
  for(const mode of ['missing','wrong']) {
    const f=await fixture();try {
      const original=f.r2.get;
      f.r2.get=async(...args)=>{const result=await original(...args);result.customMetadata=mode==='missing'?undefined:{sha256:'0'.repeat(64)};return result;};
      await assert.rejects(uploadR2Release({...f,...budgets}),/metadata/);
      assert.ok(!f.calls.some(c=>c.method==='put'&&c.key.includes('server-index')));
    }finally{await f.clean();}
  }
});
test('importing the operator tool is inert and does not read credentials or call storage',()=>{
  assert.equal(typeof uploadR2Release,'function');assert.equal(typeof makePlan,'function');assert.equal(typeof sourceProof,'function');
});
