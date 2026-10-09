const test = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../dist/progress.js');

const KEY = 'lumen-reader-v1';
const PREFS = 'lumen-reader-preferences-v1';
const chapter = {
  id: 'chapter-001-pilot', chapterId: 'chapter-001', bookId: 'lumen', manuscriptVersion: 'v6',
  duration: 50, audio: { sha256: 'a'.repeat(64), narratorId: 'charon' }
};
const sentences = [
  { id: 'p001-s01', start: 5, end: 15 },
  { id: 'p001-s02', start: 20, end: 40 }
];
function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), values };
}
function recording(overrides = {}) {
  return { ...chapter, ...overrides, audio: { ...chapter.audio, ...overrides.audio } };
}

test('reload resumes the exact media clock, including silence, on the same recording', () => {
  const storage = memoryStorage();
  const first = create(chapter, sentences, storage);
  const saved = first.save(17.25);
  assert.equal(saved.sentenceId, 'p001-s01');
  assert.equal(saved.sentenceFraction, 1);
  const reload = create(chapter, sentences, storage);
  assert.deepEqual(reload.resolve(reload.read()), { time: 17.25, completed: false, mapped: false });
  assert.equal(saved.bookId, 'lumen');
  assert.equal(saved.manuscriptVersion, 'v6');
  assert.equal(saved.readingExtentId, 'chapter-001-pilot');
  assert.equal(saved.narratorId, 'charon');
  assert.ok(!Number.isNaN(Date.parse(saved.updatedAt)));
});

test('another narrator maps the canonical sentence fraction instead of old seconds', () => {
  const storage = memoryStorage();
  create(chapter, sentences, storage).save(25);
  const female = create(recording({ audio: { sha256: 'b'.repeat(64), narratorId: 'autonoe' } }), [
    { id: 'p001-s01', start: 2, end: 8 },
    { id: 'p001-s02', start: 12, end: 36 }
  ], storage);
  assert.deepEqual(female.resolve(female.read()), { time: 18, completed: false, mapped: true });
  assert.equal(female.save(18).narratorId, 'autonoe');
});

test('other chapters, manuscripts, and books are rejected without destroying progress', () => {
  const storage = memoryStorage();
  const original = create(chapter, sentences, storage).save(25);
  const serialized = storage.getItem(KEY);
  for (const overrides of [
    { chapterId: 'chapter-002' }, { manuscriptVersion: 'v7' }, { bookId: 'another-book' }
  ]) {
    const reader = create(recording(overrides), sentences, storage);
    assert.deepEqual(reader.read(), original);
    assert.equal(reader.resolve(reader.read()), null);
    assert.equal(storage.getItem(KEY), serialized);
  }
});

test('completion keeps the end position when the recording is unchanged', () => {
  const storage = memoryStorage();
  const reader = create(chapter, sentences, storage);
  const saved = reader.save(50, { completed: true });
  assert.equal(saved.audioTime, 50);
  assert.equal(saved.completed, true);
  assert.deepEqual(reader.resolve(saved), { time: 50, completed: true, mapped: false });
  assert.equal(reader.save(20).completed, false);
});

test('a finished pilot maps to its last sentence in a full chapter, never the new ending', () => {
  const storage = memoryStorage();
  create(chapter, sentences, storage).save(50, { completed: true });
  const full = create(recording({ id: 'chapter-001', duration: 300, audio: { sha256: 'c'.repeat(64) } }), [
    { id: 'p001-s01', start: 7, end: 19 }, { id: 'p001-s02', start: 23, end: 47 },
    { id: 'p002-s01', start: 49, end: 70 }
  ], storage);
  assert.deepEqual(full.resolve(full.read()), { time: 47, completed: false, mapped: true });
});

test('a missing canonical sentence cannot fall back to unrelated seconds in a changed recording', () => {
  const storage = memoryStorage();
  const old = create(chapter, sentences, storage).save(25);
  const changed = create(recording({ audio: { sha256: 'b'.repeat(64) } }), [sentences[0]], storage);
  assert.equal(changed.resolve(old), null);
  assert.deepEqual(changed.read(), old);
});

test('a bookmark at the beginning retains the introduction in another recording', () => {
  const storage = memoryStorage();
  create(chapter, sentences, storage).save(0);
  const changed = create(recording({ audio: { sha256: 'b'.repeat(64) } }), [
    { id: 'p001-s01', start: 8, end: 16 }, { id: 'p001-s02', start: 22, end: 42 }
  ], storage);
  assert.deepEqual(changed.resolve(changed.read()), { time: 0, completed: false, mapped: true });
});

test('corrupt JSON, malformed values, and denied storage fail safely', () => {
  for (const raw of ['{', 'null', '[]', JSON.stringify({ schemaVersion: 1, audioTime: '20' })]) {
    const reader = create(chapter, sentences, memoryStorage({ [KEY]: raw, [PREFS]: raw }));
    assert.equal(reader.read(), null);
    assert.deepEqual(reader.readPreferences(), { speed: 1, textSize: 20, narratorId: 'charon' });
  }
  const denied = { getItem() { throw new Error('Denied'); }, setItem() { throw new Error('Denied'); } };
  const reader = create(chapter, sentences, denied);
  assert.equal(reader.read(), null);
  assert.doesNotThrow(() => reader.save(25));
  assert.doesNotThrow(() => reader.savePreferences({ speed: 1.5 }));
  assert.deepEqual(reader.readPreferences(), { speed: 1, textSize: 20, narratorId: 'charon' });
});

test('valid legacy progress migrates once with preferences and retains the original', () => {
  const legacyKey = 'lumen-pilot-v0.1-' + chapter.audio.sha256.slice(0, 12);
  const legacyRaw = JSON.stringify({ time: 25, speed: 1.5, textSize: 24 });
  const storage = memoryStorage({ [legacyKey]: legacyRaw });
  const reader = create(chapter, sentences, storage);
  assert.equal(reader.read().sentenceId, 'p001-s02');
  assert.equal(reader.read().sentenceFraction, .25);
  assert.deepEqual(reader.readPreferences(), { speed: 1.5, textSize: 24, narratorId: 'charon' });
  assert.equal(storage.getItem(legacyKey), legacyRaw);
  reader.save(30);
  storage.setItem(legacyKey, JSON.stringify({ time: 10, speed: .75 }));
  assert.equal(create(chapter, sentences, storage).read().audioTime, 30);
});

test('invalid legacy times do not create progress or overwrite existing preferences', () => {
  const legacyKey = 'lumen-pilot-v0.1-' + chapter.audio.sha256.slice(0, 12);
  for (const time of [-1, 51, '25', null]) {
    const storage = memoryStorage({ [legacyKey]: JSON.stringify({ time, speed: 1.5 }) });
    assert.equal(create(chapter, sentences, storage).read(), null);
    assert.equal(storage.getItem(KEY), null);
  }
  const storage = memoryStorage({
    [legacyKey]: JSON.stringify({ time: 25, speed: 1.5, textSize: 24 }),
    [PREFS]: JSON.stringify({ speed: 1.25, textSize: 22, narratorId: 'autonoe' })
  });
  assert.deepEqual(create(chapter, sentences, storage).readPreferences(), { speed: 1.25, textSize: 22, narratorId: 'autonoe' });
});

test('legacy resume survives a readable storage area that rejects new writes', () => {
  const legacyKey = 'lumen-pilot-v0.1-' + chapter.audio.sha256.slice(0, 12);
  const storage = {
    getItem: key => key === legacyKey ? JSON.stringify({ time: 25, speed: 1.5, textSize: 24 }) : null,
    setItem() { throw new Error('Quota exceeded'); }
  };
  const reader = create(chapter, sentences, storage);
  assert.deepEqual(reader.readPreferences(), { speed: 1.5, textSize: 24, narratorId: 'charon' });
  assert.deepEqual(reader.resolve(reader.read()), { time: 25, completed: false, mapped: false });
});

test('preferences persist independently and partial updates preserve other settings', () => {
  const storage = memoryStorage();
  const reader = create(chapter, sentences, storage);
  reader.save(25);
  const before = storage.getItem(KEY);
  reader.savePreferences({ speed: 1.25, textSize: 26, narratorId: 'autonoe' });
  reader.savePreferences({ textSize: 22 });
  assert.deepEqual(create(chapter, sentences, storage).readPreferences(), { speed: 1.25, textSize: 22, narratorId: 'autonoe' });
  assert.equal(storage.getItem(KEY), before);
  assert.deepEqual(reader.savePreferences({ speed: Infinity, textSize: -10, narratorId: '' }), { speed: 1, textSize: 20, narratorId: 'charon' });
});

test('payloads without explicit stable identity use compatible pilot defaults', () => {
  const fallback = { id: chapter.id, duration: chapter.duration, audio: { sha256: chapter.audio.sha256 } };
  const reader = create(fallback, sentences, memoryStorage());
  const saved = reader.save(10);
  assert.equal(saved.bookId, 'lumen');
  assert.equal(saved.manuscriptVersion, 'v6');
  assert.equal(saved.chapterId, chapter.id);
  assert.equal(saved.narratorId, 'charon');
  assert.deepEqual(reader.resolve(saved), { time: 10, completed: false, mapped: false });
});
