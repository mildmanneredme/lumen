#!/usr/bin/env node
'use strict';
// Operator engine only: importing this module performs no IO or account access.
// The injected adapter uses R2Object/R2ObjectBody metadata. PUT bodies are Node
// streams (or Buffers for the small index); a Worker adapter converts to Web
// streams. It must enforce If-None-Match:* atomically, never emulate it with HEAD.
// https://developers.cloudflare.com/r2/api/workers/workers-api-reference/
const fs=require('node:fs/promises'),nativeFS=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {Readable}=require('node:stream');
const {buildServerIndex,sha256}=require('../server/private-access.cjs');
const HASH=/^[a-f0-9]{64}$/;
function ensure(condition,message='R2 release validation failed') {if(!condition)throw Error(message);}
function positive(value) {return Number.isSafeInteger(value)&&value>0;}
function owned(root,filename) {const relative=path.relative(root,filename);return relative!==''&&relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative);}
function samplesFor(asset) {
  ensure(positive(asset.bytes)&&HASH.test(asset.sha256||''),'Invalid source byte identity');
  const width=Math.min(1024,asset.bytes),starts=[0,Math.floor((asset.bytes-width)/2),asset.bytes-width];
  ensure(Array.isArray(asset.samples)&&asset.samples.length===3,'Three exact source samples are required');
  return asset.samples.map((sample,i)=>{
    ensure(sample&&sample.start===starts[i]&&sample.end===starts[i]+width-1&&HASH.test(sample.sha256||''),'Source sample bounds differ');
    return Object.freeze({start:sample.start,end:sample.end,sha256:sample.sha256});
  });
}
function makePlan(inventory,manifestURL) {
  ensure(inventory&&Array.isArray(inventory.assets)&&inventory.assets.length>0);
  const snapshot={schemaVersion:inventory.schemaVersion,releaseId:inventory.releaseId,accessModel:inventory.accessModel,
    appOrigin:inventory.appOrigin,mediaOrigins:Array.isArray(inventory.mediaOrigins)?[...inventory.mediaOrigins]:null};
  const prefix=String(snapshot.appOrigin)+'/api/assets/';
  snapshot.assets=inventory.assets.map(asset=>{
    ensure(asset&&typeof asset.sourcePath==='string'&&path.isAbsolute(asset.sourcePath),'Source path must be absolute');
    return Object.freeze({url:asset.url,sha256:asset.sha256,bytes:asset.bytes,contentType:asset.contentType,immutable:asset.immutable,
      sourcePath:asset.sourcePath,samples:Object.freeze(samplesFor(asset))});
  });
  const receipts=snapshot.assets.map(asset=>{
    const objectKey=`lumen/releases/${snapshot.releaseId}/assets/${String(asset.url).slice(prefix.length)}`;
    // blobPath is the existing server-index field, independent of provider.
    return Object.freeze({url:asset.url,objectKey,blobPath:objectKey,sha256:asset.sha256,bytes:asset.bytes,contentType:asset.contentType,access:'private'});
  });
  const index=buildServerIndex(snapshot,receipts,manifestURL),indexBytes=Buffer.from(JSON.stringify(index)+'\n'),indexSha256=sha256(indexBytes);
  const uploadBytes=snapshot.assets.reduce((sum,asset)=>sum+asset.bytes,indexBytes.length);
  const verificationBytes=snapshot.assets.reduce((sum,asset)=>sum+asset.bytes+asset.samples.reduce((total,sample)=>total+sample.end-sample.start+1,0),indexBytes.length);
  ensure(positive(uploadBytes)&&positive(verificationBytes),'Release byte totals are unsafe');
  return {assets:Object.freeze(snapshot.assets),receipts:Object.freeze(receipts),index,indexBytes,indexSha256,
    indexPath:`lumen/releases/${snapshot.releaseId}/server-index.${indexSha256}.json`,uploadBytes,verificationBytes};
}
function identity(info) {
  return Object.freeze(Object.fromEntries(['dev','ino','size','mtimeNs','ctimeNs'].map(key=>[key,String(info[key])])));
}
function sameIdentity(a,b) {return ['dev','ino','size','mtimeNs','ctimeNs'].every(key=>a[key]===b[key]);}
async function openOwned(root,filename) {
  const real=await fs.realpath(filename);ensure(owned(root,real),'Upload source is outside the owned root');
  ensure(!(await fs.lstat(filename)).isSymbolicLink(),'Upload source is a symbolic link');
  const handle=await fs.open(filename,nativeFS.constants.O_RDONLY|nativeFS.constants.O_NOFOLLOW|nativeFS.constants.O_NONBLOCK);
  try {
    const info=await handle.stat({bigint:true});ensure(info.isFile(),'Upload source is not a regular file');
    const clock=identity(info);await checkSource(root,filename,handle,clock);return {handle,clock};
  }catch(error){await handle.close();throw error;}
}
async function checkSource(root,filename,handle,clock) {
  const real=await fs.realpath(filename);ensure(owned(root,real),'Upload source is outside the owned root');
  const current=await fs.lstat(filename,{bigint:true}),held=await handle.stat({bigint:true});
  ensure(current.isFile()&&held.isFile()&&sameIdentity(identity(current),clock)&&sameIdentity(identity(held),clock),'Upload source identity changed');
}
async function sourceProof(root,asset) {
  root=await fs.realpath(root);const samples=samplesFor(asset),{handle,clock}=await openOwned(root,asset.sourcePath);
  try {
    ensure(clock.size===String(asset.bytes),'Upload source size differs');
    for(const sample of samples) {
      const bytes=Buffer.alloc(sample.end-sample.start+1),result=await handle.read(bytes,0,bytes.length,sample.start);
      ensure(result.bytesRead===bytes.length&&sha256(bytes)===sample.sha256,'Upload source sample SHA-256 differs');
      await checkSource(root,asset.sourcePath,handle,clock);
    }
    const hash=crypto.createHash('sha256');let offset=0;
    while(offset<asset.bytes) {
      const chunk=Buffer.alloc(Math.min(64*1024,asset.bytes-offset)),result=await handle.read(chunk,0,chunk.length,offset);
      ensure(result.bytesRead>0,'Upload source size differs');await checkSource(root,asset.sourcePath,handle,clock);
      hash.update(chunk.subarray(0,result.bytesRead));offset+=result.bytesRead;
    }
    ensure((await handle.read(Buffer.alloc(1),0,1,offset)).bytesRead===0,'Upload source grew');
    ensure(hash.digest('hex')===asset.sha256,'Upload source SHA-256 differs');
    await checkSource(root,asset.sourcePath,handle,clock);
    return Object.freeze({sha256:asset.sha256,bytes:asset.bytes,identity:clock});
  }finally{await handle.close();}
}
async function sourceBody(root,asset,proof) {
  const {handle,clock}=await openOwned(root,asset.sourcePath);
  try {ensure(sameIdentity(clock,proof.identity),'Upload source identity changed after preflight');}
  catch(error){await handle.close();throw error;}
  let complete=false;
  const body=Readable.from((async function*(){
    const hash=crypto.createHash('sha256');let offset=0;
    while(offset<asset.bytes) {
      await checkSource(root,asset.sourcePath,handle,clock);
      const chunk=Buffer.alloc(Math.min(64*1024,asset.bytes-offset)),result=await handle.read(chunk,0,chunk.length,offset);
      ensure(result.bytesRead>0,'Upload source size differs');await checkSource(root,asset.sourcePath,handle,clock);
      const bytes=chunk.subarray(0,result.bytesRead);hash.update(bytes);offset+=bytes.length;yield bytes;
    }
    ensure((await handle.read(Buffer.alloc(1),0,1,offset)).bytesRead===0,'Upload source grew');
    ensure(hash.digest('hex')===asset.sha256,'Upload source SHA-256 differs');
    await checkSource(root,asset.sourcePath,handle,clock);complete=true;
  })());
  // A failed adapter may abandon the stream; retain a listener while destroying.
  body.on('error',()=>{});
  return {body,finished:()=>complete,close:async()=>{body.destroy();await handle.close();}};
}
function remoteIdentity(result,key,expected,pinned) {
  const metadata=result?.httpMetadata;
  ensure(result&&result.key===key&&result.size===expected.bytes&&typeof result.etag==='string'&&result.etag.length>0&&
    metadata?.contentType===expected.contentType&&(!metadata.contentEncoding||metadata.contentEncoding==='identity')&&
    result.customMetadata?.sha256===expected.sha256,'R2 object metadata differs');
  const clock={etag:result.etag,version:result.version??null};
  ensure(clock.version===null||typeof clock.version==='string'&&clock.version.length>0,'R2 object version differs');
  if(pinned)ensure(clock.etag===pinned.etag&&clock.version===pinned.version,'R2 object identity changed');
  return Object.freeze(clock);
}
async function remoteRead(r2,key,expected,pinned,range,ledger) {
  const result=await r2.get(key,{onlyIf:{etagMatches:pinned.etag},...(range?{range:{offset:range.start,length:range.end-range.start+1}}:{})});
  try {
    remoteIdentity(result,key,expected,pinned);
    const size=range?range.end-range.start+1:expected.bytes;
    ensure(result.body&&typeof result.body.getReader==='function','R2 object body is missing');
    // Native R2 also supplies offset0/lengthsize for an un-ranged full GET.
    ensure(range?result.range?.offset===range.start&&result.range?.length===size:
      !result.range||(result.range.offset===0&&result.range.length===size),'R2 range metadata differs');
    const reader=result.body.getReader(),hash=crypto.createHash('sha256');let bytes=0;
    try {
      while(true) {
        const part=await reader.read();if(part.done)break;
        ensure(part.value instanceof Uint8Array,'R2 body contains invalid bytes');
        bytes+=part.value.byteLength;ledger.read+=part.value.byteLength;
        ensure(bytes<=size&&ledger.read<=ledger.limit,'R2 response exceeds byte budget');hash.update(part.value);
      }
      ensure(bytes===size&&hash.digest('hex')===(range?range.sha256:expected.sha256),'R2 remote SHA-256 differs');
    }catch(error){await reader.cancel().catch(()=>{});throw error;}
    finally{reader.releaseLock();}
    if(range)ledger.ranges++;
  }catch(error){if(result?.body&&!result.body.locked)await result.body.cancel().catch(()=>{});throw error;}
}
function putOptions(expected) {
  return {onlyIf:new Headers({'If-None-Match':'*'}),httpMetadata:{contentType:expected.contentType,cacheControl:'private, no-store'},
    customMetadata:{sha256:expected.sha256},sha256:expected.sha256,contentLength:expected.bytes};
}
async function uploadR2Release({root,inventory,manifestURL,r2,maxUploadBytes,verifyByteBudget,onProgress=async()=>{}}) {
  ensure(positive(maxUploadBytes)&&positive(verifyByteBudget),'Explicit byte budgets are required');
  ensure(r2&&['head','get','put'].every(method=>typeof r2[method]==='function'),'R2 get, head and conditional put adapter is required');
  ensure(typeof onProgress==='function');root=await fs.realpath(root);
  const plan=makePlan(inventory,manifestURL);
  ensure(plan.uploadBytes<=maxUploadBytes,'Upload byte budget is insufficient');
  ensure(plan.verificationBytes<=verifyByteBudget,'Verification byte budget is insufficient');
  const proofs=[];
  // Prove every local source before any remote reads or mutations.
  for(const asset of plan.assets)proofs.push(await sourceProof(root,asset));
  const completed=[],ledger={limit:verifyByteBudget,read:0,ranges:0};let uploadedBytes=0;
  for(let i=0;i<plan.assets.length;i++) {
    const asset=plan.assets[i],receipt=plan.receipts[i],key=receipt.objectKey;
    let metadata=await r2.head(key);
    if(metadata===null) {
      const source=await sourceBody(root,asset,proofs[i]);
      try {
        metadata=await r2.put(key,source.body,putOptions(asset));
        ensure(metadata!==null,'R2 conditional create failed');ensure(source.finished(),'R2 upload did not consume the complete source');
      }finally{await source.close();}
      uploadedBytes+=asset.bytes;
    }
    const pinned=remoteIdentity(metadata,key,asset);
    await remoteRead(r2,key,asset,pinned,undefined,ledger);
    for(const sample of asset.samples)await remoteRead(r2,key,asset,pinned,sample,ledger);
    remoteIdentity(await r2.head(key),key,asset,pinned);
    completed.push({...receipt,...pinned,fullSha256Verified:true,sampleSha256Verified:true,headVerified:true});
    await onProgress({schemaVersion:1,storageProvider:'cloudflare-r2',releaseId:plan.index.releaseId,status:'uploading',assets:completed.map(row=>({...row}))});
  }
  const expectedIndex={bytes:plan.indexBytes.length,sha256:plan.indexSha256,contentType:'application/json'};
  let metadata=await r2.head(plan.indexPath);
  if(metadata===null) {
    metadata=await r2.put(plan.indexPath,plan.indexBytes,putOptions(expectedIndex));
    ensure(metadata!==null,'R2 conditional index create failed');uploadedBytes+=plan.indexBytes.length;
  }
  const pinned=remoteIdentity(metadata,plan.indexPath,expectedIndex);
  await remoteRead(r2,plan.indexPath,expectedIndex,pinned,undefined,ledger);
  remoteIdentity(await r2.head(plan.indexPath),plan.indexPath,expectedIndex,pinned);
  ensure(ledger.read===plan.verificationBytes,'R2 verification byte count differs');
  return {schemaVersion:1,storageProvider:'cloudflare-r2',releaseId:plan.index.releaseId,status:'uploaded-private-r2',assets:completed,
    index:plan.index,indexPath:plan.indexPath,indexSha256:plan.indexSha256,uploadBytes:plan.uploadBytes,uploadedBytes,
    verificationBytes:ledger.read,rangeReads:ledger.ranges,accessPolicyVerified:false};
}
module.exports={makePlan,sourceProof,uploadR2Release};
if(require.main===module) {
  console.error('R2 upload requires an explicitly configured adapter and byte budgets. No account operation was attempted.');
  process.exitCode=1;
}
