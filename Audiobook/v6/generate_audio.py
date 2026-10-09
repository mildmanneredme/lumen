"""Resumable Gemini narration; secrets stay in request headers, never outputs."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse, base64, hashlib, io, json, math, os, re, socket
import threading, time, urllib.error, urllib.request, uuid, wave

ROOT = Path(__file__).resolve().parents[2]
JOB = Path(__file__).resolve().parent
SOURCE = ROOT / 'Draft/v6/audiobook'
MODEL = 'gemini-3.8-flash-lite-tts'
VOICE = 'Charon'
STYLE = ('Clear, grounded English literary thriller narration. One consistent adult '
         'narrator voice, natural conversational delivery, approximately 150 words per '
         'minute. Restrained emotion and modest dialogue inflection. Preserve every '
         'word and punctuation-driven cadence. No music, sound effects, added dialogue, '
         'or spoken stage directions. Treat <short pause> as a brief silent scene break. '
         'Rob Xie is the author; pronounce Xie as shyeh. Lumen is LOO-men. '
         'Adrian is AY-dree-an; Kai is KYE; Nadia is NAH-dee-ah; Osei is oh-SAY; '
         'Ólafur is OH-lah-vur; Tomás is toh-MAHSH; Ines is ee-NESS.')
LOCK = threading.Lock()
MAX_REQUEST_USD = 16384 * 6 / 1e6 + 8192 * .5 / 1e6

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

def key_from_project():
    if os.environ.get('GEMINI_API_KEY'):
        return os.environ['GEMINI_API_KEY']
    for line in (ROOT / '.env').read_text().splitlines():
        match = re.match(r'\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*(.*)', line)
        if match:
            return match.group(1).strip().strip('\"\'')
    raise RuntimeError('No Gemini key configured')

def prepare():
    items = [json.loads(s) for s in (SOURCE / 'tts-chunks.jsonl').read_text().splitlines()]
    assert len(items) == 241
    items[0]['text'] = items[0]['text'].replace(
        'Lumen. A novel.',
        'Lumen. Written by Rob Xie. Narration generated using an AI voice from Gemini.')
    items[0]['section'] = 'opening-credits-and-epigraph'
    closing = 'You have been listening to Lumen, written by Rob Xie. Narration was generated using Gemini. The End.'
    items.append({'id': 'closing-credits', 'chapter': 90, 'chunk': 1,
                  'section': 'closing-credits', 'text': closing,
                  'words': len(closing.split())})
    for item in items:
        item['model'] = MODEL
        item['voice'] = VOICE
        item['style'] = STYLE
        item['words'] = len(re.findall(r"\b\w+(?:['’\-]\w+)*\b", re.sub(r'<[^>]+>', '', item['text'])))
        item['request_sha256'] = hashlib.sha256(
            (MODEL + VOICE + STYLE + item['text']).encode()).hexdigest()
        item['output'] = str(JOB / 'raw' / (item['id'] + '.wav'))
    (JOB / 'raw').mkdir(exist_ok=True)
    atomic_json(JOB / 'generation-manifest.json', {'author': 'Rob Xie', 'model': MODEL,
        'voice': VOICE, 'items': items, 'source_sha256': json.loads((SOURCE / 'manifest.json').read_text())['source_sha256']})
    plan = json.loads((JOB / 'production-plan.json').read_text())
    plan.update({'author_credit': 'Rob Xie', 'generation_authorized': True,
        'distribution_intent': 'Gemini edition now; Audible listing deferred',
        'third_party_ai_authorization': 'Not asserted; Audible deferred by user',
        'voice': VOICE, 'generation_items_with_closing_credits': len(items)})
    atomic_json(JOB / 'production-plan.json', plan)
    print(json.dumps({'prepared_items': len(items), 'words_with_credits': sum(i['words'] for i in items), 'voice': VOICE}), flush=True)
    return items

def bound_attempt(item, attempt_file):
    if not attempt_file.exists():
        return None
    previous = json.loads(attempt_file.read_text())
    if previous.get('request_sha256') != item['request_sha256']:
        raise RuntimeError('Attempt does not match current request: ' + item['id'])
    return previous

def bound_receipt(item, receipt, attempt_file):
    previous = bound_attempt(item, attempt_file)
    if previous is None:
        raise RuntimeError('Response receipt has no matching attempt: ' + item['id'])
    if previous.get('state') == 'rejected_before_generation':
        raise RuntimeError('Response receipt conflicts with rejected attempt: ' + item['id'])
    data = json.loads(receipt.read_text())
    # Legacy receipts retain their binding through the matching durable attempt.
    # New receipts also carry their own hash so they cannot be relabeled later.
    if data.get('_request_sha256', previous['request_sha256']) != item['request_sha256']:
        raise RuntimeError('Response receipt does not match current request: ' + item['id'])
    return data

def daily_quota_exhausted(body):
    text = body.lower()
    return bool(re.search(r'daily|per[\s_-]*day', text) and
                re.search(r'quota|exceed|exhaust|limit', text))

def request_audio(item, key, reservation_token=None):
    receipt = JOB / 'receipts' / (item['id'] + '.json')
    attempt_file = JOB / 'attempts' / (item['id'] + '.json')
    payload = {'model': item['model'], 'background': True, 'input': [{'type': 'user_input', 'content': [
        {'type': 'text', 'text': item['text'], 'annotations': [
            {'type': 'speech_metadata', 'style': item['style']}]}]}],
        'response_format': {'type': 'audio'},
        'generation_config': {'speech_config': [{'voice': item['voice']}]}}
    request = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/interactions',
        data=json.dumps(payload).encode(), headers={'x-goog-api-key': key, 'Content-Type': 'application/json'}, method='POST')
    if receipt.exists():
        data = bound_receipt(item, receipt, attempt_file)
    else:
        with LOCK:
            previous = bound_attempt(item, attempt_file)
            if (previous is None or previous.get('state') != 'reserved' or
                    not reservation_token or previous.get('reservation_token') != reservation_token):
                raise RuntimeError('No matching dispatch reservation; will not send duplicate: ' + item['id'])
            atomic_json(attempt_file, {**previous, 'state': 'dispatching'})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    data = json.load(response)
                with LOCK:
                    atomic_json(receipt, {**data, '_request_sha256': item['request_sha256']})
                    atomic_json(attempt_file, {**previous, 'state': 'response_received',
                                              'interaction_id': data.get('id')})
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors='replace')
                daily_limit = exc.code == 429 and daily_quota_exhausted(body)
                if exc.code == 429 and not daily_limit and attempt < 3:
                    time.sleep(15 * (attempt + 1))
                    continue
                known_rejection = exc.code in (400, 401, 403, 404, 429)
                with LOCK:
                    atomic_json(attempt_file, {**previous,
                        'state': 'rejected_before_generation' if known_rejection else 'uncertain',
                        'reserved_usd': 0 if known_rejection else MAX_REQUEST_USD,
                        'http_status': exc.code, 'daily_quota_exhausted': daily_limit})
                body = body.replace(key, '[REDACTED]')
                raise RuntimeError(f'HTTP {exc.code}: {body[:700]}') from None
    # Save the remote ID before waiting for audio. Read-only polling can be resumed
    # after a timeout without sending another billed narration request.
    deadline = time.monotonic() + 1800
    while data.get('status') in ('in_progress', 'queued'):
        remote_id = data.get('id')
        if not remote_id or not re.fullmatch(r'[A-Za-z0-9_-]+', remote_id):
            raise RuntimeError('In-progress interaction has no valid recovery ID')
        if time.monotonic() >= deadline:
            raise RuntimeError('Background interaction is still pending; resume polling, never regenerate: ' + item['id'])
        time.sleep(5)
        poll = urllib.request.Request(
            'https://generativelanguage.googleapis.com/v1beta/interactions/' + remote_id,
            headers={'x-goog-api-key': key}, method='GET')
        try:
            with urllib.request.urlopen(poll, timeout=180) as response:
                updated = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                continue
            raise RuntimeError('Cannot retrieve saved interaction (HTTP ' + str(exc.code) +
                               '); receipt retained, no regeneration sent') from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            # Keep the last durable remote ID; the next attempt is only a GET.
            continue
        if updated.get('id') != remote_id:
            raise RuntimeError('Polled interaction ID changed; response preserved for review')
        data = updated
        with LOCK:
            atomic_json(receipt, {**data, '_request_sha256': item['request_sha256']})
    if data.get('status') != 'completed':
        raise RuntimeError('Interaction status is not completed: ' + str(data.get('status')))
    blocks = [part for step in data.get('steps', []) if step.get('type') == 'model_output'
              for part in step.get('content', []) if part.get('type') == 'audio' and part.get('data')]
    if not blocks and data.get('output_audio'):
        blocks = [data['output_audio']]
    if not blocks:
        raise RuntimeError('Response contained no audio; fields=' + ','.join(data.keys()))
    if len(blocks) != 1:
        raise RuntimeError('Unexpected multiple audio blocks; preserve response for review')
    block = blocks[0]
    audio = base64.b64decode(block['data'])
    if not audio.startswith(b'RIFF'):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(audio)
        audio = buffer.getvalue()
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        seconds = wav.getnframes() / wav.getframerate()
        channels, rate, width = wav.getnchannels(), wav.getframerate(), wav.getsampwidth()
    if seconds <= 0:
        raise RuntimeError('Empty audio response')
    usage = data.get('usage', data.get('usage_metadata', {}))
    if item['words'] > 50 and item['words'] / seconds * 60 > 250:
        raise RuntimeError('Audio is implausibly short for the narration; preserve receipt for recovery: ' + item['id'])
    audio_tokens = sum(t.get('tokens', 0) for t in usage.get('output_tokens_by_modality', [])
                       if t.get('modality') == 'audio')
    token_seconds = audio_tokens / 25
    if token_seconds > 5 and abs(seconds - token_seconds) > max(1, token_seconds * .2):
        raise RuntimeError('Audio duration differs from reported audio tokens; preserve receipt: ' + item['id'])
    metadata = {'id': item['id'], 'chapter': item['chapter'], 'chunk': item.get('chunk'),
        'request_sha256': item['request_sha256'], 'model': item['model'], 'voice': item['voice'],
        'interaction_status': data.get('status'),
        'interaction_id': data.get('id'), 'duration_seconds': seconds, 'channels': channels,
        'sample_rate': rate, 'sample_width': width, 'words': item['words'],
        'pace_words_per_minute': item['words'] / seconds * 60,
        'usage': usage, 'cost_usd_duration_based_estimate': seconds * 25 * 6 / 1e6 +
            (len(item['text']) + len(item['style'])) / 4 * .5 / 1e6,
        'audio_sha256': hashlib.sha256(audio).hexdigest()}
    if 'total_input_tokens' in usage and 'total_output_tokens' in usage:
        metadata['cost_usd_from_reported_tokens'] = usage['total_input_tokens'] * .5 / 1e6 + usage['total_output_tokens'] * 6 / 1e6
    return audio, metadata

def spent():
    records = {p.stem: json.loads(p.read_text()) for p in (JOB / 'attempts').glob('*.json')}
    for p in (JOB / 'raw').glob('*.json'):
        m = json.loads(p.read_text())
        if p.stem not in records:
            records[p.stem] = {'state': 'completed', 'cost_usd': m.get('cost_usd_from_reported_tokens', m.get('cost_usd_duration_based_estimate', 0))}
    return sum(r.get('cost_usd', r.get('reserved_usd', 0)) for r in records.values())

def generate(item, key, ceiling):
    output = Path(item['output']); meta_path = output.with_suffix('.json')
    receipt = JOB / 'receipts' / (item['id'] + '.json')
    attempt_file = JOB / 'attempts' / (item['id'] + '.json')
    actual_hash = hashlib.sha256((item['model'] + item['voice'] + item['style'] + item['text']).encode()).hexdigest()
    if actual_hash != item['request_sha256']:
        raise RuntimeError('Prepared request hash mismatch: ' + item['id'])
    reservation_token = None
    with LOCK:
        previous = bound_attempt(item, attempt_file)
        if receipt.exists():
            bound_receipt(item, receipt, attempt_file)
        if output.exists() and meta_path.exists():
            old = json.loads(meta_path.read_text())
            if old.get('request_sha256') != item['request_sha256']:
                raise RuntimeError('Cached audio does not match current request: ' + item['id'])
            if old.get('audio_sha256'):
                if hashlib.sha256(output.read_bytes()).hexdigest() != old['audio_sha256']:
                    raise RuntimeError('Cached WAV integrity check failed: ' + item['id'])
                return {'id': item['id'], 'status': 'cached', 'seconds': round(old['duration_seconds'], 2)}
            if not receipt.exists():
                raise RuntimeError('Cached WAV has no integrity hash or response receipt: ' + item['id'])
            # Rebuild legacy metadata locally from the bound response, never bill again.
        if (output.exists() or meta_path.exists()) and not receipt.exists():
            raise RuntimeError('Orphan cache without response receipt; refusing a duplicate charge: ' + item['id'])
        if not receipt.exists():
            if previous and previous.get('state') != 'rejected_before_generation':
                raise RuntimeError('Prior request has uncertain billing; will not send duplicate: ' + item['id'])
            if not math.isfinite(ceiling) or ceiling < 0:
                raise RuntimeError('Generation budget must be finite and non-negative')
            if spent() + MAX_REQUEST_USD > ceiling:
                raise RuntimeError('Generation budget guard reached; no request sent')
            reservation_token = uuid.uuid4().hex
            # The durable ledger is the only reservation counter, installed atomically
            # before releasing LOCK or allowing any request to be dispatched.
            atomic_json(attempt_file, {'id': item['id'], 'state': 'reserved',
                'request_sha256': item['request_sha256'], 'reserved_usd': MAX_REQUEST_USD,
                'reservation_token': reservation_token})
    started = time.monotonic()
    audio, metadata = request_audio(item, key, reservation_token)
    metadata['elapsed_seconds'] = round(time.monotonic() - started, 2)
    with LOCK:
        temp = output.with_suffix('.wav.tmp'); temp.write_bytes(audio); temp.replace(output)
        atomic_json(meta_path, metadata)
        atomic_json(attempt_file,
            {'id': item['id'], 'state': 'completed', 'request_sha256': item['request_sha256'],
             'cost_usd': metadata.get('cost_usd_from_reported_tokens', metadata['cost_usd_duration_based_estimate']),
             'interaction_id': metadata.get('interaction_id')})
    return {'id': item['id'], 'status': 'generated', 'seconds': round(metadata['duration_seconds'], 2),
        'pace': round(metadata['pace_words_per_minute'], 1), 'estimated_usd': round(metadata['cost_usd_duration_based_estimate'], 4)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'pilot', 'all', 'status'])
    parser.add_argument('--budget-usd', type=float, default=15)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    for folder in ('receipts', 'attempts'):
        (JOB / folder).mkdir(exist_ok=True)
    if args.mode == 'prepare':
        prepare(); return
    manifest = JOB / 'generation-manifest.json'
    if not manifest.exists():
        raise SystemExit('Run prepare first')
    items = json.loads(manifest.read_text())['items']
    if args.mode == 'status':
        metadata = [json.loads(p.read_text()) for p in (JOB / 'raw').glob('*.json')]
        print(json.dumps({'completed': len(metadata), 'expected': len(items),
            'hours': sum(m['duration_seconds'] for m in metadata)/3600,
            'estimated_usd': spent()}, indent=2)); return
    if args.mode == 'pilot':
        items = [items[0], next(i for i in items if i['chapter'] == 1)]
    key = key_from_project()
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(generate, item, key, args.budget_usd): item for item in items}
        for future in as_completed(futures):
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as exc:
                message = str(exc).replace(key, '[REDACTED]')
                errors.append({'id': futures[future]['id'], 'error': message})
                print(json.dumps(errors[-1]), flush=True)
                for pending in futures:
                    pending.cancel()
                break
    atomic_json(JOB / 'generation-errors.json', errors)
    print(json.dumps({'errors': len(errors), 'estimated_spent_usd': round(spent(), 4)}), flush=True)
    if errors:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
