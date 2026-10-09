(function (root) {
  'use strict';

  const BOOKMARK_KEY = 'lumen-reader-v1';
  const PREFERENCES_KEY = 'lumen-reader-preferences-v1';
  const nonempty = value => typeof value === 'string' && value.length > 0;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

  function validBookmark(value) {
    return object(value) && value.schemaVersion === 1 &&
      ['bookId', 'manuscriptVersion', 'chapterId', 'sentenceId', 'audioSha256'].every(key => nonempty(value[key])) &&
      finite(value.audioTime) && value.audioTime >= 0 &&
      finite(value.sentenceFraction) && value.sentenceFraction >= 0 && value.sentenceFraction <= 1 &&
      typeof value.completed === 'boolean' &&
      (value.readingExtentId === undefined || nonempty(value.readingExtentId));
  }

  function create(chapter, sentences, storage) {
    if (storage === undefined) {
      try { storage = root.localStorage; } catch (_) { storage = null; }
    }
    const identity = {
      bookId: chapter.bookId || 'lumen',
      manuscriptVersion: chapter.manuscriptVersion || 'v6',
      chapterId: chapter.chapterId || chapter.id,
      readingExtentId: chapter.id || chapter.chapterId,
      audioSha256: chapter.audio.sha256,
      narratorId: chapter.audio.narratorId || 'charon'
    };
    const duration = chapter.duration;
    const cues = sentences.filter(sentence => nonempty(sentence.id) && finite(sentence.start) &&
      finite(sentence.end) && sentence.start >= 0 && sentence.end > sentence.start)
      .slice().sort((a, b) => a.start - b.start);
    const byId = new Map(cues.map(sentence => [sentence.id, sentence]));
    let migrationAttempted = false, migratedBookmark = null, migratedPreferences = null;

    function readJSON(key) {
      try { return JSON.parse(storage?.getItem(key) || 'null'); } catch (_) { return null; }
    }
    function writeJSON(key, value) {
      try { storage?.setItem(key, JSON.stringify(value)); } catch (_) {}
    }
    function preferences(value) {
      value = object(value) ? value : {};
      return {
        speed: finite(value.speed) && value.speed >= .25 && value.speed <= 4 ? value.speed : 1,
        textSize: finite(value.textSize) && value.textSize >= 18 && value.textSize <= 28 ? value.textSize : 20,
        narratorId: nonempty(value.narratorId) ? value.narratorId : identity.narratorId,
        ...(typeof value.artworkVisible === 'boolean' ? {artworkVisible:value.artworkVisible} : {})
      };
    }
    function at(time) {
      let selected = cues[0];
      for (const sentence of cues) {
        if (sentence.start > time) break;
        selected = sentence;
      }
      return selected;
    }
    function makeBookmark(time, options = {}) {
      const audioTime = clamp(finite(time) ? time : 0, 0, duration);
      const sentence = at(audioTime);
      return {
        schemaVersion: 1,
        bookId: identity.bookId,
        manuscriptVersion: identity.manuscriptVersion,
        chapterId: identity.chapterId,
        readingExtentId: identity.readingExtentId,
        sentenceId: sentence?.id || '',
        sentenceFraction: sentence ? clamp((audioTime - sentence.start) / (sentence.end - sentence.start), 0, 1) : 0,
        narratorId: nonempty(options.narratorId) ? options.narratorId : identity.narratorId,
        audioSha256: identity.audioSha256,
        audioTime,
        completed: options.completed === true,
        updatedAt: new Date().toISOString()
      };
    }
    function migrate() {
      if (migrationAttempted) return migratedBookmark;
      migrationAttempted = true;
      const legacy = readJSON('lumen-pilot-v0.1-' + identity.audioSha256.slice(0, 12));
      if (!object(legacy) || !finite(legacy.time) || legacy.time < 0 || legacy.time > duration) return null;
      const bookmark = makeBookmark(legacy.time);
      if (!validBookmark(bookmark)) return null;
      migratedBookmark = bookmark;
      writeJSON(BOOKMARK_KEY, bookmark);
      if (!object(readJSON(PREFERENCES_KEY))) {
        migratedPreferences = preferences(legacy);
        writeJSON(PREFERENCES_KEY, migratedPreferences);
      }
      return bookmark;
    }
    function read() {
      const value = readJSON(BOOKMARK_KEY);
      // An existing bookmark for another chapter or manuscript must survive.
      if (validBookmark(value)) return value;
      return migrate();
    }
    function readPreferences() {
      read();
      return preferences(readJSON(PREFERENCES_KEY) || migratedPreferences);
    }
    function resolve(bookmark) {
      if (!validBookmark(bookmark) || bookmark.bookId !== identity.bookId ||
          bookmark.manuscriptVersion !== identity.manuscriptVersion || bookmark.chapterId !== identity.chapterId) return null;
      if (bookmark.audioSha256 === identity.audioSha256) {
        const completed = bookmark.completed && (!bookmark.readingExtentId || bookmark.readingExtentId === identity.readingExtentId);
        return { time: completed ? duration : clamp(bookmark.audioTime, 0, duration), completed, mapped: false };
      }
      const sentence = byId.get(bookmark.sentenceId);
      if (!sentence) return null;
      if (bookmark.audioTime === 0) return { time: 0, completed: false, mapped: true };
      // A finished excerpt must never mark a longer recording as finished.
      // Audio revisions also deserve a fresh completion state after mapping.
      return {
        time: clamp(sentence.start + bookmark.sentenceFraction * (sentence.end - sentence.start), 0, duration),
        completed: false,
        mapped: true
      };
    }
    function save(time, options = {}) {
      const bookmark = makeBookmark(time, options);
      if (validBookmark(bookmark)) writeJSON(BOOKMARK_KEY, bookmark);
      return bookmark;
    }
    function savePreferences(value) {
      const next = preferences({ ...readPreferences(), ...value });
      writeJSON(PREFERENCES_KEY, next);
      return next;
    }
    return { read, readPreferences, resolve, save, savePreferences };
  }

  const api = { create };
  root.LumenProgress = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
