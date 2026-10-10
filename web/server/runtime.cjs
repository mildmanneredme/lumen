'use strict';
const {createPrivateReader,boundedBytes,sha256,validateIndex} = require('./private-access.cjs');
const INDEX_LIMIT=2*1024*1024;
let cachedIndex;
function createIndexLoader({env=process.env,getBlob}) {
  return async function loadIndex(signal) {
    const path=env.LUMEN_RELEASE_INDEX_PATH, hash=env.LUMEN_RELEASE_INDEX_SHA256;
    if(typeof path !== 'string' || !/^lumen\/releases\/[a-z][a-z0-9-]*\/server-index\.[a-f0-9]{64}\.json$/.test(path) ||
      !/^[a-f0-9]{64}$/.test(hash || '') || !path.endsWith(`.${hash}.json`)) throw new Error('Invalid release index binding');
    const key=path+':'+hash;
    if(cachedIndex?.key===key) return cachedIndex.value;
    const result=await getBlob(path,{access:'private',headers:{'Accept-Encoding':'identity'},abortSignal:signal});
    if(!result || result.statusCode!==200 || !result.stream || result.headers.has('content-range')) throw new Error('Private index unavailable');
    const bytes=await boundedBytes(result.stream,INDEX_LIMIT);
    if(sha256(bytes)!==hash) throw new Error('Private index binding differs');
    const value=JSON.parse(bytes.toString('utf8'));
    validateIndex(value,env.LUMEN_APP_ORIGIN);
    cachedIndex={key,value};
    return value;
  };
}
let reader;
async function getReader() {
  if(!reader) {
    const {get}=await import('@vercel/blob');
    reader=createPrivateReader({loadIndex:createIndexLoader({getBlob:get}),getBlob:get});
  }
  return reader;
}
module.exports={createIndexLoader,getReader};
