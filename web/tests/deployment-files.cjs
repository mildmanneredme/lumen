/* Test the documented gitignore semantics before CLI deployment. */
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {execFileSync,spawnSync}=require('node:child_process');
const project=path.resolve(__dirname,'..'),fixture=fs.mkdtempSync(path.join(os.tmpdir(),'lumen-deploy-files-'));
const privateShell=fs.readFileSync(path.join(project,'dist/index.html'),'utf8').includes('legacy-pilot.js');
const allowed=['api/session.js','api/book.js','api/assets/[...path].js','server/private-access.cjs',
  'server/runtime.cjs','server/node-handler.cjs','dist/index.html','dist/app.js','dist/styles.css','dist/sw.js',
  'dist/icons/icon-192.png','dist/assets/opening-room.webp','package.json','package-lock.json','vercel.json'];
const blocked=['.env','.env.shared','.env.keys','server/.env','audit/index.html','art-direction/cast-bible.json',
  'data/chapter-001.json','assets/source.png','scripts/audit_server.py','tests/private-access.cjs','node_modules/example.js'];
const historical=['dist/data/chapter-001.js','dist/assets/chapter-001-pilot.mp3'];
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
  console.log('PASS deployment includes APIs and shell, excludes secrets/source, and preserves coherent pilot migration');
} finally {fs.rmSync(fixture,{recursive:true,force:true});}
