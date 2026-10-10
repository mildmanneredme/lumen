'use strict';
// Operator library: no credential lookup, account calls or IO on import.
const {Readable}=require('node:stream');
const PREFIX='lumen/releases/lumen-private-192-v4/';
const SHA=/^[a-f0-9]{64}$/,ETAG=/^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$/;
const TYPES={'application/json':'.json','audio/mpeg':'.mp3','image/webp':'.webp'};
function ensure(value){if(!value)throw Error('Invalid R2 worker bridge contract');}
function positive(value){return Number.isSafeInteger(value)&&value>0;}
function validKey(value){return typeof value==='string'&&value.length<=1024&&value.startsWith(PREFIX)&&
  value.split('/').every(part=>/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(part)&&part!=='.'&&part!=='..');}
async function cancel(response){if(response?.body&&!response.body.locked)await response.body.cancel().catch(()=>{});}
function decodedMetadata(response,key){
  const header=response.headers.get('X-Lumen-R2-Metadata');
  ensure(typeof header==='string'&&header.length>0&&header.length<=8192&&/^[a-zA-Z0-9_-]+$/.test(header));
  const bytes=Buffer.from(header,'base64url');ensure(bytes.toString('base64url')===header);
  const info=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  ensure(info&&info.key===key&&positive(info.size)&&ETAG.test(info.etag||'')&&
    (info.version===undefined||typeof info.version==='string'&&info.version.length>0&&info.version.length<=512)&&
    TYPES[info.httpMetadata?.contentType]&&key.endsWith(TYPES[info.httpMetadata.contentType])&&
    (!info.httpMetadata.contentEncoding||info.httpMetadata.contentEncoding==='identity')&&SHA.test(info.customMetadata?.sha256||''));
  ensure(response.headers.get('Content-Type')===info.httpMetadata.contentType&&response.headers.get('ETag')==='"'+info.etag+'"'&&
    (!response.headers.get('Content-Encoding')||response.headers.get('Content-Encoding')==='identity'));
  return info;
}
function contentLength(response,expected){ensure(response.headers.get('Content-Length')===String(expected));}
function createR2WorkerAdapter({workerOrigin,secret,fetch:fetcher=globalThis.fetch}={}){
  const url=new URL(workerOrigin);
  ensure(url.protocol==='https:'&&url.origin===workerOrigin&&!url.username&&!url.password&&
    typeof secret==='string'&&/^[a-zA-Z0-9_-]{43,128}$/.test(secret)&&typeof fetcher==='function');
  // Capture all trusted context before the first await; callers cannot redirect
  // later operations by mutating their configuration or global fetch.
  const origin=url.origin,token=secret,transport=fetcher;
  async function send(key,method,headers,body){
    ensure(validKey(key));
    const supplied=new Headers(headers);supplied.set('Authorization','Bearer '+token);supplied.set('Accept-Encoding','identity');
    return transport(origin+'/'+key,{method,headers:supplied,redirect:'error',...(body===undefined?{}:{body,duplex:'half'})});
  }
  async function head(key){
    const response=await send(key,'HEAD');
    try{
      if(response.status===404)return null;
      ensure(response.status===200);const info=decodedMetadata(response,key);ensure(!info.range);contentLength(response,info.size);return info;
    }finally{await cancel(response);}
  }
  async function get(key,options={}){
    ensure(options&&options.onlyIf&&Object.keys(options.onlyIf).length===1&&ETAG.test(options.onlyIf.etagMatches||''));
    const etag=options.onlyIf.etagMatches,headers=new Headers({'If-Match':'"'+etag+'"'}),range=options.range;
    if(range!==undefined){ensure(range&&Object.keys(range).length===2&&Number.isSafeInteger(range.offset)&&range.offset>=0&&positive(range.length)&&
      Number.isSafeInteger(range.offset+range.length-1));headers.set('Range',`bytes=${range.offset}-${range.offset+range.length-1}`);}
    const requested=range?{offset:range.offset,length:range.length}:undefined;
    const response=await send(key,'GET',headers);
    try{
      if(response.status===404){await cancel(response);return null;}
      ensure(response.status===412||response.status===(requested?206:200));const info=decodedMetadata(response,key);
      if(response.status===412){contentLength(response,0);await cancel(response);return info;}
      ensure(info.etag===etag&&response.body&&typeof response.body.getReader==='function');
      if(requested){ensure(info.range?.offset===requested.offset&&info.range?.length===requested.length&&requested.offset+requested.length<=info.size);
        contentLength(response,requested.length);ensure(response.headers.get('Content-Range')===`bytes ${requested.offset}-${requested.offset+requested.length-1}/${info.size}`);}
      else{ensure(!info.range&&!response.headers.has('Content-Range'));contentLength(response,info.size);}
      return {...info,body:response.body};
    }catch(error){await cancel(response);throw error;}
  }
  async function put(key,body,options){
    ensure(options&&options.onlyIf instanceof Headers&&options.onlyIf.get('If-None-Match')==='*'&&positive(options.contentLength)&&
      SHA.test(options.sha256||'')&&options.customMetadata?.sha256===options.sha256&&TYPES[options.httpMetadata?.contentType]&&
      options.httpMetadata.cacheControl==='private, no-store'&&(!options.httpMetadata.contentEncoding||options.httpMetadata.contentEncoding==='identity'));
    ensure(Buffer.isBuffer(body)||body instanceof Readable);if(Buffer.isBuffer(body))ensure(body.length===options.contentLength);
    const hash=options.sha256,size=options.contentLength,type=options.httpMetadata.contentType;
    const stream=Buffer.isBuffer(body)?body:Readable.toWeb(body);
    const response=await send(key,'PUT',{'If-None-Match':'*','Content-Length':String(size),'Content-Type':type,'X-Lumen-Sha256':hash},stream);
    try{
      if(response.status===412)return null;
      ensure(response.status===201);const info=decodedMetadata(response,key);ensure(!info.range&&info.size===size&&info.customMetadata.sha256===hash);contentLength(response,0);return info;
    }finally{await cancel(response);}
  }
  return Object.freeze({head,get,put});
}
module.exports={createR2WorkerAdapter};
