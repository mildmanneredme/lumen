(() => {
  'use strict';
  const pilot = window.LUMEN_CHAPTER || window.LUMEN_PILOT_REFERENCE;
  let chapter = {title:'Lumen',duration:pilot?.duration || 1,paragraphs:[],audio:{narratorId:'autonoe'},scenes:[]};
  const $ = id => document.getElementById(id);
  const audio = $('narration'), pane = $('reading-pane'), prose = $('prose');
  const play = $('play'), seek = $('seek'), followButton = $('follow');
  if (!pilot || !window.LumenBook || !window.LumenProgress) {
    $('play-status').textContent = 'The book could not be loaded. Please reload.';
    play.disabled = true;
    return;
  }
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let duration = chapter.duration, scenes = [];
  let book, manifest, transitionToken = 0, retryTransition = null, continuationToken = 0, committing = false;
  let accessToken = 0;
  let automaticEndHandled = false;
  const descriptions = {
    'opening-room': 'Adrian sits in his Berkeley living room, watching a glass office tower on television; a cold coffee rests nearby.',
    'unnamed-suspect': 'The television shows an unnamed young man with a faint smile being escorted in handcuffs outside a glass office building.',
    'personal-connection': 'In the same living room, Adrian pauses with his hand near the remote as the television continues to show the tower.',
    'boardroom-dismissal': 'A memory of a corporate boardroom: polished shoes and a long table dominate the composition, with Adrian isolated at its far end.',
    'kitchen-calculation': 'Adrian sits alone in his modest kitchen at night, calculating what the equity he missed would be worth.',
    'cognitive-grid': 'A visual metaphor for the cognitive age: warm lights link a painted city of contemporary glass office towers.',
    'classroom-lever': 'Adrian teaches a university seminar, facing a group of attentive adult students with laptops.',
    'people-as-clay': 'A visual metaphor for behavioural manipulation: hands shape a soft clay bust with an unfinished, anonymous face.'
  };
  let follow = true, activeSentence = null, activeParagraph = null, activeScene = null;
  let sceneToken = 0, sceneTimer = 0, animationFrame = 0, lastSaved = -1;
  let pendingSeek = null, issuedSeek = null, resumeAfterSeek = false, seekTimer = 0;
  let progress, bookmarkDirty = false, completed = false;
  let artworkVisible = true, lastMediaUpdate = 0;
  const warmedScenes = new Set();
  const sentences = [], paragraphElements = new Map();
  const sentenceElements = new Map();
  const formatTime = seconds => {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    return Math.floor(value / 60) + ':' + String(value % 60).padStart(2,'0');
  };
  const getAt = (items,time) => {
    let left = 0, right = items.length - 1, result = null;
    while (left <= right) {
      const middle = (left + right) >> 1;
      if (items[middle].start <= time) { result = items[middle]; left = middle + 1; }
      else right = middle - 1;
    }
    return result;
  };
  const currentPosition = () => pendingSeek ?? audio.currentTime;
  function canSeekTo(time) {
    if (audio.readyState < 1) return false;
    if (time === 0) return true;
    for (let i = 0; i < audio.seekable.length; i++) {
      if (time >= audio.seekable.start(i) - .1 && time <= audio.seekable.end(i) + .1) return true;
    }
    return false;
  }
  function finishSeek() {
    if (pendingSeek === null) return;
    if (Math.abs(audio.currentTime - pendingSeek) > .35) {
      issuedSeek = null;
      setPlayState();
      return;
    }
    pendingSeek = issuedSeek = null;
    clearTimeout(seekTimer);
    if (completed) audio.pause();
    render(audio.currentTime, true);
    savePosition(true);
    const resume = resumeAfterSeek;
    resumeAfterSeek = false;
    setPlayState();
    if (resume && !completed && audio.currentTime < duration - .08) startPlayback();
  }
  function attemptSeek() {
    if (pendingSeek === null || issuedSeek === pendingSeek) return;
    if (!canSeekTo(pendingSeek)) { setPlayState(); return; }
    try {
      issuedSeek = pendingSeek;
      audio.currentTime = pendingSeek;
      if (!audio.seeking) finishSeek();
    } catch (_) {
      issuedSeek = null;
      setPlayState();
    }
  }
  function seekTo(value, restoring = false) {
    if (!book?.getActive()) return;
    const next = Math.min(duration, Math.max(0, Number(value) || 0));
    if (next < duration - .08) automaticEndHandled = false;
    if (!restoring) { bookmarkDirty = true; $('resume-panel').hidden = true; completed = next >= duration - .08; }
    resumeAfterSeek = resumeAfterSeek || !audio.paused;
    pendingSeek = next;
    issuedSeek = null;
    // Pausing while preparing a seek keeps narration and the displayed text together.
    if (!canSeekTo(next) && !audio.paused) audio.pause();
    render(next, true);
    savePosition(true);
    attemptSeek();
    setPlayState();
    clearTimeout(seekTimer);
    if (pendingSeek !== null && !audio.error) seekTimer = setTimeout(() => {
      if (pendingSeek !== null && !audio.error) $('seek-feedback').textContent = 'Still loading your place. Keep this page open, or reload if the connection has stopped.';
    }, 8000);
  }
  function setFollow(value, jump = false) {
    follow = value;
    followButton.setAttribute('aria-pressed', String(follow));
    followButton.textContent = follow ? 'Following text' : 'Follow text';
    if (jump) scrollToActive(true);
  }
  function scrollToActive(force = false) {
    if (!follow || !activeSentence) return;
    const element = sentenceElements.get(activeSentence.id);
    if (!element) return;
    const box = element.getBoundingClientRect(), frame = pane.getBoundingClientRect();
    if (force || box.top < frame.top + 45 || box.bottom > frame.bottom - 65) {
      const top = pane.scrollTop + box.top - frame.top - Math.max(60,frame.height * .26);
      pane.scrollTo({top:Math.max(0,top),behavior:reduced.matches || force ? 'auto' : 'smooth'});
    }
  }
  function clearScene() {
    ++sceneToken; clearTimeout(sceneTimer); activeScene = null; warmedScenes.clear();
    $('scene-stage').classList.add('placeholder-art'); $('scene-stage').removeAttribute('data-scene');
    $('scene-incoming').classList.remove('visible');
    $('scene-image').removeAttribute('src'); $('scene-incoming').removeAttribute('src'); $('scene-image').alt = '';
    $('scene-title').textContent = $('scene-number').textContent = '';
  }
  function showScene(scene) {
    if (!scene) {
      if (activeScene || !$('scene-stage').classList.contains('placeholder-art')) clearScene();
      return;
    }
    if (activeScene?.id === scene.id) return;
    activeScene = scene;
    const token = ++sceneToken;
    const stage = $('scene-stage'), image = $('scene-image'), incoming = $('scene-incoming');
    stage.dataset.scene = scene.id;
    $('scene-title').textContent = scene.title;
    $('scene-number').textContent = String(scenes.indexOf(scene) + 1).padStart(2,'0') + ' / ' + String(scenes.length).padStart(2,'0');
    clearTimeout(sceneTimer);
    incoming.classList.remove('visible');
    const preload = new Image();
    preload.onload = () => {
      if (token !== sceneToken) return;
      stage.classList.remove('placeholder-art');
      incoming.src = scene.src;
      image.alt = scene.alt || scene.description || descriptions[scene.id] || scene.title || 'Illustration';
      const next = scenes[scenes.indexOf(scene) + 1];
      if (next && !warmedScenes.has(next.id) && !navigator.connection?.saveData) {
        warmedScenes.add(next.id);
        const ahead = new Image(); ahead.decoding = 'async'; ahead.fetchPriority = 'low'; ahead.src = next.src;
      }
      if (reduced.matches || audio.paused) { image.src = scene.src; return; }
      requestAnimationFrame(() => {
        if (token !== sceneToken) return;
        incoming.classList.add('visible');
        sceneTimer = setTimeout(() => {
          if (token !== sceneToken) return;
          image.src = scene.src;
          incoming.classList.remove('visible');
        },1300);
      });
    };
    preload.onerror = () => {
      if (token === sceneToken) $('play-status').textContent = 'Illustration unavailable';
    };
    preload.src = scene.src;
  }
  function render(time = currentPosition(), force = false) {
    if (committing || !book?.getActive()) return;
    const clamped = Math.min(duration,Math.max(0,time || 0));
    seek.value = clamped;
    seek.style.setProperty('--progress', (clamped / duration * 100) + '%');
    seek.setAttribute('aria-valuetext', Math.floor(clamped / 60) + ' minutes ' + Math.floor(clamped % 60) + ' seconds');
    $('elapsed').textContent = formatTime(clamped);
    updateMediaSession(force);
    showScene(getAt(scenes,clamped));
    const candidate = getAt(sentences,clamped);
    const sentence = candidate && clamped <= candidate.end ? candidate : null;
    const paragraph = sentence ? getAt(chapter.paragraphs.filter(p => Number.isFinite(p.start)),clamped) : null;
    if (sentence?.id !== activeSentence?.id) {
      if (activeSentence) sentenceElements.get(activeSentence.id)?.classList.remove('active');
      activeSentence = sentence;
      if (sentence) sentenceElements.get(sentence.id)?.classList.add('active');
      scrollToActive(force);
    } else if (force) scrollToActive(true);
    if (paragraph?.id !== activeParagraph?.id) {
      if (activeParagraph) paragraphElements.get(activeParagraph.id)?.classList.remove('active-paragraph');
      activeParagraph = paragraph;
      if (paragraph) paragraphElements.get(paragraph.id)?.classList.add('active-paragraph');
    }
  }
  function appendText(parent,text,start,end,emphasis) {
    let cursor = start;
    for (const mark of emphasis) {
      const a = Math.max(start,mark.start), b = Math.min(end,mark.end);
      if (b <= a) continue;
      if (a > cursor) parent.append(document.createTextNode(text.slice(cursor,a)));
      const em = document.createElement(mark.kind === 'bold' ? 'strong' : 'em'); em.textContent = text.slice(a,b); parent.append(em); cursor = b;
    }
    if (cursor < end) parent.append(document.createTextNode(text.slice(cursor,end)));
  }
  function buildProse() {
  for (const block of chapter.blocks || chapter.paragraphs.map(p => ({kind:'paragraph',paragraphId:p.id}))) {
    if (block.kind === 'scene-break') { prose.append(document.createElement('hr')); continue; }
    const paragraph = chapter.paragraphs.find(p => p.id === block.paragraphId);
    if (!paragraph) continue;
    const p = document.createElement(paragraph.headingLevel ? 'h' + paragraph.headingLevel : 'p'); p.id = paragraph.id;
    paragraphElements.set(paragraph.id,p);
    let cursor = 0;
    for (const sentence of paragraph.sentences) {
      if (sentence.textStart > cursor) appendText(p,paragraph.text,cursor,sentence.textStart,paragraph.emphasis);
      const span = document.createElement('span');
      span.className = 'sentence'; span.id = sentence.id;
      const measured = Number.isFinite(sentence.start) && Number.isFinite(sentence.end);
      if (!measured) {
        span.classList.add('unsynchronized');
        appendText(span,paragraph.text,sentence.textStart,sentence.textEnd,paragraph.emphasis);
        p.append(span); sentenceElements.set(sentence.id,span); cursor = sentence.textEnd;
        continue;
      }
      span.setAttribute('role','button'); span.tabIndex = sentences.length ? -1 : 0;
      span.setAttribute('aria-label','Listen: ' + sentence.text);
      appendText(span,paragraph.text,sentence.textStart,sentence.textEnd,paragraph.emphasis);
      const activate = () => { setFollow(true); seekTo(sentence.start); };
      span.addEventListener('click',activate);
      span.addEventListener('keydown',event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault(); event.stopPropagation();
          const next = sentences[sentences.indexOf(sentence) + (event.key === 'ArrowDown' ? 1 : -1)];
          if (next) { span.tabIndex = -1; const target = sentenceElements.get(next.id); target.tabIndex = 0; target.focus(); }
        }
      });
      p.append(span); sentenceElements.set(sentence.id,span); sentences.push(sentence); cursor = sentence.textEnd;
    }
    if (cursor < paragraph.text.length) appendText(p,paragraph.text,cursor,paragraph.text.length,paragraph.emphasis);
    prose.append(p);
  }
  }
  function savePosition(force = false) {
    if (committing || !progress || !book?.getActive() || !bookmarkDirty) return;
    const time = currentPosition();
    if (!force && Math.abs(time-lastSaved) < 2) return;
    lastSaved = time;
    book.save(time, {completed: completed || (pendingSeek === null && audio.ended)});
    renderChapterNavigation();
  }
  function savePreferences() {
    const preferences = {speed:audio.playbackRate, textSize:Number($('text-size').value), narratorId:chapter.audio.narratorId,
      artworkVisible, autoContinue:$('auto-continue').checked};
    if (book) book.savePreferences(preferences); else progress.savePreferences(preferences);
  }
  function setArtworkVisible(value) {
    artworkVisible = value;
    document.body.classList.toggle('art-hidden', !value);
    $('art-toggle').setAttribute('aria-pressed', String(value));
    $('art-toggle').setAttribute('aria-label', value ? 'Hide artwork' : 'Show artwork');
  }
  function updateMediaSession(force = false) {
    if (!('mediaSession' in navigator) || committing || !book?.getActive()) return;
    const now = performance.now();
    if (!force && now - lastMediaUpdate < 1000) return;
    lastMediaUpdate = now;
    navigator.mediaSession.playbackState = audio.paused ? 'paused' : 'playing';
    if (navigator.mediaSession.setPositionState && Number.isFinite(audio.duration) && audio.duration > 0) {
      try { navigator.mediaSession.setPositionState({duration:audio.duration, playbackRate:audio.playbackRate,
        position:Math.max(0,Math.min(audio.duration,audio.currentTime))}); } catch (_) {}
    }
  }
  function setPlayState() {
    const ended = completed || currentPosition() >= duration-.08;
    const waiting = pendingSeek !== null;
    const errorMessage = audio.error ? (navigator.onLine ? 'Narration could not be loaded. Select Play to try again.' : 'You’re offline. Reconnect to listen; your place is saved.') : '';
    $('seek-feedback').hidden = !waiting && !errorMessage;
    if (errorMessage) $('seek-feedback').textContent = errorMessage;
    else if (waiting) $('seek-feedback').textContent = 'Loading your place at ' + formatTime(pendingSeek) + '…';
    const wantsPlayback = !audio.paused || (waiting && resumeAfterSeek);
    $('play-icon').textContent = wantsPlayback ? 'Ⅱ' : '▶';
    play.setAttribute('aria-label', ended ? 'Replay audiobook' : wantsPlayback ? 'Pause audiobook' : 'Play audiobook');
    $('play-status').textContent = errorMessage || (ended ? endingLabel() : chapter.title + (audio.paused ? ' · paused' : ' · listening'));
    updateMediaSession(true);
  }
  async function startPlayback() {
    try { await audio.play(); } catch (_) { $('play-status').textContent = 'Playback could not start. Select Play to try again.'; }
  }
  async function togglePlayback() {
    if (!book?.getActive()) return;
    bookmarkDirty = true;
    $('resume-panel').hidden = true;
    if (audio.error) {
      const retryAt = currentPosition();
      audio.load(); pendingSeek = retryAt; issuedSeek = null; resumeAfterSeek = false;
    }
    if (completed) { startOver(true); return; }
    if (pendingSeek !== null) {
      if (!audio.paused) { resumeAfterSeek = false; audio.pause(); }
      else resumeAfterSeek = !resumeAfterSeek;
      attemptSeek();
      // Load the first audio packet from this user gesture on browsers that defer preloading.
      if (pendingSeek !== null && resumeAfterSeek && audio.readyState < 1) {
        try { await audio.play(); if (pendingSeek !== null) audio.pause(); } catch (_) {}
      }
      setPlayState();
      return;
    }
    if (!audio.paused) { audio.pause(); return; }
    if (audio.ended || audio.currentTime >= duration-.08) seekTo(0);
    if (pendingSeek !== null) { resumeAfterSeek = true; return; }
    await startPlayback();
  }
  function tick() {
    render(); savePosition();
    if (!audio.paused && !audio.ended) animationFrame = requestAnimationFrame(tick);
  }
  play.addEventListener('click',togglePlayback);
  $('back').addEventListener('click',() => seekTo(currentPosition()-15));
  $('forward').addEventListener('click',() => seekTo(currentPosition()+15));
  seek.max = duration;
  seek.addEventListener('input',() => seekTo(seek.value));
  seek.addEventListener('change',setPlayState);
  followButton.addEventListener('click',() => setFollow(!follow,true));
  $('art-toggle').addEventListener('click',() => {
    setArtworkVisible(!artworkVisible); savePreferences();
    requestAnimationFrame(() => scrollToActive(true));
  });
  for (const event of ['wheel','touchmove']) pane.addEventListener(event,() => setFollow(false),{passive:true});
  pane.addEventListener('keydown',event => {
    if (['ArrowDown','ArrowUp','PageDown','PageUp','Home','End'].includes(event.key)) setFollow(false);
  });
  $('speed').addEventListener('change',() => { audio.playbackRate = Number($('speed').value); savePreferences(); render(); });
  function startOver(listen = false) {
    resumeAfterSeek = false;
    audio.pause(); setFollow(true); seekTo(0);
    pane.scrollTo({top:0,behavior:'auto'});
    if (listen) { if (pendingSeek !== null) resumeAfterSeek = true; else startPlayback(); }
  }
  $('replay').addEventListener('click',() => startOver(true));
  $('start-over').addEventListener('click',() => startOver());
  $('resume-continue').addEventListener('click',togglePlayback);
  audio.addEventListener('play',() => { setPlayState(); cancelAnimationFrame(animationFrame); tick(); });
  audio.addEventListener('pause',() => { cancelAnimationFrame(animationFrame); render(); savePosition(true); setPlayState(); });
  audio.addEventListener('timeupdate',() => { render(); savePosition(); });
  audio.addEventListener('seeked',() => { finishSeek(); render(currentPosition(),true); setPlayState(); });
  audio.addEventListener('ended',() => {
    if (pendingSeek !== null) return;
    completed = true; cancelAnimationFrame(animationFrame); render(duration); savePosition(true); setPlayState();
    if (automaticEndHandled) return;
    automaticEndHandled = true;
    const token = ++continuationToken;
    if ($('auto-continue').checked && nextTrack(1)) {
      queueMicrotask(() => { if (token === continuationToken && completed) moveTrack(1, true); });
    }
  });
  audio.addEventListener('waiting',() => { $('play-status').textContent = 'Loading narration…'; });
  audio.addEventListener('playing',setPlayState);
  audio.addEventListener('error',() => { clearTimeout(seekTimer); setPlayState(); });
  for (const event of ['loadedmetadata','progress','canplay','loadeddata']) audio.addEventListener(event,attemptSeek);
  const settings = $('settings');
  $('settings-open').addEventListener('click',() => settings.showModal());
  $('settings-close').addEventListener('click',() => settings.close());
  settings.addEventListener('click',event => { if (event.target === settings) { const r=settings.getBoundingClientRect(); if (event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom) settings.close(); } });
  function setTextSize(value) {
    const size = Math.min(28,Math.max(18,Number(value) || 20));
    document.documentElement.style.setProperty('--text-size',size+'px');
    $('text-size').value = size; $('text-size-value').textContent = size+' px';
  }
  $('text-size').addEventListener('input',() => { setTextSize($('text-size').value); savePreferences(); });
  window.addEventListener('pagehide',() => savePosition(true));
  document.addEventListener('visibilitychange',() => { if (document.visibilityState === 'hidden') savePosition(true); });
  document.addEventListener('keydown',event => {
    const interactive = event.target.closest('button,input,select,[role=button],dialog');
    if (interactive || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.code === 'Space') { event.preventDefault(); togglePlayback(); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); seekTo(currentPosition()-15); }
    if (event.key === 'ArrowRight') { event.preventDefault(); seekTo(currentPosition()+15); }
  });
  clearScene();
  progress = window.LumenProgress.create(pilot, pilot.paragraphs.flatMap(p => p.sentences));
  const saved = progress.readPreferences();
  if ([.75,1,1.25,1.5,2].includes(Number(saved.speed))) { audio.playbackRate=Number(saved.speed); $('speed').value=saved.speed; }
  setTextSize(saved.textSize);
  setArtworkVisible(saved.artworkVisible !== false);
  function updateMediaMetadata() {
    if ('mediaSession' in navigator && 'MediaMetadata' in window) navigator.mediaSession.metadata = new MediaMetadata({
      title: 'Lumen · ' + chapter.title, artist: chapter.author, album: 'Lumen',
      artwork: [{src:new URL('icons/icon-192.png', location.href).href,sizes:'192x192',type:'image/png'},
                {src:new URL('icons/icon-512.png', location.href).href,sizes:'512x512',type:'image/png'}]
    });
  }
  if ('mediaSession' in navigator) {
    const actions = {
      play: () => { if (audio.paused && !resumeAfterSeek) togglePlayback(); },
      pause: () => { resumeAfterSeek = false; audio.pause(); savePosition(true); setPlayState(); },
      stop: () => { resumeAfterSeek = false; audio.pause(); savePosition(true); setPlayState(); },
      seekbackward: details => seekTo(currentPosition() - (details.seekOffset || 15)),
      seekforward: details => seekTo(currentPosition() + (details.seekOffset || 15)),
      seekto: details => { if (Number.isFinite(details.seekTime)) seekTo(details.seekTime); },
      previoustrack: () => moveTrack(-1), nexttrack: () => moveTrack(1)
    };
    for (const [action, handler] of Object.entries(actions)) {
      try { navigator.mediaSession.setActionHandler(action, handler); } catch (_) {}
    }
  }
  function nextTrack(offset) {
    if (!manifest || !book?.getActive()) return null;
    const index = manifest.tracks.findIndex(track => track.id === chapter.chapterId);
    return manifest.tracks[index + offset] || null;
  }
  function endingLabel() {
    if (chapter.readingExtent === 'excerpt' || chapter.id === pilot.id) return 'End of the pilot';
    return nextTrack(1) ? 'End of ' + chapter.title : 'End of the book';
  }
  function renderChapterNavigation() {
    if (!manifest || !book?.getActive()) return;
    const index = manifest.tracks.findIndex(track => track.id === chapter.chapterId);
    const history = book.getHistory(), count = manifest.tracks.filter(track => track.kind === 'story').length;
    const finished = manifest.tracks.filter(track => track.kind === 'story' && history[track.id]?.completed).length;
    $('book-progress').textContent = manifest.tracks.length === 1 ? 'Opening excerpt' :
      (index + 1) + ' / ' + manifest.tracks.length + ' tracks · ' + finished + ' / ' + count + ' chapters finished';
    $('track-prev').disabled = !nextTrack(-1); $('track-next').disabled = !nextTrack(1);
    $('chapter-continue').hidden = !nextTrack(1);
    for (const element of $('chapter-list').querySelectorAll('[data-track-id]')) {
      const current = element.dataset.trackId === chapter.chapterId;
      if (current) element.setAttribute('aria-current','page'); else element.removeAttribute('aria-current');
      const done = history[element.dataset.trackId]?.completed === true;
      element.querySelector('.chapter-state').textContent = done ? 'Finished' : current ? 'Current' : element.dataset.available;
    }
  }
  function narratorOptions(trackId, narratorId) {
    const track = manifest?.tracks.find(track => track.id === trackId);
    if (!track) return;
    $('narrator').replaceChildren();
    for (const narrator of manifest.narrators) {
      const option = document.createElement('option'); option.value = narrator.id;
      const available = track.recordings[narrator.id]?.status === 'ready';
      option.textContent = narrator.label + (available ? '' : ' · unavailable'); option.disabled = !available;
      $('narrator').append(option);
    }
    $('narrator').value = narratorId;
    $('narrator').disabled = !Array.from($('narrator').options).some(option => !option.disabled);
  }
  function makeChapterList() {
    $('chapter-list').replaceChildren(); let lastPart;
    for (const track of manifest.tracks) {
      if (track.part !== lastPart && track.part != null) {
        const heading = document.createElement('h3'); heading.textContent = track.partTitle ||
          (typeof track.part === 'number' ? 'Part ' + track.part : track.part);
        $('chapter-list').append(heading); lastPart = track.part;
      }
      const button = document.createElement('button'); button.type = 'button'; button.className = 'chapter-choice';
      button.dataset.trackId = track.id;
      const available = Object.values(track.recordings).some(recording => recording.status === 'ready');
      button.dataset.available = available ? '' : 'Unavailable';
      const label = document.createElement('span'); label.textContent = track.title;
      const state = document.createElement('span'); state.className = 'chapter-state'; state.textContent = button.dataset.available;
      button.append(label,state);
      button.addEventListener('click',() => {
        $('chapters').close(); transitionTo(track.id, chapter.audio.narratorId, {reason:'chapter'});
      });
      $('chapter-list').append(button);
    }
  }
  function updateURL(trackId,narratorId,mode) {
    if (mode === 'none') return;
    const url = new URL(location.href); url.searchParams.set('chapter',trackId); url.searchParams.set('voice',narratorId);
    const state = {trackId,narratorId};
    if (mode === 'replace') history.replaceState(state,'',url); else history.pushState(state,'',url);
  }
  function commitChapter(payload,position,{initial=false,listen=false}={}) {
    committing = true;
    audio.pause(); cancelAnimationFrame(animationFrame); clearTimeout(seekTimer); clearTimeout(sceneTimer);
    ++sceneToken; ++continuationToken;
    pendingSeek = issuedSeek = null; resumeAfterSeek = false;
    activeSentence = activeParagraph = activeScene = null; lastSaved = -1; bookmarkDirty = false;
    warmedScenes.clear(); sentences.length = 0; sentenceElements.clear(); paragraphElements.clear(); prose.replaceChildren();
    chapter = payload; window.LUMEN_CHAPTER = chapter; duration = chapter.duration;
    scenes = chapter.scenes.map(scene => ({...scene,src:localAssetURL(scene.src || 'assets/' + scene.id + '.webp').href}));
    completed = position.completed === true; automaticEndHandled = completed;
    buildProse(); progress = window.LumenProgress.create(chapter,sentences);
    const preferences = book.readPreferences();
    $('chapter-title').textContent = chapter.title;
    $('part-title').textContent = chapter.partTitle || (typeof chapter.part === 'number' ? 'Part ' + chapter.part : chapter.part || 'Lumen');
    $('edition-name').textContent = chapter.readingExtent === 'excerpt' || chapter.id === pilot.id ? 'Opening excerpt' :
      manifest.narrators.find(narrator => narrator.id === chapter.audio.narratorId)?.label || 'Audiobook';
    prose.setAttribute('aria-label',chapter.title + ' prose');
    $('ending-label').textContent = endingLabel();
    $('total').textContent = $('excerpt-duration').textContent = formatTime(duration); seek.max = duration;
    clearScene(); $('art-toggle').disabled = false;
    setArtworkVisible(preferences.artworkVisible); setTextSize(preferences.textSize);
    $('auto-continue').checked = preferences.autoContinue === true;
    audio.playbackRate = preferences.speed; $('speed').value = preferences.speed;
    const src = localAssetURL(chapter.audio.src);
    if (!src.pathname.startsWith('/api/assets/')) src.searchParams.set('v',chapter.audio.sha256.slice(0,12));
    audio.src = src.href;
    narratorOptions(chapter.chapterId,chapter.audio.narratorId); renderChapterNavigation(); updateMediaMetadata();
    pane.scrollTo({top:0,behavior:'auto'}); setFollow(true);
    $('resume-panel').hidden = true;
    if (initial && (position.time > 0 || completed)) {
      $('resume-panel').hidden = false;
      $('resume-message').textContent = completed ? (chapter.id === pilot.id ? 'You’ve finished the opening excerpt.' : 'You’ve finished ' + chapter.title + '.') : 'Your place is saved · ' + chapter.title;
      $('resume-continue').textContent = completed ? 'Listen again' : 'Continue from ' + formatTime(position.time);
    }
    committing = false; bookmarkDirty = true;
    seekTo(position.time,true);
    play.disabled = false;
    if (listen && !completed) { if (pendingSeek !== null) resumeAfterSeek = true; else startPlayback(); }
    setPlayState();
  }
  async function transitionTo(trackId,narratorId,options={}) {
    if (!book) return;
    const token = ++transitionToken, oldActive = book.getActive();
    const listen = options.listen ?? (!audio.paused || resumeAfterSeek);
    const sameTrackVoice = options.reason === 'voice' && oldActive?.trackId === trackId;
    const bookmark = options.bookmark === undefined && sameTrackVoice ? book.capture(currentPosition(),{completed}) : options.bookmark;
    if (oldActive) savePosition(true);
    resumeAfterSeek = false; audio.pause();
    $('transition-panel').hidden = false; $('transition-status').textContent = 'Loading chapter…'; $('transition-retry').hidden = true;
    retryTransition = {trackId,narratorId,options:{...options,bookmark,listen}};
    narratorOptions(trackId,narratorId);
    try {
      const result = await book.load(trackId,narratorId,{bookmark});
      if (result.status === 'stale' || token !== transitionToken) return;
      commitChapter(result.chapter,result.position,{initial:options.initial,listen});
      updateURL(trackId,narratorId,options.history || (sameTrackVoice || options.initial ? 'replace' : 'push'));
      $('transition-panel').hidden = true; retryTransition = null;
    } catch (error) {
      if (token !== transitionToken) return;
      $('transition-status').textContent = error.message || 'This chapter could not be loaded. Try again.';
      $('transition-retry').hidden = false;
      if (error.code === 'ACCESS_REQUIRED' || error.cause?.code === 'ACCESS_REQUIRED') { closeAccess(); return; }
      if (oldActive && listen) startPlayback();
    }
  }
  function moveTrack(offset,listen) {
    const track = nextTrack(offset);
    if (track) transitionTo(track.id,chapter.audio.narratorId,{reason:'chapter',listen});
  }
  $('track-prev').addEventListener('click',() => moveTrack(-1));
  $('track-next').addEventListener('click',() => moveTrack(1));
  $('chapter-continue').addEventListener('click',() => moveTrack(1,true));
  $('chapters-open').addEventListener('click',() => $('chapters').showModal());
  $('chapters-close').addEventListener('click',() => $('chapters').close());
  $('transition-retry').addEventListener('click',() => { if (retryTransition) transitionTo(retryTransition.trackId,retryTransition.narratorId,retryTransition.options); });
  $('narrator').addEventListener('change',() => {
    const target = retryTransition?.trackId || chapter.chapterId;
    transitionTo(target,$('narrator').value,{reason:'voice'});
  });
  $('auto-continue').addEventListener('change',savePreferences);
  window.addEventListener('popstate',() => {
    if (!book) return;
    const params = new URL(location.href).searchParams;
    try { const selection = book.initialSelection({trackId:params.get('chapter'),narratorId:params.get('voice')});
      transitionTo(selection.trackId,selection.narratorId,{bookmark:selection.bookmark,reason:'history',history:'none'});
    } catch (error) { $('transition-panel').hidden = false; $('transition-status').textContent = error.message; }
  });
  async function bootstrap() {
    const token = ++accessToken;
    play.disabled = true; $('play-status').textContent = 'Loading your book…';
    try {
      const session = await fetch('/api/session',{cache:'no-store'});
      if (token !== accessToken) return;
      if (!session.ok) throw new Error('The reading room could not be reached. Try again.');
      const sessionState = await session.json();
      if (token !== accessToken) return;
      if (sessionState.authenticated !== true) { closeAccess(); return; }
      document.body.classList.add('authenticated'); $('sign-out').hidden = $('sign-out-settings').hidden = false;
      const response = await fetch('/api/book',{cache:'no-store'});
      if (token !== accessToken) return;
      if (response.status === 401) { closeAccess(); return; }
      if (!response.ok) throw new Error('The book index could not be loaded. Reconnect and try again.');
      const loadedManifest = await response.json();
      if (token !== accessToken) return;
      manifest = loadedManifest;
      book = window.LumenBook.create({manifest,pilot,loadChapter:async (url,{signal,trackId,narratorId}) => {
        const record = manifest.tracks.find(track => track.id === trackId)?.recordings[narratorId];
        if (window.LUMEN_CHAPTER === pilot && trackId === pilot.chapterId && narratorId === pilot.audio.narratorId && record?.audioSha256 === pilot.audio.sha256) return pilot;
        const loaded = await fetch(localAssetURL(url),{signal,cache:'no-store'});
        if (loaded.status === 401) { const error = new Error('Your invitation needs to be reopened.'); error.code = 'ACCESS_REQUIRED'; throw error; }
        if (!loaded.ok) throw new Error('Chapter response ' + loaded.status);
        return loaded.json();
      }});
      window.LUMEN_BOOK = book; makeChapterList();
      const params = new URL(location.href).searchParams;
      const selection = book.initialSelection({trackId:params.get('chapter'),narratorId:params.get('voice')});
      await transitionTo(selection.trackId,selection.narratorId,{bookmark:selection.bookmark,initial:true,listen:false});
    } catch (error) {
      if (token !== accessToken) return;
      if (!document.body.classList.contains('authenticated')) { $('access-status').textContent = error.message; return; }
      $('transition-panel').hidden = false; $('transition-status').textContent = error.message;
      $('transition-retry').hidden = false;
      retryTransition = null;
    }
  }
  $('transition-retry').addEventListener('click',() => { if (!retryTransition) bootstrap(); });
  function localAssetURL(value) {
    const url = new URL(value,location.href);
    // A preview uses its own authenticated host while retaining production's
    // immutable asset paths. Cookies never travel to a separate media origin.
    if (url.origin === 'https://lumen-phi-five.vercel.app' && url.pathname.startsWith('/api/assets/')) {
      return new URL(url.pathname,location.origin);
    }
    return url;
  }
  function closeAccess() {
    ++accessToken; ++transitionToken; ++sceneToken; ++continuationToken;
    resumeAfterSeek = false; savePosition(true); committing = true;
    audio.pause(); audio.removeAttribute('src'); audio.load();
    cancelAnimationFrame(animationFrame); clearTimeout(seekTimer); clearTimeout(sceneTimer);
    pendingSeek = issuedSeek = null; bookmarkDirty = false; book = null; manifest = null; retryTransition = null;
    prose.replaceChildren(); sentences.length = 0; sentenceElements.clear(); paragraphElements.clear();
    window.LUMEN_CHAPTER = undefined; window.LUMEN_BOOK = undefined;
    clearScene();
    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = null; navigator.mediaSession.playbackState = 'none';
      try { navigator.mediaSession.setPositionState?.(); } catch (_) {}
    }
    for (const dialog of [$('settings'),$('chapters')]) if (dialog.open) dialog.close();
    $('resume-panel').hidden = $('transition-panel').hidden = true; $('sign-out').hidden = $('sign-out-settings').hidden = true;
    document.body.classList.remove('authenticated'); committing = false;
    $('access-status').textContent = 'Use your invitation to open the book. Your listening place is saved on this browser.';
  }
  function inviteToken(value) {
    const text = value.trim();
    if (/^[A-Za-z0-9_-]{43}$/.test(text)) return text;
    try { return new URLSearchParams(new URL(text).hash.slice(1)).get('invite') || ''; } catch (_) { return ''; }
  }
  async function activateInvite(value) {
    const token = inviteToken(value);
    if (!/^[A-Za-z0-9_-]{43}$/.test(token)) { $('access-status').textContent = 'Open or paste the invitation link you received.'; return; }
    const attempt = ++accessToken;
    $('invite-submit').disabled = true; $('access-status').textContent = 'Opening your reading room…';
    try {
      const response = await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({invite:token})});
      if (attempt !== accessToken) return;
      if (!response.ok) throw new Error('This invitation could not be verified. Check the link and try again.');
      const sessionState = await response.json();
      if (attempt !== accessToken) return;
      if (sessionState.authenticated !== true) throw new Error('This invitation could not be verified. Check the link and try again.');
      $('invite-code').value = ''; await bootstrap();
    } catch (error) { $('access-status').textContent = error.message || 'Your invitation could not be opened. Try again.'; }
    finally { $('invite-submit').disabled = false; }
  }
  $('invite-form').addEventListener('submit',event => { event.preventDefault(); activateInvite($('invite-code').value); });
  async function signOut() {
    ++accessToken;
    try {
      const response = await fetch('/api/session',{method:'DELETE'});
      if (!response.ok) throw new Error('Sign out could not finish. Please try again.');
      closeAccess();
    } catch (error) { $('transition-panel').hidden = false; $('transition-status').textContent = error.message; }
  }
  $('sign-out').addEventListener('click',signOut);
  $('sign-out-settings').addEventListener('click',signOut);
  audio.addEventListener('error',async () => {
    if (!book) return;
    const token = accessToken, activeBook = book;
    try { const response = await fetch('/api/session',{cache:'no-store'});
      if (token !== accessToken || book !== activeBook || !response.ok) return;
      const session = await response.json();
      if (token === accessToken && book === activeBook && session.authenticated !== true) closeAccess();
    } catch (_) { /* A connection failure keeps the existing retry and bookmark. */ }
  });
  function consumeInvitation() {
    const invited = new URLSearchParams(location.hash.slice(1)).get('invite');
    if (!invited) return false;
    const clean = new URL(location.href); clean.hash = ''; history.replaceState(null,'',clean);
    activateInvite(invited); return true;
  }
  window.addEventListener('hashchange',consumeInvitation);
  if (!consumeInvitation()) bootstrap();
})();
