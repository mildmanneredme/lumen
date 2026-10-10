'use strict';
// Operator library only: importing or directly running this file is inert.
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const WORDLIST = path.join(__dirname, 'data/eff-short-wordlist-1.txt');
const WORDLIST_SHA256 = '8f5ca830b8bffb6fe39c9736c024a00a6a6411adb3f83a9be8bfeeb6e067ae69';
const invalid = () => new Error('Invalid shared access code configuration');
const requireValue = condition => { if (!condition) throw invalid(); };
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');

function wordsFrom(raw) {
  requireValue(Buffer.isBuffer(raw) && raw.length === 13660 && sha256(raw) === WORDLIST_SHA256);
  const rows = raw.toString('utf8').trimEnd().split('\n');
  requireValue(rows.length === 1296);
  const words = [];
  for (let index = 0; index < rows.length; index++) {
    const match = /^([1-6]{4})\t([a-z]{3,5}|yo-yo)$/.exec(rows[index]);
    const dice = index.toString(6).padStart(4, '0').replace(/[0-5]/g, digit => String(Number(digit) + 1));
    requireValue(match && match[1] === dice);
    // Keep original yoyo; omit yo-yo so every choice is one unambiguous word.
    if (match[2] !== 'yo-yo') words.push(match[2]);
  }
  requireValue(words.length === 1295 && new Set(words).size === 1295);
  return words;
}

function generateSharedAccess(options = {}) {
  try {
    requireValue(options !== null && typeof options === 'object' && !Array.isArray(options));
    // Expiry and clock use Unix seconds, matching the private-reader registry.
    // RNG, clock and wordlistBytes injection are for deterministic fixtures only.
    const {id = 'shared-code', version = 1, expiresAt} = options;
    const now = options.now === undefined ? () => Math.floor(Date.now() / 1000) : options.now;
    const randomInt = options.randomInt === undefined ? crypto.randomInt : options.randomInt;
    requireValue(typeof id === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(id) &&
      Number.isSafeInteger(version) && version > 0 && typeof now === 'function' && typeof randomInt === 'function');
    const current = now();
    requireValue(Number.isSafeInteger(current) && current > 0 && Number.isSafeInteger(expiresAt) && expiresAt > current);
    const raw = options.wordlistBytes === undefined ? fs.readFileSync(WORDLIST) : options.wordlistBytes;
    const words = wordsFrom(raw), selected = [];
    for (let index = 0; index < 6; index++) {
      const choice = randomInt(words.length);
      requireValue(Number.isSafeInteger(choice) && choice >= 0 && choice < words.length);
      selected.push(words[choice]); // Repeated independent choices remain valid.
    }
    const code = selected.join('-');
    return {code, invite: {id, tokenHash: sha256(code), version, expiresAt, revoked: false}};
  } catch (_) {
    throw invalid();
  }
}
module.exports = {generateSharedAccess};
