"""Audition English female-presenting Gemini voices with the selected literary direction."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib, importlib.util, json, re, sys

ROOT=Path(__file__).resolve().parents[2]
V6=Path(__file__).resolve().parent
BASE=V6/'voice-comparison'
JOB=BASE/'female'
JOB.joinpath('raw').mkdir(parents=True,exist_ok=True)

spec=importlib.util.spec_from_file_location('female_audition_generator',V6/'generate_audio.py')
sys.path.insert(0,str(V6));g=importlib.util.module_from_spec(spec);sys.modules['generate_audio']=g;sys.modules[spec.name]=g;spec.loader.exec_module(g);g.JOB=JOB
stream_spec=importlib.util.spec_from_file_location('female_audition_streamed',V6/'generate_streamed_audio.py')
s=importlib.util.module_from_spec(stream_spec);sys.modules[stream_spec.name]=s;stream_spec.loader.exec_module(s)

source=json.loads((BASE/'audition-manifest.json').read_text())['variants'][1]
text=source['text'];style=source['style'];words=len(re.findall(r"\b\w+(?:['’\-]\w+)*\b",text));model='gemini-3.8-flash-lite-tts'
voices=[
 ('achernar','Achernar'),('aoede','Aoede'),('autonoe','Autonoe'),('callirrhoe','Callirrhoe'),('despina','Despina'),
 ('en-us-storyteller-1','US Storyteller 1'),('en-us-storyteller-11','US Storyteller 11'),('en-au-storyteller-4','Sydney Storyteller 4'),
]
items=[]
for voice,label in voices:
 ident='female-'+re.sub(r'[^a-z0-9]+','-',voice.lower()).strip('-')
 request_hash=hashlib.sha256((model+voice+style+text).encode()).hexdigest()
 items.append({'id':ident,'chapter':1,'chunk':2,'text':text,'words':words,'model':model,'voice':voice,'voice_label':label,'style':style,'request_sha256':request_hash,'output':str(JOB/'raw'/(ident+'.wav'))})
manifest={'model':model,'source_chunk':'chapter-001-002','text_sha256':hashlib.sha256(text.encode()).hexdigest(),'style':style,'variants':items,'production_audio_modified':False}
manifest_path=JOB/'audition-manifest.json'
if manifest_path.exists():
 old=json.loads(manifest_path.read_text())
 if old!=manifest:raise SystemExit('Existing female audition manifest differs; refusing to reuse its request IDs')
else:g.atomic_json(manifest_path,manifest)
key=g.key_from_project(); errors=[]
with ThreadPoolExecutor(max_workers=2) as pool:
 futures={pool.submit(s.generate,item,key,1.0):item for item in items}
 for future,item in futures.items():
  try: print(json.dumps(future.result()),flush=True)
  except Exception as exc:
   error={'id':item['id'],'error':str(exc).replace(key,'[REDACTED]')};errors.append(error);print(json.dumps(error),flush=True)
completed=[]
for item in items:
 p=Path(item['output']).with_suffix('.json')
 if p.exists():
  meta=json.loads(p.read_text());completed.append({'id':item['id'],'voice':item['voice'],'voice_label':item['voice_label'],'duration_seconds':meta['duration_seconds'],'word_count':words,'pace_wpm':meta['pace_words_per_minute'],'estimated_cost_usd':meta.get('cost_usd_from_reported_tokens',meta['cost_usd_duration_based_estimate'])})
result={'completed':completed,'errors':errors,'total_estimated_cost_usd':round(sum(x['estimated_cost_usd'] for x in completed),5),'production_audio_modified':False}
g.atomic_json(JOB/'audition-results.json',result)
print(json.dumps({'completed_samples':len(completed),'failed_samples':len(errors),'estimated_cost_usd':result['total_estimated_cost_usd'],'output_dir':str(JOB)}),flush=True)
