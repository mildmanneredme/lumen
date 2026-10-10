'use strict';
const {Readable}=require('node:stream');
const {pipeline}=require('node:stream/promises');
const {getReader}=require('./runtime.cjs');
const {trustedRequestOrigin}=require('./private-access.cjs');
function createNodeHandler({loadReader=getReader,env=process.env}={}) {
  return async function nodeHandler(request,response) {
    const controller=new AbortController();
    const aborted=()=>controller.abort();
    request.once('aborted',aborted);
    response.once('close',aborted);
    try {
      const url=new URL(request.url,trustedRequestOrigin(env));
      const requestHeaders=new Headers();
      for(const [name,value] of Object.entries(request.headers)) {
        if(Array.isArray(value)) requestHeaders.set(name,value.join(name==='cookie'?'; ':', '));
        else if(value!==undefined) requestHeaders.set(name,value);
      }
      let body;
      if(request.method==='POST' && url.pathname==='/api/session') {
        const chunks=[];let bytes=0;
        for await(const chunk of request) {
          bytes+=chunk.length;
          if(bytes>4096) {
            response.writeHead(413,{'Content-Type':'application/json','Cache-Control':'private, no-store'});
            response.end('{"error":"Request rejected."}');
            return;
          }
          chunks.push(chunk);
        }
        body=Buffer.concat(chunks,bytes);
      }
      const reader=await loadReader();
      const result=await reader(new Request(url,{method:request.method,headers:requestHeaders,signal:controller.signal,
        ...(body!==undefined?{body}:{})}));
      if(controller.signal.aborted) return;
      const outputHeaders={};
      for(const [name,value] of result.headers) outputHeaders[name]=value;
      response.writeHead(result.status,outputHeaders);
      if(result.body) await pipeline(Readable.fromWeb(result.body),response);
      else response.end();
    } catch(_) {
      if(!response.headersSent && !response.destroyed) {
        response.writeHead(503,{'Content-Type':'application/json','Cache-Control':'private, no-store'});
        response.end('{"error":"The private reader is temporarily unavailable."}');
      } else if(!response.destroyed) response.destroy();
    } finally {
      request.removeListener('aborted',aborted);
      response.removeListener('close',aborted);
    }
  };
}
module.exports={createNodeHandler};
