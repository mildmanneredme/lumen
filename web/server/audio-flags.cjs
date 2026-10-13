'use strict';
// Owner feedback stays outside immutable reader releases and their asset index.
const crypto=require('node:crypto');
const PREFIX='lumen/owner-audio-flags/v1/';
const JSON_LIMIT=2*1024*1024, FLAG_LIMIT=128*1024;
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const HASH=/^[a-f0-9]{64}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const digest=value=>crypto.createHash('sha256').update(value).digest('hex');
class AudioFlagError extends Error {constructor(status){super('Audio flag rejected.');this.status=status;}}
function ensure(value,status=503){if(!value)throw new AudioFlagError(status);}
async function bytesOf(stream,limit) {
  ensure(stream&&typeof stream.getReader==='function');
  const reader=stream.getReader(),chunks=[];let count=0;
  try {while(true){const part=await reader.read();if(part.done)break;count+=part.value.byteLength;
    if(count>limit){await reader.cancel();throw new AudioFlagError(503);}chunks.push(Buffer.from(part.value));}
    return Buffer.concat(chunks,count);
  }finally{reader.releaseLock();}
}
function assetForURL(value,assets,origin) {
  ensure(typeof value==='string'&&value.startsWith(origin+'/api/assets/'));
  const path=value.slice((origin+'/api/assets/').length),asset=assets.get(path);
  ensure(asset);return asset;
}
async function readJSON(asset,getBlob,signal) {
  ensure(asset?.contentType==='application/json'&&asset.bytes<=JSON_LIMIT);
  const result=await getBlob(asset.blobPath,{access:'private',headers:{'Accept-Encoding':'identity'},abortSignal:signal});
  ensure(result&&result.statusCode===200&&result.stream&&result.headers.get('content-length')===String(asset.bytes)&&
    !result.headers.has('content-range')&&(!result.headers.get('content-encoding')||result.headers.get('content-encoding')==='identity'));
  const bytes=await bytesOf(result.stream,JSON_LIMIT);
  ensure(bytes.length===asset.bytes&&digest(bytes)===asset.sha256);
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
function identity(record) {
  const {createdAt,bindingSha256,...value}=record;
  return digest(JSON.stringify(value));
}
async function buildAudioFlag({input,index,assets,getBlob,config,now,signal}) {
  const fields=['id','releaseId','chapterId','narratorId','audioSha256','start','end','note'];
  ensure(object(input)&&Object.keys(input).every(key=>fields.includes(key))&&UUID.test(input.id||'')&&
    typeof input.releaseId==='string'&&/^[a-z][a-z0-9-]*$/.test(input.releaseId)&&
    typeof input.chapterId==='string'&&/^chapter-[0-9]{3}$/.test(input.chapterId)&&
    typeof input.narratorId==='string'&&/^[a-z][a-z0-9-]*$/.test(input.narratorId)&&HASH.test(input.audioSha256||'')&&
    finite(input.start)&&finite(input.end)&&input.start>=0&&input.end>input.start&&input.end-input.start<=120&&
    typeof input.note==='string'&&input.note.length<=1000,400);
  ensure(input.releaseId===index.releaseId,409);
  const manifest=await readJSON(assets.get(index.manifestPath),getBlob,signal);
  ensure(object(manifest)&&manifest.schemaVersion===1&&manifest.releaseId===index.releaseId&&manifest.bookId==='lumen'&&
    typeof manifest.manuscriptVersion==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(manifest.manuscriptVersion)&&Array.isArray(manifest.tracks));
  const tracks=manifest.tracks.filter(row=>row?.id===input.chapterId);ensure(tracks.length===1,400);
  const recording=tracks[0].recordings?.[input.narratorId];ensure(recording?.status==='ready',400);
  ensure(recording.audioSha256===input.audioSha256,409);
  const chapter=await readJSON(assetForURL(recording.url,assets,config.origin),getBlob,signal);
  ensure(object(chapter)&&[1,2].includes(chapter.schemaVersion)&&chapter.bookId===manifest.bookId&&
    chapter.manuscriptVersion===manifest.manuscriptVersion&&chapter.chapterId===input.chapterId&&
    chapter.audio?.sha256===input.audioSha256&&chapter.audio.narratorId===input.narratorId&&
    finite(chapter.duration)&&chapter.duration>0&&Array.isArray(chapter.paragraphs));
  const audio=assetForURL(chapter.audio.src,assets,config.origin);
  ensure(audio.contentType==='audio/mpeg'&&audio.sha256===input.audioSha256);
  ensure(input.end<=chapter.duration,400);
  const sentenceIds=[],passages=[];let prior=0;const seen=new Set();
  for(const paragraph of chapter.paragraphs) {
    ensure(object(paragraph)&&typeof paragraph.text==='string'&&Array.isArray(paragraph.sentences));
    const overlap=[];let cursor=0;
    for(const sentence of paragraph.sentences) {
      ensure(object(sentence)&&typeof sentence.id==='string'&&sentence.id.startsWith(manifest.manuscriptVersion+':'+input.chapterId+':')&&
        !seen.has(sentence.id)&&Number.isInteger(sentence.textStart)&&Number.isInteger(sentence.textEnd)&&
        sentence.textStart>=cursor&&sentence.textEnd>sentence.textStart&&sentence.textEnd<=paragraph.text.length&&
        paragraph.text.slice(sentence.textStart,sentence.textEnd)===sentence.text);
      seen.add(sentence.id);cursor=sentence.textEnd;
      if(chapter.schemaVersion===2&&sentence.syncStatus==='unavailable') {ensure(sentence.start===null&&sentence.end===null);continue;}
      ensure(finite(sentence.start)&&finite(sentence.end)&&sentence.start>=prior&&sentence.start<sentence.end&&sentence.end<=chapter.duration);
      prior=sentence.end;
      if(sentence.start<input.end&&sentence.end>input.start){sentenceIds.push(sentence.id);overlap.push(sentence);}
    }
    if(overlap.length)passages.push(paragraph.text.slice(overlap[0].textStart,overlap.at(-1).textEnd));
  }
  const record={schemaVersion:1,id:input.id,releaseId:index.releaseId,bookId:manifest.bookId,
    manuscriptVersion:manifest.manuscriptVersion,chapterId:input.chapterId,narratorId:input.narratorId,
    audioSha256:input.audioSha256,audioSrc:chapter.audio.src,start:input.start,end:input.end,note:input.note.trim(),
    sentenceIds,text:passages.join('\n\n'),textStatus:sentenceIds.length?'measured':'unavailable',
    status:'regenerate',createdAt:new Date(now*1000).toISOString()};
  record.bindingSha256=identity(record);ensure(Buffer.byteLength(JSON.stringify(record))<=FLAG_LIMIT);
  return record;
}
function validRecord(record) {
  return object(record)&&record.schemaVersion===1&&UUID.test(record.id||'')&&HASH.test(record.bindingSha256||'')&&
    identity(record)===record.bindingSha256&&typeof record.createdAt==='string'&&
    Number.isFinite(Date.parse(record.createdAt))&&new Date(record.createdAt).toISOString()===record.createdAt&&
    record.status==='regenerate'&&typeof record.text==='string'&&Array.isArray(record.sentenceIds);
}
function createR2FlagStore(bucket) {
  async function read(id) {
    ensure(UUID.test(id));const key=PREFIX+id+'.json',result=await bucket.get(key);
    if(!result)return null;
    ensure(result.key===key&&result.httpMetadata?.contentType==='application/json'&&
      !result.httpMetadata.contentEncoding&&Number.isSafeInteger(result.size)&&result.size>0&&result.size<=FLAG_LIMIT&&
      HASH.test(result.customMetadata?.sha256||''));
    const bytes=await bytesOf(result.body,FLAG_LIMIT);ensure(bytes.length===result.size&&digest(bytes)===result.customMetadata.sha256);
    const record=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
    ensure(validRecord(record)&&record.id===id&&result.customMetadata.createdAt===record.createdAt);
    return record;
  }
  return {
    async save(record) {
      ensure(validRecord(record)&&typeof bucket.put==='function');
      const key=PREFIX+record.id+'.json',bytes=Buffer.from(JSON.stringify(record)),hash=digest(bytes);
      ensure(bytes.length<=FLAG_LIMIT);
      const result=await bucket.put(key,bytes,{onlyIf:new Headers({'If-None-Match':'*'}),sha256:hash,
        httpMetadata:{contentType:'application/json',cacheControl:'private, no-store'},
        customMetadata:{sha256:hash,createdAt:record.createdAt}});
      if(result)ensure(result.key===key&&result.size===bytes.length&&result.customMetadata?.sha256===hash);
      const saved=await read(record.id);ensure(saved);
      ensure(saved.bindingSha256===record.bindingSha256,409);
      if(result)ensure(JSON.stringify(saved)===JSON.stringify(record));
      return saved;
    },
    async list() {
      ensure(typeof bucket.list==='function');let cursor,selected=[];const cursors=new Set();
      for(let page=0;page<20;page++) {
        const result=await bucket.list({prefix:PREFIX,limit:1000,include:['customMetadata'],...(cursor?{cursor}:{})});
        ensure(result&&Array.isArray(result.objects)&&typeof result.truncated==='boolean');
        for(const row of result.objects) {
          const id=row.key?.slice(PREFIX.length,-5),createdAt=row.customMetadata?.createdAt;
          ensure(row.key===PREFIX+id+'.json'&&UUID.test(id||'')&&typeof createdAt==='string'&&Number.isFinite(Date.parse(createdAt)));
          selected.push({id,createdAt});
        }
        selected.sort((a,b)=>b.createdAt.localeCompare(a.createdAt)||b.id.localeCompare(a.id));selected=selected.slice(0,100);
        if(!result.truncated)return Promise.all(selected.map(async row=>{const record=await read(row.id);ensure(record&&record.createdAt===row.createdAt);return record;}));
        ensure(typeof result.cursor==='string'&&result.cursor&&!cursors.has(result.cursor));cursors.add(result.cursor);cursor=result.cursor;
      }
      throw new AudioFlagError(503);
    }
  };
}
module.exports={AudioFlagError,buildAudioFlag,createR2FlagStore};
