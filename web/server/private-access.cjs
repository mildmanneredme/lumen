'use strict';
// Server only. Reader assets and the invite registry never enter the static bundle.
const crypto = require('node:crypto');
const SESSION_COOKIE = '__Host-lumen_session';
const SESSION_SECONDS = 30 * 86400;
const BODY_LIMIT = 4096;
const JSON_LIMIT = 2 * 1024 * 1024;
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const isHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const object = value => value && typeof value === 'object' && !Array.isArray(value);
function requireValue(condition) { if (!condition) throw new Error('Invalid private reader configuration'); }
function equal(left, right) {
  const a = Buffer.from(left), b = Buffer.from(right);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
function validPath(value) {
  return typeof value === 'string' && value.length <= 500 &&
    value.split('/').every(part => /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(part) && part !== '.' && part !== '..');
}
function appOrigin(value) {
  requireValue(typeof value === 'string');
  const url = new URL(value);
  requireValue(url.protocol === 'https:' && value === url.origin && !url.username && !url.password);
  return value;
}
function trustedRequestOrigin(env) {
  const canonical = appOrigin(env.LUMEN_APP_ORIGIN);
  if (env.VERCEL_ENV !== 'preview') return canonical;
  // Vercel supplies this server-side hostname. Never derive trust from a
  // request Host/X-Forwarded-Host header or accept a URL/path in this setting.
  const host = env.VERCEL_URL;
  requireValue(typeof host === 'string' && host.length <= 253 && host.endsWith('.vercel.app') &&
    host.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)));
  return appOrigin('https://' + host);
}
function readConfig(env) {
  const origin = appOrigin(env.LUMEN_APP_ORIGIN);
  const requestOrigin = trustedRequestOrigin(env);
  const value = env.LUMEN_SESSION_SECRET;
  requireValue(typeof value === 'string' && /^[a-zA-Z0-9_-]{43,86}$/.test(value));
  const secret = Buffer.from(value, 'base64url');
  requireValue(secret.length >= 32 && secret.length <= 64 && secret.toString('base64url') === value);
  const invites = JSON.parse(env.LUMEN_INVITES_JSON);
  requireValue(Array.isArray(invites) && invites.length <= 50);
  const ids = new Set(), hashes = new Set();
  for (const invite of invites) {
    requireValue(object(invite) && typeof invite.id === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(invite.id) &&
      !ids.has(invite.id) && isHash(invite.tokenHash) && !hashes.has(invite.tokenHash) &&
      Number.isSafeInteger(invite.version) && invite.version > 0 && Number.isSafeInteger(invite.expiresAt) &&
      invite.expiresAt > 0 && typeof invite.revoked === 'boolean');
    ids.add(invite.id); hashes.add(invite.tokenHash);
  }
  return {origin, requestOrigin, secret, invites};
}
function sign(payload, secret) { return crypto.createHmac('sha256', secret).update(payload).digest('base64url'); }
function signedSession(invite, config, now) {
  const claims = {i:invite.id, v:invite.version, exp:Math.min(now + SESSION_SECONDS, invite.expiresAt)};
  const payload = Buffer.from(JSON.stringify(claims)).toString('base64url');
  return {value:payload + '.' + sign(payload, config.secret), expires:claims.exp};
}
function authorize(request, config, now) {
  const cookies = (request.headers.get('cookie') || '').split(';').map(value => value.trim());
  const matching = cookies.filter(value => value.startsWith(SESSION_COOKIE + '='));
  if (matching.length !== 1) return false;
  const cookie = matching[0].slice(SESSION_COOKIE.length + 1);
  if (cookie.length > 1024 || !/^[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]{43}$/.test(cookie)) return false;
  const [payload, signature] = cookie.split('.');
  if (!equal(signature, sign(payload, config.secret))) return false;
  try {
    const claims = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
    return object(claims) && Number.isSafeInteger(claims.exp) && claims.exp > now &&
      claims.exp <= now + SESSION_SECONDS && config.invites.some(invite => invite.id === claims.i &&
        invite.version === claims.v && !invite.revoked && invite.expiresAt >= claims.exp && invite.expiresAt > now);
  } catch (_) { return false; }
}
function headers(extra = {}) {
  return new Headers({'Cache-Control':'private, no-store','Vary':'Cookie',
    'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
    'Cross-Origin-Resource-Policy':'same-origin','X-Robots-Tag':'noindex, nofollow, noarchive',...extra});
}
function json(value, status = 200, extra = {}) {
  return new Response(JSON.stringify(value), {status, headers:headers({'Content-Type':'application/json; charset=utf-8',...extra})});
}
function failure(status) {
  return json({error: status === 401 ? 'An invitation is required.' : status === 503 ?
    'The private reader is temporarily unavailable.' : status === 404 ? 'Not found.' : 'Request rejected.'}, status);
}
function sameOrigin(request, config, writing = false) {
  const supplied = request.headers.get('origin');
  const site = request.headers.get('sec-fetch-site');
  const host = request.headers.get('host');
  const trusted = config.requestOrigin;
  return new URL(request.url).origin === trusted &&
    (!host || host.toLowerCase() === new URL(trusted).host) &&
    (!supplied || supplied === trusted) && (!writing || supplied === trusted) &&
    (!site || ['same-origin','none'].includes(site));
}
async function boundedBytes(stream, limit) {
  requireValue(stream);
  const reader = stream.getReader(), chunks = [];
  let bytes = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      bytes += part.value.byteLength;
      if (bytes > limit) { await reader.cancel(); throw new Error('Body limit'); }
      chunks.push(Buffer.from(part.value));
    }
    return Buffer.concat(chunks, bytes);
  } finally { reader.releaseLock(); }
}
function parseRange(value, size) {
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2])) return null;
  const first = match[1] ? Number(match[1]) : null, last = match[2] ? Number(match[2]) : null;
  if ((first !== null && !Number.isSafeInteger(first)) || (last !== null && !Number.isSafeInteger(last))) return null;
  if (first === null) return last > 0 ? {start:Math.max(0,size-last),end:size-1} : null;
  if (first >= size || (last !== null && last < first)) return null;
  return {start:first,end:last === null ? size-1 : Math.min(last,size-1)};
}
function validateIndex(index, origin) {
  requireValue(object(index) && index.schemaVersion === 1 &&
    typeof index.releaseId === 'string' && /^[a-z][a-z0-9-]*$/.test(index.releaseId) && index.appOrigin === origin &&
    validPath(index.manifestPath) && Array.isArray(index.assets) && index.assets.length > 0 && index.assets.length <= 5000);
  const map = new Map(), blobs = new Set();
  for (const asset of index.assets) {
    requireValue(object(asset) && validPath(asset.path) && validPath(asset.blobPath) &&
      isHash(asset.sha256) && Number.isSafeInteger(asset.bytes) && asset.bytes > 0 &&
      ['application/json','audio/mpeg','image/webp'].includes(asset.contentType) &&
      asset.path.includes(asset.sha256 + '.') && !map.has(asset.path) && !blobs.has(asset.blobPath));
    requireValue((asset.contentType === 'application/json' && asset.path.endsWith('.json') && asset.bytes <= JSON_LIMIT) ||
      (asset.contentType === 'audio/mpeg' && asset.path.endsWith('.mp3')) ||
      (asset.contentType === 'image/webp' && asset.path.endsWith('.webp')));
    map.set(asset.path, asset); blobs.add(asset.blobPath);
  }
  requireValue(map.get(index.manifestPath)?.contentType === 'application/json');
  return map;
}
function buildServerIndex(inventory, receipts, manifestURL) {
  requireValue(object(inventory) && inventory.schemaVersion === 1 &&
    ['private','authenticated'].includes(inventory.accessModel) && Array.isArray(inventory.assets) &&
    Array.isArray(receipts) && receipts.length === inventory.assets.length);
  const origin = appOrigin(inventory.appOrigin), prefix = origin + '/api/assets/';
  requireValue(Array.isArray(inventory.mediaOrigins) && inventory.mediaOrigins.every(value => value === origin));
  requireValue(typeof manifestURL === 'string' && manifestURL.startsWith(prefix));
  const byURL = new Map();
  for (const receipt of receipts) {
    requireValue(object(receipt) && typeof receipt.url === 'string' && !byURL.has(receipt.url) && receipt.access === 'private');
    byURL.set(receipt.url, receipt);
  }
  const assets = inventory.assets.map(asset => {
    requireValue(object(asset) && typeof asset.url === 'string' && asset.url.startsWith(prefix) && asset.immutable === true);
    const receipt = byURL.get(asset.url);
    requireValue(receipt && ['sha256','bytes','contentType'].every(key => receipt[key] === asset[key]));
    return {path:asset.url.slice(prefix.length),blobPath:receipt.blobPath,sha256:asset.sha256,bytes:asset.bytes,contentType:asset.contentType};
  });
  const index = {schemaVersion:1,releaseId:inventory.releaseId,appOrigin:origin,manifestPath:manifestURL.slice(prefix.length),assets};
  validateIndex(index,origin);
  return index;
}
function exactStream(stream, expected) {
  let count = 0;
  return stream.pipeThrough(new TransformStream({
    transform(chunk, controller) {
      count += chunk.byteLength;
      if (count > expected) throw new Error('Private asset length mismatch');
      controller.enqueue(chunk);
    },
    flush() { if (count !== expected) throw new Error('Private asset length mismatch'); }
  }));
}
function createPrivateReader({env = process.env, now = Date.now, loadIndex, getBlob}) {
  requireValue(typeof loadIndex === 'function' && typeof getBlob === 'function');
  return async function privateReader(request) {
    let config;
    try { config = readConfig(env); } catch (_) { return failure(503); }
    const url = new URL(request.url), method = request.method;
    const session = url.pathname === '/api/session';
    const resource = url.pathname === '/api/book' || url.pathname.startsWith('/api/assets/');
    if (!session && !resource) return failure(404);
    if (url.search) return failure(400);
    if (!(session ? ['GET','POST','DELETE'] : ['GET','HEAD']).includes(method)) return json({error:'Method not allowed.'},405,{Allow:session?'GET, POST, DELETE':'GET, HEAD'});
    if (!sameOrigin(request,config,session && method !== 'GET')) return failure(403);
    const clock = Math.floor(now()/1000);
    if (session) {
      if (method === 'GET') return json({authenticated:authorize(request,config,clock)});
      if (method === 'DELETE') return json({authenticated:false},200,{'Set-Cookie':`${SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`});
      if (!/^application\/json(?:;|$)/i.test(request.headers.get('content-type') || '')) return failure(400);
      const contentLength = request.headers.get('content-length');
      if (contentLength && (!/^\d+$/.test(contentLength) || Number(contentLength)>BODY_LIMIT)) return failure(413);
      let body;
      try { body=JSON.parse((await boundedBytes(request.body,BODY_LIMIT)).toString('utf8')); }
      catch (_) { return failure(contentLength && Number(contentLength)>BODY_LIMIT ? 413 : 400); }
      if (!object(body) || typeof body.invite !== 'string' || !/^[a-zA-Z0-9_-]{43}$/.test(body.invite) ||
        Buffer.from(body.invite,'base64url').toString('base64url') !== body.invite) return failure(400);
      const hash = sha256(body.invite);
      const invite = config.invites.find(value => equal(value.tokenHash,hash) && !value.revoked && value.expiresAt > clock);
      if (!invite) return failure(401);
      const signed = signedSession(invite,config,clock);
      return json({authenticated:true},200,{'Set-Cookie':`${SESSION_COOKIE}=${signed.value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${signed.expires-clock}`});
    }
    if (!authorize(request,config,clock)) return failure(401);
    try {
      const index = await loadIndex(request.signal);
      const assets = validateIndex(index,config.origin);
      const path = url.pathname === '/api/book' ? index.manifestPath : url.pathname.slice('/api/assets/'.length);
      if (!validPath(path)) return failure(400);
      const asset = assets.get(path);
      if (!asset) return failure(404);
      const etag = `"sha256-${asset.sha256}"`;
      const responseHeaders = headers({'Content-Type':asset.contentType,'Content-Length':String(asset.bytes),'Accept-Ranges':'bytes','ETag':etag});
      if ((request.headers.get('if-none-match') || '').split(',').map(value=>value.trim()).some(value=>value === etag || value === 'W/'+etag || value === '*')) {
        responseHeaders.delete('Content-Length');
        return new Response(null,{status:304,headers:responseHeaders});
      }
      if (method === 'HEAD') return new Response(null,{status:200,headers:responseHeaders});
      const requestedRange = request.headers.get('range');
      const ifRange = request.headers.get('if-range');
      const range = requestedRange && (!ifRange || ifRange === etag) ? parseRange(requestedRange,asset.bytes) : null;
      if (requestedRange && (!ifRange || ifRange === etag) && !range) {
        return json({error:'Range not satisfiable.'},416,{'Content-Range':`bytes */${asset.bytes}`,'Accept-Ranges':'bytes'});
      }
      const count = range ? range.end-range.start+1 : asset.bytes;
      const result = await getBlob(asset.blobPath,{access:'private',headers:{'Accept-Encoding':'identity',...(range?{Range:`bytes=${range.start}-${range.end}`}:{})},abortSignal:request.signal});
      // @vercel/blob 2.8.1 labels successful ranged fetches 200; raw headers retain Content-Range.
      const matches=result && result.statusCode === 200 && result.stream &&
        result.headers.get('content-length') === String(count) &&
        (!result.headers.get('content-encoding') || result.headers.get('content-encoding') === 'identity') &&
        (range ? result.headers.get('content-range') === `bytes ${range.start}-${range.end}/${asset.bytes}` : !result.headers.get('content-range'));
      if(!matches) {
        if(result?.stream) await result.stream.cancel();
        throw new Error('Private asset metadata differs');
      }
      let stream = exactStream(result.stream,count);
      if (!range && asset.contentType === 'application/json') {
        const bytes = await boundedBytes(stream,JSON_LIMIT);
        requireValue(sha256(bytes) === asset.sha256);
        stream = bytes;
      }
      if (range) {
        responseHeaders.set('Content-Range',`bytes ${range.start}-${range.end}/${asset.bytes}`);
        responseHeaders.set('Content-Length',String(count));
      }
      return new Response(stream,{status:range?206:200,headers:responseHeaders});
    } catch (_) { return failure(503); }
  };
}
module.exports={createPrivateReader,buildServerIndex,parseRange,validateIndex,sha256,boundedBytes,trustedRequestOrigin};
