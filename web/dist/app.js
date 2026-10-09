(() => {
  'use strict';
  const chapter = window.LUMEN_CHAPTER;
  const $ = id => document.getElementById(id);
  const audio = $('narration'), pane = $('reading-pane'), prose = $('prose');
  const play = $('play'), seek = $('seek'), followButton = $('follow');
  if (!chapter || !chapter.paragraphs?.length) {
    $('play-status').textContent = 'The book could not be loaded. Please reload.';
    play.disabled = true;
    return;
  }
  const recordingSrc = chapter.audio.src + '?v=' + chapter.audio.sha256.slice(0,12);
  if (audio.getAttribute('src') !== recordingSrc) audio.src = recordingSrc;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const duration = chapter.duration;
  const scenes = chapter.scenes.map(scene => ({ ...scene, src: 'assets/' + scene.id + '.png' }));
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
    const next = Math.min(duration, Math.max(0, Number(value) || 0));
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
    if (pendingSeek !== null) seekTimer = setTimeout(() => {
      if (pendingSeek !== null) $('seek-feedback').textContent = 'Still loading your place. Keep this page open, or reload if the connection has stopped.';
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
  function showScene(scene) {
    if (!scene || activeScene?.id === scene.id) return;
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
      incoming.src = scene.src;
      image.alt = descriptions[scene.id];
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
      if (token === sceneToken) $('play-status').textContent = 'Illustration unavailable · narration continues';
    };
    preload.src = scene.src;
  }
  function render(time = currentPosition(), force = false) {
    const clamped = Math.min(duration,Math.max(0,time || 0));
    seek.value = clamped;
    seek.style.setProperty('--progress', (clamped / duration * 100) + '%');
    seek.setAttribute('aria-valuetext', Math.floor(clamped / 60) + ' minutes ' + Math.floor(clamped % 60) + ' seconds');
    $('elapsed').textContent = formatTime(clamped);
    showScene(getAt(scenes,clamped) || scenes[0]);
    const sentence = getAt(sentences,clamped);
    const paragraph = getAt(chapter.paragraphs,clamped);
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
      const em = document.createElement('em'); em.textContent = text.slice(a,b); parent.append(em); cursor = b;
    }
    if (cursor < end) parent.append(document.createTextNode(text.slice(cursor,end)));
  }
  for (const paragraph of chapter.paragraphs) {
    const p = document.createElement('p'); p.id = paragraph.id;
    paragraphElements.set(paragraph.id,p);
    let cursor = 0;
    for (const sentence of paragraph.sentences) {
      if (sentence.textStart > cursor) appendText(p,paragraph.text,cursor,sentence.textStart,paragraph.emphasis);
      const span = document.createElement('span');
      span.className = 'sentence'; span.id = sentence.id;
      span.setAttribute('role','button'); span.tabIndex = 0;
      span.setAttribute('aria-label','Listen: ' + sentence.text);
      appendText(span,paragraph.text,sentence.textStart,sentence.textEnd,paragraph.emphasis);
      const activate = () => { setFollow(true); seekTo(sentence.start); };
      span.addEventListener('click',activate);
      span.addEventListener('keydown',event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
      });
      p.append(span); sentenceElements.set(sentence.id,span); sentences.push(sentence); cursor = sentence.textEnd;
    }
    if (cursor < paragraph.text.length) appendText(p,paragraph.text,cursor,paragraph.text.length,paragraph.emphasis);
    prose.append(p);
  }
  function savePosition(force = false) {
    if (!progress || !bookmarkDirty) return;
    const time = currentPosition();
    if (!force && Math.abs(time-lastSaved) < 2) return;
    lastSaved = time;
    progress.save(time, {completed: completed || (pendingSeek === null && audio.ended)});
  }
  function savePreferences() {
    progress.savePreferences({speed:audio.playbackRate, textSize:Number($('text-size').value), narratorId:chapter.audio.narratorId});
  }
  function setPlayState() {
    const ended = completed || currentPosition() >= duration-.08;
    const waiting = pendingSeek !== null;
    $('seek-feedback').hidden = !waiting;
    if (waiting) $('seek-feedback').textContent = 'Loading your place at ' + formatTime(pendingSeek) + '…';
    const wantsPlayback = !audio.paused || (waiting && resumeAfterSeek);
    $('play-icon').textContent = wantsPlayback ? 'Ⅱ' : '▶';
    play.setAttribute('aria-label', ended ? 'Replay audiobook' : wantsPlayback ? 'Pause audiobook' : 'Play audiobook');
    $('play-status').textContent = ended ? 'End of the pilot' : audio.paused ? 'Chapter 1 · paused' : 'Chapter 1 · listening';
  }
  async function startPlayback() {
    try { await audio.play(); } catch (_) { $('play-status').textContent = 'Playback could not start. Select Play to try again.'; }
  }
  async function togglePlayback() {
    bookmarkDirty = true;
    $('resume-panel').hidden = true;
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
  audio.addEventListener('ended',() => { if (pendingSeek !== null) return; completed = true; cancelAnimationFrame(animationFrame); render(duration); savePosition(true); setPlayState(); });
  audio.addEventListener('waiting',() => { $('play-status').textContent = 'Loading narration…'; });
  audio.addEventListener('playing',setPlayState);
  audio.addEventListener('error',() => { $('play-status').textContent = 'Narration could not be loaded. Please reload.'; $('seek-feedback').hidden = false; $('seek-feedback').textContent = 'Narration could not be loaded. Please reload to try again.'; });
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
  progress = window.LumenProgress.create(chapter, sentences);
  const saved = progress.readPreferences();
  if ([.75,1,1.25,1.5,2].includes(Number(saved.speed))) { audio.playbackRate=Number(saved.speed); $('speed').value=saved.speed; }
  setTextSize(saved.textSize);
  $('total').textContent = formatTime(duration); $('excerpt-duration').textContent = formatTime(duration);
  setFollow(true);
  const restored = progress.resolve(progress.read());
  completed = restored?.completed || false;
  if (restored?.time > 0 || completed) {
    $('resume-panel').hidden = false;
    $('resume-message').textContent = completed ? 'You’ve finished the opening excerpt.' : 'Your place is saved · Chapter 1';
    $('resume-continue').textContent = completed ? 'Listen again' : 'Continue from ' + formatTime(restored.time);
    seekTo(restored.time, true);
  } else { render(0); setPlayState(); }
  for (const scene of scenes) { const image = new Image(); image.src = scene.src; }
})();
