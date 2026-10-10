'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {uploadPrivateRelease,verifyPrivateAPI}=require('../scripts/upload_private_release.cjs');
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const origin='https://lumen.example';
async function setup() {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'lumen-private-upload-'));
  const bytes=Buffer.from('{"bookId":"lumen","privateText":"fixture"}\n');
  const digest=hash(bytes),logical=`tracks/chapter-000/book-manifest.${digest}.json`,filename=path.join(root,'book.json');
  await fs.writeFile(filename,bytes);
  const sample={start:0,end:bytes.length-1,sha256:digest};
  const inventory={schemaVersion:1,releaseId:'fixture-release',accessModel:'private',appOrigin:origin,mediaOrigins:[origin],assets:[{url:origin+'/api/assets/'+logical,sha256:digest,bytes:bytes.length,contentType:'application/json',immutable:true,sourcePath:filename,samples:[sample,sample,sample]}]};
  const objects=new Map(),calls=[];
  const blob={
    async put(name,stream,options) {
      calls.push({method:'put',name,options});const parts=[];
      if(Buffer.isBuffer(stream)) parts.push(stream);
      else for await(const chunk of stream) parts.push(Buffer.from(chunk));
      const body=Buffer.concat(parts);objects.set(name,body);
      return {pathname:name,url:'https://fixture.private.blob.vercel-storage.com/'+name};
    },
    async get(name,options) {
      calls.push({method:'get',name,options});const body=objects.get(name);
      if(!body) return null;
      const match=/^bytes=(\d+)-(\d+)$/.exec(options.headers?.Range||'');
      const selected=match?body.subarray(Number(match[1]),Number(match[2])+1):body;
      const headers=new Headers({'content-length':String(selected.length)});
      if(match) headers.set('content-range',`bytes ${match[1]}-${match[2]}/${body.length}`);
      return {statusCode:200,headers,stream:new Response(selected).body};
    }
  };
  return {root,bytes,inventory,blob,calls,objects,manifestURL:inventory.assets[0].url};
}
test('private uploader verifies exact source, private stream, complete remote SHA and three ranges before index',async()=>{
  const f=await setup();try {
    const result=await uploadPrivateRelease({...f,maxUploadBytes:1000,verifyByteBudget:5000});
    assert.equal(result.assets.length,1);assert.equal(result.assets[0].fullSha256Verified,true);assert.equal(result.assets[0].sampleSha256Verified,true);
    assert.ok(result.indexPath.endsWith(result.indexSha256+'.json'));assert.ok(!JSON.stringify(result.index).includes('sourcePath'));
    for(const call of f.calls) assert.equal(call.options.access,'private');
    const uploads=f.calls.filter(row=>row.method==='put');assert.equal(uploads.length,2);assert.equal(uploads[0].options.allowOverwrite,false);
    assert.ok(uploads[0].options.addRandomSuffix===false);assert.ok(uploads.at(-1).name.includes('server-index'));
  }finally{await fs.rm(f.root,{recursive:true,force:true});}
});
test('resuming a verified immutable upload reuses exact private bytes without overwriting',async()=>{
  const f=await setup();try {
    await uploadPrivateRelease({...f,maxUploadBytes:1000,verifyByteBudget:5000});
    f.calls.length=0;
    const result=await uploadPrivateRelease({...f,maxUploadBytes:1000,verifyByteBudget:5000});
    assert.equal(result.status,'uploaded-private');assert.equal(f.calls.filter(row=>row.method==='put').length,0);
    const key=Array.from(f.objects.keys()).find(value=>value.includes('/assets/'));f.objects.set(key,Buffer.alloc(f.bytes.length));
    f.calls.length=0;await assert.rejects(()=>uploadPrivateRelease({...f,maxUploadBytes:1000,verifyByteBudget:5000}));assert.equal(f.calls.filter(row=>row.method==='put').length,0);
  }finally{await fs.rm(f.root,{recursive:true,force:true});}
});
test('invalid inventory, foreign files, bad local SHA and undersized budgets fail before uploading',async()=>{
  const f=await setup();try {
    const outside=await fs.mkdtemp(path.join(os.tmpdir(),'lumen-private-outside-'));
    await fs.writeFile(path.join(outside,'other.json'),f.bytes);
    for(const alter of [i=>i.accessModel='public',i=>i.assets[0].sha256='0'.repeat(64),i=>i.assets[0].sourcePath=path.join(outside,'other.json'),i=>i.assets[0].samples[0].sha256='0'.repeat(64),i=>i.assets[0].url='https://evil.example/api/assets/x',i=>i.assets.push(i.assets[0])]) {
      const inventory=JSON.parse(JSON.stringify(f.inventory));alter(inventory);
      await assert.rejects(()=>uploadPrivateRelease({...f,inventory,maxUploadBytes:1000,verifyByteBudget:5000}));assert.equal(f.calls.length,0);
    }
    for(const budget of [{maxUploadBytes:1,verifyByteBudget:5000},{maxUploadBytes:1000,verifyByteBudget:1}]) {await assert.rejects(()=>uploadPrivateRelease({...f,...budget}));assert.equal(f.calls.length,0);}
    await fs.rm(outside,{recursive:true,force:true});
  }finally{await fs.rm(f.root,{recursive:true,force:true});}
});
test('source symlinks and output-directory symlinks cannot bypass private ownership',async()=>{
  const f=await setup();try {
    const target=f.inventory.assets[0].sourcePath,alias=path.join(f.root,'alias.json');await fs.symlink(target,alias);
    const inventory=JSON.parse(JSON.stringify(f.inventory));inventory.assets[0].sourcePath=alias;
    await assert.rejects(()=>uploadPrivateRelease({...f,inventory,maxUploadBytes:1000,verifyByteBudget:5000}));assert.equal(f.calls.length,0);
    const publicDirectory=path.join(f.root,'web','dist'),privateRoot=path.join(f.root,'Audiobook','author-audit');
    await fs.mkdir(publicDirectory,{recursive:true});await fs.mkdir(privateRoot,{recursive:true});await fs.symlink(publicDirectory,path.join(privateRoot,'unsafe'));
    const inventoryPath=path.join(f.root,'inventory.json');await fs.writeFile(inventoryPath,JSON.stringify(f.inventory));
    const {execFile}=require('node:child_process'),{promisify}=require('node:util');
    await assert.rejects(()=>promisify(execFile)(process.execPath,[path.resolve(__dirname,'../scripts/upload_private_release.cjs'),'--root',f.root,'--inventory',inventoryPath,'--out',path.join(privateRoot,'unsafe'),'--manifest-url',f.manifestURL,'--max-upload-bytes','1000','--verify-byte-budget','5000'],{env:{PATH:process.env.PATH}}));
    assert.deepEqual(await fs.readdir(publicDirectory),[]);
  }finally{await fs.rm(f.root,{recursive:true,force:true});}
});
test('public return URL, wrong remote bytes and ignored ranges never publish a release index',async()=>{
  for(const mode of ['public','bytes','range']) {
    const f=await setup();try {
      const originalPut=f.blob.put,originalGet=f.blob.get;
      if(mode==='public') f.blob.put=async(...args)=>({...await originalPut(...args),url:'https://fixture.public.blob.vercel-storage.com/wrong'});
      if(mode==='bytes') f.blob.get=async(...args)=>{const result=await originalGet(...args);return {...result,stream:new Response(Buffer.alloc(f.bytes.length)).body};};
      if(mode==='range') f.blob.get=async(...args)=>{const result=await originalGet(...args);result.headers.delete('content-range');return result;};
      await assert.rejects(()=>uploadPrivateRelease({...f,maxUploadBytes:1000,verifyByteBudget:5000}));
      assert.ok(!f.calls.some(row=>row.method==='put'&&row.name.includes('server-index')));
    }finally{await fs.rm(f.root,{recursive:true,force:true});}
  }
});
test('remote verifier checks private authorization, exact ranges and 416 without public CORS grants',async()=>{
  const f=await setup();try {
    const requests=[];
    const fetch=async(url,options={})=>{
      requests.push({url,options});const authorized=options.headers?.Cookie==='fixture-cookie';
      const h={'Cache-Control':'private, no-store','Cross-Origin-Resource-Policy':'same-origin'};
      if(!authorized) return new Response('{"error":"An invitation is required."}',{status:401,headers:h});
      if(options.headers.Origin==='https://unauthorized.invalid') return new Response(null,{status:403,headers:h});
      h['Content-Length']=String(f.bytes.length);h['Content-Type']='application/json';h['Accept-Ranges']='bytes';
      h['ETag']='"sha256-'+hash(f.bytes)+'"';
      if(url===origin+'/api/book') return new Response(f.bytes,{status:200,headers:h});
      const range=options.headers?.Range;
      if(range===`bytes=${f.bytes.length}-`) return new Response(null,{status:416,headers:{...h,'Content-Range':`bytes */${f.bytes.length}`}});
      if(range) {const m=/bytes=(\d+)-(\d+)/.exec(range);h['Content-Range']=`bytes ${m[1]}-${m[2]}/${f.bytes.length}`;return new Response(f.bytes,{status:206,headers:h});}
      return new Response(null,{status:200,headers:h});
    };
    const report=await verifyPrivateAPI({inventory:f.inventory,cookie:'fixture-cookie',fetch,byteBudget:1000});
    assert.equal(report.accessPolicyVerified,true);assert.equal(report.assets[0].sampleSha256Verified,true);
    assert.ok(requests.every(row=>!row.url.includes('fixture-cookie')));
    assert.ok(!JSON.stringify(report).includes('fixture-cookie'));assert.ok(!JSON.stringify(report).includes('sourcePath'));
    assert.ok(!JSON.stringify(report).includes('privateText'));
  }finally{await fs.rm(f.root,{recursive:true,force:true});}
});
test('API verification rejects a wrong authorized book manifest before certifying the release',async()=>{
  const f=await setup();try {
    let authorizedBookRead=false;
    const fetch=async(url,options)=>{
      const h={'Cache-Control':'private, no-store','Cross-Origin-Resource-Policy':'same-origin'};
      if(!options.headers.Cookie) return new Response(null,{status:401,headers:h});
      if(options.headers.Origin==='https://unauthorized.invalid') return new Response(null,{status:403,headers:h});
      if(url===origin+'/api/book') {
        authorizedBookRead=true;
        return new Response(Buffer.alloc(f.bytes.length),{status:200,headers:{...h,'Content-Type':'application/json','Content-Length':String(f.bytes.length),'ETag':'"sha256-'+hash(f.bytes)+'"'}});
      }
      throw Error('Asset verification must wait for book identity');
    };
    await assert.rejects(verifyPrivateAPI({inventory:f.inventory,cookie:'fixture-cookie',fetch,byteBudget:1000}),/book.*hash|manifest.*SHA/i);
    assert.equal(authorizedBookRead,true);
  }finally{await fs.rm(f.root,{recursive:true,force:true});}
});
test('API verifier rejects public responses, wrong ranges, loose caching, cross-site access and redirects',async()=>{
  const f=await setup();try {
    for(const mode of ['public','range','cache','cross-site','redirect']) {
      const fetch=async(url,options={})=>{
        const auth=options.headers?.Cookie==='fixture-cookie',cross=options.headers?.Origin==='https://unauthorized.invalid';
        const headers={'Cache-Control':mode==='cache'?'public, max-age=3600':'private, no-store','Cross-Origin-Resource-Policy':'same-origin'};
        if(!auth) return new Response(null,{status:mode==='public'?200:401,headers});
        if(cross) return new Response(null,{status:mode==='cross-site'?200:403,headers});
        if(mode==='redirect') return new Response(null,{status:302,headers:{...headers,Location:'https://evil.example'}});
        if(options.method==='HEAD') return new Response(null,{status:200,headers:{...headers,'Content-Length':String(f.bytes.length),'Content-Type':'application/json','Accept-Ranges':'bytes'}});
        return new Response(f.bytes,{status:206,headers:{...headers,'Content-Length':String(f.bytes.length),'Content-Range':mode==='range'?'bytes 1-2/999':`bytes 0-${f.bytes.length-1}/${f.bytes.length}`}});
      };
      await assert.rejects(()=>verifyPrivateAPI({inventory:f.inventory,cookie:'fixture-cookie',fetch,byteBudget:1000}));
    }
  }finally{await fs.rm(f.root,{recursive:true,force:true});}
});
