(() => {
  'use strict';
  const pilot = window.LUMEN_CHAPTER || window.LUMEN_PILOT_REFERENCE;
  let chapter = {title:'Lumen',duration:pilot?.duration || 1,paragraphs:[],audio:{narratorId:'autonoe'},scenes:[]};
  const $ = id => document.getElementById(id);
  const audio = $('narration'), pane = $('reading-pane'), prose = $('prose');
  const play = $('play'), seek = $('seek'), followButton = $('follow');
  const icons = window.LumenIcons;
  // Labels change independently of the decorative SVG, including restored state.
  function setControlLabel(control, label) {
    const target = control.querySelector('.control-label');
    if (target) target.textContent = label;
    else control.textContent = label;
  }
  if (!pilot || !window.LumenBook || !window.LumenProgress) {
    $('play-status').textContent = 'The book could not be loaded. Please reload.';
    play.disabled = true;
    return;
  }
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const phoneArtwork = window.matchMedia('(max-width: 700px)');
  let duration = chapter.duration, scenes = [];
  let book, manifest, transitionToken = 0, retryTransition = null, continuationToken = 0, committing = false;
  let pendingTransition = null, accessToken = 0, inviteRequest = 0, retainedInvite = '', sessionExchange = null;
  let canFlagAudio = false, flagGeneration = 0, flagDraft = null, savedAudioFlags = [];
  let flagRequest = null, flagListRequest = null, flagPreviewEnd = null, flagPreviewTimer = 0, flagPreviewGeneration = 0;
  const flagPlayer = $('audio-flag-player');
  const transitionStart = document.createElement('button');
  transitionStart.id = 'transition-start'; transitionStart.type = 'button'; transitionStart.className = 'text-button';
  transitionStart.textContent = 'Start this chapter from the beginning'; transitionStart.hidden = true;
  icons?.set(transitionStart, 'restart', transitionStart.textContent);
  transitionStart.setAttribute('aria-describedby','transition-status'); $('transition-panel').append(transitionStart);
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
  let follow = true, activeSentence = null, activeParagraph = null, activeScene = null, activeSceneSource = '';
  let displayedSceneId = null;
  let sceneToken = 0, sceneTimer = 0, sceneRetryTimer = 0, sceneLoadState = 'idle', animationFrame = 0, lastSaved = -1;
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
    stopFlagPreview();
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
    stopFlagPreview();
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
    const label = follow ? 'Following text' : 'Follow text';
    setControlLabel(followButton, label);
    followButton.setAttribute('aria-label', label); followButton.title = label;
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
  function resetSceneDisplay() {
    clearTimeout(sceneTimer);
    displayedSceneId = null;
    $('scene-stage').classList.add('placeholder-art'); $('scene-stage').removeAttribute('data-scene');
    $('scene-incoming').classList.remove('visible');
    $('scene-image').removeAttribute('src'); $('scene-incoming').removeAttribute('src'); $('scene-image').alt = '';
    $('scene-title').textContent = $('scene-number').textContent = '';
  }
  function clearScene() {
    ++sceneToken; clearTimeout(sceneRetryTimer); sceneLoadState = 'idle';
    activeScene = null; activeSceneSource = ''; warmedScenes.clear();
    resetSceneDisplay();
  }
  function sceneStageVisible() {
    if (!artworkVisible) return false;
    const bounds = $('scene-stage').getBoundingClientRect();
    return bounds.width > 0 && bounds.height > 0;
  }
  function cancelSceneLoading() {
    ++sceneToken; clearTimeout(sceneRetryTimer); clearTimeout(sceneTimer);
    $('scene-incoming').classList.remove('visible');
    if (sceneLoadState === 'loading' || sceneLoadState === 'retrying' || displayedSceneId !== activeScene?.id ||
        $('scene-image').getAttribute('src') !== activeSceneSource) {
      sceneLoadState = 'idle';
    }
  }
  function sceneSource(scene) {
    if (!scene.phoneSrc) return scene.src;
    const width = $('scene-stage').getBoundingClientRect().width;
    const pixels = width * Math.max(1, window.devicePixelRatio || 1);
    return navigator.connection?.saveData || phoneArtwork.matches || (width > 0 && pixels <= 960)
      ? scene.phoneSrc : scene.src;
  }
  function showScene(scene) {
    if (!scene) {
      if (activeScene || !$('scene-stage').classList.contains('placeholder-art')) clearScene();
      return;
    }
    // Hidden panels have no usable dimensions. Select a variant when artwork
    // is shown, rather than downloading a full image based on zero width.
    if (!sceneStageVisible()) { cancelSceneLoading(); return; }
    const source = sceneSource(scene);
    if (activeScene?.id === scene.id && activeSceneSource === source && sceneLoadState !== 'idle') return;
    // Rewinding must clear a later reveal while its earlier painting loads.
    if (activeScene && scene.start < activeScene.start) clearScene();
    activeScene = scene;
    activeSceneSource = source;
    clearTimeout(sceneRetryTimer);
    const token = ++sceneToken;
    const stage = $('scene-stage'), image = $('scene-image'), incoming = $('scene-incoming');
    stage.dataset.scene = scene.id;
    $('scene-title').textContent = scene.title;
    $('scene-number').textContent = String(scenes.indexOf(scene) + 1).padStart(2,'0') + ' / ' + String(scenes.length).padStart(2,'0');
    clearTimeout(sceneTimer);
    incoming.classList.remove('visible');
    function canContinuePainting() {
      if (token !== sceneToken) return false;
      if (sceneStageVisible()) return true;
      cancelSceneLoading();
      return false;
    }
    function loadPainting(attempt) {
      if (!canContinuePainting()) return;
      sceneLoadState = 'loading';
      const preload = new Image();
      preload.onload = () => {
        if (!canContinuePainting()) return;
        sceneLoadState = 'loaded';
        stage.classList.remove('placeholder-art');
        incoming.src = source;
        image.alt = scene.alt || scene.description || descriptions[scene.id] || scene.title || 'Illustration';
        const next = scenes[scenes.indexOf(scene) + 1];
        const nextSource = next && sceneSource(next);
        if (next && !warmedScenes.has(nextSource) && !navigator.connection?.saveData) {
          warmedScenes.add(nextSource);
          const ahead = new Image(); ahead.decoding = 'async'; ahead.fetchPriority = 'low'; ahead.src = nextSource;
        }
        if (reduced.matches || audio.paused) { image.src = source; displayedSceneId = scene.id; return; }
        requestAnimationFrame(() => {
          if (!canContinuePainting()) return;
          incoming.classList.add('visible');
          sceneTimer = setTimeout(() => {
            if (!canContinuePainting()) return;
            image.src = source;
            displayedSceneId = scene.id;
            incoming.classList.remove('visible');
          },1300);
        });
      };
      preload.onerror = () => {
        if (!canContinuePainting()) return;
        if (attempt === 0) {
          sceneLoadState = 'retrying';
          sceneRetryTimer = setTimeout(() => loadPainting(1),1000);
        } else {
          sceneLoadState = 'failed';
          if (displayedSceneId !== scene.id || !image.getAttribute('src')) resetSceneDisplay();
        }
      };
      preload.src = source;
    }
    loadPainting(0);
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
  function displayProse(text) {
    return text.replace(/\u2014/g,'-');
  }
  function appendText(parent,text,start,end,emphasis) {
    let cursor = start;
    for (const mark of emphasis) {
      const a = Math.max(start,mark.start), b = Math.min(end,mark.end);
      if (b <= a) continue;
      if (a > cursor) parent.append(document.createTextNode(displayProse(text.slice(cursor,a))));
      const em = document.createElement(mark.kind === 'bold' ? 'strong' : 'em'); em.textContent = displayProse(text.slice(a,b)); parent.append(em); cursor = b;
    }
    if (cursor < end) parent.append(document.createTextNode(displayProse(text.slice(cursor,end))));
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
      span.setAttribute('aria-label','Listen: ' + displayProse(sentence.text));
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
  function savePreferences(changes) {
    if (book) book.savePreferences(changes); else progress.savePreferences(changes);
  }
  function setArtworkVisible(value) {
    artworkVisible = value;
    document.body.classList.toggle('art-hidden', !value);
    $('art-toggle').setAttribute('aria-checked', String(value));
    $('art-toggle').title = value ? 'Hide artwork' : 'Show artwork';
    if (value) render(currentPosition());
    else cancelSceneLoading();
  }
  function updateMediaSession(force = false) {
    if (!('mediaSession' in navigator) || committing || !book?.getActive()) return;
    const now = performance.now();
    if (!force && now - lastMediaUpdate < 1000) return;
    lastMediaUpdate = now;
    const player = flagPreviewEnd !== null && !flagPlayer.paused ? flagPlayer : audio;
    navigator.mediaSession.playbackState = player.paused ? 'paused' : 'playing';
    if (navigator.mediaSession.setPositionState && Number.isFinite(player.duration) && player.duration > 0) {
      try { navigator.mediaSession.setPositionState({duration:player.duration, playbackRate:player.playbackRate,
        position:Math.max(0,Math.min(player.duration,player.currentTime))}); } catch (_) {}
    }
  }
  function setPlayState() {
    const ended = completed || currentPosition() >= duration-.08;
    const waiting = pendingSeek !== null;
    const errorMessage = audio.error ? (navigator.onLine ? 'Narration could not be loaded. Select Play to try again.' : 'You’re offline. Reconnect to listen; your place is saved.') : '';
    $('seek-feedback').hidden = !waiting && !errorMessage;
    if (errorMessage) $('seek-feedback').textContent = errorMessage;
    else if (waiting) $('seek-feedback').textContent = 'Loading your place at ' + formatTime(pendingSeek) + '…';
    const wantsPlayback = pendingTransition?.listen ?? (!audio.paused || (waiting && resumeAfterSeek));
    const iconName = ended && !pendingTransition ? 'replay' : wantsPlayback ? 'pause' : 'play';
    const iconHost = $('play-icon');
    if (iconHost.dataset.lumenIcon !== iconName) {
      iconHost.dataset.lumenIcon = iconName;
      if (icons) icons.hydrate(iconHost);
      else iconHost.textContent = wantsPlayback ? 'Ⅱ' : '▶';
    }
    play.setAttribute('aria-label', ended && !pendingTransition ? 'Replay audiobook' : wantsPlayback ? 'Pause audiobook' : 'Play audiobook');
    play.title = play.getAttribute('aria-label');
    $('play-status').textContent = errorMessage || (ended ? endingLabel() : chapter.title + (audio.paused ? ' · paused' : ' · listening'));
    updateMediaSession(true);
  }
  async function startPlayback() {
    stopFlagPreview();
    try { await audio.play(); } catch (_) { $('play-status').textContent = 'Playback could not start. Select Play to try again.'; }
  }
  function setListeningIntent(listen) {
    if (pendingTransition) pendingTransition.listen = listen;
    if (retryTransition) retryTransition.options.listen = listen;
  }
  async function togglePlayback() {
    if (!book?.getActive()) return;
    stopFlagPreview();
    bookmarkDirty = true;
    $('resume-panel').hidden = true;
    if (pendingTransition) {
      setListeningIntent(!pendingTransition.listen);
      setPlayState();
      return;
    }
    setListeningIntent(audio.paused && !resumeAfterSeek);
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
    setArtworkVisible(!artworkVisible); savePreferences({artworkVisible});
    requestAnimationFrame(() => scrollToActive(true));
  });
  for (const event of ['wheel','touchmove']) pane.addEventListener(event,() => setFollow(false),{passive:true});
  pane.addEventListener('keydown',event => {
    if (['ArrowDown','ArrowUp','PageDown','PageUp','Home','End'].includes(event.key)) setFollow(false);
  });
  $('speed').addEventListener('change',() => { audio.playbackRate = Number($('speed').value); savePreferences({speed:audio.playbackRate}); render(); });
  function startOver(listen = false) {
    stopFlagPreview();
    setListeningIntent(listen);
    resumeAfterSeek = false;
    audio.pause(); setFollow(true); seekTo(0);
    pane.scrollTo({top:0,behavior:'auto'});
    if (listen && !pendingTransition) { if (pendingSeek !== null) resumeAfterSeek = true; else startPlayback(); }
  }
  $('replay').addEventListener('click',() => startOver(true));
  $('start-over').addEventListener('click',() => startOver());
  $('resume-continue').addEventListener('click',togglePlayback);
  audio.addEventListener('play',() => { if (!audio.paused) stopFlagPreview(); setPlayState(); cancelAnimationFrame(animationFrame); tick(); });
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
  function stopFlagPreview(clear = false) {
    ++flagPreviewGeneration;
    flagPreviewEnd = null; clearTimeout(flagPreviewTimer); flagPlayer.pause();
    $('audio-flag-preview').textContent = 'Preview snippet';
    if (clear) { flagPlayer.removeAttribute('src'); flagPlayer.load(); }
    updateMediaSession(true);
  }
  function clearAudioFlags() {
    ++flagGeneration;
    flagRequest?.abort(); flagListRequest?.abort(); flagRequest = flagListRequest = null;
    stopFlagPreview(true); flagDraft = null; savedAudioFlags = [];
    for (const id of ['audio-flag-dialog','audio-flags-dialog']) {
      if ($(id).open) $(id).close(); $(id).hidden = true;
    }
    $('audio-flag-form').reset(); $('audio-flags-list').replaceChildren();
    for (const id of ['audio-flag-context','audio-flag-excerpt','audio-flag-status','audio-flags-status']) $(id).textContent = '';
    $('audio-flags-export').disabled = true;
  }
  function updateFlagControls() {
    $('audio-flag-open').hidden = $('audio-flag-settings').hidden = !canFlagAudio || !book?.getActive();
    $('audio-flag-open').disabled = !!pendingTransition;
  }
  function setFlagPermission(value) {
    const allowed = value === true;
    if (!allowed) clearAudioFlags();
    canFlagAudio = allowed; updateFlagControls();
  }
  function flagTime(value) {
    // Keep the displayed endpoint inside fractional decoded recording durations.
    const truncated = Math.floor(value * 1000) / 1000;
    return Math.floor(truncated / 60) + ':' + (truncated % 60).toFixed(3).padStart(6,'0');
  }
  function parseFlagTime(value) {
    const text = value.trim();
    if (/^\d+(?:\.\d{1,3})?$/.test(text)) return Number(text);
    const match = /^(\d+):([0-5]\d(?:\.\d{1,3})?)$/.exec(text);
    return match ? Number(match[1]) * 60 + Number(match[2]) : NaN;
  }
  function flagRange() {
    const startText = $('audio-flag-start').value, endText = $('audio-flag-end').value;
    // Display milliseconds, but retain the mastered clock's full precision until edited.
    const start = flagDraft && startText === flagDraft.initialRange.startText ? flagDraft.initialRange.start : parseFlagTime(startText);
    const end = flagDraft && endText === flagDraft.initialRange.endText ? flagDraft.initialRange.end : parseFlagTime(endText);
    if (!flagDraft || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end > flagDraft.duration || end <= start || end - start > 120) return null;
    return {start,end};
  }
  function flagContext(record) {
    const title = manifest?.tracks.find(track => track.id === record.chapterId)?.title || record.chapterId;
    const voice = manifest?.narrators.find(narrator => narrator.id === record.narratorId)?.label || record.narratorId;
    return title + ' · ' + voice;
  }
  function flagExcerpt(range) {
    if (!range || !flagDraft) return '';
    const relevant = flagDraft.sentences.filter(sentence => Number.isFinite(sentence.start) && Number.isFinite(sentence.end) && sentence.end > range.start && sentence.start < range.end);
    return relevant.length ? relevant.map(sentence => displayProse(sentence.text)).join(' ') : 'Sentence timing is unavailable here. Preview the selected audio to check the range.';
  }
  function updateFlagForm() {
    const range = flagRange(), pending = !!flagRequest, saved = !!flagDraft?.saved, stale = !!flagDraft?.stale;
    for (const id of ['audio-flag-start','audio-flag-end','audio-flag-note']) $(id).disabled = pending || saved || stale;
    $('audio-flag-save').disabled = !range || pending || saved || stale;
    $('audio-flag-save').textContent = pending ? 'Saving…' : saved ? 'Saved' : 'Save flag';
    $('audio-flag-preview').disabled = !range || stale;
    $('audio-flag-excerpt').textContent = flagExcerpt(range);
    if (!range && flagDraft) $('audio-flag-status').textContent = 'Enter a valid range of up to 2 minutes within this recording.';
  }
  function openAudioFlag() {
    const active = book?.getActive();
    if (!canFlagAudio || !active || pendingTransition) return;
    clearAudioFlags();
    const time = Math.max(0,Math.min(duration,currentPosition()));
    resumeAfterSeek = false; setListeningIntent(false); audio.pause(); savePosition(true);
    const canonical = chapter.paragraphs.flatMap(paragraph => paragraph.sentences || []);
    const cue = canonical.find(sentence => Number.isFinite(sentence.start) && Number.isFinite(sentence.end) && sentence.start <= time &&
      (time < sentence.end || (time === duration && sentence.end === duration)));
    let start = cue?.start ?? Math.max(0,time - 10), end = cue?.end ?? time;
    if (end <= start) end = Math.min(duration,start + 10);
    end = Math.min(end,start + 120,duration);
    const initialRange = {start,end,startText:flagTime(start),endText:flagTime(end)};
    flagDraft = {id:crypto.randomUUID(),releaseId:manifest.releaseId,chapterId:active.trackId,narratorId:active.narratorId,
      audioSha256:chapter.audio.sha256,duration,sentences:canonical,saved:false,payload:null,stale:false};
    flagDraft.initialRange = initialRange;
    $('audio-flag-context').textContent = flagContext(flagDraft);
    $('audio-flag-start').value = initialRange.startText; $('audio-flag-end').value = initialRange.endText;
    $('audio-flag-note').value = ''; $('audio-flag-status').textContent = cue ? 'The current sentence is selected. Adjust the range if needed.' : 'The last 10 seconds are selected. Adjust the range if needed.';
    flagPlayer.src = audio.currentSrc || audio.src;
    updateFlagForm(); $('audio-flag-dialog').hidden = false; $('audio-flag-dialog').showModal();
  }
  function flagResponseCurrent(generation, token, draft) {
    const active = book?.getActive();
    return canFlagAudio && generation === flagGeneration && token === accessToken &&
      (!draft || (draft === flagDraft && active?.trackId === draft.chapterId && active?.narratorId === draft.narratorId));
  }
  function handleFlagAccess(response) {
    if (response.status === 401) { closeAccess(); return true; }
    if (response.status === 403) {
      setFlagPermission(false);
      $('transition-panel').hidden = false; $('transition-status').textContent = 'Audio flagging access has expired. Reopen your owner invitation to flag audio.';
      $('transition-retry').hidden = true; transitionStart.hidden = true;
      return true;
    }
    return false;
  }
  $('audio-flag-open').addEventListener('click',openAudioFlag);
  $('audio-flag-close').addEventListener('click',() => $('audio-flag-dialog').close());
  $('audio-flag-dialog').addEventListener('close',() => {
    if ($('audio-flag-dialog').open) return;
    stopFlagPreview(true);
    ++flagGeneration; flagRequest?.abort(); flagRequest = null; flagDraft = null;
    $('audio-flag-form').reset(); $('audio-flag-context').textContent = $('audio-flag-excerpt').textContent = $('audio-flag-status').textContent = '';
    $('audio-flag-dialog').hidden = true;
  });
  for (const id of ['audio-flag-start','audio-flag-end','audio-flag-note']) $(id).addEventListener('input',() => {
    if (!flagDraft) return;
    stopFlagPreview();
    if (flagDraft.payload) { flagDraft.id = crypto.randomUUID(); flagDraft.payload = null; }
    $('audio-flag-status').textContent = ''; updateFlagForm();
  });
  function watchFlagPreview(generation = flagPreviewGeneration) {
    if (generation !== flagPreviewGeneration || flagPreviewEnd === null || flagPlayer.paused) return;
    if (flagPlayer.currentTime >= flagPreviewEnd || flagPlayer.ended) { stopFlagPreview(); return; }
    flagPreviewTimer = setTimeout(() => watchFlagPreview(generation),Math.max(10,Math.min(50,(flagPreviewEnd-flagPlayer.currentTime)*1000/flagPlayer.playbackRate)));
  }
  flagPlayer.addEventListener('playing',() => updateMediaSession(true));
  flagPlayer.addEventListener('timeupdate',() => {
    if (flagPreviewEnd !== null && flagPlayer.currentTime >= flagPreviewEnd) stopFlagPreview();
    else updateMediaSession();
  });
  flagPlayer.addEventListener('pause',() => {
    if (flagPlayer.paused && flagPreviewEnd !== null) stopFlagPreview();
    else updateMediaSession(true);
  });
  flagPlayer.addEventListener('ended',() => { if (flagPlayer.ended) stopFlagPreview(); });
  flagPlayer.addEventListener('error',() => {
    stopFlagPreview();
    if (flagDraft) $('audio-flag-status').textContent = 'Preview could not load. Reconnect and try again.';
  });
  $('audio-flag-preview').addEventListener('click',async () => {
    const range = flagRange(), draft = flagDraft, generation = flagGeneration, token = accessToken;
    if (!range || !canFlagAudio || !draft) return;
    if (flagPreviewEnd !== null || !flagPlayer.paused) { stopFlagPreview(); return; }
    audio.pause(); resumeAfterSeek = false; setListeningIntent(false);
    const previewGeneration = ++flagPreviewGeneration;
    try {
      flagPlayer.currentTime = range.start; flagPlayer.playbackRate = audio.playbackRate;
      flagPreviewEnd = range.end; await flagPlayer.play();
      if (previewGeneration !== flagPreviewGeneration) return;
      if (!flagResponseCurrent(generation,token,draft)) { stopFlagPreview(); return; }
      $('audio-flag-preview').textContent = 'Stop preview'; updateMediaSession(true); watchFlagPreview();
    } catch (_) {
      if (previewGeneration !== flagPreviewGeneration) return;
      stopFlagPreview();
      if (flagResponseCurrent(generation,token,draft)) $('audio-flag-status').textContent = 'Preview could not start. Reconnect and try again.';
    }
  });
  $('audio-flag-form').addEventListener('submit',async event => {
    event.preventDefault();
    const range = flagRange(), draft = flagDraft, generation = flagGeneration, token = accessToken;
    if (!range || !draft || !canFlagAudio || flagRequest || draft.saved || draft.stale) return;
    stopFlagPreview();
    draft.payload ||= {id:draft.id,releaseId:draft.releaseId,chapterId:draft.chapterId,narratorId:draft.narratorId,
      audioSha256:draft.audioSha256,start:range.start,end:range.end,note:$('audio-flag-note').value.trim()};
    const controller = flagRequest = new AbortController();
    $('audio-flag-status').textContent = 'Saving your flag…'; updateFlagForm();
    try {
      const response = await fetch('/api/audio-flags',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(draft.payload),cache:'no-store',signal:controller.signal});
      if (!flagResponseCurrent(generation,token,draft)) return;
      if (handleFlagAccess(response)) return;
      if (response.status === 409) {
        draft.stale = true;
        $('audio-flag-status').textContent = 'This recording has changed. Close this flag and reload the chapter, then create a fresh flag.';
        return;
      }
      if (!response.ok) throw new Error('Flag response ' + response.status);
      const result = await response.json();
      if (!flagResponseCurrent(generation,token,draft)) return;
      if (result.flag?.id !== draft.id) throw new Error('The saved flag could not be verified.');
      draft.saved = true; $('audio-flag-status').textContent = 'Saved for regeneration. Find it in Settings → Saved audio flags.';
    } catch (error) {
      if (flagResponseCurrent(generation,token,draft) && error.name !== 'AbortError')
        $('audio-flag-status').textContent = 'Your flag has not been confirmed saved. Reconnect, then select Save flag to retry.';
    } finally {
      if (flagRequest === controller) flagRequest = null;
      if (flagResponseCurrent(generation,token,draft)) updateFlagForm();
    }
  });
  async function loadAudioFlags() {
    if (!canFlagAudio || !book?.getActive()) return;
    flagListRequest?.abort();
    const controller = flagListRequest = new AbortController(), generation = flagGeneration, token = accessToken;
    savedAudioFlags = []; $('audio-flags-list').replaceChildren(); $('audio-flags-export').disabled = true;
    $('audio-flags-status').textContent = 'Loading saved flags…';
    try {
      const response = await fetch('/api/audio-flags',{cache:'no-store',signal:controller.signal});
      if (!flagResponseCurrent(generation,token) || flagListRequest !== controller) return;
      if (handleFlagAccess(response)) return;
      if (!response.ok) throw new Error('Flag list response ' + response.status);
      const result = await response.json();
      if (!flagResponseCurrent(generation,token) || flagListRequest !== controller) return;
      if (!Array.isArray(result.flags)) throw new Error('Invalid flag list');
      savedAudioFlags = result.flags.slice(0,100);
      for (const record of savedAudioFlags) {
        const item = document.createElement('li'), heading = document.createElement('strong');
        heading.textContent = flagContext(record) + ' · ' + flagTime(record.start) + '–' + flagTime(record.end);
        item.append(heading);
        for (const text of [record.note,record.text]) if (text) { const paragraph = document.createElement('p'); paragraph.textContent = text; item.append(paragraph); }
        $('audio-flags-list').append(item);
      }
      $('audio-flags-status').textContent = savedAudioFlags.length ? savedAudioFlags.length + ' saved flag' + (savedAudioFlags.length === 1 ? '' : 's') + (result.truncated === true ? ' · newest shown; older flags omitted because this list is large' : savedAudioFlags.length === 100 ? ' · latest 100 shown' : '') : 'No audio flags saved yet.';
      $('audio-flags-export').disabled = !savedAudioFlags.length;
    } catch (error) {
      if (flagResponseCurrent(generation,token) && flagListRequest === controller && error.name !== 'AbortError')
        $('audio-flags-status').textContent = 'Saved flags could not load. Reconnect, then select Refresh.';
    } finally { if (flagListRequest === controller) flagListRequest = null; }
  }
  $('audio-flags-open').addEventListener('click',() => {
    if (!canFlagAudio) return;
    settings.close(); $('audio-flags-dialog').hidden = false; $('audio-flags-dialog').showModal(); loadAudioFlags();
  });
  $('audio-flags-refresh').addEventListener('click',loadAudioFlags);
  $('audio-flags-close').addEventListener('click',() => $('audio-flags-dialog').close());
  $('audio-flags-dialog').addEventListener('close',() => {
    if ($('audio-flags-dialog').open) return;
    flagListRequest?.abort(); flagListRequest = null; savedAudioFlags = [];
    $('audio-flags-list').replaceChildren(); $('audio-flags-status').textContent = ''; $('audio-flags-export').disabled = true;
    $('audio-flags-dialog').hidden = true;
  });
  $('audio-flags-export').addEventListener('click',() => {
    if (!canFlagAudio || !savedAudioFlags.length) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({schemaVersion:1,bookId:manifest.bookId,exportedAt:new Date().toISOString(),flags:savedAudioFlags},null,2) + '\n'],{type:'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = 'lumen-audio-flags-' + new Date().toISOString().slice(0,10) + '.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  function setTextSize(value) {
    const size = Math.min(28,Math.max(18,Number(value) || 20));
    document.documentElement.style.setProperty('--text-size',size+'px');
    $('text-size').value = size; $('text-size-value').textContent = size+' px';
  }
  $('text-size').addEventListener('input',() => { setTextSize($('text-size').value); savePreferences({textSize:Number($('text-size').value)}); });
  window.addEventListener('pagehide',() => savePosition(true));
  document.addEventListener('visibilitychange',() => { if (document.visibilityState === 'hidden') savePosition(true); });
  document.addEventListener('keydown',event => {
    const interactive = event.target.closest('button,input,select,textarea,[role=button],dialog');
    if (interactive || document.querySelector('dialog[open]') || event.ctrlKey || event.metaKey || event.altKey) return;
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
      play: () => { stopFlagPreview(); if (pendingTransition) { setListeningIntent(true); setPlayState(); } else if (audio.paused && !resumeAfterSeek) togglePlayback(); },
      pause: () => { stopFlagPreview(); setListeningIntent(false); resumeAfterSeek = false; audio.pause(); savePosition(true); setPlayState(); },
      stop: () => { stopFlagPreview(); setListeningIntent(false); resumeAfterSeek = false; audio.pause(); savePosition(true); setPlayState(); },
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
      element.dataset.state = done ? 'finished' : current ? 'current' : 'ready';
      const marker = element.querySelector('.chapter-marker');
      if (marker && icons) { marker.dataset.lumenIcon = done ? 'check' : current ? 'bookmark' : 'book-open'; icons.hydrate(marker); }
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
  function requestedNarrator() {
    return pendingTransition?.narratorId || chapter.audio.narratorId;
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
      const marker = document.createElement('span'); marker.className = 'chapter-marker'; marker.setAttribute('aria-hidden','true');
      marker.dataset.lumenIcon = 'book-open'; icons?.hydrate(marker);
      const label = document.createElement('span'); label.className = 'chapter-label'; label.textContent = track.title;
      const state = document.createElement('span'); state.className = 'chapter-state'; state.textContent = button.dataset.available;
      const arrow = icons?.create('arrow-right');
      button.append(marker,label,state); if (arrow) button.append(arrow);
      button.addEventListener('click',() => {
        $('chapters').close(); transitionTo(track.id, requestedNarrator(), {reason:'chapter'});
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
    clearAudioFlags();
    committing = true;
    audio.pause(); cancelAnimationFrame(animationFrame); clearTimeout(seekTimer); clearTimeout(sceneTimer);
    ++sceneToken; ++continuationToken;
    pendingSeek = issuedSeek = null; resumeAfterSeek = false;
    activeSentence = activeParagraph = activeScene = null; lastSaved = -1; bookmarkDirty = false;
    warmedScenes.clear(); sentences.length = 0; sentenceElements.clear(); paragraphElements.clear(); prose.replaceChildren();
    chapter = payload; window.LUMEN_CHAPTER = chapter; duration = chapter.duration;
    scenes = chapter.scenes.map(scene => ({...scene,src:localAssetURL(scene.src || 'assets/' + scene.id + '.webp').href,
      ...(scene.phoneSrc ? {phoneSrc:localAssetURL(scene.phoneSrc).href} : {})}));
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
      const resumeLabel = completed ? 'Listen again' : 'Continue from ' + formatTime(position.time);
      if (icons) icons.set($('resume-continue'), completed ? 'replay' : 'play', resumeLabel);
      else setControlLabel($('resume-continue'), resumeLabel);
    }
    committing = false; bookmarkDirty = true;
    seekTo(position.time,true);
    play.disabled = false;
    updateFlagControls();
    if (listen && !completed) { if (pendingSeek !== null) resumeAfterSeek = true; else startPlayback(); }
    setPlayState();
  }
  async function transitionTo(trackId,narratorId,options={}) {
    if (!book) return;
    clearAudioFlags();
    const token = ++transitionToken, oldActive = book.getActive();
    const listen = options.listen ?? pendingTransition?.listen ?? (!audio.paused || resumeAfterSeek);
    const resumePrevious = pendingTransition?.resumePrevious ?? (!audio.paused && !audio.ended && !completed);
    const persistNarrator = options.reason === 'voice' || options.persistNarrator === true ||
      (options.reason === 'chapter' && pendingTransition?.persistNarrator === true && pendingTransition.narratorId === narratorId);
    pendingTransition = {listen,resumePrevious,narratorId,persistNarrator};
    updateFlagControls();
    const sameTrackVoice = options.reason === 'voice' && oldActive?.trackId === trackId;
    const bookmark = options.bookmark === undefined && sameTrackVoice ? book.capture(currentPosition(),{completed}) : options.bookmark;
    if (oldActive) savePosition(true);
    resumeAfterSeek = false; audio.pause();
    $('transition-panel').hidden = false; $('transition-status').textContent = 'Loading chapter…'; $('transition-retry').hidden = true;
    transitionStart.hidden = true;
    retryTransition = {trackId,narratorId,options:{...options,bookmark,listen,persistNarrator}};
    narratorOptions(trackId,narratorId);
    setPlayState();
    try {
      const result = await book.load(trackId,narratorId,{bookmark,startFromBeginning:options.startFromBeginning === true});
      if (result.status === 'stale' || token !== transitionToken) return;
      const requestedListening = pendingTransition.listen, persistCommittedNarrator = pendingTransition.persistNarrator;
      pendingTransition = null;
      commitChapter(result.chapter,result.position,{initial:options.initial,listen:requestedListening});
      if (persistCommittedNarrator) book.savePreferences({narratorId:result.chapter.audio.narratorId});
      updateURL(trackId,narratorId,options.history || (sameTrackVoice || options.initial ? 'replace' : 'push'));
      $('transition-panel').hidden = true; retryTransition = null;
    } catch (error) {
      if (token !== transitionToken) return;
      const requestedListening = pendingTransition.listen;
      pendingTransition = null;
      updateFlagControls();
      $('transition-status').textContent = error.message || 'This chapter could not be loaded. Try again.';
      $('transition-retry').hidden = false;
      retryTransition.errorCode = error.code; transitionStart.hidden = error.code !== 'MISSING_ANCHOR';
      if (error.code === 'ACCESS_REQUIRED' || error.cause?.code === 'ACCESS_REQUIRED') { closeAccess(); return; }
      if (oldActive && resumePrevious && requestedListening) startPlayback();
      setPlayState();
    }
  }
  function moveTrack(offset,listen) {
    const track = nextTrack(offset);
    if (track) transitionTo(track.id,requestedNarrator(),{reason:'chapter',listen});
  }
  $('track-prev').addEventListener('click',() => moveTrack(-1));
  $('track-next').addEventListener('click',() => moveTrack(1));
  $('chapter-continue').addEventListener('click',() => moveTrack(1,true));
  $('chapters-open').addEventListener('click',() => $('chapters').showModal());
  $('chapters-close').addEventListener('click',() => $('chapters').close());
  $('transition-retry').addEventListener('click',() => {
    if (!retryTransition) return;
    const {trackId,narratorId,options} = retryTransition;
    const bookmark = options.reason === 'voice' && book?.getActive()?.trackId === trackId ? undefined : options.bookmark;
    transitionTo(trackId,narratorId,{...options,bookmark});
  });
  transitionStart.addEventListener('click',() => {
    if (transitionStart.hidden || retryTransition?.errorCode !== 'MISSING_ANCHOR') return;
    const {trackId,narratorId,options} = retryTransition;
    transitionTo(trackId,narratorId,{...options,bookmark:null,startFromBeginning:true,listen:false});
  });
  $('narrator').addEventListener('change',() => {
    const target = retryTransition?.trackId || chapter.chapterId;
    transitionTo(target,$('narrator').value,{reason:'voice'});
  });
  $('auto-continue').addEventListener('change',() => savePreferences({autoContinue:$('auto-continue').checked}));
  window.addEventListener('popstate',() => {
    if (!book) return;
    const params = new URL(location.href).searchParams;
    try { const selection = book.initialSelection({trackId:params.get('chapter'),narratorId:params.get('voice')});
      transitionTo(selection.trackId,selection.narratorId,{bookmark:selection.bookmark,reason:'history',history:'none'});
    } catch (error) { transitionStart.hidden = true; retryTransition = null; $('transition-panel').hidden = false; $('transition-status').textContent = error.message; }
  });
  async function bootstrap() {
    const token = ++accessToken;
    setFlagPermission(false);
    transitionStart.hidden = true;
    play.disabled = true; $('play-status').textContent = 'Loading your book…';
    try {
      const session = await fetch('/api/session',{cache:'no-store'});
      if (token !== accessToken) return;
      if (!session.ok) throw new Error('The reading room could not be reached. Try again.');
      const sessionState = await session.json();
      if (token !== accessToken) return;
      if (sessionState.authenticated !== true) { closeAccess(); return; }
      setFlagPermission(sessionState.canFlagAudio);
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
        if (loaded.status === 401) { const error = new Error('Enter your access code again to continue.'); error.code = 'ACCESS_REQUIRED'; throw error; }
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
      retryTransition = null; transitionStart.hidden = true;
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
  window.addEventListener('resize', () => render(currentPosition()));
  window.addEventListener('online', () => {
    if (sceneLoadState === 'failed') { sceneLoadState = 'idle'; render(currentPosition()); }
  });
  function closeAccess() {
    setFlagPermission(false);
    ++accessToken; ++inviteRequest; ++transitionToken; ++sceneToken; ++continuationToken;
    cancelSessionExchange();
    retainedInvite = ''; pendingTransition = null; $('invite-code').required = true; $('invite-submit').disabled = false;
    resumeAfterSeek = false; savePosition(true); committing = true;
    audio.pause(); audio.removeAttribute('src'); audio.load();
    cancelAnimationFrame(animationFrame); clearTimeout(seekTimer); clearTimeout(sceneTimer);
    pendingSeek = issuedSeek = null; bookmarkDirty = false; book = null; manifest = null; retryTransition = null; transitionStart.hidden = true;
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
    $('access-status').textContent = 'Use your access code to open the book. Your listening place is saved on this browser.';
  }
  function canonicalAccessCode(value) {
    if (value.length > 256) return '';
    const words = value.trim().toLowerCase().split(/[\s\u2010-\u2015-]+/);
    const valid = (words.length === 6 && words.every(word => /^[a-z]{3,5}$/.test(word))) ||
      (words.length === 2 && words.every(word => /^[a-z]{3,16}$/.test(word)));
    return valid ? words.join('-') : '';
  }
  function inviteToken(value) {
    const text = value.trim();
    if (/^[A-Za-z0-9_-]{43}$/.test(text)) return text;
    const code = canonicalAccessCode(value);
    if (code) return code;
    try {
      const invited = new URLSearchParams(new URL(text).hash.slice(1)).get('invite') || '';
      return /^[A-Za-z0-9_-]{43}$/.test(invited) ? invited : canonicalAccessCode(invited);
    } catch (_) { return ''; }
  }
  function cancelSessionExchange() {
    const exchange = sessionExchange;
    exchange?.controller.abort();
    return exchange ? exchange.settled.catch(() => {}) : Promise.resolve();
  }
  function exchangeSession(options) {
    const exchange = {method:options.method,controller:new AbortController(),settled:null};
    sessionExchange = exchange;
    exchange.settled = (async () => {
      try {
        const response = await fetch('/api/session',{...options,signal:exchange.controller.signal});
        const state = response.ok ? await response.json() : null;
        return {response,state};
      } finally { if (sessionExchange === exchange) sessionExchange = null; }
    })();
    return exchange.settled;
  }
  async function activateInvite(value) {
    if (value.trim()) retainedInvite = inviteToken(value);
    const token = retainedInvite, valid = /^[A-Za-z0-9_-]{43}$/.test(token) || !!canonicalAccessCode(token);
    $('invite-code').required = !valid;
    $('invite-submit').disabled = false;
    if (!valid) {
      if (sessionExchange?.method === 'POST') { ++accessToken; ++inviteRequest; cancelSessionExchange(); }
      $('access-status').textContent = 'Enter the access code you received, or paste your invitation link.';
      return;
    }
    const attempt = ++accessToken, request = ++inviteRequest, canceled = cancelSessionExchange();
    setFlagPermission(false);
    $('invite-submit').disabled = true; $('access-status').textContent = 'Opening your reading room…';
    try {
      await canceled;
      if (attempt !== accessToken || request !== inviteRequest) return;
      const {response,state:sessionState} = await exchangeSession({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({invite:token})});
      if (attempt !== accessToken) return;
      if (!response.ok) throw new Error('This access code could not be verified. Check the code or link and try again.');
      if (sessionState.authenticated !== true) throw new Error('This access code could not be verified. Check the code or link and try again.');
      retainedInvite = ''; $('invite-code').required = true; $('invite-code').value = ''; await bootstrap();
    } catch (error) {
      if (attempt === accessToken && request === inviteRequest) $('access-status').textContent = error.message || 'Your access code could not be verified. Try again.';
    } finally { if (request === inviteRequest) $('invite-submit').disabled = false; }
  }
  $('invite-form').addEventListener('submit',event => { event.preventDefault(); activateInvite($('invite-code').value); });
  async function signOut() {
    const attempt = ++accessToken;
    setFlagPermission(false);
    ++inviteRequest; retainedInvite = ''; $('invite-code').required = true;
    try {
      await cancelSessionExchange();
      if (attempt !== accessToken) return;
      const {response} = await exchangeSession({method:'DELETE'});
      if (attempt !== accessToken) return;
      if (!response.ok) throw new Error('Sign out could not finish. Please try again.');
      closeAccess();
    } catch (error) { if (attempt === accessToken) { $('transition-panel').hidden = false; $('transition-status').textContent = error.message; } }
  }
  $('sign-out').addEventListener('click',signOut);
  $('sign-out-settings').addEventListener('click',signOut);
  audio.addEventListener('error',async () => {
    if (!book || sessionExchange) return;
    const token = accessToken, activeBook = book;
    try { const response = await fetch('/api/session',{cache:'no-store'});
      if (token !== accessToken || book !== activeBook || sessionExchange || !response.ok) return;
      const session = await response.json();
      if (token === accessToken && book === activeBook && !sessionExchange) {
        if (session.authenticated !== true) closeAccess();
        else setFlagPermission(session.canFlagAudio);
      }
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
