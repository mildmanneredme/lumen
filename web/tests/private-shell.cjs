/* Private book responses must remain in the browser's native network stack. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const handlers={},stores=new Map(),deleted=[];
let online=true,responseStatus=200,responseRange=false;
function openCache(name){
  if(!stores.has(name)) stores.set(name,new Map());
  const data=stores.get(name);
  return {put:async(key,response)=>data.set(typeof key==='string'?key:key.url,response),
    match:async key=>data.get(typeof key==='string'?key:key.url)?.clone(),
    keys:async()=>[...data.keys()].map(url=>new BrowserRequest(url)),
    delete:async key=>data.delete(typeof key==='string'?key:key.url)};
}
class BrowserRequest extends Request {constructor(url,options){super(new URL(url,'https://lumen.example'),options);}}
const context={URL,Request:BrowserRequest,Response,setTimeout,clearTimeout,
  self:{location:{origin:'https://lumen.example'},addEventListener:(type,fn)=>handlers[type]=fn},
  caches:{open:async name=>openCache(name),keys:async()=>['lumen-shell-old','lumen-art-v1','lumen-art-old','lumen-media-v1','other-app'],
    delete:async key=>deleted.push(key)},fetch:async()=>{
      if(!online)throw Error('offline');
      return new Response('shell',{status:responseStatus,headers:responseRange?{'Content-Range':'bytes 0-4/9'}:{}});
    }};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../dist/sw.js'),'utf8'),context);
(async()=>{
  let installation;handlers.install({waitUntil:p=>installation=p});await installation;
  const cached=[...stores.values()].flatMap(data=>[...data.keys()]);
  assert.ok(cached.includes('/book.js') && cached.includes('/legacy-pilot.js'),'install the private reader modules');
  assert.ok(!cached.some(key=>key.includes('/data/') || key.includes('/api/')),'no prose in the shell cache');
  for(const [url,headers] of [['/api/book',{}],['/api/assets/release/audio.mp3',{}],['/api/assets/release/art.webp',{}],
    ['/app.js',{Range:'bytes=0-10'}]]) {
    let intercepted=false;
    handlers.fetch({request:new Request('https://lumen.example'+url,{headers}),respondWith:()=>intercepted=true,waitUntil:()=>{}});
    assert.equal(intercepted,false,'private assets, paintings and Range requests bypass caching: '+url);
  }
  async function painting(filename){
    let artwork,artworkWrites=[];
    handlers.fetch({request:new Request('https://lumen.example/assets/'+filename),
      respondWith:p=>artwork=p,waitUntil:p=>artworkWrites.push(p)});
    assert.ok(artwork,'public pilot paintings retain bounded visited-image caching');
    const response=await artwork;await Promise.all(artworkWrites);return response;
  }
  for(let i=0;i<45;i++)await painting('painting-'+i+'.webp');
  const art=stores.get('lumen-art-v1');
  assert.equal(art.size,40,'visited-image cache is bounded');
  assert.ok(!art.has('https://lumen.example/assets/painting-0.webp'));
  assert.ok(art.has('https://lumen.example/assets/painting-44.webp'));
  responseStatus=206;responseRange=true;await painting('partial.webp');
  assert.ok(!art.has('https://lumen.example/assets/partial.webp'),'never cache partial art');
  responseStatus=200;responseRange=false;online=false;
  assert.equal((await painting('painting-44.webp')).status,200,'visited public painting works offline');
  assert.equal((await painting('missing.webp')).status,503,'uncached painting remains unavailable offline');
  let activation;handlers.activate({waitUntil:p=>activation=p});await activation;
  assert.deepEqual(deleted.sort(),['lumen-art-old','lumen-media-v1','lumen-shell-old']);
  console.log('PASS private shell bypasses private content and preserves public visited paintings');
})().catch(error=>{console.error(error);process.exitCode=1;});
