"""Check generated narration against its transcript with Gemini transcription."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse, base64, difflib, importlib.util, json, re, subprocess
import time, unicodedata, urllib.request, urllib.error

JOB = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('narration_generator', JOB / 'generate_audio.py')
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
MODEL = 'gemini-3.5-transcribe'
VOCAB = ['Rob Xie','Lumen','Adrian Marsh','Sebastian Hale','Kai Nakamura','Nadia Osei',
         'Ólafur Sigurdsson','Tomás Ferreira','Ines Brandt','Andrei Lepp','Sophie Richter',
         'Priya Chandrasekaran','Daniel Yoon','James Morrow','Harrison Polk','Elias Strutt',
         'Arthur Venn','Magnus Cole','Geoffrey Laine','Clive Barrington','NovaMind','NovaTok']

def number_words(n):
    small = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
    tens = ['', '', 'twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety']
    if n < 20: return small[n]
    if n < 100: return tens[n//10] + (' '+small[n%10] if n%10 else '')
    if n < 1000: return small[n//100]+' hundred'+(' '+number_words(n%100) if n%100 else '')
    for scale,label in [(10**12,'trillion'),(10**9,'billion'),(10**6,'million'),(1000,'thousand')]:
        if n >= scale: return number_words(n//scale)+' '+label+(' '+number_words(n%scale) if n%scale else '')
    return str(n)

def normalize(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = unicodedata.normalize('NFKD', text).lower()
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'").replace("'", '').replace('%',' percent ')
    text = re.sub(r'\d+', lambda m:number_words(int(m.group())), text)
    return re.findall(r'\w+', text)

def compare(expected, heard):
    source, actual = normalize(expected), normalize(heard)
    changes = []
    for tag,a,b,c,d in difflib.SequenceMatcher(None,source,actual,autojunk=False).get_opcodes():
        if tag != 'equal':
            changes.append({'type':tag,'source_word':a,'expected':' '.join(source[a:b]),
                            'heard':' '.join(actual[c:d]),'changed_words':max(b-a,d-c)})
    fraction = sum(c['changed_words'] for c in changes)/max(1,len(source))
    tail = difflib.SequenceMatcher(None,source[-25:],actual[-25:],autojunk=False).ratio()
    return {'source_words_normalized':len(source),'transcribed_words_normalized':len(actual),
            'changed_word_fraction':fraction,'tail_similarity':tail,'differences':changes,
            'needs_review':fraction>.035 or tail<.8}

def one(item, key, ceiling):
    output = JOB/'transcripts'/(item['id']+'.json')
    if output.exists():
        saved=json.loads(output.read_text())
        if saved['request_sha256']==item['request_sha256']:
            return {'id':item['id'],'status':'cached','needs_review':saved['comparison']['needs_review']}
        raise RuntimeError('Transcript cache mismatch: '+item['id'])
    wave_path=Path(item['output'])
    if not wave_path.exists(): raise RuntimeError('Missing narration: '+item['id'])
    receipt=JOB/'transcription-receipts'/(item['id']+'.json')
    attempt=JOB/'attempts'/('asr-'+item['id']+'.json')
    if receipt.exists():
        response=json.loads(receipt.read_text())
    else:
        if attempt.exists(): raise RuntimeError('Prior transcription attempt unresolved; refusing duplicate')
        metadata=json.loads(wave_path.with_suffix('.json').read_text())
        reserve=metadata['duration_seconds']*25*2/1e6+2048*12/1e6
        # This checker runs after narration or as a small pilot. Reservation records
        # participate in the same durable job ledger as generation.
        with g.LOCK:
            if g.spent()+reserve>ceiling: raise RuntimeError('Combined generation/checking budget guard reached')
            g.atomic_json(attempt,{'id':'asr-'+item['id'],'state':'dispatching','reserved_usd':reserve})
        flac=JOB/'transcription-inputs'/(item['id']+'.flac')
        flac.parent.mkdir(exist_ok=True)
        subprocess.run(['/opt/homebrew/bin/ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(wave_path),'-c:a','flac',str(flac)],check=True)
        payload={'model':MODEL,'input':[{'type':'audio','data':base64.b64encode(flac.read_bytes()).decode(),'mime_type':'audio/flac'}],
                 'generation_config':{'max_output_tokens':2048,'transcription_config':{'mode':{'type':'verbatim'},'custom_vocabulary':VOCAB}}}
        request=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/interactions',data=json.dumps(payload).encode(),
                  headers={'x-goog-api-key':key,'Content-Type':'application/json'},method='POST')
        for retry in range(4):
            try:
                with urllib.request.urlopen(request,timeout=300) as res:response=json.load(res)
                g.atomic_json(receipt,response);break
            except urllib.error.HTTPError as exc:
                if exc.code==429 and retry<3:time.sleep(15*(retry+1));continue
                known=exc.code in (400,401,403,404,429)
                g.atomic_json(attempt,{'id':'asr-'+item['id'],'state':'rejected_before_generation' if known else 'uncertain','reserved_usd':0 if known else reserve})
                body=exc.read().decode(errors='replace').replace(key,'[REDACTED]')
                raise RuntimeError(f'HTTP {exc.code}: {body[:700]}') from None
    if response.get('status')!='completed':raise RuntimeError('Transcription status '+str(response.get('status')))
    text='\n'.join(p['text'] for s in response.get('steps',[]) if s.get('type')=='model_output'
                   for p in s.get('content',[]) if p.get('type')=='text')
    if not text.strip():raise RuntimeError('Empty transcript')
    usage=response.get('usage',{})
    cost=usage.get('total_input_tokens',0)*2/1e6+usage.get('total_output_tokens',0)*12/1e6
    result={'id':item['id'],'request_sha256':item['request_sha256'],'transcription_model':MODEL,
            'interaction_id':response.get('id'),'transcript':text,'comparison':compare(item['text'],text),'usage':usage,'cost_usd':cost}
    g.atomic_json(output,result)
    g.atomic_json(attempt,{'id':'asr-'+item['id'],'state':'completed','cost_usd':cost})
    return {'id':item['id'],'status':'checked','changed_word_fraction':round(result['comparison']['changed_word_fraction'],4),
            'needs_review':result['comparison']['needs_review'],'cost_usd':round(cost,4)}

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['pilot','all','status']);p.add_argument('--budget-usd',type=float,default=15);p.add_argument('--workers',type=int,default=3)
    args=p.parse_args()
    manifest=json.loads((JOB/'generation-manifest.json').read_text());items=manifest['items']
    if args.mode=='pilot':items=[items[0],next(i for i in items if i['chapter']==1)]
    if args.mode=='status':
        checked=[json.loads(p.read_text()) for p in (JOB/'transcripts').glob('*.json')]
        print(json.dumps({'checked':len(checked),'review_ids':[c['id'] for c in checked if c['comparison']['needs_review']],'combined_estimated_usd':g.spent()},indent=2));return
    key=g.key_from_project();errors=[]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures={executor.submit(one,i,key,args.budget_usd):i for i in items}
        for future in as_completed(futures):
            try:print(json.dumps(future.result()),flush=True)
            except Exception as exc:
                errors.append({'id':futures[future]['id'],'error':str(exc).replace(key,'[REDACTED]')});print(json.dumps(errors[-1]),flush=True)
                for pending in futures:pending.cancel()
                break
    g.atomic_json(JOB/'transcription-errors.json',errors)
    if errors:raise SystemExit(1)

if __name__=='__main__':main()
