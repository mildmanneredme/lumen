(function (root) {
  'use strict';
  const BOOKMARK_KEY = 'lumen-book-v2',
    LEGACY_BOOKMARK_KEY = 'lumen-reader-v1',
    PREFERENCES_KEY = 'lumen-reader-preferences-v1';
  const object = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
  const nonempty = (v) => typeof v === 'string' && v.length > 0;
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const hash = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const copy = (v) => JSON.parse(JSON.stringify(v));
  class BookError extends Error {
    constructor(code, message, cause) {
      super(message);
      this.name = 'BookError';
      this.code = code;
      if (cause) this.cause = cause;
    }
  }
  function ensure(condition, code, message) {
    if (!condition) throw new BookError(code, message);
  }
  function safeURL(value) {
    try {
      if (!nonempty(value)) return false;
      const url = new URL(value, 'https://lumen.invalid/');
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password;
    } catch (_) {
      return false;
    }
  }
  function validBookmark(value) {
    return (
      object(value) &&
      value.schemaVersion === 1 &&
      ['bookId', 'manuscriptVersion', 'chapterId', 'sentenceId', 'audioSha256'].every((k) =>
        nonempty(value[k]),
      ) &&
      hash(value.audioSha256) &&
      finite(value.audioTime) &&
      value.audioTime >= 0 &&
      finite(value.sentenceFraction) &&
      value.sentenceFraction >= 0 &&
      value.sentenceFraction <= 1 &&
      typeof value.completed === 'boolean' &&
      (value.readingExtentId === undefined || nonempty(value.readingExtentId))
    );
  }
  function validateManifest(manifest) {
    const code = 'INVALID_MANIFEST';
    ensure(
      object(manifest) &&
        manifest.schemaVersion === 1 &&
        manifest.bookId === 'lumen' &&
        nonempty(manifest.manuscriptVersion) &&
        /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(manifest.manuscriptVersion) &&
        nonempty(manifest.releaseId),
      code,
      'The book release identity is invalid.',
    );
    ensure(
      Array.isArray(manifest.narrators) &&
        manifest.narrators.length &&
        Array.isArray(manifest.tracks) &&
        manifest.tracks.length,
      code,
      'The book release needs narrators and tracks.',
    );
    const voices = new Set(),
      tracks = new Set();
    for (const narrator of manifest.narrators) {
      ensure(
        object(narrator) &&
          nonempty(narrator.id) &&
          /^[a-z][a-z0-9-]*$/.test(narrator.id) &&
          !voices.has(narrator.id) &&
          nonempty(narrator.label),
        code,
        'Narrator identities are missing or duplicated.',
      );
      voices.add(narrator.id);
    }
    let prior = -1;
    for (const track of manifest.tracks) {
      ensure(
        object(track) &&
          typeof track.id === 'string' &&
          /^chapter-[0-9]{3}$/.test(track.id) &&
          !tracks.has(track.id) &&
          Number(track.id.slice(-3)) > prior &&
          nonempty(track.title) &&
          ['opening-credits', 'story', 'closing-credits'].includes(track.kind) &&
          object(track.recordings),
        code,
        'The chapter list is missing, duplicated, or out of order.',
      );
      tracks.add(track.id);
      prior = Number(track.id.slice(-3));
      for (const [voice, recording] of Object.entries(track.recordings)) {
        ensure(
          voices.has(voice) &&
            object(recording) &&
            ['ready', 'pending', 'unavailable'].includes(recording.status),
          code,
          'A recording has an invalid narrator or release status.',
        );
        if (recording.status === 'ready')
          ensure(
            safeURL(recording.url) && hash(recording.audioSha256),
            code,
            'A ready recording needs a safe data URL and final audio hash.',
          );
      }
    }
    ensure(
      tracks.has(manifest.defaultTrackId) && voices.has(manifest.defaultNarratorId),
      code,
      'The default chapter or narrator is unavailable.',
    );
    const aliases = manifest.legacyAliases || {};
    ensure(object(aliases), code, 'Legacy bookmark aliases must be an object.');
    for (const alias of Object.values(aliases)) {
      ensure(
        object(alias) &&
          tracks.has(alias.trackId) &&
          object(alias.sentenceIds) &&
          Object.entries(alias.sentenceIds).every(
            ([old, target]) =>
              nonempty(old) &&
              nonempty(target) &&
              target.startsWith(`${manifest.manuscriptVersion}:${alias.trackId}:`),
          ),
        code,
        'Legacy bookmark aliases do not match this manuscript.',
      );
      if (alias.completedExcerpt)
        ensure(
          object(alias.completedExcerpt) &&
            nonempty(alias.completedExcerpt.sentenceId) &&
            alias.completedExcerpt.sentenceId.startsWith(
              `${manifest.manuscriptVersion}:${alias.trackId}:`,
            ) &&
            finite(alias.completedExcerpt.sentenceFraction) &&
            alias.completedExcerpt.sentenceFraction >= 0 &&
            alias.completedExcerpt.sentenceFraction <= 1 &&
            alias.completedExcerpt.trackCompleted === false,
          code,
          'Completed excerpt aliases must retain their passage without finishing the chapter.',
        );
    }
    return manifest;
  }
  function create(options) {
    ensure(object(options), 'INVALID_MANIFEST', 'Reader options are required.');
    const manifest = copy(validateManifest(options.manifest)),
      pilot = options.pilot;
    ensure(
      typeof options.loadChapter === 'function',
      'INVALID_MANIFEST',
      'A chapter loader is required.',
    );
    let storage = options.storage;
    if (storage === undefined) {
      try {
        storage = root.localStorage;
      } catch (_) {
        storage = null;
      }
    }
    const trackById = new Map(manifest.tracks.map((t) => [t.id, t]));
    let active = null,
      sequence = 0,
      abort = null,
      memory = null,
      observedState = null,
      pendingLastPosition = null,
      preferenceMemory = null;
    const pendingHistory = new Map(),
      pendingPreferenceKeys = new Set();
    function readJSON(key) {
      try {
        return JSON.parse(storage?.getItem(key) || 'null');
      } catch (_) {
        return null;
      }
    }
    function writeJSON(key, value) {
      try {
        if (!storage?.setItem) return false;
        const serialized = JSON.stringify(value);
        storage.setItem(key, serialized);
        return storage.getItem(key) === serialized;
      } catch (_) {
        return false;
      }
    }
    function validState(value) {
      return (
        object(value) &&
        value.schemaVersion === 2 &&
        nonempty(value.bookId) &&
        nonempty(value.manuscriptVersion) &&
        validBookmark(value.lastPosition) &&
        object(value.history)
      );
    }
    function compatible(value) {
      return (
        value.bookId === manifest.bookId && value.manuscriptVersion === manifest.manuscriptVersion
      );
    }
    function readState() {
      const value = readJSON(BOOKMARK_KEY);
      if (memory && JSON.stringify(value) === observedState) return memory;
      if (!validState(value)) return memory;
      if (!pendingHistory.size || !compatible(value)) return value;
      const merged = copy(value);
      for (const [trackId, pending] of pendingHistory) {
        const persisted = merged.history[trackId],
          completedAt = pending.completedAt || persisted?.completedAt;
        merged.history[trackId] = {
          ...copy(pending),
          completed: pending.completed === true || persisted?.completed === true,
          ...(completedAt ? { completedAt } : {}),
        };
      }
      // Unsaved local intent wins for the touched chapter, including a rewind;
      // other chapters and completion evidence still merge from the latest tab.
      if (pendingLastPosition) merged.lastPosition = copy(pendingLastPosition);
      memory = merged;
      observedState = JSON.stringify(value);
      return memory;
    }
    function emptyState() {
      return {
        schemaVersion: 2,
        bookId: manifest.bookId,
        manuscriptVersion: manifest.manuscriptVersion,
        releaseId: manifest.releaseId,
        lastPosition: null,
        history: {},
      };
    }
    function getHistory() {
      const value = readState();
      return value && compatible(value) ? copy(value.history) : {};
    }
    function choice(trackId, narratorId) {
      const track = trackById.get(trackId);
      ensure(
        track,
        'TRACK_UNAVAILABLE',
        'This chapter is unavailable in the current release. Your saved place is kept.',
      );
      const recording = track.recordings[narratorId];
      ensure(
        recording?.status === 'ready',
        'NARRATOR_UNAVAILABLE',
        'This narrator recording is not available for this chapter. Choose an available recording.',
      );
      return { track, recording };
    }
    function normalizePreferences(v) {
      return {
        speed: finite(v.speed) && v.speed >= 0.25 && v.speed <= 4 ? v.speed : 1,
        textSize: finite(v.textSize) && v.textSize >= 18 && v.textSize <= 28 ? v.textSize : 20,
        narratorId: nonempty(v.narratorId) ? v.narratorId : manifest.defaultNarratorId,
        artworkVisible: v.artworkVisible !== false,
        autoContinue: v.autoContinue === true,
      };
    }
    function readPreferences() {
      const saved = readJSON(PREFERENCES_KEY),
        value = normalizePreferences(object(saved) ? saved : preferenceMemory || {});
      for (const key of pendingPreferenceKeys) value[key] = preferenceMemory[key];
      preferenceMemory = { ...value };
      return value;
    }
    function savePreferences(changes) {
      const updates = object(changes) ? changes : {},
        normalized = normalizePreferences({ ...readPreferences(), ...updates });
      preferenceMemory = { ...normalized };
      if (writeJSON(PREFERENCES_KEY, normalized)) pendingPreferenceKeys.clear();
      else
        for (const key of Object.keys(normalized))
          if (Object.prototype.hasOwnProperty.call(updates, key)) pendingPreferenceKeys.add(key);
      return normalized;
    }
    function initialSelection(link = {}) {
      const state = readState(),
        old = readJSON(LEGACY_BOOKMARK_KEY),
        last = state?.lastPosition || (validBookmark(old) ? old : null);
      const explicit = nonempty(link.trackId);
      if (!explicit && last)
        ensure(
          compatible(last),
          'BOOKMARK_IDENTITY',
          'Your saved manuscript differs from this release. Your original bookmark is kept.',
        );
      const trackId = explicit ? link.trackId : last?.chapterId || manifest.defaultTrackId;
      const narratorId = nonempty(link.narratorId)
        ? link.narratorId
        : !explicit && last?.narratorId
          ? last.narratorId
          : readPreferences().narratorId;
      choice(trackId, narratorId);
      const history = state && compatible(state) ? state.history[trackId]?.bookmark : null;
      const bookmark = explicit
        ? validBookmark(history)
          ? history
          : compatible(last || {}) && last?.chapterId === trackId
            ? last
            : null
        : last;
      return { trackId, narratorId, bookmark };
    }
    function trustedPilot(chapter) {
      return (
        object(pilot) &&
        chapter.id === pilot.id &&
        chapter.chapterId === (pilot.chapterId || pilot.id) &&
        chapter.audio?.sha256 === pilot.audio?.sha256 &&
        chapter.audio?.narratorId === pilot.audio?.narratorId
      );
    }
    function validateChapter(value, track, narratorId, recording) {
      const code = 'INVALID_CHAPTER';
      ensure(
        object(value) &&
          [1,2].includes(value.schemaVersion) &&
          value.bookId === manifest.bookId &&
          value.manuscriptVersion === manifest.manuscriptVersion &&
          value.chapterId === track.id &&
          nonempty(value.id),
        code,
        'The loaded chapter does not match this book release.',
      );
      ensure(
        object(value.audio) &&
          value.audio.narratorId === narratorId &&
          value.audio.sha256 === recording.audioSha256 &&
          safeURL(value.audio.src) &&
          finite(value.duration) &&
          value.duration > 0 &&
          (value.audio.decodedDuration === undefined ||
            (finite(value.audio.decodedDuration) &&
              Math.abs(value.duration - value.audio.decodedDuration) < 0.001)),
        code,
        'The chapter audio identity or media clock does not match its release record.',
      );
      ensure(
        value.readingExtent === undefined || ['full', 'excerpt'].includes(value.readingExtent),
        code,
        'The reading extent is invalid.',
      );
      ensure(
        Array.isArray(value.paragraphs) && value.paragraphs.length && Array.isArray(value.scenes),
        code,
        'Chapter prose or scene metadata is missing.',
      );
      const local = trustedPilot(value),
        all = [],
        ids = new Set();
      let prior = 0;
      for (let index = 0; index < value.paragraphs.length; index++) {
        const p = value.paragraphs[index],
          prefix = local ? '' : `${manifest.manuscriptVersion}:${track.id}:`,
          paragraphId = prefix + `p${String(index + 1).padStart(3, '0')}`;
        ensure(
          object(p) &&
            p.id === paragraphId &&
            nonempty(p.text) &&
            (p.headingLevel === undefined || Number.isInteger(p.headingLevel) && p.headingLevel >= 3 && p.headingLevel <= 6) &&
            Array.isArray(p.sentences) &&
            p.sentences.length &&
            Array.isArray(p.emphasis),
          code,
          'Chapter paragraphs need faithful text and qualified anchors.',
        );
        let cursor = 0;
        for (let offset = 0; offset < p.sentences.length; offset++) {
          const s = p.sentences[offset],
            id = paragraphId + `-s${String(offset + 1).padStart(2, '0')}`;
          ensure(
            object(s) &&
              s.id === id &&
              !ids.has(s.id) &&
              Number.isInteger(s.textStart) &&
              Number.isInteger(s.textEnd) &&
              cursor <= s.textStart &&
              s.textStart < s.textEnd &&
              s.textEnd <= p.text.length &&
              !p.text.slice(cursor, s.textStart).trim() &&
              p.text.slice(s.textStart, s.textEnd) === s.text &&
              (value.schemaVersion === 2 && s.syncStatus === 'unavailable'
                ? s.start === null && s.end === null
                : (s.syncStatus === undefined || s.syncStatus === 'measured') &&
                  finite(s.start) && finite(s.end) && prior <= s.start && s.start < s.end && s.end <= value.duration),
            code,
            'Sentence text, anchors, or timing cues are invalid.',
          );
          ids.add(s.id);
          cursor = s.textEnd;
          if (finite(s.end)) prior = s.end;
          all.push(s);
        }
        const measured=p.sentences.filter(s=>finite(s.start) && finite(s.end));
        ensure(
          !p.text.slice(cursor).trim() &&
            (measured.length
              ? finite(p.start) && finite(p.end) && Math.abs(p.start-measured[0].start)<.001 && Math.abs(p.end-measured.at(-1).end)<.001
              : value.schemaVersion === 2 && p.start === null && p.end === null),
          code,
          'Paragraph prose coverage or timing is incomplete.',
        );
        let emphasisEnd = 0;
        for (const e of p.emphasis) {
          ensure(
            object(e) &&
              Number.isInteger(e.start) &&
              Number.isInteger(e.end) &&
              emphasisEnd <= e.start &&
              e.start < e.end &&
              e.end <= p.text.length &&
              (e.kind === undefined || ['italic', 'bold'].includes(e.kind)),
            code,
            'Chapter emphasis ranges are invalid.',
          );
          emphasisEnd = e.end;
        }
      }
      if (value.blocks !== undefined) {
        ensure(Array.isArray(value.blocks) && value.blocks.length > 0, code, 'Chapter blocks are invalid.');
        let paragraphIndex = 0;
        for (const block of value.blocks) {
          ensure(object(block), code, 'Chapter blocks must contain supported prose or scene breaks.');
          if (block.kind === 'scene-break') {
            ensure(typeof block.markdown === 'string' && /^\s*(?:-{3,}|\*{3,})\s*$/.test(block.markdown) &&
              block.paragraphId === undefined, code, 'The chapter scene break is invalid.');
            continue;
          }
          const paragraph = value.paragraphs[paragraphIndex++];
          ensure(paragraph && block.paragraphId === paragraph.id &&
            block.kind === (paragraph.headingLevel === undefined ? 'paragraph' : 'heading'),
            code, 'Chapter blocks must reconstruct every paragraph exactly once in order.');
        }
        ensure(paragraphIndex === value.paragraphs.length, code, 'Chapter blocks omit prose.');
      }
      let priorScene = -1;
      const sceneIds = new Set();
      for (const scene of value.scenes) {
        ensure(
          object(scene) &&
            nonempty(scene.id) &&
            !sceneIds.has(scene.id) &&
            safeURL(scene.src) &&
            finite(scene.start) &&
            priorScene <= scene.start &&
            scene.start <= value.duration &&
            (scene.sentenceId === undefined || all.some(s=>s.id===scene.sentenceId && finite(s.start))),
          code,
          'Illustration metadata or its narration trigger is invalid.',
        );
        sceneIds.add(scene.id);
        priorScene = scene.start;
      }
      const uncertainWindows=[];
      for(let index=0;index<all.length;index++) {
        if(all[index].syncStatus!=='unavailable') continue;
        const previous=all.slice(0,index).findLast(s=>finite(s.end));
        const next=all.slice(index+1).find(s=>finite(s.start));
        uncertainWindows.push({start:previous?.end || 0,end:next?.start ?? value.duration});
      }
      return {
        chapter: copy(value),
        sentences: all.filter(s=>finite(s.start) && finite(s.end)),
        byId: new Map(all.map((s) => [s.id, s])),
        uncertainWindows,
        extent: value.readingExtent || (local ? 'excerpt' : 'full'),
      };
    }
    function legacyAnchorMeasured(bookmark) {
      // V1 did not record whether its clamped first-sentence fraction came
      // from that sentence or from the introduction. Inspect the exact source
      // clock before migrating local sentence IDs; another hash cannot supply it.
      const source = [active?.chapter, pilot].find(chapter => object(chapter) &&
        chapter.audio?.sha256 === bookmark.audioSha256 && chapter.bookId === bookmark.bookId &&
        chapter.manuscriptVersion === bookmark.manuscriptVersion && chapter.chapterId === bookmark.chapterId &&
        (!bookmark.readingExtentId || bookmark.readingExtentId === (chapter.readingExtentId || chapter.id)));
      if (!source || !Array.isArray(source.paragraphs) || !finite(source.duration)) return false;
      const sentences = source.paragraphs.flatMap(p => Array.isArray(p?.sentences) ? p.sentences : []);
      const measured = sentence => object(sentence) && finite(sentence.start) && finite(sentence.end) &&
        0 <= sentence.start && sentence.start < sentence.end && sentence.end <= source.duration;
      const first = sentences.find(measured), sentence = sentences.find(s => s?.id === bookmark.sentenceId);
      return !!(first && measured(sentence) && bookmark.audioTime >= first.start &&
        bookmark.audioTime >= sentence.start && bookmark.audioTime <= source.duration &&
        !(source === active?.chapter && active.uncertainWindows.some(window => bookmark.audioTime >= window.start &&
          (bookmark.audioTime < window.end || (window.end === source.duration && bookmark.audioTime === window.end)))));
    }
    function resolve(bookmark, loaded, track) {
      if (!bookmark) return { time: 0, completed: false, mapped: false };
      ensure(
        validBookmark(bookmark) && compatible(bookmark) && bookmark.chapterId === track.id,
        'BOOKMARK_IDENTITY',
        'The saved place does not belong to this chapter and manuscript.',
      );
      const chapter = loaded.chapter,
        extent = chapter.readingExtentId || chapter.id;
      if (bookmark.audioSha256 === chapter.audio.sha256) {
        const completed =
          bookmark.completed && (!bookmark.readingExtentId || bookmark.readingExtentId === extent);
        return {
          time: completed ? chapter.duration : clamp(bookmark.audioTime, 0, chapter.duration),
          completed,
          mapped: false,
        };
      }
      if (bookmark.completed && loaded.extent === 'full' && bookmark.readingExtentId === extent) {
        return { time: chapter.duration, completed: true, mapped: true };
      }
      const alias =
        manifest.legacyAliases?.[
          bookmark.readingExtentId || (!bookmark.sentenceId.includes(':') ? pilot?.id : '')
        ];
      const anchor =
        bookmark.completed && alias?.trackId === track.id && alias.completedExcerpt
          ? alias.completedExcerpt.sentenceId
          : (alias?.trackId === track.id && alias.sentenceIds[bookmark.sentenceId]) ||
            bookmark.sentenceId;
      const sentence = loaded.byId.get(anchor);
      const anchorMeasured = bookmark.anchorMeasured === true || (bookmark.anchorMeasured === undefined &&
        ((bookmark.completed && alias?.trackId === track.id && alias.completedExcerpt) || legacyAnchorMeasured(bookmark)));
      ensure(
        sentence && (bookmark.audioTime === 0 || (anchorMeasured && finite(sentence.start) && finite(sentence.end))),
        'MISSING_ANCHOR',
        'Your saved passage could not be mapped to this recording. Your old bookmark is kept; you can start this chapter from the beginning.',
      );
      if (bookmark.audioTime === 0) return { time: 0, completed: false, mapped: true };
      const fraction =
        bookmark.completed && alias?.completedExcerpt
          ? alias.completedExcerpt.sentenceFraction
          : bookmark.sentenceFraction;
      return {
        time: clamp(
          sentence.start + fraction * (sentence.end - sentence.start),
          0,
          chapter.duration,
        ),
        completed: false,
        mapped: true,
      };
    }
    async function load(trackId, narratorId, request = {}) {
      const token = ++sequence;
      abort?.abort();
      abort = new AbortController();
      const controller = abort;
      const { track, recording } = choice(trackId, narratorId),
        previous = active;
      try {
        const payload = await options.loadChapter(recording.url, {
          signal: controller.signal,
          trackId,
          narratorId,
        });
        if (token !== sequence) return { status: 'stale' };
        const loaded = validateChapter(payload, track, narratorId, recording);
        const stored = getHistory()[trackId]?.bookmark;
        const bookmark =
          request.startFromBeginning === true
            ? null
            : request.bookmark === undefined
            ? validBookmark(stored)
              ? stored
              : null
            : request.bookmark;
        const position = resolve(bookmark, loaded, track);
        if (token !== sequence) return { status: 'stale' };
        active = { trackId, narratorId, chapter: loaded.chapter, position, ...loaded };
        return { status: 'ready', chapter: active.chapter, position, previous };
      } catch (error) {
        if (token !== sequence) return { status: 'stale' };
        if (error instanceof BookError) throw error;
        throw new BookError(
          'LOAD_FAILED',
          'This chapter could not be loaded. Your previous chapter and saved place are kept. Try again.',
          error,
        );
      }
    }
    function capture(time, settings = {}) {
      if (!active) return null;
      const chapter = active.chapter,
        audioTime = clamp(finite(time) ? time : 0, 0, chapter.duration);
      let sentence = active.sentences[0] || chapter.paragraphs[0].sentences[0];
      for (const candidate of active.sentences) {
        if (candidate.start > audioTime) break;
        sentence = candidate;
      }
      return {
        schemaVersion: 1,
        bookId: manifest.bookId,
        manuscriptVersion: manifest.manuscriptVersion,
        chapterId: active.trackId,
        readingExtentId: chapter.readingExtentId || chapter.id,
        sentenceId: sentence.id,
        anchorMeasured: finite(sentence.start) && finite(sentence.end) && audioTime >= sentence.start &&
          !active.uncertainWindows.some(window=>audioTime>=window.start &&
            (audioTime<window.end || (window.end===chapter.duration && audioTime===window.end))),
        sentenceFraction: finite(sentence.start) && finite(sentence.end) ? clamp(
          (audioTime - sentence.start) / (sentence.end - sentence.start),
          0,
          1,
        ) : 0,
        narratorId: active.narratorId,
        audioSha256: chapter.audio.sha256,
        audioTime,
        completed: settings.completed === true && audioTime >= chapter.duration - 0.08,
        updatedAt: new Date().toISOString(),
      };
    }
    function save(time, settings = {}) {
      const bookmark = capture(time, settings);
      if (!bookmark) return null;
      const latest = readState(),
        value = latest && compatible(latest) ? copy(latest) : emptyState();
      const prior = value.history[active.trackId];
      value.lastPosition = bookmark;
      value.releaseId = manifest.releaseId;
      value.history[active.trackId] = {
        bookmark,
        completed: prior?.completed === true || (active.extent === 'full' && bookmark.completed),
        ...(prior?.completedAt
          ? { completedAt: prior.completedAt }
          : active.extent === 'full' && bookmark.completed
            ? { completedAt: bookmark.updatedAt }
            : {}),
      };
      memory = value;
      pendingHistory.set(active.trackId, copy(value.history[active.trackId]));
      pendingLastPosition = copy(bookmark);
      active.position = { time: bookmark.audioTime, completed: bookmark.completed, mapped: false };
      if (writeJSON(BOOKMARK_KEY, value)) {
        pendingHistory.clear();
        pendingLastPosition = null;
        writeJSON(LEGACY_BOOKMARK_KEY, bookmark);
      }
      observedState = JSON.stringify(readJSON(BOOKMARK_KEY));
      return bookmark;
    }
    return {
      initialSelection,
      load,
      getActive: () => active,
      getHistory,
      capture,
      save,
      readPreferences,
      savePreferences,
      getManifest: () => copy(manifest),
    };
  }
  const api = {
    create,
    validateManifest,
    BookError,
    BOOKMARK_KEY,
    LEGACY_BOOKMARK_KEY,
    PREFERENCES_KEY,
  };
  root.LumenBook = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
