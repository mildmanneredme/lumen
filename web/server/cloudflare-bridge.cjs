'use strict';
// A server-only control bridge. Cloudflare is the sole session authority;
// media responses are redirects, so no MP3/image bytes traverse Vercel.
const {trustedRequestOrigin,boundedBytes}=require('./private-access.cjs');
const forwarded=['host','origin','sec-fetch-site','cookie','content-type','range','if-range','if-none-match'];
function failure(status) {
  return new Response(JSON.stringify({error:status===503?'The private reader is temporarily unavailable.':'Request rejected.'}),
    {status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'private, no-store',
      'Vary':'Cookie','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'}});
}
function bridgeConfig(env) {
  const requestOrigin=trustedRequestOrigin(env),worker=new URL(env.LUMEN_WORKER_ORIGIN);
  if(worker.protocol!=='https:'||worker.origin!==env.LUMEN_WORKER_ORIGIN||worker.username||worker.password) throw Error('Invalid media origin');
  const key=env.LUMEN_BRIDGE_KEY;
  if(typeof key!=='string'||!/^[a-zA-Z0-9_-]{43,86}$/.test(key)) throw Error('Invalid bridge key');
  const bytes=Buffer.from(key,'base64url');
  if(bytes.length<32||bytes.length>64||bytes.toString('base64url')!==key) throw Error('Invalid bridge key');
  return {requestOrigin,worker:worker.origin,key};
}
function createCloudflareBridge({env=process.env,fetch=globalThis.fetch}={}) {
  return async request=>{
    let config;
    try {config=bridgeConfig(env);}catch(_){return failure(503);}
    const url=new URL(request.url),session=url.pathname==='/api/session';
    if(!session&&url.pathname!=='/api/book'&&!url.pathname.startsWith('/api/assets/'))return failure(404);
    if(url.search)return failure(400);
    if(!(session?['GET','POST','DELETE']:['GET','HEAD']).includes(request.method))return failure(405);
    const supplied=request.headers.get('origin'),host=request.headers.get('host'),site=request.headers.get('sec-fetch-site');
    if(url.origin!==config.requestOrigin||(host&&host.toLowerCase()!==new URL(config.requestOrigin).host)||
      (supplied&&supplied!==config.requestOrigin)||(site&&!['same-origin','none'].includes(site))||
      (session&&request.method!=='GET'&&supplied!==config.requestOrigin))return failure(403);
    const headers={};
    for(const name of forwarded)if(request.headers.has(name))headers[name]=request.headers.get(name);
    let body;
    try {
      if(session&&request.method==='POST') {
        if(!/^application\/json(?:;|$)/i.test(headers['content-type']||''))return failure(400);
        const length=request.headers.get('content-length');
        if(length&&(!/^\d+$/.test(length)||Number(length)>4096))return failure(413);
        body=(await boundedBytes(request.body,4096)).toString('utf8');
      }
    }catch(_){return failure(413);}
    try {
      const envelope=JSON.stringify({url:request.url,method:request.method,headers,...(body!==undefined?{body}:{})});
      if(Buffer.byteLength(envelope)>12288)return failure(413);
      const upstreamHeaders={'Content-Type':'application/json','X-Lumen-Bridge-Key':config.key,'Accept-Encoding':'identity'};
      const head=request.method==='HEAD';
      if(head)upstreamHeaders['X-Lumen-Request']=Buffer.from(envelope).toString('base64url');
      const result=await fetch(config.worker+'/bridge',{method:head?'HEAD':'POST',redirect:'manual',signal:request.signal,
        headers:upstreamHeaders,...(head?{}:{body:envelope})});
      // Never propagate arbitrary redirects or accidental media streaming.
      if(result.status===307) {
        const target=new URL(result.headers.get('location'));
        if(target.origin!==config.worker||!target.pathname.startsWith('/media/')||target.username||target.password||target.hash)throw Error('Invalid media redirect');
        if(result.body)await result.body.cancel();
        return new Response(null,{status:307,headers:result.headers});
      }
      if(result.status>=300&&result.status<400&&result.status!==304) {
        if(result.body)await result.body.cancel();throw Error('Unexpected upstream redirect');
      }
      if((result.headers.get('content-type')||'').split(';')[0]!=='application/json') {
        if(result.body)await result.body.cancel();throw Error('Unexpected upstream body');
      }
      const bytes=result.body?await boundedBytes(result.body,2*1024*1024):null;
      const outputHeaders=new Headers(result.headers);
      if(!head&&result.status!==304) {
        // Node fetch decodes upstream compression. Its retained transport
        // headers describe compressed bytes, not the buffer sent to the reader.
        outputHeaders.delete('content-encoding');
        outputHeaders.set('content-length',String(bytes?.length||0));
      }
      return new Response(head||result.status===304?null:bytes,{status:result.status,headers:outputHeaders});
    }catch(_){return failure(503);}
  };
}
module.exports={createCloudflareBridge};
