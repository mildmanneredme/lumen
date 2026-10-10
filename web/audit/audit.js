/* Author-only audit client. Decisions are durable on the local server, not this browser. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const statusLabels = {pending:'Not reviewed',accepted:'Keep as recorded',regenerate:'Regenerate',unsure:'Needs discussion'};
  const rank = {high:0,medium:1,low:2};
  const state = {candidates:[],token:null,selected:null,filters:{edition:'all',source:'all',severity:'all',status:'all',search:''},dirty:false,saving:false,action:null,bounded:false,seek:null};
  const audio = $('audit-audio');
  function current() { return state.candidates.find(c=>c.candidateId===state.selected); }
  function filtered() {
    const f=state.filters,q=f.search.trim().toLowerCase();
    return state.candidates.filter(c=>(f.edition==='all'||c.edition===f.edition)&&(f.source==='all'||(c.source||'Unspecified review source')===f.source)&&(f.severity==='all'||c.severity===f.severity)&&(f.status==='all'||(c.decision?.status||'pending')===f.status)&&(!q||[c.title,c.chapter,c.narrator,c.edition,c.chunkId,c.category,c.source,c.expected,c.heard,c.expectedContext].join(' ').toLowerCase().includes(q)))
      .sort((a,b)=>(Number((a.decision?.status||'pending')!=='pending')-Number((b.decision?.status||'pending')!=='pending')) || (rank[a.severity]??3)-(rank[b.severity]??3)||Number(a.chapter)-Number(b.chapter)||a.candidateId.localeCompare(b.candidateId));
  }
  function clock(value) { const s=Math.max(0,Number(value)||0);return `${Math.floor(s/60)}:${(s%60).toFixed(1).padStart(4,'0')}`; }
  function prose(value) { return Array.isArray(value)?value.map(v=>typeof v==='string'?v:JSON.stringify(v)).join('\n'):String(value??''); }
  function category(value) { return String(value||'Review flag').replace(/[_-]+/g,' ').replace(/^./,s=>s.toUpperCase()); }
  function syncFilters() { $('edition-filter').value=state.filters.edition;$('source-filter').value=state.filters.source;$('severity-filter').value=state.filters.severity;$('status-filter').value=state.filters.status;$('search').value=state.filters.search; }
  function showSave(message,error=false) { $('save-status').textContent=message;$('save-status').classList.toggle('error',error); }
  function counts() {
    const n={pending:0,accepted:0,regenerate:0,unsure:0};
    state.candidates.forEach(c=>n[c.decision?.status||'pending']++);
    $('review-counts').textContent=`${n.pending} not reviewed · ${n.accepted} kept · ${n.regenerate} to regenerate · ${n.unsure} need discussion`;
  }
  function renderQueue() {
    const items=filtered();$('queue-count').textContent=`${items.length} / ${state.candidates.length}`;
    $('candidate-list').replaceChildren();
    items.forEach(c=>{
      const button=document.createElement('button');button.type='button';button.className='queue-item';button.dataset.id=c.candidateId;button.setAttribute('aria-current',String(c.candidateId===state.selected));
      const title=document.createElement('span');title.className='queue-title';title.textContent=c.title||`Chapter ${c.chapter}`;
      const detail=document.createElement('span');detail.className='queue-detail';detail.textContent=`${c.narrator||c.edition} · ${clock(c.start)} · ${category(c.category)} · ${c.source||'Unspecified review source'}`;
      const status=document.createElement('span');status.className='queue-state';status.textContent=`${category(c.severity)} priority · ${statusLabels[c.decision?.status||'pending']}`;
      button.append(title,detail,status);button.addEventListener('click',()=>requestAction(()=>select(c.candidateId)));$('candidate-list').append(button);
    });
    counts();
  }
  function renderNavigation() {
    const items=filtered(),index=items.findIndex(c=>c.candidateId===state.selected);
    $('candidate-position').textContent=index<0?'':`${index+1} of ${items.length}`;
    $('previous-candidate').disabled=state.saving||index<=0;
    $('next-candidate').disabled=state.saving||index<0||index>=items.length-1;
  }
  function seek(value) {
    const limit=Number.isFinite(audio.duration)?audio.duration:Infinity,target=Math.max(0,Math.min(Number(value)||0,limit));
    if(audio.readyState>=1) {try{audio.currentTime=target;state.seek=null;}catch(_){state.seek=target;}}
    else state.seek=target;
  }
  function select(id) {
    const prior=state.selected;audio.pause();state.bounded=false;state.selected=id;state.dirty=false;
    const c=current();
    $('candidate').hidden=!c;$('empty-state').hidden=!!c;
    if(!c) {
      audio.removeAttribute('src');audio.load();
      $('empty-message').textContent=state.candidates.length?'No passages match these filters. Change the filters to continue reviewing.':'There are no flagged passages in the current audit data. Review coverage is shown above; uncompleted checks still need to finish.';
      renderQueue();renderNavigation();return;
    }
    $('candidate-title').textContent=c.title||`Chapter ${c.chapter}`;
    $('candidate-meta').textContent=`${c.narrator||c.edition} · ${c.edition} · Piece ${c.chunkId||c.candidateId}`;
    $('severity-badge').textContent=`${category(c.severity)} priority`;$('severity-badge').dataset.severity=c.severity;
    $('flag-category').textContent=category(c.category);
    $('candidate-source').textContent=`Review source: ${c.source||'Unspecified review source'}`;
    $('precision').textContent=`Timing: ${prose(c.precision)||'Approximate'}. ${c.scope==='chapter'?'Chapter recording':prose(c.scope)||'Recording scope unspecified'}. Confirm by listening.`;
    $('window-time').textContent=`${clock(c.start)} – ${clock(c.end)}`;
    $('expected').textContent=prose(c.expected)||(/addition|insert/i.test(c.category)?'No added passage expected.':'No manuscript excerpt was supplied for this flag.');
    $('heard').textContent=prose(c.heard)||(/omission|missing|delet/i.test(c.category)?'Not recognised.':'No machine transcription was supplied for this flag.');
    $('expected-context').textContent=prose(c.expectedContext)||prose(c.expected);
    $('decision-status').value=c.decision?.status||'pending';$('feedback-notes').value=c.decision?.notes||'';
    $('conflict-review').hidden=true;$('conflict-detail').textContent='';
    showSave('Feedback saves to the local audit workspace.');$('audio-status').textContent='Press Listen to passage to play the flagged window. Native audio controls let you hear the surrounding recording.';
    if(prior!==id || !audio.getAttribute('src')) {audio.src=c.audioUrl;audio.load();}
    seek(c.start);audio.playbackRate=Number($('playback-rate').value);
    renderQueue();renderNavigation();
  }
  function applyFilters(next) {state.filters=next;syncFilters();const list=filtered();select(list.some(c=>c.candidateId===state.selected)?state.selected:list[0]?.candidateId||null);}
  function requestAction(action) {
    if(state.saving)return;
    if(!state.dirty)return action();
    state.action=action;$('guard-error').textContent='';if(!$('unsaved-dialog').open)$('unsaved-dialog').showModal();
  }
  function clearGuard() {state.action=null;syncFilters();$('unsaved-dialog').close();}
  function updateDirty() {
    const c=current();if(!c)return;
    state.dirty=$('decision-status').value!==(c.decision?.status||'pending')||$('feedback-notes').value!==(c.decision?.notes||'');
    showSave(state.dirty?'Unsaved changes. Save your decision to keep this feedback.':'Feedback saves to the local audit workspace.');
  }
  function saving(value) {
    state.saving=value;
    ['save-decision','save-next','guard-save','guard-discard','keep-editing','decision-status','feedback-notes','edition-filter','source-filter','severity-filter','status-filter','search','review-latest'].forEach(id=>$(id).disabled=value);
    document.querySelectorAll('.queue-item').forEach(button=>button.disabled=value);
    renderNavigation();
  }
  async function save() {
    const c=current();if(!c||state.saving)return false;
    const payload={candidateId:c.candidateId,audioHash:c.audioHash,status:$('decision-status').value,notes:$('feedback-notes').value,revision:c.decision?.revision||0};
    saving(true);showSave('Saving feedback…');
    try {
      const response=await fetch('/api/decisions',{method:'POST',headers:{'Content-Type':'application/json','X-Audit-Token':state.token},body:JSON.stringify(payload),credentials:'same-origin',cache:'no-store'});
      const body=await response.json();if(!response.ok){if(response.status===409){$('conflict-review').hidden=false;$('conflict-detail').textContent='Your draft is still here. Review the latest saved decision before saving again.';}throw new Error(body.error||`Feedback could not be saved (${response.status}).`);}
      if(!body.decision || !Object.hasOwn(statusLabels,body.decision.status)||!Number.isInteger(body.decision.revision))throw new Error('The server did not confirm this decision. Your notes have been kept.');
      c.decision=body.decision;state.dirty=false;$('conflict-review').hidden=true;showSave('Saved to the audit workspace.');renderQueue();return true;
    } catch(error) {
      const message=error.message||'Could not save feedback. Your notes are still here. Try again.';
      showSave(message,true);$('guard-error').textContent=message;return false;
    } finally {saving(false);}
  }
  async function load() {
    $('load-error').hidden=true;$('workspace').hidden=true;$('coverage').textContent='Loading audio review coverage…';
    try {
      const response=await fetch('/api/audit',{credentials:'same-origin',cache:'no-store'});const body=await response.json();
      if(!response.ok)throw new Error(body.error||'Audit data unavailable.');
      if(body.schemaVersion!==1||!Array.isArray(body.candidates)||!body.csrfToken)throw new Error('The audit server returned incompatible data.');
      state.candidates=body.candidates;state.token=body.csrfToken;
      const coverage=body.coverage||{};
      $('coverage').textContent=`OpenAI checks ${coverage.complete?'complete':'in progress'} · ${coverage.checked??0} / ${coverage.selected??0} production pieces checked`;
      const localCount=state.candidates.filter(c=>/^Local ASR/i.test(c.source||'')).length;
      $('source-coverage').textContent=`${localCount} local ASR ${localCount===1?'flag is':'flags are'} additional to the OpenAI review. Local ASR uses original source takes; OpenAI uses mastered chapters. The progress count above covers OpenAI checks only.`;
      const edition=$('edition-filter');edition.replaceChildren(new Option('Both narrators','all'));
      [...new Set(state.candidates.map(c=>c.edition))].sort().forEach(e=>{const c=state.candidates.find(c=>c.edition===e);edition.add(new Option(`${c.narrator||e} (${e})`,e));});
      if(![...edition.options].some(o=>o.value===state.filters.edition))state.filters.edition='all';
      const source=$('source-filter');source.replaceChildren(new Option('All sources','all'));
      [...new Set(state.candidates.map(c=>c.source||'Unspecified review source'))].sort().forEach(s=>source.add(new Option(s,s)));
      if(![...source.options].some(o=>o.value===state.filters.source))state.filters.source='all';
      syncFilters();$('workspace').hidden=false;select(filtered()[0]?.candidateId||null);
    } catch(error) {
      $('load-error').hidden=false;$('load-error-message').textContent=`${error.message} Start or check the local audit server, then try again. Saved feedback remains in the workspace.`;
      $('coverage').textContent='Review coverage unavailable.';$('review-counts').textContent='Saved decisions could not be loaded.';
      $('source-coverage').textContent='';
    }
  }
  for(const [id,key] of [['edition-filter','edition'],['source-filter','source'],['severity-filter','severity'],['status-filter','status'],['search','search']]) {
    $(id).addEventListener(id==='search'?'input':'change',()=>{const next={...state.filters,[key]:$(id).value};requestAction(()=>applyFilters(next));});
  }
  $('decision-status').addEventListener('change',updateDirty);$('feedback-notes').addEventListener('input',updateDirty);
  $('decision-form').addEventListener('submit',async event=>{event.preventDefault();await save();});
  $('save-next').addEventListener('click',async()=>{const items=filtered(),index=items.findIndex(c=>c.candidateId===state.selected),next=items[index+1]?.candidateId;if(await save()){const remaining=filtered();select(remaining.find(c=>c.candidateId===next)?.candidateId||remaining.find(c=>(c.decision?.status||'pending')==='pending')?.candidateId||remaining.find(c=>c.candidateId===state.selected)?.candidateId||remaining[0]?.candidateId||null);}});
  for(const [id,offset] of [['previous-candidate',-1],['next-candidate',1]])$(id).addEventListener('click',()=>{const items=filtered(),index=items.findIndex(c=>c.candidateId===state.selected),next=items[index+offset];if(next)requestAction(()=>select(next.candidateId));});
  $('keep-editing').addEventListener('click',clearGuard);
  $('unsaved-dialog').addEventListener('cancel',event=>{event.preventDefault();clearGuard();});
  $('guard-discard').addEventListener('click',()=>{const action=state.action;clearGuard();state.dirty=false;action?.();});
  $('guard-save').addEventListener('click',async()=>{if(await save()){const action=state.action;clearGuard();action?.();}});
  $('reload-audit').addEventListener('click',()=>requestAction(load));
  $('review-latest').addEventListener('click',async()=>{
    const c=current();if(!c||state.saving)return;saving(true);
    try {
      const response=await fetch('/api/audit',{credentials:'same-origin',cache:'no-store'}),body=await response.json();
      if(!response.ok)throw new Error(body.error||'Could not load the latest feedback.');
      const latest=body.candidates?.find(item=>item.candidateId===c.candidateId);
      if(!latest||latest.audioHash!==c.audioHash){$('conflict-detail').textContent='This recording or review flag has changed. Keep a copy of your notes, then reload the audit to listen to the current recording before deciding.';return;}
      if(!latest.decision||!Number.isInteger(latest.decision.revision)||!Object.hasOwn(statusLabels,latest.decision.status)||!body.csrfToken)throw new Error('The server did not return valid latest feedback.');
      c.decision=latest.decision;state.token=body.csrfToken;updateDirty();renderQueue();
      $('conflict-detail').textContent=`Latest saved decision: ${statusLabels[c.decision.status]}. Saved notes: ${c.decision.notes||'None.'} Your draft above has been kept. Review both, then press Save decision to apply your draft.`;
    } catch(error){$('conflict-detail').textContent=error.message;}
    finally{saving(false);}
  });
  $('play-window').addEventListener('click',async()=>{
    const c=current();if(!c)return;const id=c.candidateId;seek(c.start);state.bounded=true;
    try {await audio.play();if(current()?.candidateId===id)$('audio-status').textContent=`Playing ${clock(c.start)} – ${clock(c.end)}. Playback pauses at the end of this flagged window.`;}
    catch(_){state.bounded=false;$('audio-status').textContent='The recording could not play. Check the audit server or connection, then try again.';}
  });
  for(const [id,offset] of [['skip-back',-5],['skip-forward',5]])$(id).addEventListener('click',()=>{state.bounded=false;seek((state.seek??audio.currentTime)+offset);$('audio-status').textContent='You can listen beyond the flagged window with the audio controls.';});
  $('playback-rate').addEventListener('change',()=>{audio.playbackRate=Number($('playback-rate').value);});
  audio.addEventListener('loadedmetadata',()=>{if(state.seek!==null)seek(state.seek);});
  audio.addEventListener('timeupdate',()=>{const c=current();if(state.bounded&&c&&audio.currentTime>=Number(c.end)){audio.pause();state.bounded=false;$('audio-status').textContent='End of the flagged window. Listen again, or use the audio controls for more context.';}});
  audio.addEventListener('seeking',()=>{const c=current();if(state.bounded&&c&&(audio.currentTime<Number(c.start)-.2||audio.currentTime>Number(c.end)+.2))state.bounded=false;});
  audio.addEventListener('error',()=>{$('audio-status').textContent='Recording unavailable. Check the local audit server and audio file, then try again.';});
  window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
  load();
})();
