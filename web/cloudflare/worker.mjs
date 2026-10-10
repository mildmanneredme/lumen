import crypto from 'node:crypto';
import {Buffer} from 'node:buffer';
import access from '../server/private-access.cjs';

const {createPrivateReader,readConfig,claimsAllowed,validateIndex,parseRange,boundedBytes,sha256}=access;
const JSON_LIMIT=2*1024*1024;
const BRIDGE_LIMIT=12288;
const BODY_LIMIT=4096;
const forwarded=new Set(['host','origin','sec-fetch-site','cookie','content-type','content-length','range','if-range','if-none-match']);
const grantFields=['schemaVersion','aud','origin','releaseId','path','sha256','i','v','exp'];
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const pathAllowed=value=>typeof value==='string'&&value.length<=500&&
  value.split('/').every(part=>/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(part)&&part!=='.'&&part!=='..');
function requireValue(value) { if(!value)throw Error('Invalid private delivery configuration'); }
function equal(left,right) {
  if(typeof left!=='string'||typeof right!=='string')return false;
  const a=Buffer.from(left),b=Buffer.from(right);return a.length===b.length&&crypto.timingSafeEqual(a,b);
}
function originOf(value) {
  requireValue(typeof value==='string');const url=new URL(value);
  requireValue(url.protocol==='https:'&&url.origin===value&&!url.username&&!url.password);
  return value;
}
function secret(value) {
  requireValue(typeof value==='string'&&/^[a-zA-Z0-9_-]{43,86}$/.test(value));
  const bytes=Buffer.from(value,'base64url');requireValue(bytes.length>=32&&bytes.length<=64&&bytes.toString('base64url')===value);return bytes;
}
function deliveryConfig(env) {
  const origin=originOf(env.LUMEN_APP_ORIGIN),audience=originOf(env.LUMEN_WORKER_ORIGIN);
  const media=secret(env.LUMEN_MEDIA_SECRET),bridge=secret(env.LUMEN_BRIDGE_KEY);
  const config=readConfig({...env,VERCEL_ENV:'production'});
  requireValue(!equal(env.LUMEN_MEDIA_SECRET,env.LUMEN_SESSION_SECRET)&&
    !equal(env.LUMEN_MEDIA_SECRET,env.LUMEN_BRIDGE_KEY)&&!equal(env.LUMEN_BRIDGE_KEY,env.LUMEN_SESSION_SECRET));
  const allowed=new Set([origin]);
  if(env.LUMEN_ALLOWED_ORIGINS_JSON!==undefined) {
    const previews=JSON.parse(env.LUMEN_ALLOWED_ORIGINS_JSON);requireValue(Array.isArray(previews)&&previews.length<=8);
    for(const value of previews) {
      const preview=originOf(value),url=new URL(preview);
      requireValue(!url.port&&url.hostname.endsWith('.vercel.app')&&
        url.hostname.split('.').every(label=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)));
      requireValue(!allowed.has(preview));allowed.add(preview);
    }
  }
  requireValue(env.LUMEN_R2&&typeof env.LUMEN_R2.get==='function');
  return {origin,audience,media,bridge,allowed,config};
}
function privateHeaders(extra={}) {
  return new Headers({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
    'Referrer-Policy':'no-referrer','X-Robots-Tag':'noindex, nofollow, noarchive',...extra});
}
function failure(status) {
  return new Response(JSON.stringify({error:status===503?'The private reader is temporarily unavailable.':'Request rejected.'}),
    {status,headers:privateHeaders({'Content-Type':'application/json; charset=utf-8'})});
}
function readerEnvironment(env,origin,canonical) {
  return {...env,VERCEL_ENV:origin===canonical?'production':'preview',VERCEL_URL:new URL(origin).hostname};
}
function hmac(payload,key) { return crypto.createHmac('sha256',key).update(payload).digest('base64url'); }
function signGrant(claims,config) {
  const payload=Buffer.from(JSON.stringify(claims)).toString('base64url');return payload+'.'+hmac(payload,config.media);
}
function decodeGrant(value,config,clock) {
  if(typeof value!=='string'||value.length>4096||!/^([a-zA-Z0-9_-]+)\.([a-zA-Z0-9_-]{43})$/.test(value))return null;
  const [payload,signature]=value.split('.');if(!equal(signature,hmac(payload,config.media)))return null;
  try {
    const bytes=Buffer.from(payload,'base64url');if(bytes.toString('base64url')!==payload)return null;
    const claims=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
    if(!object(claims)||Object.keys(claims).length!==grantFields.length||!grantFields.every(key=>Object.hasOwn(claims,key))||
      claims.schemaVersion!==1||claims.aud!==config.audience||!config.allowed.has(claims.origin)||
      typeof claims.releaseId!=='string'||!/^[a-z][a-z0-9-]*$/.test(claims.releaseId)||
      !pathAllowed(claims.path)||!hash(claims.sha256)||!claimsAllowed(claims,config.config,clock))return null;
    return claims;
  }catch(_){return null;}
}
function checkSignal(signal) { if(signal?.aborted)throw Error('Private request cancelled'); }
async function cancelBody(value) { if(value?.body)try{await value.body.cancel();}catch(_){} }
function cancellableBody(body,signal) {
  return signal?body.pipeThrough(new TransformStream(),{signal}):body;
}
function matchesMetadata(value,expected,range) {
  return value&&value.body&&value.size===expected.bytes&&
    value.httpMetadata?.contentType===expected.contentType&&value.customMetadata?.sha256===expected.sha256&&
    (!value.httpMetadata?.contentEncoding||value.httpMetadata.contentEncoding==='identity')&&
    (range?value.range?.offset===range.start&&value.range?.length===range.end-range.start+1:
      !value.range||(value.range.offset===0&&value.range.length===expected.bytes));
}
function mediaHeaders(response,origin,supplied) {
  const headers=new Headers(response.headers);headers.set('Cross-Origin-Resource-Policy','cross-origin');
  headers.set('Access-Control-Allow-Origin',supplied==='null'?'null':origin);headers.set('Vary','Origin');
  headers.set('Access-Control-Expose-Headers','Accept-Ranges, Content-Length, Content-Range, ETag');
  headers.delete('Set-Cookie');
  let body=response.body;
  if(body&&typeof globalThis.FixedLengthStream==='function'&&headers.has('Content-Length')) {
    const length=Number(headers.get('Content-Length'));requireValue(Number.isSafeInteger(length)&&length>=0);
    const output=new globalThis.FixedLengthStream(length);
    // Native workerd ignores manually assigned lengths on generic streams.
    // FixedLengthStream keeps the length without buffering and fails truncation.
    body.pipeTo(output.writable).catch(()=>{});body=output.readable;
  }
  return new Response(body,{status:response.status,headers});
}

export function createWorker({now=Date.now}={}) {
  // Only physically verified immutable indexes are retained, never access policy.
  const indexes=new WeakMap();
  function storage(env,config) {
    const bucket=env.LUMEN_R2;
    const loadIndex=async signal=>{
      checkSignal(signal);
      const path=env.LUMEN_RELEASE_INDEX_PATH,expectedHash=env.LUMEN_RELEASE_INDEX_SHA256;
      requireValue(typeof path==='string'&&/^lumen\/releases\/[a-z][a-z0-9-]*\/server-index\.[a-f0-9]{64}\.json$/.test(path)&&
        hash(expectedHash)&&path.endsWith('.'+expectedHash+'.json'));
      const key=config.origin+':'+path+':'+expectedHash;
      let values=indexes.get(bucket);if(!values){values=new Map();indexes.set(bucket,values);}
      if(values.has(key))return values.get(key);
      const value=await bucket.get(path);
      if(signal?.aborted){await cancelBody(value);throw Error('Private request cancelled');}
      if(!value||!Number.isSafeInteger(value.size)||value.size<=0||value.size>JSON_LIMIT||
        !matchesMetadata(value,{bytes:value.size,contentType:'application/json',sha256:expectedHash},null)) {
        await cancelBody(value);throw Error('Private index metadata differs');
      }
      const bytes=await boundedBytes(cancellableBody(value.body,signal),JSON_LIMIT);checkSignal(signal);
      requireValue(bytes.length===value.size&&sha256(bytes)===expectedHash);
      const index=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));validateIndex(index,config.origin);
      requireValue(path.startsWith('lumen/releases/'+index.releaseId+'/'));
      values.set(key,index);return index;
    };
    const getBlob=async(key,options={})=>{
      checkSignal(options.abortSignal);
      const index=await loadIndex(options.abortSignal),asset=index.assets.find(row=>row.blobPath===key);
      requireValue(asset&&options.access==='private');
      const suppliedRange=options.headers?.Range,range=suppliedRange?parseRange(suppliedRange,asset.bytes):null;
      requireValue(!suppliedRange||range);
      const value=await bucket.get(key,range?{range:{offset:range.start,length:range.end-range.start+1}}:{});
      if(options.abortSignal?.aborted){await cancelBody(value);throw Error('Private request cancelled');}
      if(!matchesMetadata(value,asset,range)){await cancelBody(value);throw Error('Private asset metadata differs');}
      const count=range?range.end-range.start+1:asset.bytes;
      const headers=new Headers({'Content-Type':asset.contentType,'Content-Length':String(count)});
      if(range)headers.set('Content-Range',`bytes ${range.start}-${range.end}/${asset.bytes}`);
      return {statusCode:200,headers,stream:cancellableBody(value.body,options.abortSignal)};
    };
    return {loadIndex,getBlob};
  }
  async function bridge(request,env,config) {
    let envelope;
    try {
      if(request.method==='HEAD') {
        const encoded=request.headers.get('x-lumen-request');
        if(!encoded||encoded.length>Math.ceil(BRIDGE_LIMIT*4/3)||!/^[a-zA-Z0-9_-]+$/.test(encoded))return failure(encoded?.length>Math.ceil(BRIDGE_LIMIT*4/3)?413:400);
        const bytes=Buffer.from(encoded,'base64url');if(bytes.length>BRIDGE_LIMIT)return failure(413);
        if(bytes.toString('base64url')!==encoded)return failure(400);
        envelope=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
      }else {
        if(!/^application\/json(?:;|$)/i.test(request.headers.get('content-type')||''))return failure(400);
        const length=request.headers.get('content-length');if(length&&(!/^\d+$/.test(length)||Number(length)>BRIDGE_LIMIT))return failure(413);
        const bytes=await boundedBytes(request.body,BRIDGE_LIMIT);envelope=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
      }
    }catch(_){return failure(413);}
    if(!object(envelope)||(Object.hasOwn(envelope,'body')&&typeof envelope.body!=='string')||
      Object.keys(envelope).some(key=>!['url','method','headers','body'].includes(key))||
      typeof envelope.url!=='string'||envelope.url.length>2048||!object(envelope.headers)||typeof envelope.method!=='string')return failure(400);
    if(request.method==='HEAD'?envelope.method!=='HEAD':envelope.method==='HEAD')return failure(400);
    let url;try {url=new URL(envelope.url);}catch(_){return failure(400);}
    if(url.href!==envelope.url||url.username||url.password||url.hash||url.search)return failure(400);
    if(!config.allowed.has(url.origin))return failure(403);
    const isSession=url.pathname==='/api/session';
    if(!isSession&&url.pathname!=='/api/book'&&!url.pathname.startsWith('/api/assets/'))return failure(404);
    if(!(isSession?['GET','POST','DELETE']:['GET','HEAD']).includes(envelope.method))return failure(405);
    const bodyExpected=isSession&&envelope.method==='POST';
    if(bodyExpected?!Object.hasOwn(envelope,'body'):Object.hasOwn(envelope,'body'))return failure(400);
    if(bodyExpected&&Buffer.byteLength(envelope.body)>BODY_LIMIT)return failure(413);
    const headers=new Headers();
    for(const [name,value] of Object.entries(envelope.headers)) {
      if(!forwarded.has(name)||typeof value!=='string'||value.length>4096||/[\r\n]/.test(value))return failure(400);
      headers.set(name,value);
    }
    const source=new Request(url,{method:envelope.method,headers,signal:request.signal,
      ...(bodyExpected?{body:envelope.body}:{})});
    const dependencies=storage(env,config);
    const reader=createPrivateReader({env:readerEnvironment(env,url.origin,config.origin),now,...dependencies,
      assetResponse:({request:original,index,asset,claims})=>{
        if(!['audio/mpeg','image/webp'].includes(asset.contentType))return null;
        const grant={schemaVersion:1,aud:config.audience,origin:url.origin,releaseId:index.releaseId,path:asset.path,
          sha256:asset.sha256,i:claims.i,v:claims.v,exp:claims.exp};
        const location=new URL('/media/'+asset.path,config.audience);location.searchParams.set('grant',signGrant(grant,config));
        return new Response(null,{status:307,headers:privateHeaders({'Location':location.href,'Vary':'Cookie'})});
      }});
    return reader(source);
  }
  async function media(request,env,config,url) {
    if(!['GET','HEAD','OPTIONS'].includes(request.method))return failure(405);
    if(url.searchParams.size!==1||!url.searchParams.has('grant'))return failure(400);
    const clock=Math.floor(now()/1000),claims=decodeGrant(url.searchParams.get('grant'),config,clock);
    if(!claims||url.pathname!=='/media/'+claims.path)return failure(401);
    const supplied=request.headers.get('origin');if(supplied&&supplied!=='null'&&supplied!==claims.origin)return failure(403);
    if(request.method==='OPTIONS') {
      if(!supplied)return failure(403);
      if(!['GET','HEAD'].includes(request.headers.get('access-control-request-method')))return failure(405);
      const allowedHeaders=new Set(['range','if-range','if-none-match']);
      const requested=request.headers.get('access-control-request-headers');
      if(requested&&requested.split(',').some(value=>!allowedHeaders.has(value.trim().toLowerCase())))return failure(400);
      return mediaHeaders(new Response(null,{status:204,headers:privateHeaders({
        'Access-Control-Allow-Methods':'GET, HEAD','Access-Control-Allow-Headers':'Range, If-Range, If-None-Match'})}),claims.origin,supplied);
    }
    const dependencies=storage(env,config),index=await dependencies.loadIndex(request.signal);
    if(index.releaseId!==claims.releaseId)return failure(401);
    const asset=validateIndex(index,config.origin).get(claims.path);
    if(!asset||asset.sha256!==claims.sha256)return failure(401);
    if(!['audio/mpeg','image/webp'].includes(asset.contentType))return failure(403);
    const headers=new Headers();
    for(const name of ['range','if-range','if-none-match'])if(request.headers.has(name))headers.set(name,request.headers.get(name));
    const sessionClaims={i:claims.i,v:claims.v,exp:claims.exp};
    const payload=Buffer.from(JSON.stringify(sessionClaims)).toString('base64url');
    headers.set('cookie','__Host-lumen_session='+payload+'.'+hmac(payload,config.config.secret));
    const source=new Request(claims.origin+'/api/assets/'+claims.path,{method:request.method,headers,signal:request.signal});
    const reader=createPrivateReader({env:readerEnvironment(env,claims.origin,config.origin),now,...dependencies});
    return mediaHeaders(await reader(source),claims.origin,supplied);
  }
  return {async fetch(request,env) {
    let url;try{url=new URL(request.url);}catch(_){return failure(400);}
    const isBridge=url.pathname==='/bridge';
    if(isBridge) {
      if(!['POST','HEAD'].includes(request.method))return failure(405);
      if(!equal(request.headers.get('x-lumen-bridge-key'),env.LUMEN_BRIDGE_KEY))return failure(401);
      if(url.search)return failure(400);
    }else if(!url.pathname.startsWith('/media/'))return failure(404);
    try {
      const config=deliveryConfig(env);checkSignal(request.signal);
      const host=request.headers.get('host');
      if(url.origin!==config.audience||(host&&host.toLowerCase()!==new URL(config.audience).host))return failure(403);
      return await(isBridge?bridge(request,env,config):media(request,env,config,url));
    }catch(_){return failure(503);}
  }};
}
export default createWorker();
