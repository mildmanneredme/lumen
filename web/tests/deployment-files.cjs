/* Keep Git deployment policy and explicit CLI file boundaries safe. */
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const test=require('node:test');
const http=require('node:http'),vm=require('node:vm');
const {getTransformedRoutes}=require('@vercel/routing-utils');
const {createNodeHandler}=require('../server/node-handler.cjs');
const {createCloudflareBridge}=require('../server/cloudflare-bridge.cjs');
const {execFileSync,spawnSync}=require('node:child_process');
const project=path.resolve(__dirname,'..');
const privateShell=fs.readFileSync(path.join(project,'dist/index.html'),'utf8').includes('legacy-pilot.js');
const allowed=['api/session.js','api/book.js','api/assets.js','server/private-access.cjs','server/cloudflare-bridge.cjs',
  'server/runtime.cjs','server/node-handler.cjs','dist/index.html','dist/app.js','dist/styles.css','dist/controls.css','dist/icons.js','dist/sw.js',
  'dist/icons/icon-192.png','dist/assets/opening-room.webp','package.json','package-lock.json','vercel.json'];
const blocked=['.env','.env.shared','.env.keys','server/.env','audit/index.html','art-direction/cast-bible.json',
  'data/chapter-001.json','assets/source.png','scripts/audit_server.py','tests/private-access.cjs','node_modules/example.js','cloudflare/worker.mjs'];
const historical=['dist/data/chapter-001.js','dist/assets/chapter-001-pilot.mp3'];
test('repository-root Git integration cannot automatically publish the project sources',()=>{
  const config=JSON.parse(fs.readFileSync(path.join(project,'../vercel.json'),'utf8'));
  assert.equal(config.git?.deploymentEnabled,false,'Every Git branch must require an explicit deployment');
  assert.deepEqual(Object.keys(config).sort(),['$schema','git'],'The repository-root guard must not configure a source build');
  assert.equal(config.$schema,'https://openapi.vercel.sh/vercel.json');
});
test('web-root Git integration stays disabled while preserving explicit CLI reader configuration',()=>{
  const config=JSON.parse(fs.readFileSync(path.join(project,'vercel.json'),'utf8'));
  assert.equal(config.git?.deploymentEnabled,false,'A future web root must still require an explicit deployment');
  const {git,rewrites,...cli}=config;
  assert.deepEqual(cli,{$schema:'https://openapi.vercel.sh/vercel.json',framework:null,buildCommand:null,outputDirectory:'dist',
    functions:{'api/**/*.js':{maxDuration:300}},headers:[
      {source:'/sw.js',headers:[{key:'Cache-Control',value:'public, max-age=0, must-revalidate'}]},
      {source:'/manifest.webmanifest',headers:[{key:'Content-Type',value:'application/manifest+json'},
        {key:'Cache-Control',value:'public, max-age=0, must-revalidate'}]}
    ]},'Manual reader deployment must preserve its API duration, static output and cache headers');
});
test('explicit CLI deployment includes APIs and shell and excludes private sources',()=>{
  const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'lumen-deploy-files-'));
  try {
  fs.writeFileSync(path.join(fixture,'.gitignore'),fs.readFileSync(path.join(project,'.vercelignore')));
  execFileSync('git',['init','--quiet'],{cwd:fixture});
  for(const filename of [...allowed,...blocked,...historical]) {
    const location=path.join(fixture,filename);fs.mkdirSync(path.dirname(location),{recursive:true});fs.writeFileSync(location,'fixture');
  }
  const excluded=filename=>{const r=spawnSync('git',['-c','core.excludesFile=/dev/null','check-ignore','--no-index','--quiet','--',filename],{cwd:fixture});assert.ok([0,1].includes(r.status));return r.status===0;};
  for(const filename of allowed) assert.equal(excluded(filename),false,'Required deployed resource excluded: '+filename);
  for(const filename of blocked) assert.equal(excluded(filename),true,'Private/source file exposed: '+filename);
  for(const filename of historical) assert.equal(excluded(filename),privateShell,'Pilot removal must match private shell migration: '+filename);
  } finally {fs.rmSync(fixture,{recursive:true,force:true});}
});


// Compile the deployment configuration with Vercel's actual route utility.
// Standalone Node functions do not implement Next.js [...path] catch-all routing.
const appOrigin='https://lumen-fixture.example',workerOrigin='https://media-fixture.example';
const immutableReader='/api/assets/lumen-private-192-v4/tracks/chapter-000/reader-autonoe.'+'a'.repeat(64)+'.json';
const immutableAudio='/api/assets/lumen-private-192-v4/audio/charon/chapter-001.'+'b'.repeat(64)+'.mp3';
const immutablePainting='/api/assets/lumen-private-192-v4/scenes/scene-001.'+'c'.repeat(64)+'.webp';
function compiledAssetRoute(url) {
  const config=JSON.parse(fs.readFileSync(path.join(project,'vercel.json'),'utf8'));
  const result=getTransformedRoutes({rewrites:config.rewrites});
  assert.equal(result.error,null,'Vercel must accept the configured routing syntax');
  return (result.routes||[]).find(route=>route.src&&new RegExp(route.src).test(new URL(url,appOrigin).pathname));
}
test('standalone immutable asset paths reach one plain function without injected capture queries',()=>{
  for(const requestPath of [immutableReader,immutableAudio,immutablePainting]) {
    const route=compiledAssetRoute(requestPath);
    assert.ok(route,'A deep immutable asset URL must reach the function instead of Vercel 404: '+requestPath);
    assert.equal(route.dest,'/api/assets','A rewrite capture must not inject query parameters rejected by the private bridge');
    assert.equal(route.check,true,'The rewrite must resolve a deployed same-project function');
  }
  for(const requestPath of ['/','/styles.css','/api/book','/api/session','/api/assets-extra/a','/assets/public.webp'])
    assert.equal(compiledAssetRoute(requestPath),undefined,'The asset rewrite must not intercept another route: '+requestPath);
  assert.ok(fs.statSync(path.join(project,'api/assets.js')).isFile());
  assert.equal(fs.existsSync(path.join(project,'api/assets/[...path].js')),false,'Do not depend on unsupported standalone catch-all names');
});
function invokePlainEntrypoint(reader) {
  const file=path.join(project,'api/assets.js'),module={exports:{}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{module,require:specifier=>{
    assert.equal(specifier,'../server/node-handler.cjs');
    return {createNodeHandler:()=>createNodeHandler({env:{LUMEN_APP_ORIGIN:appOrigin},loadReader:async()=>reader})};
  }},{filename:file});
  assert.equal(typeof module.exports,'function');return module.exports;
}
function localRequest(server,requestPath,method='GET',headers={}) {
  return new Promise((resolve,reject)=>{
    const request=http.request({hostname:'127.0.0.1',port:server.address().port,path:requestPath,method,
      headers:{Host:new URL(appOrigin).host,Cookie:'fixture-device-session',...headers}},response=>{
      const chunks=[];response.on('data',chunk=>chunks.push(chunk));response.on('error',reject);
      response.on('end',()=>resolve({status:response.statusCode,headers:response.headers,body:Buffer.concat(chunks)}));
    });request.on('error',reject);request.end();
  });
}
test('compiled deep GET/HEAD routes preserve the original URL through the actual Node handler and private bridge',async()=>{
  const upstream=[],json=Buffer.from('{"fixture":"reader-json"}');
  const reader=createCloudflareBridge({env:{LUMEN_APP_ORIGIN:appOrigin,LUMEN_WORKER_ORIGIN:workerOrigin,
    LUMEN_BRIDGE_KEY:Buffer.alloc(32,8).toString('base64url')},fetch:async(url,init)=>{
    assert.equal(url,workerOrigin+'/bridge');
    const envelope=JSON.parse(init.method==='HEAD'?Buffer.from(init.headers['X-Lumen-Request'],'base64url').toString():init.body);
    upstream.push(envelope);
    if(/\.(mp3|webp)$/.test(new URL(envelope.url).pathname))
      return new Response(null,{status:307,headers:{Location:workerOrigin+'/media/'+new URL(envelope.url).pathname.slice('/api/assets/'.length)+'?grant=fixture-only',
        'Cache-Control':'private, no-store'}});
    return new Response(envelope.method==='HEAD'?null:json,{status:200,headers:{'Content-Type':'application/json',
      'Content-Length':String(json.length),'Cache-Control':'private, no-store','Cross-Origin-Resource-Policy':'same-origin'}});
  }});
  let entrypoint;const server=http.createServer(async(request,response)=>{
    const route=compiledAssetRoute(request.url);
    if(!route){response.writeHead(404,{'Content-Type':'text/plain'});response.end('Route not found');return;}
    assert.equal(route.dest,'/api/assets');
    // The fixture selects the function without rewriting the incoming URL.
    // Hosted preview acceptance remains the gate for Vercel's actual URL delivery.
    entrypoint ||= invokePlainEntrypoint(reader);await entrypoint(request,response);
  });
  try {
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    let response=await localRequest(server,immutableReader);
    assert.equal(response.status,200,'Deep reader JSON must reach the handler rather than a platform 404');
    assert.ok(response.body.equals(json));assert.equal(upstream.at(-1).url,appOrigin+immutableReader);
    assert.equal(upstream.at(-1).method,'GET');assert.equal(upstream.at(-1).headers.cookie,'fixture-device-session');
    response=await localRequest(server,immutableReader,'HEAD');assert.equal(response.status,200);
    assert.equal(response.body.length,0);assert.equal(response.headers['content-length'],String(json.length));
    assert.equal(upstream.at(-1).url,appOrigin+immutableReader);assert.equal(upstream.at(-1).method,'HEAD');
    response=await localRequest(server,immutableAudio,'GET',{Range:'bytes=1-4','If-Range':'"fixture-etag"'});
    assert.equal(response.status,307);assert.equal(response.body.length,0);
    assert.equal(upstream.at(-1).url,appOrigin+immutableAudio);assert.equal(upstream.at(-1).headers.range,'bytes=1-4');
    assert.equal(upstream.at(-1).headers['if-range'],'"fixture-etag"');
    response=await localRequest(server,immutableAudio,'HEAD');assert.equal(response.status,307);assert.equal(response.body.length,0);
    assert.equal(upstream.at(-1).method,'HEAD');assert.equal(upstream.at(-1).url,appOrigin+immutableAudio);
    response=await localRequest(server,immutablePainting);assert.equal(response.status,307);assert.equal(response.body.length,0);
    let before=upstream.length;
    response=await localRequest(server,immutableReader+'?path=untrusted');assert.equal(response.status,400);
    assert.equal(upstream.length,before,'Original query strings must be preserved and rejected before reaching Cloudflare');
    response=await localRequest(server,immutableReader,'GET',{Host:'foreign.example'});assert.equal(response.status,403);
    assert.equal(upstream.length,before,'Rewriting must not turn a forged Host into a trusted request origin');
    response=await localRequest(server,immutableReader,'GET',{Origin:'https://foreign.example'});assert.equal(response.status,403);
    assert.equal(upstream.length,before,'The route must retain the private bridge Origin check');
    response=await localRequest(server,immutableReader,'GET',{'x-vercel-original-path':'/api/book'});assert.equal(response.status,200);
    assert.equal(upstream.at(-1).url,appOrigin+immutableReader,'Client headers must never replace the original immutable path');
  } finally {await new Promise(resolve=>server.close(resolve));}
});
