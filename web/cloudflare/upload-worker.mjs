// Temporary operator-only uploader. Remove this Worker after release verification.
// R2 performs the SHA-256 check atomically before committing the streamed object.
// https://developers.cloudflare.com/r2/api/workers/workers-api-reference/
const PREFIX='lumen/releases/lumen-private-192-v4/';
const SHA=/^[a-f0-9]{64}$/;
const ETAG=/^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$/;
const TYPES={'application/json':'.json','audio/mpeg':'.mp3','image/webp':'.webp'};
function requireValue(condition){if(!condition)throw Error('Invalid upload request');}
function positive(value){return Number.isSafeInteger(value)&&value>0;}
function secretValid(value){return typeof value==='string'&&/^[a-zA-Z0-9_-]{43,128}$/.test(value);}
function authenticated(request,secret){
  const supplied=request.headers.get('Authorization')||'',expected='Bearer '+secret;
  let difference=supplied.length^expected.length;
  for(let i=0;i<expected.length;i++)difference|=expected.charCodeAt(i)^(supplied.charCodeAt(i)||0);
  return difference===0;
}
function validKey(value){return typeof value==='string'&&value.length<=1024&&value.startsWith(PREFIX)&&
  value.split('/').every(part=>/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(part)&&part!=='.'&&part!=='..');}
function privateHeaders(){return new Headers({'Cache-Control':'private, no-store','Vary':'Authorization',
  'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow, noarchive'});}
function failure(status){return new Response(null,{status,headers:privateHeaders()});}
function objectMetadata(result,key){
  requireValue(result&&result.key===key&&positive(result.size)&&ETAG.test(result.etag||'')&&
    typeof result.httpMetadata==='object'&&TYPES[result.httpMetadata.contentType]&&
    key.endsWith(TYPES[result.httpMetadata.contentType])&&
    (!result.httpMetadata.contentEncoding||result.httpMetadata.contentEncoding==='identity')&&SHA.test(result.customMetadata?.sha256||''));
  requireValue(result.version===undefined||typeof result.version==='string'&&result.version.length>0&&result.version.length<=512);
  return {key:result.key,size:result.size,etag:result.etag,...(result.version===undefined?{}:{version:result.version}),
    httpMetadata:{contentType:result.httpMetadata.contentType,...(result.httpMetadata.contentEncoding?{contentEncoding:'identity'}:{}),cacheControl:'private, no-store'},
    customMetadata:{sha256:result.customMetadata.sha256}};
}
function metadataHeaders(info,length){
  const headers=privateHeaders();
  const encoded=new TextEncoder().encode(JSON.stringify(info));
  headers.set('X-Lumen-R2-Metadata',btoa(String.fromCharCode(...encoded)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''));
  headers.set('Content-Type',info.httpMetadata.contentType);headers.set('ETag','"'+info.etag+'"');headers.set('Content-Length',String(length));
  return headers;
}
function pinnedETag(value){const match=/^"([a-zA-Z0-9][a-zA-Z0-9._:-]{0,255})"$/.exec(value||'');return match?.[1];}
function closedRange(value){
  if(value===null)return undefined;
  const match=/^bytes=(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$/.exec(value||'');requireValue(match);
  const start=Number(match[1]),end=Number(match[2]);
  requireValue(Number.isSafeInteger(start)&&Number.isSafeInteger(end)&&end>=start&&positive(end-start+1));
  return {offset:start,length:end-start+1};
}
function uploadStream(body,size){
  let count=0;
  // R2 requires a known-length stream. Workers' FixedLengthStream also rejects
  // short/long bodies. The standards-only fallback permits offline fixtures.
  const guarded=typeof globalThis.FixedLengthStream==='function'?new globalThis.FixedLengthStream(size):new TransformStream({
    transform(chunk,controller){requireValue(chunk instanceof Uint8Array);count+=chunk.byteLength;requireValue(count<=size);controller.enqueue(chunk);},
    flush(){requireValue(count===size);}
  });
  const abort=new AbortController(),finished=body.pipeTo(guarded.writable,{signal:abort.signal});
  finished.catch(()=>{});
  return {body:guarded.readable,finished,abort:()=>abort.abort()};
}
async function put(request,env,key){
  const rawLength=request.headers.get('Content-Length'),size=Number(rawLength),type=request.headers.get('Content-Type'),hash=request.headers.get('X-Lumen-Sha256');
  if(request.headers.get('If-None-Match')!=='*'||!/^[1-9][0-9]*$/.test(rawLength||'')||!positive(size)||!SHA.test(hash||'')||
    !TYPES[type]||!key.endsWith(TYPES[type])||!request.body||request.headers.has('Range')||request.headers.has('If-Match')||
    (request.headers.get('Content-Encoding')&&request.headers.get('Content-Encoding')!=='identity'))return failure(400);
  const stream=uploadStream(request.body,size);
  try{
    const result=await env.LUMEN_R2.put(key,stream.body,{onlyIf:new Headers({'If-None-Match':'*'}),sha256:hash,
      httpMetadata:{contentType:type,cacheControl:'private, no-store'},customMetadata:{sha256:hash}});
    if(result===null){stream.abort();return failure(412);}
    await stream.finished;
    const info=objectMetadata(result,key);requireValue(info.size===size&&info.customMetadata.sha256===hash);
    return new Response(null,{status:201,headers:metadataHeaders(info,0)});
  }finally{stream.abort();}
}
async function read(request,env,key){
  if(request.method==='HEAD'){
    if(request.headers.has('Range')||request.headers.has('If-Match'))return failure(400);
    const result=await env.LUMEN_R2.head(key);if(result===null)return failure(404);
    const info=objectMetadata(result,key);return new Response(null,{headers:metadataHeaders(info,info.size)});
  }
  let range,etag;
  try{etag=pinnedETag(request.headers.get('If-Match'));requireValue(etag);range=closedRange(request.headers.get('Range'));}catch{return failure(400);}
  const result=await env.LUMEN_R2.get(key,{onlyIf:{etagMatches:etag},...(range?{range}:{})});if(result===null)return failure(404);
  let info;
  try{
    info=objectMetadata(result,key);
    if(!result.body)return new Response(null,{status:412,headers:metadataHeaders(info,0)});
    requireValue(info.etag===etag&&typeof result.body.getReader==='function');
    if(range){requireValue(range.offset+range.length<=info.size&&result.range?.offset===range.offset&&result.range?.length===range.length);info.range=range;}
    // Native R2 also reports an exact full-object range for ordinary GETs.
    // Keep that internal shape out of full HTTP response metadata.
    else requireValue(!result.range||result.range.offset===0&&result.range.length===info.size);
    const headers=metadataHeaders(info,range?range.length:info.size);
    if(range)headers.set('Content-Range',`bytes ${range.offset}-${range.offset+range.length-1}/${info.size}`);
    return new Response(result.body,{status:range?206:200,headers});
  }catch(error){if(result.body&&!result.body.locked)await result.body.cancel().catch(()=>{});throw error;}
}
export default {
  async fetch(request,env){
    if(!env||!secretValid(env.LUMEN_UPLOAD_SECRET)||env.LUMEN_UPLOAD_PREFIX!==PREFIX||
      !env.LUMEN_R2||!['head','get','put'].every(method=>typeof env.LUMEN_R2[method]==='function'))return failure(503);
    if(!authenticated(request,env.LUMEN_UPLOAD_SECRET))return failure(401);
    const url=new URL(request.url),key=url.pathname.slice(1);
    if(url.search||!validKey(key))return failure(400);
    if(!['HEAD','GET','PUT'].includes(request.method))return failure(405);
    try{return request.method==='PUT'?await put(request,env,key):await read(request,env,key);}catch{return failure(503);}
  }
};
