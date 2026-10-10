/* Private book responses must remain in the browser's native network stack. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const handlers={},cached=[],deleted=[];
const cache={put:async key=>cached.push(key),match:async()=>undefined};
class BrowserRequest extends Request {constructor(url,options){super(new URL(url,'https://lumen.example'),options);}}
const context={URL,Request:BrowserRequest,Response,setTimeout,clearTimeout,
  self:{location:{origin:'https://lumen.example'},addEventListener:(type,fn)=>handlers[type]=fn},
  caches:{open:async()=>cache,keys:async()=>['lumen-shell-old','lumen-art-v1','lumen-media-v1','other-app'],
    delete:async key=>deleted.push(key)},fetch:async()=>new Response('shell',{status:200})};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../dist/sw.js'),'utf8'),context);
(async()=>{
  let installation;handlers.install({waitUntil:p=>installation=p});await installation;
  assert.ok(cached.includes('/book.js') && cached.includes('/legacy-pilot.js'),'install the private reader modules');
  assert.ok(!cached.some(key=>key.includes('/data/') || key.includes('/api/')),'no prose in the shell cache');
  for(const [url,headers] of [['/api/book',{}],['/api/assets/release/audio.mp3',{}],['/api/assets/release/art.webp',{}],
    ['/assets/old-painting.webp',{}],['/app.js',{Range:'bytes=0-10'}]]) {
    let intercepted=false;
    handlers.fetch({request:new Request('https://lumen.example'+url,{headers}),respondWith:()=>intercepted=true,waitUntil:()=>{}});
    assert.equal(intercepted,false,'private assets, paintings and Range requests bypass caching: '+url);
  }
  let activation;handlers.activate({waitUntil:p=>activation=p});await activation;
  assert.deepEqual(deleted.sort(),['lumen-art-v1','lumen-media-v1','lumen-shell-old']);
  console.log('PASS private shell excludes book data and clears old content caches');
})().catch(error=>{console.error(error);process.exitCode=1;});
