const test = require('node:test');
const assert = require('node:assert/strict');
const { create, validateManifest, BOOKMARK_KEY, LEGACY_BOOKMARK_KEY } = require('../dist/book.js');
const clone = (value) => JSON.parse(JSON.stringify(value));
function storage(seed = {}) {
  const values = new Map(Object.entries(seed));
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}
function fixture() {
  const manifest = {
    schemaVersion: 1,
    bookId: 'lumen',
    manuscriptVersion: 'v6',
    releaseId: 'fixture-release',
    defaultTrackId: 'chapter-001',
    defaultNarratorId: 'charon',
    narrators: [
      { id: 'charon', label: 'Male narrator' },
      { id: 'autonoe', label: 'Female narrator' },
    ],
    tracks: [],
    legacyAliases: {
      'chapter-001-pilot': {
        trackId: 'chapter-001',
        sentenceIds: {
          'p001-s01': 'v6:chapter-001:p001-s01',
          'p001-s02': 'v6:chapter-001:p001-s02',
        },
        completedExcerpt: {
          sentenceId: 'v6:chapter-001:p001-s02',
          sentenceFraction: 1,
          trackCompleted: false,
        },
      },
    },
  };
  const payloads = {};
  for (let i = 0; i < 5; i++) {
    const id = `chapter-${String(i).padStart(3, '0')}`,
      track = {
        id,
        title: i === 0 ? 'Opening credits' : i === 4 ? 'Closing credits' : `Chapter ${i}`,
        kind: i === 0 ? 'opening-credits' : i === 4 ? 'closing-credits' : 'story',
        part: 'Part One',
        recordings: {},
      };
    for (const voice of ['charon', 'autonoe']) {
      const female = voice === 'autonoe',
        hash = (female ? 'b' : 'a').repeat(64),
        url = `https://example.test/${id}/${voice}.json`;
      track.recordings[voice] = { status: 'ready', url, audioSha256: hash };
      payloads[url] = {
        schemaVersion: 1,
        id: id + '-full',
        readingExtentId: id + '-full',
        readingExtent: 'full',
        chapterId: id,
        bookId: 'lumen',
        manuscriptVersion: 'v6',
        title: track.title,
        kind: track.kind,
        duration: female ? 60 : 40,
        audio: {
          src: `https://media.example.test/${id}/${voice}.mp3`,
          sha256: hash,
          narratorId: voice,
          decodedDuration: female ? 60 : 40,
        },
        scenes: [],
        paragraphs: [
          {
            id: `v6:${id}:p001`,
            text: 'First thought. Second thought.',
            emphasis: [],
            start: female ? 8 : 5,
            end: female ? 50 : 35,
            sentences: [
              {
                id: `v6:${id}:p001-s01`,
                text: 'First thought.',
                textStart: 0,
                textEnd: 14,
                start: female ? 8 : 5,
                end: female ? 18 : 15,
              },
              {
                id: `v6:${id}:p001-s02`,
                text: 'Second thought.',
                textStart: 15,
                textEnd: 30,
                start: female ? 30 : 20,
                end: female ? 50 : 35,
              },
            ],
          },
        ],
      };
    }
    manifest.tracks.push(track);
  }
  const pilot = clone(payloads[manifest.tracks[1].recordings.charon.url]);
  pilot.id = pilot.readingExtentId = 'chapter-001-pilot';
  pilot.readingExtent = 'excerpt';
  pilot.audio.sha256 = 'f'.repeat(64);
  pilot.paragraphs[0].id = 'p001';
  pilot.paragraphs[0].sentences.forEach((s, i) => (s.id = `p001-s0${i + 1}`));
  return { manifest, payloads, pilot, storage: storage() };
}
function reader(f, overrides = {}) {
  return create({
    manifest: f.manifest,
    pilot: f.pilot,
    storage: f.storage,
    loadChapter: async (url) => clone(f.payloads[url]),
    ...overrides,
  });
}
async function opened() {
  const f = fixture(),
    book = reader(f);
  await book.load('chapter-001', 'charon');
  return { f, book };
}
function v1(f, changes = {}) {
  return {
    schemaVersion: 1,
    bookId: 'lumen',
    manuscriptVersion: 'v6',
    chapterId: 'chapter-001',
    readingExtentId: 'chapter-001-full',
    sentenceId: 'v6:chapter-001:p001-s02',
    sentenceFraction: 1 / 3,
    narratorId: 'charon',
    audioSha256: 'a'.repeat(64),
    audioTime: 25,
    completed: false,
    updatedAt: '2026-10-10T12:00:00Z',
    ...changes,
  };
}

test('explicit unsynchronized prose keeps faithful text and exact same-recording bookmarks', async () => {
  const f=fixture(),url=f.manifest.tracks[1].recordings.charon.url,p=f.payloads[url].paragraphs[0];
  f.payloads[url].schemaVersion=2;
  p.sentences[1]={...p.sentences[1],start:null,end:null,syncStatus:'unavailable'};
  p.end=p.sentences[0].end;
  const book=reader(f);await book.load('chapter-001','charon');
  const bookmark=book.save(25);
  assert.equal(book.getActive().chapter.paragraphs[0].text,'First thought. Second thought.');
  assert.equal(bookmark.audioTime,25);
  assert.equal(bookmark.sentenceId,p.sentences[0].id);
  assert.equal(bookmark.anchorMeasured,false);
  const restored=await book.load('chapter-001','charon',{bookmark});
  assert.equal(restored.position.time,25);
  await assert.rejects(book.load('chapter-001','autonoe',{bookmark}),error=>error.code==='MISSING_ANCHOR');
  const tail=book.capture(40,{completed:true});assert.equal(tail.anchorMeasured,false);
  assert.deepEqual((await book.load('chapter-001','autonoe',{bookmark:tail})).position,
    {time:60,completed:true,mapped:true});
});
test('missing cue never becomes an invented narrator-switch time', async () => {
  const {f,book}=await opened(),bookmark=book.capture(25);
  const payload=f.payloads[f.manifest.tracks[1].recordings.autonoe.url],p=payload.paragraphs[0];
  payload.schemaVersion=2;p.sentences[1]={...p.sentences[1],start:null,end:null,syncStatus:'unavailable'};p.end=p.sentences[0].end;
  await assert.rejects(book.load('chapter-001','autonoe',{bookmark}),error=>error.code==='MISSING_ANCHOR');
  assert.equal(book.getActive().narratorId,'charon');
});
test('a chapter without measured cues can play and resume but cannot map a later voice position', async () => {
  const f=fixture(),payload=f.payloads[f.manifest.tracks[1].recordings.charon.url],p=payload.paragraphs[0];
  payload.schemaVersion=2;p.start=p.end=null;
  p.sentences.forEach(s=>Object.assign(s,{start:null,end:null,syncStatus:'unavailable'}));
  const book=reader(f);await book.load('chapter-001','charon');
  const bookmark=book.save(12);
  assert.equal(bookmark.audioTime,12);assert.equal(bookmark.sentenceFraction,0);
  assert.equal((await book.load('chapter-001','charon',{bookmark})).position.time,12);
  await assert.rejects(book.load('chapter-001','autonoe',{bookmark}),error=>error.code==='MISSING_ANCHOR');
});
test('unavailable cues require explicit status and null bounds; schema one stays strict', async () => {
  for(const change of [p=>{p.schemaVersion=1;p.paragraphs[0].sentences[1].syncStatus='unavailable';},
    p=>delete p.paragraphs[0].sentences[1].syncStatus,p=>p.paragraphs[0].sentences[1].end=30]) {
    const f=fixture(),p=f.payloads[f.manifest.tracks[1].recordings.charon.url];
    p.schemaVersion=2;Object.assign(p.paragraphs[0].sentences[1],{start:null,end:null,syncStatus:'unavailable'});p.paragraphs[0].end=15;change(p);
    await assert.rejects(reader(f).load('chapter-001','charon'),error=>error.code==='INVALID_CHAPTER');
  }
});

test('manifest rejects duplicate tracks, unsafe record URLs, invalid hashes and missing defaults', () => {
  for (const change of [
    (m) => m.tracks.push(clone(m.tracks[1])),
    (m) => (m.defaultTrackId = 'missing'),
    (m) => (m.tracks[1].recordings.charon.url = 'javascript:alert(1)'),
    (m) => (m.tracks[1].recordings.charon.audioSha256 = 'bad'),
    (m) => m.narrators.push(clone(m.narrators[0])),
  ]) {
    const f = fixture();
    change(f.manifest);
    assert.throws(
      () => validateManifest(f.manifest),
      (e) => e.code === 'INVALID_MANIFEST',
    );
  }
});
test('initial selection reads saved chapter before the default and explicit links outrank it', () => {
  const f = fixture(),
    bookmark = v1(f, {
      chapterId: 'chapter-002',
      readingExtentId: 'chapter-002-full',
      sentenceId: 'v6:chapter-002:p001-s02',
    });
  f.storage.setItem(
    BOOKMARK_KEY,
    JSON.stringify({
      schemaVersion: 2,
      bookId: 'lumen',
      manuscriptVersion: 'v6',
      lastPosition: bookmark,
      history: {},
    }),
  );
  const book = reader(f);
  assert.equal(book.initialSelection().trackId, 'chapter-002');
  assert.equal(book.initialSelection({ trackId: 'chapter-003' }).trackId, 'chapter-003');
  assert.equal(book.initialSelection({ trackId: 'chapter-003' }).bookmark, null);
});
test('unknown/pending saved targets and missing voices fail explicitly without rewriting progress', async () => {
  const f = fixture(),
    bookmark = v1(f, { chapterId: 'chapter-099' }),
    raw = JSON.stringify(bookmark);
  f.storage.setItem(LEGACY_BOOKMARK_KEY, raw);
  const book = reader(f);
  assert.throws(
    () => book.initialSelection(),
    (e) => e.code === 'TRACK_UNAVAILABLE',
  );
  assert.equal(f.storage.getItem(LEGACY_BOOKMARK_KEY), raw);
  f.manifest.tracks[1].recordings.autonoe = { status: 'pending' };
  const second = reader(f);
  assert.throws(
    () => second.initialSelection({ trackId: 'chapter-001', narratorId: 'autonoe' }),
    (e) => e.code === 'NARRATOR_UNAVAILABLE',
  );
  await assert.rejects(
    second.load('chapter-001', 'missing'),
    (e) => e.code === 'NARRATOR_UNAVAILABLE',
  );
  assert.equal(second.getActive(), null);
});
test('same recording resumes exact seconds inside silence and returns paused intent', async () => {
  const { book } = await opened();
  const saved = book.save(17.25);
  const result = await book.load('chapter-001', 'charon', { bookmark: saved });
  assert.deepEqual(result.position, { time: 17.25, completed: false, mapped: false });
  assert.equal(book.getActive().chapter.chapterId, 'chapter-001');
});
test('capture is side-effect free and voice switching maps the current semantic fraction', async () => {
  const { book, f } = await opened(),
    before = [...f.storage.values];
  const bookmark = book.capture(25);
  assert.equal(bookmark.sentenceFraction, 1 / 3);
  assert.deepEqual([...f.storage.values], before);
  const result = await book.load('chapter-001', 'autonoe', { bookmark, reason: 'voice' });
  assert.ok(Math.abs(result.position.time - (30 + 20 / 3)) < 1e-9);
  assert.equal(result.position.mapped, true);
  assert.equal(result.position.completed, false);
});

test('an unmeasured introduction keeps exact recording time and cannot map to another narrator', async () => {
  const { f, book } = await opened();
  const bookmark = book.save(2.5);
  assert.equal(bookmark.anchorMeasured, false);
  const stored = f.storage.values.get(BOOKMARK_KEY);
  assert.deepEqual((await book.load('chapter-001', 'charon', { bookmark })).position,
    { time: 2.5, completed: false, mapped: false });
  const prior = book.getActive();
  await assert.rejects(book.load('chapter-001', 'autonoe', { bookmark }), error => error.code === 'MISSING_ANCHOR');
  assert.equal(book.getActive(), prior);
  assert.equal(f.storage.values.get(BOOKMARK_KEY), stored);
  assert.equal(book.capture(5).anchorMeasured, true);
  assert.deepEqual((await book.load('chapter-001', 'autonoe', { bookmark: book.capture(0) })).position,
    { time: 0, completed: false, mapped: true });
});
test('completed pilot maps through verified aliases to its endpoint without finishing the chapter', async () => {
  const f = fixture(),
    bookmark = v1(f, {
      readingExtentId: 'chapter-001-pilot',
      sentenceId: 'p001-s02',
      sentenceFraction: 1,
      audioSha256: f.pilot.audio.sha256,
      audioTime: 40,
      completed: true,
    });
  f.storage.setItem(LEGACY_BOOKMARK_KEY, JSON.stringify(bookmark));
  const book = reader(f),
    selection = book.initialSelection();
  const result = await book.load(selection.trackId, selection.narratorId, {
    bookmark: selection.bookmark,
  });
  assert.deepEqual(result.position, { time: 35, completed: false, mapped: true });
  book.save(result.position.time);
  assert.equal(book.getHistory()['chapter-001'].completed, false);
});

test('completed full extents stay completed at the changed recording endpoint without a sentence anchor', async () => {
  const { f, book } = await opened(), bookmark = book.save(40, { completed:true });
  const target=f.payloads[f.manifest.tracks[1].recordings.autonoe.url],paragraph=target.paragraphs[0];
  target.schemaVersion=2;paragraph.start=paragraph.end=null;
  paragraph.sentences.forEach(sentence=>Object.assign(sentence,{start:null,end:null,syncStatus:'unavailable'}));
  const switched=await book.load('chapter-001','autonoe',{bookmark});
  assert.deepEqual(switched.position,{time:60,completed:true,mapped:true});
  const saved=book.save(switched.position.time,{completed:switched.position.completed});
  assert.equal(saved.completed,true);assert.equal(saved.anchorMeasured,false);
  assert.equal(book.getHistory()['chapter-001'].completed,true);
});

test('completion does not finish a different full reading extent', async () => {
  const { f, book } = await opened(),bookmark=book.save(40,{completed:true});
  f.payloads[f.manifest.tracks[1].recordings.autonoe.url].readingExtentId='chapter-001-extended';
  assert.deepEqual((await book.load('chapter-001','autonoe',{bookmark})).position,
    {time:50,completed:false,mapped:true});
});
test('trusted pilot accepts its local IDs and completion remains excerpt-specific', async () => {
  const f = fixture();
  f.manifest.tracks[1].recordings.charon.audioSha256 = f.pilot.audio.sha256;
  f.payloads[f.manifest.tracks[1].recordings.charon.url] = f.pilot;
  const book = reader(f);
  await book.load('chapter-001', 'charon');
  const saved = book.save(40, { completed: true });
  assert.equal(saved.completed, true);
  assert.equal(book.getHistory()['chapter-001'].completed, false);
  assert.equal(
    (await book.load('chapter-001', 'charon', { bookmark: saved })).position.completed,
    true,
  );
});
test('untrusted local IDs, mismatched voices/hashes, invalid cues and changed prose reject before commit', async () => {
  const { f, book } = await opened(),
    old = book.getActive();
  const url = f.manifest.tracks[2].recordings.charon.url,
    original = clone(f.payloads[url]);
  for (const change of [
    (c) => (c.audio.sha256 = 'c'.repeat(64)),
    (c) => (c.audio.narratorId = 'autonoe'),
    (c) => (c.paragraphs[0].sentences[0].id = 'p001-s01'),
    (c) => (c.paragraphs[0].sentences[1].start = 10),
    (c) => (c.paragraphs[0].sentences[1].end = 60),
    (c) => (c.paragraphs[0].sentences[0].text = 'Changed words.'),
    (c) => c.paragraphs[0].sentences.pop(),
    (c) => (c.scenes = [{ id: 'unsafe', start: 0, src: 'javascript:alert(1)' }]),
  ]) {
    f.payloads[url] = clone(original);
    change(f.payloads[url]);
    await assert.rejects(book.load('chapter-002', 'charon'), (e) => e.code === 'INVALID_CHAPTER');
    assert.equal(book.getActive(), old);
  }
});
test('missing semantic anchors cannot guess a changed recording time or overwrite the previous place', async () => {
  const { book, f } = await opened(),
    bookmark = book.save(25),
    raw = f.storage.getItem(BOOKMARK_KEY);
  bookmark.sentenceId = 'v6:chapter-001:p999-s01';
  await assert.rejects(
    book.load('chapter-001', 'autonoe', { bookmark }),
    (e) => e.code === 'MISSING_ANCHOR',
  );
  assert.equal(book.getActive().narratorId, 'charon');
  assert.equal(f.storage.getItem(BOOKMARK_KEY), raw);
});
test('failed loading preserves old chapter, bookmark and retry target', async () => {
  const f = fixture();
  let fail = false;
  const book = reader(f, {
    loadChapter: async (url) => {
      if (fail) throw new Error('offline');
      return clone(f.payloads[url]);
    },
  });
  await book.load('chapter-001', 'charon');
  book.save(25);
  const before = f.storage.getItem(BOOKMARK_KEY);
  fail = true;
  await assert.rejects(book.load('chapter-002', 'charon'), (e) => e.code === 'LOAD_FAILED');
  assert.equal(book.getActive().trackId, 'chapter-001');
  assert.equal(f.storage.getItem(BOOKMARK_KEY), before);
  fail = false;
  assert.equal((await book.load('chapter-002', 'charon')).chapter.chapterId, 'chapter-002');
});
test('rapid loads abort older requests and ignore late success or failure', async () => {
  const f = fixture(),
    requests = [];
  const book = reader(f, {
    loadChapter: (url, options) =>
      new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
  });
  const first = book.load('chapter-001', 'charon'),
    second = book.load('chapter-002', 'autonoe');
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(requests[1].options.trackId, 'chapter-002');
  requests[1].resolve(clone(f.payloads[requests[1].url]));
  await second;
  requests[0].resolve(clone(f.payloads[requests[0].url]));
  assert.deepEqual(await first, { status: 'stale' });
  assert.equal(book.getActive().trackId, 'chapter-002');
  const third = book.load('chapter-003', 'charon'),
    fourth = book.load('chapter-001', 'charon');
  requests[3].resolve(clone(f.payloads[requests[3].url]));
  await fourth;
  requests[2].reject(new Error('late offline'));
  assert.deepEqual(await third, { status: 'stale' });
  assert.equal(book.getActive().trackId, 'chapter-001');
});
test('history preserves chapter places and completion separately from deliberate rewind', async () => {
  const { book } = await opened();
  book.save(40, { completed: true });
  book.save(10);
  assert.equal(book.getHistory()['chapter-001'].completed, true);
  assert.equal(book.getHistory()['chapter-001'].bookmark.completed, false);
  await book.load('chapter-002', 'charon');
  book.save(25);
  const result = await book.load('chapter-001', 'charon');
  assert.equal(result.position.time, 10);
  assert.equal(result.position.completed, false);
});
test('v2 is written before v1 interoperability and failed v2 writes preserve usable v1 data', async () => {
  const f = fixture(),
    old = JSON.stringify(v1(f, { audioTime: 20 }));
  f.storage.setItem(LEGACY_BOOKMARK_KEY, old);
  const set = f.storage.setItem;
  f.storage.setItem = (key, value) => {
    if (key === BOOKMARK_KEY) throw new Error('quota');
    set(key, value);
  };
  const book = reader(f);
  await book.load('chapter-001', 'charon');
  assert.doesNotThrow(() => book.save(25));
  assert.equal(f.storage.getItem(LEGACY_BOOKMARK_KEY), old);
  f.storage.setItem = set;
  book.save(26);
  assert.equal(JSON.parse(f.storage.getItem(BOOKMARK_KEY)).lastPosition.audioTime, 26);
  assert.equal(JSON.parse(f.storage.getItem(LEGACY_BOOKMARK_KEY)).audioTime, 26);
});
test('quota failure retains newer in-memory history during chapter navigation', async () => {
  const { f, book } = await opened();
  book.save(20);
  const set = f.storage.setItem;
  f.storage.setItem = (key, value) => {
    if (key === BOOKMARK_KEY) throw new Error('quota');
    set(key, value);
  };
  book.save(27.25);
  assert.equal(book.getHistory()['chapter-001'].bookmark.audioTime, 27.25);
  await book.load('chapter-002', 'charon');
  book.save(23);
  assert.equal((await book.load('chapter-001', 'charon')).position.time, 27.25);
  assert.equal(JSON.parse(f.storage.getItem(BOOKMARK_KEY)).lastPosition.audioTime, 20);
});
test('interleaved tabs merge chapter histories instead of dropping another chapter', async () => {
  const f = fixture(),
    first = reader(f),
    second = reader(f);
  await first.load('chapter-001', 'charon');
  first.save(20);
  await second.load('chapter-002', 'charon');
  second.save(23);
  first.save(27);
  assert.equal(first.getHistory()['chapter-002'].bookmark.audioTime, 23);
  assert.equal(second.getHistory()['chapter-001'].bookmark.audioTime, 27);
});
test('failed local saves retain their chapter intent while another tab writes and recovery merges both', async () => {
  const f = fixture();
  let fail = false;
  const first = reader(f, {
      storage: {
        getItem: f.storage.getItem,
        setItem: (key, value) => {
          if (fail && key === BOOKMARK_KEY) throw new Error('quota');
          f.storage.setItem(key, value);
        },
      },
    }),
    second = reader(f);
  await first.load('chapter-001', 'charon');
  first.save(20);
  fail = true;
  first.save(27.25);
  await second.load('chapter-002', 'charon');
  second.save(23);
  assert.equal(first.getHistory()['chapter-001'].bookmark.audioTime, 27.25);
  assert.equal(first.getHistory()['chapter-002'].bookmark.audioTime, 23);
  assert.equal(first.initialSelection().trackId, 'chapter-001');
  await first.load('chapter-002', 'charon');
  assert.equal((await first.load('chapter-001', 'charon')).position.time, 27.25);
  fail = false;
  first.save(28);
  const persisted = JSON.parse(f.storage.getItem(BOOKMARK_KEY));
  assert.equal(persisted.history['chapter-001'].bookmark.audioTime, 28);
  assert.equal(persisted.history['chapter-002'].bookmark.audioTime, 23);
});
test('pending deliberate rewind preserves completion subsequently written by another tab', async () => {
  const f = fixture();
  let fail = false;
  const first = reader(f, {
    storage: {
      getItem: f.storage.getItem,
      setItem: (key, value) => {
        if (fail && key === BOOKMARK_KEY) throw new Error('quota');
        f.storage.setItem(key, value);
      },
    },
  });
  await first.load('chapter-001', 'charon');
  first.save(20);
  fail = true;
  first.save(10);
  const second = reader(f);
  await second.load('chapter-001', 'charon');
  second.save(40, { completed: true });
  const history = first.getHistory()['chapter-001'];
  assert.equal(history.bookmark.audioTime, 10);
  assert.equal(history.bookmark.completed, false);
  assert.equal(history.completed, true);
  assert.ok(history.completedAt);
});
test('corrupt/denied storage recovers and foreign manuscript bookmarks remain untouched', async () => {
  for (const raw of ['{', '[]', 'null']) {
    const f = fixture();
    f.storage.setItem(BOOKMARK_KEY, raw);
    const book = reader(f);
    assert.equal(book.initialSelection().trackId, 'chapter-001');
    await book.load('chapter-001', 'charon');
    assert.doesNotThrow(() => book.save(10));
  }
  const f = fixture(),
    foreign = JSON.stringify(v1(f, { manuscriptVersion: 'v7' }));
  f.storage.setItem(LEGACY_BOOKMARK_KEY, foreign);
  assert.throws(
    () => reader(f).initialSelection(),
    (e) => e.code === 'BOOKMARK_IDENTITY',
  );
  assert.equal(f.storage.getItem(LEGACY_BOOKMARK_KEY), foreign);
  const denied = reader(f, {
    storage: {
      getItem() {
        throw new Error('Denied');
      },
      setItem() {
        throw new Error('Denied');
      },
    },
  });
  assert.equal(denied.initialSelection().trackId, 'chapter-001');
  await denied.load('chapter-001', 'charon');
  assert.doesNotThrow(() => denied.save(10));
});
test('preferences retain speed/type/art and persist independently of book position', async () => {
  const { f, book } = await opened();
  book.save(25);
  const raw = f.storage.getItem(BOOKMARK_KEY);
  book.savePreferences({
    speed: 1.25,
    textSize: 26,
    artworkVisible: false,
    narratorId: 'autonoe',
    autoContinue: true,
  });
  book.savePreferences({ textSize: 22 });
  assert.deepEqual(book.readPreferences(), {
    speed: 1.25,
    textSize: 22,
    narratorId: 'autonoe',
    artworkVisible: false,
    autoContinue: true,
  });
  assert.equal(f.storage.getItem(BOOKMARK_KEY), raw);
});
test('denied preference storage retains normalized choices in memory across partial updates', () => {
  const f = fixture(),
    book = reader(f, {
      storage: {
        getItem() { throw new Error('Denied'); },
        setItem() { throw new Error('Denied'); },
      },
    });
  book.savePreferences({ speed: 1.25, textSize: 26, artworkVisible: false,
                         narratorId: 'autonoe', autoContinue: true });
  assert.deepEqual(book.readPreferences(), {
    speed: 1.25, textSize: 26, artworkVisible: false,
    narratorId: 'autonoe', autoContinue: true,
  });
  book.savePreferences({ textSize: 22 });
  assert.equal(book.readPreferences().textSize, 22);
  assert.equal(book.readPreferences().speed, 1.25);
  assert.equal(book.initialSelection().narratorId, 'autonoe');
});
test('failed preference writes merge dirty fields with fresh other-tab choices and recover', () => {
  const f = fixture();
  let fail = true;
  const first = reader(f, {
    storage: {
      getItem: f.storage.getItem,
      setItem: (key, value) => {
        if (fail) throw new Error('quota');
        f.storage.setItem(key, value);
      },
    },
  }), second = reader(f);
  first.savePreferences({ speed: 1.25 });
  second.savePreferences({ textSize: 26, autoContinue: true });
  assert.equal(first.readPreferences().speed, 1.25);
  assert.equal(first.readPreferences().textSize, 26);
  assert.equal(first.readPreferences().autoContinue, true);
  first.savePreferences({ artworkVisible: false });
  fail = false;
  first.savePreferences({});
  assert.deepEqual(second.readPreferences(), {
    speed: 1.25, textSize: 26, artworkVisible: false,
    narratorId: 'charon', autoContinue: true,
  });
});
test('preferences already read remain usable after storage access is denied', () => {
  const f = fixture(), book = reader(f);
  book.savePreferences({ speed: 1.5, textSize: 24 });
  const reopened = reader(f);
  assert.equal(reopened.readPreferences().speed, 1.5);
  f.storage.getItem = () => { throw new Error('Denied'); };
  assert.equal(reopened.readPreferences().speed, 1.5);
  assert.equal(reopened.readPreferences().textSize, 24);
});
test('zero position retains introductions when changing voices, and credits load as ordinary tracks', async () => {
  const { book } = await opened();
  assert.deepEqual(
    (await book.load('chapter-001', 'autonoe', { bookmark: book.capture(0), reason: 'voice' }))
      .position,
    { time: 0, completed: false, mapped: true },
  );
  const result = await book.load('chapter-000', 'charon');
  assert.equal(result.chapter.kind, 'opening-credits');
  assert.equal(result.position.time, 0);
  assert.equal((await book.load('chapter-004', 'autonoe')).chapter.kind, 'closing-credits');
});
