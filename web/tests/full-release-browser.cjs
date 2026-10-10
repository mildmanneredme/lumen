/* Operational check of the prepared private release, with existing MP3 bytes.
 * This creates only an ephemeral loopback fixture and private /tmp QA evidence.
 * It neither publishes files nor claims a full human listening/alignment review.
 */
'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const {parseArgs} = require('node:util');
const {create, validateManifest} = require('../dist/book.js');
const {parseRange} = require('../server/private-access.cjs');

const root = path.resolve(__dirname, '../..');
const dist = path.join(root, 'web/dist');
const {values} = parseArgs({options:{
  inventory:{type:'string'}, registry:{type:'string'}, out:{type:'string'},
  staging:{type:'string'}, recordings:{type:'string'}
}});
const staging = path.resolve(values.staging || path.join(root,
  'Audiobook/author-audit/web-release/staging/lumen-private-192-v4'));
const output = path.resolve(values.out || '/private/tmp/lumen-full-release-qa');
const tmpRoot = fs.realpathSync(os.tmpdir());
assert.ok(output.startsWith('/private/tmp/') || output.startsWith(tmpRoot + path.sep),
  'QA evidence must remain in a private temporary directory');
fs.mkdirSync(output, {recursive:true, mode:0o700});
const checks = [], errors = [], playback = [], requestedRanges = [];
const inputHashes = {}, sourceHashes = {};
const audioClocks = new Map();
let browser, server, origin, audioBytesServed = 0;
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const check = (name, condition, detail) => {
  checks.push({name, pass:!!condition, ...(detail === undefined ? {} : {detail})});
  assert.ok(condition, name); console.log('PASS ' + name);
};
function ownedFile(filename) {
  const real = fs.realpathSync(filename);
  assert.ok(real.startsWith(root + path.sep) && fs.statSync(real).isFile(),
    'Prepared asset must belong to the selected workspace');
  return real;
}
function readJSON(filename) {
  const real = ownedFile(filename), bytes = fs.readFileSync(real);
  inputHashes[path.relative(root, real)] = hash(bytes);
  return JSON.parse(bytes);
}
function inventoryPath() {
  if (values.inventory) return path.resolve(values.inventory);
  const directory = path.join(staging, 'tracks/chapter-000');
  const matches = fs.readdirSync(directory).filter(name => /^upload-inventory\.[a-f0-9]{64}\.json$/.test(name));
  assert.equal(matches.length, 1, 'Select one exact prepared upload inventory');
  return path.join(directory, matches[0]);
}
const privateKeys = new Set(['approvals', 'evidence', 'sourceBindings', 'sourcePath',
  'selectedClips', 'rawAudioSha256', 'requestSha256', 'cacheEvidence', 'evidenceIds',
  'recheckWindow', 'verificationReportPath', 'verificationReportSha256', 'authorDecisionSha256']);
function noPrivateFields(value) {
  if (!value || typeof value !== 'object') return;
  for (const [key, child] of Object.entries(value)) {
    assert.ok(!privateKeys.has(key), 'Private provenance leaked into a reader payload: ' + key);
    noPrivateFields(child);
  }
}
const shell = new Set(['/', '/index.html', '/styles.css', '/mobile.css', '/app.js',
  '/progress.js', '/book.js', '/legacy-pilot.js', '/install.js', '/sw.js', '/manifest.webmanifest',
  '/icons/icon.svg', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-512.png',
  '/icons/apple-touch-icon.png', '/icons/favicon-lumen-32-v2.png', '/icons/favicon-lumen-16-v2.png']);
const mime = {'.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.png':'image/png',
  '.svg':'image/svg+xml', '.webmanifest':'application/manifest+json'};
const cookieName = 'lumen_full_release_qa';
const sessionToken = crypto.randomBytes(32).toString('base64url');
const fixtureInvite = crypto.randomBytes(32).toString('base64url');
function authenticated(request) {
  const cookies = (request.headers.cookie || '').split(';').map(value => value.trim());
  const found = cookies.filter(value => value.startsWith(cookieName + '='));
  if (found.length !== 1) return false;
  const supplied = Buffer.from(found[0].slice(cookieName.length + 1));
  const expected = Buffer.from(sessionToken);
  return supplied.length === expected.length && crypto.timingSafeEqual(supplied, expected);
}
function respondJSON(response, status, value, extra = {}) {
  response.writeHead(status, {'Content-Type':'application/json', 'Cache-Control':'private, no-store',
    'Cross-Origin-Resource-Policy':'same-origin', 'Vary':'Cookie', ...extra});
  response.end(JSON.stringify(value));
}
function sendFile(request, response, asset) {
  const size = asset.bytes, requested = request.headers.range;
  const range = requested ? parseRange(requested, size) : null;
  const headers = {'Content-Type':asset.contentType, 'Cache-Control':'private, no-store',
    'Cross-Origin-Resource-Policy':'same-origin', 'Accept-Ranges':'bytes', 'Vary':'Cookie'};
  if (requested && !range) {
    response.writeHead(416, {...headers, 'Content-Range':'bytes */' + size}); response.end(); return;
  }
  const start = range?.start || 0, end = range?.end ?? size - 1;
  response.writeHead(range ? 206 : 200, {...headers, 'Content-Length':end - start + 1,
    ...(range ? {'Content-Range':`bytes ${start}-${end}/${size}`} : {})});
  if (request.method === 'HEAD') { response.end(); return; }
  if (asset.contentType === 'audio/mpeg') requestedRanges.push({path:asset.pathname, start, end, ranged:!!range});
  const stream = fs.createReadStream(asset.sourcePath, {start, end});
  if (asset.contentType === 'audio/mpeg') stream.on('data', bytes => audioBytesServed += bytes.length);
  stream.on('error', () => response.destroy());
  response.on('close', () => stream.destroy());
  stream.pipe(response);
}
async function startFixture(assetsByPath, manifestAsset) {
  server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, origin || 'http://127.0.0.1');
      if (request.headers.host !== new URL(origin).host ||
          (request.headers.origin && request.headers.origin !== origin)) {
        respondJSON(response, 403, {error:'Request rejected.'}); return;
      }
      if (url.pathname.startsWith('/api/')) {
        if (url.search) { respondJSON(response, 400, {error:'Request rejected.'}); return; }
        if (url.pathname === '/api/session') {
          if (request.method === 'GET') { respondJSON(response, 200, {authenticated:authenticated(request)}); return; }
          if (!['POST','DELETE'].includes(request.method) || request.headers.origin !== origin) {
            respondJSON(response, 403, {error:'Request rejected.'}); return;
          }
          if (request.method === 'DELETE') {
            respondJSON(response, 200, {authenticated:false},
              {'Set-Cookie':`${cookieName}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0`}); return;
          }
          const chunks = []; let count = 0;
          for await (const chunk of request) {
            count += chunk.length;
            if (count > 4096) { respondJSON(response, 413, {error:'Request rejected.'}); return; }
            chunks.push(chunk);
          }
          const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if (body.invite !== fixtureInvite) { respondJSON(response, 401, {error:'Invitation required.'}); return; }
          respondJSON(response, 200, {authenticated:true},
            {'Set-Cookie':`${cookieName}=${sessionToken}; Path=/; HttpOnly; SameSite=Strict`}); return;
        }
        if (!authenticated(request)) { respondJSON(response, 401, {error:'Invitation required.'}); return; }
        if (!['GET','HEAD'].includes(request.method)) { respondJSON(response, 405, {error:'Request rejected.'}); return; }
        const asset = url.pathname === '/api/book' ? manifestAsset : assetsByPath.get(url.pathname);
        if (!asset) { respondJSON(response, 404, {error:'Not found.'}); return; }
        sendFile(request, response, asset); return;
      }
      if (!['GET','HEAD'].includes(request.method) || !shell.has(url.pathname)) {
        respondJSON(response, 404, {error:'Not found.'}); return;
      }
      const file = ownedFile(path.join(dist, url.pathname === '/' ? 'index.html' : url.pathname.slice(1)));
      sendFile(request, response, {sourcePath:file, bytes:fs.statSync(file).size,
        contentType:mime[path.extname(file)] || 'application/octet-stream'});
    } catch (_) {
      if (!response.headersSent) respondJSON(response, 503, {error:'Fixture unavailable.'});
      else response.destroy();
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = 'http://127.0.0.1:' + server.address().port;
}

async function run() {
  const inventoryFile = inventoryPath(), inventory = readJSON(inventoryFile);
  assert.ok(['private','authenticated'].includes(inventory.accessModel));
  assert.equal(inventory.appOrigin, 'https://lumen-phi-five.vercel.app');
  const assetsByURL = new Map(), assetsByPath = new Map(), payloads = new Map();
  for (const asset of inventory.assets) {
    const url = new URL(asset.url), sourcePath = ownedFile(asset.sourcePath);
    assert.equal(url.origin, inventory.appOrigin); assert.ok(url.pathname.startsWith('/api/assets/'));
    assert.ok(!url.search && !url.hash && !assetsByURL.has(asset.url) && !assetsByPath.has(url.pathname));
    assert.equal(fs.statSync(sourcePath).size, asset.bytes);
    const entry = {...asset, sourcePath, pathname:url.pathname};
    if (entry.contentType === 'application/json') {
      const bytes = fs.readFileSync(sourcePath); assert.equal(hash(bytes), entry.sha256);
      inputHashes[path.relative(root, sourcePath)] = entry.sha256;
    }
    assetsByURL.set(asset.url, entry); assetsByPath.set(url.pathname, entry);
  }
  const manifestAsset = inventory.assets.find(asset => /\/book-manifest\.[a-f0-9]{64}\.json$/.test(asset.url));
  assert.ok(manifestAsset, 'Prepared manifest is present');
  const manifest = readJSON(manifestAsset.sourcePath); validateManifest(manifest); noPrivateFields(manifest);
  const recordings = readJSON(values.recordings || path.join(root, 'Audiobook/author-audit/web-release/release-inventory.json'));
  const byRecording = new Map(recordings.recordings.map(row => [row.trackId + '/' + row.narratorId, row]));
  const registry = values.registry ? readJSON(values.registry) : null;
  const byCanonical = registry && new Map(registry.tracks.map(track => [track.id, track]));
  const controller = create({manifest, storage:null, loadChapter:async url => JSON.parse(fs.readFileSync(assetsByURL.get(url).sourcePath))});
  let measured = 0, unavailable = 0;
  for (const track of manifest.tracks) for (const narrator of manifest.narrators) {
    const recording = track.recordings[narrator.id]; assert.equal(recording.status, 'ready');
    const loaded = await controller.load(track.id, narrator.id, {bookmark:null});
    const payload = loaded.chapter; noPrivateFields(payload); assert.equal(payload.schemaVersion, 2);
    assert.deepEqual(payload.scenes, [], 'Artwork remains a generic placeholder');
    const media = assetsByURL.get(payload.audio.src), prepared = byRecording.get(track.id + '/' + narrator.id);
    assert.ok(media && prepared); assert.equal(media.contentType, 'audio/mpeg');
    assert.equal(media.sha256, payload.audio.sha256); assert.equal(prepared.sha256, media.sha256);
    assert.equal(prepared.bitRate, 192000); assert.equal(prepared.bytes, media.bytes);
    assert.equal(ownedFile(path.join(root, prepared.sourcePath)), media.sourcePath);
    const clock = fs.statSync(media.sourcePath, {bigint:true});
    audioClocks.set(media.sourcePath, [clock.dev,clock.ino,clock.size,clock.mtimeNs,clock.ctimeNs]);
    assert.ok(Math.abs(prepared.decodedDuration - payload.duration) < .001);
    if (byCanonical) {
      const canonical = byCanonical.get(track.id); assert.ok(canonical);
      assert.deepEqual(payload.blocks, canonical.blocks);
      assert.deepEqual(payload.paragraphs.map(p => [p.id,p.text,p.markdown,p.emphasis,p.headingLevel]),
        canonical.paragraphs.map(p => [p.id,p.text,p.markdown,p.emphasis,p.headingLevel]));
    }
    for (const sentence of payload.paragraphs.flatMap(p => p.sentences)) {
      if (sentence.syncStatus === 'unavailable') { assert.equal(sentence.start, null); assert.equal(sentence.end, null); unavailable++; }
      else { assert.equal(sentence.syncStatus, 'measured'); measured++; }
    }
    payloads.set(track.id + '/' + narrator.id, payload);
  }
  check('all 91 tracks and 182 narrator payloads validate against the real reader', manifest.tracks.length === 91 && payloads.size === 182,
    {storyTracks:manifest.tracks.filter(t => t.kind === 'story').length, measuredSentenceRecords:measured, unavailableSentenceRecords:unavailable});
  check('every release recording references its prepared 192 kbps master', byRecording.size === 182);
  check('private provenance stays out of every reader payload and manuscript gaps stay explicit', true);
  if (registry) check('every paragraph and block matches the canonical registry', registry.manuscriptVersion === manifest.manuscriptVersion);
  check('female voice and generic artwork are the release defaults', manifest.defaultNarratorId === 'autonoe' &&
    manifest.narrators.find(n => n.id === 'autonoe').label === 'Female narrator');
  for (const filename of ['app.js','book.js','index.html','styles.css','mobile.css','sw.js'])
    sourceHashes['web/dist/' + filename] = hash(fs.readFileSync(path.join(dist, filename)));
  await startFixture(assetsByPath, assetsByURL.get(manifestAsset.url));
  for (const [url, method, headers] of [['/api/book','GET',{}], ['/api/assets/' + assetsByURL.get(manifestAsset.url).pathname.split('/api/assets/')[1],'HEAD',{}],
    [new URL(payloads.get('chapter-001/autonoe').audio.src).pathname,'GET',{Range:'bytes=0-1023'}]]) {
    const response = await fetch(origin + url, {method, headers});
    assert.equal(response.status, 401); await response.body?.cancel();
  }
  check('loopback fixture rejects guest book, asset metadata and audio ranges', true);
  for (const blocked of ['/data/chapter-001.js','/assets/chapter-001-pilot.mp3','/.env','/server/private-access.cjs']) {
    const response = await fetch(origin + blocked); assert.equal(response.status, 404); await response.body?.cancel();
  }
  check('public fixture serves only the empty shell and excludes historical prose/audio and secrets', true);
  let playwright;
  try { playwright = require('playwright'); }
  catch (_) { playwright = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
  browser = await playwright.chromium.launch({headless:true,
    executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  async function contextFor(viewport, mobile = false, authorized = true) {
    const context = await browser.newContext({viewport, isMobile:mobile, hasTouch:mobile, serviceWorkers:'block'});
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue();
      errors.push('Unexpected external request'); return route.abort();
    });
    if (authorized) await context.addCookies([{name:cookieName, value:sessionToken, url:origin, httpOnly:true, sameSite:'Strict'}]);
    const page = await context.newPage(); page.on('pageerror', error => errors.push(String(error)));
    return {context, page};
  }
  const guest = await contextFor({width:390,height:844}, true, false);
  await guest.page.goto(origin, {waitUntil:'domcontentloaded'});
  await guest.page.waitForFunction(() => document.querySelector('#access-status').textContent.includes('Use your invitation'));
  check('guest Chrome receives no private prose or audio source', await guest.page.evaluate(() =>
    !window.LUMEN_BOOK && !document.querySelector('#prose').textContent && !document.querySelector('#narration').getAttribute('src')));
  await guest.context.close();
  const {context, page} = await contextFor({width:390,height:844}, true);
  async function waitTrack(id, voice, at) {
    await page.waitForFunction(({id,voice,at}) => {
      const audio = document.querySelector('#narration');
      return window.LUMEN_CHAPTER?.chapterId === id && window.LUMEN_CHAPTER.audio.narratorId === voice &&
        audio.readyState >= 2 && !audio.seeking && document.querySelector('#seek-feedback').hidden &&
        (at == null || Math.abs(audio.currentTime-at) < .4);
    }, {id,voice,at}, {timeout:30000});
  }
  async function selectTrack(id) {
    await page.locator('#chapters-open').click(); await page.locator(`[data-track-id="${id}"]`).click();
    const voice = await page.locator('#narrator').inputValue(); await waitTrack(id, voice);
  }
  async function selectVoice(voice) {
    await page.locator('#settings-open').click(); await page.locator('#narrator').selectOption(voice);
    await page.locator('#settings-close').click();
  }
  async function selectVoiceFromBeginning(voice) {
    const current = await state();
    await page.locator('#seek').press('Home');
    await waitTrack(current.track, current.voice, 0);
    if (current.voice !== voice) await selectVoice(voice);
    await waitTrack(current.track, voice, 0);
  }
  const state = () => page.evaluate(() => {
    const audio = document.querySelector('#narration'); return {track:window.LUMEN_CHAPTER.chapterId,
      voice:window.LUMEN_CHAPTER.audio.narratorId, duration:audio.duration, time:audio.currentTime,
      paused:audio.paused, error:audio.error?.message};
  });
  await page.goto(origin, {waitUntil:'domcontentloaded'});
  await waitTrack(manifest.defaultTrackId, 'autonoe', 0);
  check('a new actual-release reader opens with the female voice and stays paused', (await state()).paused);
  check('all 91 chapters are available in the drawer', await page.locator('#chapter-list [data-track-id]').count() === 91);
  const story = manifest.tracks.filter(t => t.kind === 'story');
  const representatives = [manifest.tracks[0], story[0], story[Math.floor(story.length / 2)], story.at(-1), manifest.tracks.at(-1)];
  for (const track of representatives) for (const voice of ['autonoe','charon']) {
    if ((await state()).track !== track.id) await selectTrack(track.id);
    await selectVoiceFromBeginning(voice);
    await waitTrack(track.id, voice);
    const before = await state(), payload = payloads.get(track.id + '/' + voice);
    assert.ok(!before.error && Math.abs(before.duration-payload.duration) < .5,
      'Native MP3 metadata differs from the decoded release clock');
    assert.ok(await page.evaluate(() => window.LUMEN_CHAPTER.paragraphs.every(paragraph =>
      document.getElementById(paragraph.id)?.textContent === paragraph.text)),
      'Rendered representative paragraphs differ from faithful prepared prose');
    await page.locator('#play').click();
    await page.waitForFunction(at => {const audio=document.querySelector('#narration'); return !audio.paused && audio.currentTime > at + .5;}, before.time);
    await page.locator('#play').click(); const after = await state();
    playback.push({trackId:track.id,narratorId:voice,declaredDuration:payload.duration,nativeDuration:after.duration,
      observedPlayedSeconds:Number((after.time-before.time).toFixed(3)),pausedAfter:after.paused});
    check('native MP3 metadata and brief playback: ' + track.id + '/' + voice, after.paused && after.time > before.time);
    if (track.id === manifest.defaultTrackId && voice === 'autonoe') {
      const firstCue = payload.paragraphs.flatMap(p => p.sentences).find(s => s.syncStatus === 'measured');
      assert.ok(firstCue.start > .5, 'The actual opening needs a positive introduction interval');
      const introAt = firstCue.start / 2;
      await page.locator('#seek').evaluate((element, time) => {
        element.value = String(time); element.dispatchEvent(new Event('input',{bubbles:true}));
      }, introAt);
      await waitTrack(track.id, voice, introAt);
      await selectVoice('charon');
      await page.locator('#transition-retry').waitFor({state:'visible'});
      const preserved = await state();
      check('actual positive introduction refuses guessed voice mapping and preserves its source',
        preserved.voice === voice && Math.abs(preserved.time-introAt) < .4 && preserved.paused);
    }
    // Keep this diagnostic chapter history at an observed cue. A different
    // narrator cannot map the intentionally unanchored half-second playback
    // bookmark used above; that behavior has its own explicit assertion.
    const measuredStart = payload.paragraphs.flatMap(p => p.sentences).find(s => s.syncStatus === 'measured');
    await page.locator(`[id="${measuredStart.id}"]`).click();
    await waitTrack(track.id, voice, measuredStart.start);
  }
  await selectTrack(story[0].id); await selectVoiceFromBeginning('autonoe');
  const female = payloads.get(story[0].id + '/autonoe'), male = payloads.get(story[0].id + '/charon');
  const maleCues = new Map(male.paragraphs.flatMap(p=>p.sentences).map(s=>[s.id,s]));
  const shared = female.paragraphs.flatMap(p=>p.sentences).find(s => s.syncStatus === 'measured' && s.end-s.start > 3 &&
    maleCues.get(s.id)?.syncStatus === 'measured' && maleCues.get(s.id).end-maleCues.get(s.id).start > 3);
  assert.ok(shared, 'A measured common passage is needed for the voice-switch check');
  await page.locator(`[id="${shared.id}"]`).click(); await waitTrack(story[0].id, 'autonoe', shared.start);
  const sourceBookmark = await page.evaluate(() => window.LUMEN_BOOK.capture(document.querySelector('#narration').currentTime));
  const maleCue = maleCues.get(sourceBookmark.sentenceId);
  await selectVoice('charon');
  const mapped = maleCue.start + sourceBookmark.sentenceFraction*(maleCue.end-maleCue.start);
  await waitTrack(story[0].id, 'charon', mapped);
  check('paused voice change maps the measured canonical passage on actual recordings', (await state()).paused);
  await page.locator(`[id="${shared.id}"]`).click(); await waitTrack(story[0].id, 'charon', maleCue.start);
  await page.locator('#play').click();
  await page.waitForFunction(() => !document.querySelector('#narration').paused);
  await page.locator('#settings-open').click();
  const playingBookmark = await page.evaluate(() => window.LUMEN_BOOK.capture(document.querySelector('#narration').currentTime));
  await page.locator('#narrator').selectOption('autonoe'); await page.locator('#settings-close').click();
  await waitTrack(story[0].id, 'autonoe');
  await page.waitForFunction(() => !document.querySelector('#narration').paused);
  const femaleCue = female.paragraphs.flatMap(p=>p.sentences).find(s=>s.id === playingBookmark.sentenceId);
  const expectedPlaying = femaleCue.start + playingBookmark.sentenceFraction*(femaleCue.end-femaleCue.start);
  check('playing voice change retains listening intent and the measured passage',
    Math.abs((await state()).time-expectedPlaying) < 1.5);
  await page.locator('#play').click();
  const timeline = await page.locator('#seek').boundingBox();
  await page.touchscreen.tap(timeline.x+timeline.width*.55, timeline.y+timeline.height/2);
  await page.waitForFunction(duration => {const audio=document.querySelector('#narration');
    return !audio.seeking && document.querySelector('#seek-feedback').hidden && Math.abs(audio.currentTime/duration-.55) < .04;}, female.duration);
  check('native phone touch timeline seeks existing MP3 bytes while paused', (await state()).paused);
  const beforeForward = (await state()).time;
  await page.locator('#forward').tap(); await waitTrack(story[0].id, 'autonoe', beforeForward+15);
  check('forward fifteen seconds changes the actual MP3 position', (await state()).paused);
  await page.locator('#back').tap(); await waitTrack(story[0].id, 'autonoe', beforeForward);
  check('rewind fifteen seconds changes the actual MP3 position', (await state()).paused);
  const savedAt = (await state()).time; await page.reload({waitUntil:'domcontentloaded'});
  await waitTrack(story[0].id, 'autonoe', savedAt);
  check('reload resumes exact same-recording seconds and remains paused', (await state()).paused);
  await selectTrack(story[Math.floor(story.length/2)].id); const middle = await state();
  await page.reload({waitUntil:'domcontentloaded'}); await waitTrack(middle.track, middle.voice, middle.time);
  check('returning reader resumes the saved middle chapter instead of the book start', (await state()).paused);
  let gapCase;
  for (const payload of payloads.values()) {
    const sentences = payload.paragraphs.flatMap(p => p.sentences);
    for (let index = 0; index < sentences.length; index++) {
      if (sentences[index].syncStatus !== 'unavailable') continue;
      const before = sentences.slice(0,index).findLast(s => s.syncStatus === 'measured');
      const after = sentences.slice(index+1).find(s => s.syncStatus === 'measured');
      const start = before?.end || 0, end = after?.start ?? payload.duration;
      if (end-start > .5) { gapCase = {payload, sentence:sentences[index], time:(start+end)/2}; break; }
    }
    if (gapCase) break;
  }
  assert.ok(gapCase, 'The prepared partial-sync release needs one actual unavailable interval for this check');
  await selectTrack(gapCase.payload.chapterId);
  if ((await state()).voice !== gapCase.payload.audio.narratorId) await selectVoiceFromBeginning(gapCase.payload.audio.narratorId);
  await waitTrack(gapCase.payload.chapterId, gapCase.payload.audio.narratorId);
  const gapSpan = page.locator(`[id="${gapCase.sentence.id}"]`);
  check('actual unavailable sentence stays faithful prose without a seek control',
    await gapSpan.textContent() === gapCase.sentence.text && await gapSpan.getAttribute('role') === null);
  // This sets a known diagnostic interval through the reader's input handler.
  // Separate mouse/touch checks above and below exercise native range gestures.
  await page.locator('#seek').evaluate((element, time) => {
    element.value = String(time); element.dispatchEvent(new Event('input',{bubbles:true}));
  }, gapCase.time);
  await waitTrack(gapCase.payload.chapterId, gapCase.payload.audio.narratorId, gapCase.time);
  check('actual uncertain interval has no invented sentence highlight or semantic bookmark',
    await page.locator('.sentence.active').count() === 0 && await page.evaluate(() =>
      window.LUMEN_BOOK.capture(document.querySelector('#narration').currentTime).anchorMeasured === false));
  const gapState = await state();
  await selectVoice(gapState.voice === 'autonoe' ? 'charon' : 'autonoe');
  await page.locator('#transition-retry').waitFor({state:'visible'});
  const afterGapSwitch = await state();
  check('voice switch from actual uncertain audio preserves the source and asks for an explicit passage',
    afterGapSwitch.track === gapState.track && afterGapSwitch.voice === gapState.voice &&
    Math.abs(afterGapSwitch.time-gapState.time) < .4 && afterGapSwitch.paused);
  await selectTrack(manifest.tracks.at(-1).id);
  check('closing credits disable next-track navigation', await page.locator('#track-next').isDisabled());
  await page.locator('#track-prev').click(); await waitTrack(story.at(-1).id, (await state()).voice);
  check('previous from closing credits opens the final story chapter', (await state()).track === story.at(-1).id);
  await selectTrack(manifest.tracks[0].id);
  check('opening credits disable previous-track navigation', await page.locator('#track-prev').isDisabled());
  await page.locator('#track-next').click(); await waitTrack(story[0].id, (await state()).voice);
  check('next from opening credits opens the first story chapter', (await state()).track === story[0].id);
  await page.screenshot({path:path.join(output, 'phone-reader.png')});
  await context.close();
  for (const [name,width,height] of [['ipad',820,1180], ['desktop',1440,960]]) {
    const device = await contextFor({width,height}, name === 'ipad');
    await device.page.goto(origin + '/?chapter=' + story[Math.floor(story.length/2)].id + '&voice=autonoe', {waitUntil:'domcontentloaded'});
    await device.page.waitForFunction(() => window.LUMEN_CHAPTER && document.querySelector('#narration').readyState >= 1);
    const layout = await device.page.evaluate(() => ({width:innerWidth, body:document.documentElement.scrollWidth,
      play:document.querySelector('#play').getBoundingClientRect().toJSON(), seek:document.querySelector('#seek').getBoundingClientRect().toJSON()}));
    check(name + ' actual-release reader fits its viewport with usable controls',
      layout.body <= layout.width && layout.play.width >= 44 && layout.play.height >= 44 && layout.seek.width > 100);
    if (name === 'desktop') {
      await device.page.mouse.click(layout.seek.x+layout.seek.width*.35, layout.seek.y+layout.seek.height/2);
      await device.page.waitForFunction(() => {const audio=document.querySelector('#narration');
        return !audio.seeking && document.querySelector('#seek-feedback').hidden && Math.abs(audio.currentTime/window.LUMEN_CHAPTER.duration-.35) < .04;});
      check('native desktop mouse timeline seeks the actual middle-chapter MP3', true);
    }
    await device.page.screenshot({path:path.join(output, name + '-reader.png')}); await device.context.close();
  }
  check('native media requests used authenticated byte ranges', requestedRanges.some(r => r.ranged));
  check('no browser JavaScript errors or external network requests', errors.length === 0);
  for (const [filename,digest] of Object.entries(sourceHashes)) assert.equal(hash(fs.readFileSync(path.join(root,filename))), digest,
    'Reader sources changed during this operational check');
  for (const [filename,digest] of Object.entries(inputHashes)) assert.equal(hash(fs.readFileSync(path.join(root,filename))), digest,
    'Prepared JSON changed during this operational check');
  for (const [filename,expected] of audioClocks) {
    const clock = fs.statSync(filename, {bigint:true});
    assert.deepEqual([clock.dev,clock.ino,clock.size,clock.mtimeNs,clock.ctimeNs], expected,
      'Prepared MP3 source changed during this operational check');
  }
  return {releaseId:manifest.releaseId, manifestSha256:manifestAsset.sha256, recordings:payloads.size,
    measuredSentenceRecords:measured, unavailableSentenceRecords:unavailable,
    canonicalRegistryCompared:!!registry, audioHashesRechecked:false,
    verificationScope:'local-prepared-release-with-representative-native-playback',
    physicalDeviceTested:false, humanFullBookListeningApproval:'not-claimed',
    observedPlayedSeconds:Number(playback.reduce((sum,p)=>sum+p.observedPlayedSeconds,0).toFixed(3))};
}
let result = {};
run().then(value => result = value).catch(error => {
  errors.push(error.stack || String(error)); console.error(error.message); process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  if (server?.listening) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({schemaVersion:1, ...result,
    checks, errors, playback, requestedRanges, audioBytesServed, sourceHashes, inputHashes}, null, 2) + '\n', {mode:0o600});
});
