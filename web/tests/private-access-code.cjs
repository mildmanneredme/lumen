'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {generateSharedAccess} = require('../scripts/shared_access_code.cjs');
const SCRIPT = path.resolve(__dirname, '../scripts/shared_access_code.cjs');
const WORDS = path.resolve(__dirname, '../scripts/data/eff-short-wordlist-1.txt');
const PIN = '8f5ca830b8bffb6fe39c9736c024a00a6a6411adb3f83a9be8bfeeb6e067ae69';
const NOW = 1_800_000_000, EXPIRES = NOW + 3600;
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');
const fixture = changes => ({expiresAt:EXPIRES, now:()=>NOW, randomInt:()=>0, ...changes});
const genericFailure = error => error instanceof Error && error.message === 'Invalid shared access code configuration';

test('official 1296-row dictionary remains intact and supplies 1295 unique original lowercase words', () => {
  const raw = fs.readFileSync(WORDS);assert.equal(sha256(raw),PIN);
  const rows = raw.toString('utf8').trimEnd().split('\n');assert.equal(rows.length,1296);
  const words = rows.map((line,index)=>{
    const [dice,word] = line.split('\t');
    assert.equal(dice,index.toString(6).padStart(4,'0').replace(/[0-5]/g,digit=>String(Number(digit)+1)));
    assert.match(word,/^(?:[a-z]{3,5}|yo-yo)$/);return word;
  }).filter(word=>/^[a-z]{3,5}$/.test(word));
  assert.equal(words.length,1295);assert.equal(new Set(words).size,1295);assert.equal(words[0],'acid');assert.equal(words.at(-1),'zoom');
  assert.equal(words.filter(word=>word==='yoyo').length,1);
  assert.equal(rows.filter(row=>row.endsWith('\tyo-yo')).length,1);
});

test('six bounded deterministic official choices produce canonical code and a hash-only invite', () => {
  const picks = [0,1294,1287,0,5,1], calls = [];
  const result = generateSharedAccess(fixture({id:'reader-test',version:3,randomInt:max=>{calls.push(max);return picks.shift();}}));
  assert.equal(result.code,'acid-zoom-yoyo-acid-affix-acorn');assert.deepEqual(calls,[1295,1295,1295,1295,1295,1295]);
  assert.deepEqual(result.invite,{id:'reader-test',tokenHash:sha256(result.code),version:3,expiresAt:EXPIRES,revoked:false});
  assert.equal(Object.hasOwn(result.invite,'code'),false);assert.equal(JSON.stringify(result.invite).includes(result.code),false);
});

test('defaults use crypto.randomInt six times and the real seconds clock without biasing repeated choices', () => {
  const originalRandom = crypto.randomInt, originalClock = Date.now, calls = [];
  crypto.randomInt = max => {calls.push(max);return 0;};Date.now = ()=>NOW*1000;
  try {
    const result=generateSharedAccess({expiresAt:EXPIRES});assert.equal(result.code,Array(6).fill('acid').join('-'));
    assert.deepEqual(calls,[1295,1295,1295,1295,1295,1295]);assert.equal(result.invite.id,'shared-code');assert.equal(result.invite.version,1);
  } finally {crypto.randomInt=originalRandom;Date.now=originalClock;}
});

test('invalid identity/version/explicit future expiry/clock settings fail generically before drawing words', () => {
  const invalid=[{id:''},{id:null},{id:'a'.repeat(65)},{id:'reader.name'},{id:' space'},
    {version:0},{version:-1},{version:1.5},{version:'1'},{version:Number.MAX_SAFE_INTEGER+1},
    {expiresAt:undefined},{expiresAt:NOW},{expiresAt:0},{expiresAt:-1},{expiresAt:NOW+.5},{expiresAt:'1900000000'},
    {expiresAt:Number.MAX_SAFE_INTEGER+1},{now:()=>NaN},{now:()=>-1},{now:()=>1.5},{now:null},
    {now:()=>{throw new Error('do not expose fixture details');}}];
  for(const changes of invalid) {
    let draws=0;
    assert.throws(()=>generateSharedAccess(fixture({...changes,randomInt:()=>{draws++;return 0;}})),genericFailure);
    assert.equal(draws,0);
  }
  for(const value of [null,[],true,'options'])assert.throws(()=>generateSharedAccess(value),genericFailure);
});

test('invalid RNG functions, indexes and exceptions fail generically', () => {
  for(const randomInt of [null,42,()=>-1,()=>1295,()=>1.5,()=>NaN,()=>Infinity,()=>'0',()=>undefined,
    ()=>{throw new Error('do not expose fixture details');},async()=>0])
    assert.throws(()=>generateSharedAccess(fixture({randomInt})),genericFailure);
});

test('a missing, altered, incomplete, duplicated or unpinned injected dictionary cannot generate a code', () => {
  const original=fs.readFileSync(WORDS),tampered=Buffer.from(original);tampered[5]^=1;
  for(const wordlistBytes of [null,'words',Buffer.alloc(0),tampered,original.subarray(0,original.length-10),
    Buffer.from(original.toString().replace('1112\tacorn','1112\tacid')),Buffer.alloc(64*1024+1)])
    assert.throws(()=>generateSharedAccess(fixture({wordlistBytes})),genericFailure);
  assert.equal(generateSharedAccess(fixture({wordlistBytes:original})).invite.tokenHash,sha256(Array(6).fill('acid').join('-')));
});

test('server-compatible boundary identities and versions remain valid', () => {
  for(const id of ['a','A'.repeat(64),'reader_1-shared']) {
    const result=generateSharedAccess(fixture({id,version:Number.MAX_SAFE_INTEGER,expiresAt:Number.MAX_SAFE_INTEGER}));
    assert.equal(result.invite.id,id);assert.equal(result.invite.version,Number.MAX_SAFE_INTEGER);
  }
});

test('an unavailable default dictionary fails generically before drawing words', () => {
  const original=fs.readFileSync;let draws=0;
  fs.readFileSync=()=>{throw new Error('fixture private path must not be exposed');};
  try {
    assert.throws(()=>generateSharedAccess(fixture({randomInt:()=>{draws++;return 0;}})),genericFailure);
    assert.equal(draws,0);
  } finally {fs.readFileSync=original;}
});

test('import and direct execution create no credentials, output or files', () => {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'lumen-shared-code-inert-'));
  try {
    const imported=spawnSync(process.execPath,['-e',`require('node:crypto').randomInt=()=>{throw Error('unexpected RNG');};require(${JSON.stringify(SCRIPT)});`],{cwd:directory,encoding:'utf8'});
    const direct=spawnSync(process.execPath,[SCRIPT],{cwd:directory,encoding:'utf8'});
    for(const result of [imported,direct]) {assert.equal(result.status,0);assert.equal(result.stdout,'');assert.equal(result.stderr,'');}
    assert.deepEqual(fs.readdirSync(directory),[]);
  } finally {fs.rmSync(directory,{recursive:true,force:true});}
});
