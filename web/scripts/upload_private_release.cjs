#!/usr/bin/env node
'use strict';
// Operator only. Never imported by a Vercel Function or the browser shell.
const fs=require('node:fs/promises'),nativeFS=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {Transform}=require('node:stream');
const {buildServerIndex,sha256,boundedBytes}=require('../server/private-access.cjs');
function ensure(condition,message='Private release validation failed') {if(!condition) throw Error(message);}
function safeBudget(value) {return Number.isSafeInteger(value)&&value>0;}
function owned(root,filename) {const relative=path.relative(root,filename);return relative!==''&&!relative.startsWith('..'+path.sep)&&relative!=='..'&&!path.isAbsolute(relative);}
function makePlan(inventory,manifestURL) {
  ensure(inventory&&Array.isArray(inventory.assets)&&inventory.assets.length>0);
  const receipts=inventory.assets.map(asset=>({url:asset.url,blobPath:`lumen/releases/${inventory.releaseId}/assets/${String(asset.url).slice(String(inventory.appOrigin).length+'/api/assets/'.length)}`,sha256:asset.sha256,bytes:asset.bytes,contentType:asset.contentType,access:'private'}));
  const index=buildServerIndex(inventory,receipts,manifestURL);
  for(const asset of inventory.assets) {
    ensure(typeof asset.sourcePath==='string'&&path.isAbsolute(asset.sourcePath));
    const width=Math.min(1024,asset.bytes),starts=[0,Math.floor((asset.bytes-width)/2),asset.bytes-width];
    ensure(Array.isArray(asset.samples)&&asset.samples.length===3);
    asset.samples.forEach((sample,i)=>ensure(sample&&sample.start===starts[i]&&sample.end===starts[i]+width-1&&/^[a-f0-9]{64}$/.test(sample.sha256||'')));
  }
  const indexBytes=Buffer.from(JSON.stringify(index)+'\n'),indexSha256=sha256(indexBytes);
  return {receipts,index,indexBytes,indexSha256,indexPath:`lumen/releases/${inventory.releaseId}/server-index.${indexSha256}.json`};
}
async function openOwned(root,filename) {
  const real=await fs.realpath(filename);ensure(owned(root,real),'Upload source is outside the owned root');
  const handle=await fs.open(filename,nativeFS.constants.O_RDONLY|nativeFS.constants.O_NOFOLLOW);
  try {
    const info=await handle.stat(),current=await fs.stat(await fs.realpath(filename));
    ensure(info.isFile()&&info.dev===current.dev&&info.ino===current.ino&&owned(root,await fs.realpath(filename)),'Upload source identity differs');
    return handle;
  }catch(error){await handle.close();throw error;}
}
async function sourceProof(root,asset) {
  const handle=await openOwned(root,asset.sourcePath);
  try {
    const info=await handle.stat();ensure(info.size===asset.bytes,'Upload source size differs');
    for(const sample of asset.samples) {
      const bytes=Buffer.alloc(sample.end-sample.start+1),result=await handle.read(bytes,0,bytes.length,sample.start);
      ensure(result.bytesRead===bytes.length&&sha256(bytes)===sample.sha256,'Upload source sample differs');
    }
    const hash=crypto.createHash('sha256');
    for await(const chunk of handle.createReadStream({start:0,autoClose:false})) hash.update(chunk);
    ensure(hash.digest('hex')===asset.sha256,'Upload source SHA-256 differs');
    const after=await handle.stat();ensure(after.size===info.size&&after.mtimeMs===info.mtimeMs,'Upload source changed during verification');
  }finally{await handle.close();}
}
async function boundSourceStream(root,asset) {
  const handle=await openOwned(root,asset.sourcePath),hash=crypto.createHash('sha256');let bytes=0;
  const check=new Transform({transform(chunk,encoding,done){bytes+=chunk.length;hash.update(chunk);if(bytes>asset.bytes)done(Error('Upload source size differs'));else done(null,chunk);},flush(done){done(bytes===asset.bytes&&hash.digest('hex')===asset.sha256?null:Error('Upload source SHA-256 differs'));}});
  const source=handle.createReadStream({start:0,autoClose:true});source.on('error',error=>check.destroy(error));
  check.on('close',()=>source.destroy());source.pipe(check);return check;
}
async function blobRead(blob,blobPath,expected,range,provided) {
  const result=provided || await blob.get(blobPath,{access:'private',headers:{'Accept-Encoding':'identity',...(range?{Range:`bytes=${range.start}-${range.end}`}:{})}});
  const length=range?range.end-range.start+1:expected.bytes;
  const valid=result&&result.statusCode===200&&result.stream&&result.headers.get('content-length')===String(length)&&
    (!result.headers.get('content-encoding') || result.headers.get('content-encoding') === 'identity')&&
    (range?result.headers.get('content-range')===`bytes ${range.start}-${range.end}/${expected.bytes}`:!result.headers.get('content-range'));
  if(!valid){if(result?.stream)await result.stream.cancel();throw Error('Private Blob response metadata differs');}
  if(range) {const bytes=await boundedBytes(result.stream,length);ensure(bytes.length===length&&sha256(bytes)===range.sha256,'Private Blob range SHA-256 differs');return length;}
  const reader=result.stream.getReader(),hash=crypto.createHash('sha256');let bytes=0;
  try {
    while(true){const part=await reader.read();if(part.done)break;bytes+=part.value.byteLength;if(bytes>expected.bytes){await reader.cancel();throw Error('Private Blob size differs');}hash.update(part.value);}
    ensure(bytes===expected.bytes&&hash.digest('hex')===expected.sha256,'Private Blob full SHA-256 differs');return bytes;
  }finally{reader.releaseLock();}
}
function privatePutResult(result,blobPath) {
  ensure(result&&result.pathname===blobPath&&typeof result.url==='string','Unexpected private upload result');
  const url=new URL(result.url);ensure(url.protocol==='https:'&&/^[a-zA-Z0-9]+\.private\.blob\.vercel-storage\.com$/.test(url.hostname)&&url.pathname==='/'+blobPath&&!url.search&&!url.hash,'Upload did not target private Blob storage');
}
async function uploadPrivateRelease({root,inventory,manifestURL,blob,maxUploadBytes,verifyByteBudget,onProgress=async()=>{}}) {
  root=await fs.realpath(root);ensure(safeBudget(maxUploadBytes)&&safeBudget(verifyByteBudget),'Explicit byte budgets are required');
  ensure(blob&&typeof blob.put==='function'&&typeof blob.get==='function');
  const plan=makePlan(inventory,manifestURL);
  const uploadBytes=inventory.assets.reduce((sum,asset)=>sum+asset.bytes,plan.indexBytes.length);
  const verificationBytes=inventory.assets.reduce((sum,asset)=>sum+asset.bytes+asset.samples.reduce((total,sample)=>total+sample.end-sample.start+1,0),plan.indexBytes.length);
  ensure(Number.isSafeInteger(uploadBytes)&&uploadBytes<=maxUploadBytes,'Upload byte budget is insufficient');
  ensure(Number.isSafeInteger(verificationBytes)&&verificationBytes<=verifyByteBudget,'Verification byte budget is insufficient');
  // Finish all source/plan checks before the first remote mutation.
  for(const asset of inventory.assets) await sourceProof(root,asset);
  const completed=[];
  for(let i=0;i<inventory.assets.length;i++) {
    const asset=inventory.assets[i],receipt=plan.receipts[i];
    const existing=await blob.get(receipt.blobPath,{access:'private',headers:{'Accept-Encoding':'identity'}});
    if(existing) await blobRead(blob,receipt.blobPath,asset,undefined,existing);
    else {
      const stream=await boundSourceStream(root,asset);
      const result=await blob.put(receipt.blobPath,stream,{access:'private',addRandomSuffix:false,allowOverwrite:false,contentType:asset.contentType,cacheControlMaxAge:31536000,multipart:asset.bytes>=5*1024*1024});
      privatePutResult(result,receipt.blobPath);
      await blobRead(blob,receipt.blobPath,asset);
    }
    for(const sample of asset.samples) await blobRead(blob,receipt.blobPath,asset,sample);
    completed.push({...receipt,fullSha256Verified:true,sampleSha256Verified:true});
    await onProgress({schemaVersion:1,releaseId:inventory.releaseId,status:'uploading',assets:completed});
  }
  const existingIndex=await blob.get(plan.indexPath,{access:'private',headers:{'Accept-Encoding':'identity'}});
  if(existingIndex) await blobRead(blob,plan.indexPath,{bytes:plan.indexBytes.length,sha256:plan.indexSha256},undefined,existingIndex);
  else {
    const result=await blob.put(plan.indexPath,plan.indexBytes,{access:'private',addRandomSuffix:false,allowOverwrite:false,contentType:'application/json',cacheControlMaxAge:31536000});
    privatePutResult(result,plan.indexPath);
    await blobRead(blob,plan.indexPath,{bytes:plan.indexBytes.length,sha256:plan.indexSha256});
  }
  return {schemaVersion:1,releaseId:inventory.releaseId,status:'uploaded-private',assets:completed,index:plan.index,indexPath:plan.indexPath,indexSha256:plan.indexSha256,uploadBytes,verificationBytes,accessPolicyVerified:false};
}
function privateHeaders(response) {
  const cache=response.headers.get('cache-control')||'';
  ensure(/(?:^|,)\s*private\s*(?:,|$)/i.test(cache)&&/(?:^|,)\s*no-store\s*(?:,|$)/i.test(cache)&&!/(?:^|,)\s*public\b/i.test(cache),'API cache policy is not private');
  ensure(response.headers.get('cross-origin-resource-policy')==='same-origin','API resource policy is not same-origin');
  ensure(!response.headers.has('access-control-allow-origin'),'API unexpectedly grants cross-origin access');
}
async function discard(response) {if(response.body)await response.body.cancel();}
async function verifyPrivateAPI({inventory,cookie,fetch=globalThis.fetch,byteBudget}) {
  ensure(typeof cookie==='string'&&cookie.length>0&&cookie.length<=2048&&!/[\r\n]/.test(cookie));
  const manifest=inventory.assets.find(asset=>asset.url.includes('/book-manifest.'));
  ensure(manifest,'Manifest is missing from private inventory');
  makePlan(inventory,manifest.url);
  const needed=inventory.assets.reduce((sum,asset)=>sum+asset.samples.reduce((n,sample)=>n+sample.end-sample.start+1,0),0);
  ensure(safeBudget(byteBudget)&&needed<=byteBudget,'API verification byte budget is insufficient');
  const origin=inventory.appOrigin;
  const request=async(url,method='GET',extra={},authenticated=true)=>{
    const response=await fetch(url,{method,redirect:'manual',headers:{Origin:origin,'Accept-Encoding':'identity',...(authenticated?{Cookie:cookie}:{}),...extra}});
    privateHeaders(response);return response;
  };
  let response=await request(origin+'/api/book','GET',{},false);
  ensure(response.status===401,'Book manifest is anonymously accessible');await discard(response);
  response=await request(origin+'/api/book','GET',{Origin:'https://unauthorized.invalid'});
  ensure(response.status===403,'Cross-origin cookie access is allowed');await discard(response);
  const verified=[];
  for(const asset of inventory.assets) {
    response=await request(asset.url,'HEAD',{},false);ensure(response.status===401,'Asset metadata is anonymously accessible');await discard(response);
    response=await request(asset.url,'GET',{Range:`bytes=${asset.samples[0].start}-${asset.samples[0].end}`},false);ensure(response.status===401,'Asset bytes are anonymously accessible');await discard(response);
    response=await request(asset.url,'HEAD');
    ensure(response.status===200&&response.headers.get('content-length')===String(asset.bytes)&&response.headers.get('accept-ranges')==='bytes'&&(response.headers.get('content-type')||'').split(';')[0]===asset.contentType,'Private HEAD metadata differs');await discard(response);
    for(const sample of asset.samples) {
      response=await request(asset.url,'GET',{Range:`bytes=${sample.start}-${sample.end}`});
      const size=sample.end-sample.start+1;
      ensure(response.status===206&&response.headers.get('content-range')===`bytes ${sample.start}-${sample.end}/${asset.bytes}`&&response.headers.get('content-length')===String(size)&&(!response.headers.get('content-encoding')||response.headers.get('content-encoding')==='identity'),'Private API range metadata differs');
      const bytes=await boundedBytes(response.body,size);ensure(bytes.length===size&&sha256(bytes)===sample.sha256,'Private API range SHA-256 differs');
    }
    response=await request(asset.url,'GET',{Range:`bytes=${asset.bytes}-`});ensure(response.status===416&&response.headers.get('content-range')===`bytes */${asset.bytes}`,'Private API range rejection differs');await discard(response);
    verified.push({url:asset.url,sha256:asset.sha256,bytes:asset.bytes,sampleSha256Verified:true});
  }
  return {schemaVersion:1,releaseId:inventory.releaseId,status:'private-api-verified',accessPolicyVerified:true,assets:verified,verificationBytes:needed};
}
async function safeDirectory(root,directory) {
  ensure(owned(root,directory));
  let current=root;
  for(const part of path.relative(root,directory).split(path.sep)) {
    current=path.join(current,part);
    try {await fs.mkdir(current,{mode:0o700});}catch(error){if(error.code!=='EEXIST')throw error;}
    const info=await fs.lstat(current);ensure(info.isDirectory()&&!info.isSymbolicLink(),'Private output contains a symbolic link');
  }
}
async function writePrivate(filename,value,root) {
  const directory=path.dirname(filename);await safeDirectory(root,directory);
  const temporary=filename+'.'+crypto.randomBytes(8).toString('hex')+'.tmp';
  await fs.writeFile(temporary,JSON.stringify(value,null,2)+'\n',{mode:0o600,flag:'wx'});await fs.rename(temporary,filename);
}
async function main() {
  const {parseArgs}=require('node:util');
  const {values}=parseArgs({options:{root:{type:'string'},inventory:{type:'string'},'manifest-url':{type:'string'},out:{type:'string'},'max-upload-bytes':{type:'string'},'verify-byte-budget':{type:'string'},'verify-api':{type:'boolean'},'api-byte-budget':{type:'string'}}});
  ensure(values.root&&values.inventory&&values.out,'Explicit root, inventory and private output are required');
  const root=await fs.realpath(values.root),out=path.resolve(values.out);
  ensure(owned(path.join(root,'Audiobook','author-audit'),out),'Output must remain in ignored author-audit staging');
  await safeDirectory(root,out);
  const inventoryBytes=await fs.readFile(values.inventory),inventory=JSON.parse(inventoryBytes.toString('utf8'));
  let result;
  if(values['verify-api']) {
    ensure(process.env.LUMEN_OPERATOR_SESSION_COOKIE,'Operator session cookie must be supplied privately through the environment');
    result=await verifyPrivateAPI({inventory,cookie:process.env.LUMEN_OPERATOR_SESSION_COOKIE,byteBudget:Number(values['api-byte-budget'])});
  }else {
    const blob=await import('@vercel/blob');
    result=await uploadPrivateRelease({root,inventory,manifestURL:values['manifest-url'],blob,maxUploadBytes:Number(values['max-upload-bytes']),verifyByteBudget:Number(values['verify-byte-budget']),onProgress:async record=>writePrivate(path.join(out,'upload-progress.json'),{...record,uploadInventorySha256:sha256(inventoryBytes)},root)});
  }
  result.uploadInventorySha256=sha256(inventoryBytes);result.verifiedAt=new Date().toISOString();
  const reportPath=path.join(out,values['verify-api']?'private-api-verification.json':'private-upload-verification.json');
  await writePrivate(reportPath,result,root);
  console.log(JSON.stringify({status:result.status,assets:result.assets.length,indexPath:result.indexPath,indexSha256:result.indexSha256,reportPath}));
}
module.exports={uploadPrivateRelease,verifyPrivateAPI};
if(require.main===module) main().catch(()=>{console.error('Private release operation failed; no production release pointer was changed.');process.exitCode=1;});
