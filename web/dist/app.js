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
  const STORAGE_KEY = 'lumen-pilot-v0.1-' + chapter.audio.sha256.slice(0,12);
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
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch (_) {}
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
  function seekTo(value) {
    const next = Math.min(duration, Math.max(0, Number(value) || 0));
    try { audio.currentTime = next; } catch (_) { return; }
    render(next, true);
    savePosition(true);
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
      pane.scrollTo({top:Math.max(0,top),behavior:reduced.matches || force ? 'instant' : 'smooth'});
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
  function render(time = audio.currentTime, force = false) {
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
    const time = audio.currentTime;
    if (!force && Math.abs(time-lastSaved) < 2) return;
    lastSaved = time;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({time:time >= duration-.3 ? 0 : time, speed:audio.playbackRate, textSize:Number($('text-size').value)})); } catch (_) {}
  }
  function setPlayState() {
    const ended = audio.ended || audio.currentTime >= duration-.08;
    $('play-icon').textContent = audio.paused ? '▶' : 'Ⅱ';
    play.setAttribute('aria-label', ended ? 'Replay audiobook' : audio.paused ? 'Play audiobook' : 'Pause audiobook');
    $('play-status').textContent = ended ? 'End of the pilot' : audio.paused ? 'Chapter 1 · paused' : 'Chapter 1 · listening';
  }
  async function togglePlayback() {
    if (!audio.paused) { audio.pause(); return; }
    if (audio.ended || audio.currentTime >= duration-.08) seekTo(0);
    try { await audio.play(); } catch (_) { $('play-status').textContent = 'Playback could not start. Select Play to try again.'; }
  }
  function tick() {
    render(); savePosition();
    if (!audio.paused && !audio.ended) animationFrame = requestAnimationFrame(tick);
  }
  play.addEventListener('click',togglePlayback);
  $('back').addEventListener('click',() => seekTo(audio.currentTime-15));
  $('forward').addEventListener('click',() => seekTo(audio.currentTime+15));
  seek.max = duration;
  seek.addEventListener('input',() => seekTo(seek.value));
  seek.addEventListener('change',setPlayState);
  followButton.addEventListener('click',() => setFollow(!follow,true));
  for (const event of ['wheel','touchmove']) pane.addEventListener(event,() => setFollow(false),{passive:true});
  pane.addEventListener('keydown',event => {
    if (['ArrowDown','ArrowUp','PageDown','PageUp','Home','End'].includes(event.key)) setFollow(false);
  });
  $('speed').addEventListener('change',() => { audio.playbackRate = Number($('speed').value); savePosition(true); render(); });
  $('replay').addEventListener('click',() => { audio.pause(); setFollow(true); seekTo(0); pane.scrollTo({top:0,behavior:'instant'}); togglePlayback(); });
  audio.addEventListener('play',() => { setPlayState(); cancelAnimationFrame(animationFrame); tick(); });
  audio.addEventListener('pause',() => { cancelAnimationFrame(animationFrame); render(); savePosition(true); setPlayState(); });
  audio.addEventListener('timeupdate',() => render());
  audio.addEventListener('seeked',() => { render(audio.currentTime,true); setPlayState(); });
  audio.addEventListener('ended',() => { cancelAnimationFrame(animationFrame); render(duration); savePosition(true); setPlayState(); });
  audio.addEventListener('waiting',() => { $('play-status').textContent = 'Loading narration…'; });
  audio.addEventListener('playing',setPlayState);
  audio.addEventListener('error',() => { $('play-status').textContent = 'Narration could not be loaded. Please reload.'; });
  function restorePlayback() {
    const resume = Number(saved.time);
    if (resume > 0 && resume < duration-.5) seekTo(resume);
    setPlayState();
  }
  if (audio.readyState >= 1) restorePlayback();
  else audio.addEventListener('loadedmetadata',restorePlayback,{once:true});
  const settings = $('settings');
  $('settings-open').addEventListener('click',() => settings.showModal());
  $('settings-close').addEventListener('click',() => settings.close());
  settings.addEventListener('click',event => { if (event.target === settings) { const r=settings.getBoundingClientRect(); if (event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom) settings.close(); } });
  function setTextSize(value) {
    const size = Math.min(28,Math.max(18,Number(value) || 20));
    document.documentElement.style.setProperty('--text-size',size+'px');
    $('text-size').value = size; $('text-size-value').textContent = size+' px';
  }
  $('text-size').addEventListener('input',() => { setTextSize($('text-size').value); savePosition(true); });
  window.addEventListener('pagehide',() => savePosition(true));
  document.addEventListener('keydown',event => {
    const interactive = event.target.closest('button,input,select,[role=button],dialog');
    if (interactive || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.code === 'Space') { event.preventDefault(); togglePlayback(); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); seekTo(audio.currentTime-15); }
    if (event.key === 'ArrowRight') { event.preventDefault(); seekTo(audio.currentTime+15); }
  });
  if ([.75,1,1.25,1.5,2].includes(Number(saved.speed))) { audio.playbackRate=Number(saved.speed); $('speed').value=saved.speed; }
  setTextSize(saved.textSize);
  $('total').textContent = formatTime(duration); $('excerpt-duration').textContent = formatTime(duration);
  setFollow(true); render(0);
  for (const scene of scenes) { const image = new Image(); image.src = scene.src; }
})();
