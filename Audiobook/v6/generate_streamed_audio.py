"""Stream complete Gemini narration with durable IDs and a shared cost guard."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse, base64, hashlib, json, math, os, time, urllib.error, urllib.request, uuid
import generate_audio as g


def generate(item, key, ceiling):
    actual_hash = hashlib.sha256((item['model'] + item['voice'] + item['style'] + item['text']).encode()).hexdigest()
    if actual_hash != item['request_sha256']:
        raise RuntimeError('Prepared request hash mismatch; no request sent')
    receipt = g.JOB / 'receipts' / (item['id'] + '.json')
    attempt = g.JOB / 'attempts' / (item['id'] + '.json')
    journal = g.JOB / 'receipts' / (item['id'] + '-stream.jsonl')
    journal_handle = None
    with g.LOCK:
        previous = g.bound_attempt(item, attempt)
        if receipt.exists():
            data = g.bound_receipt(item, receipt, attempt)
            if data.get('status') != 'completed':
                raise RuntimeError('Saved incomplete interaction requires read-only recovery, never a second POST: ' + item['id'])
        else:
            if previous and previous['state'] != 'rejected_before_generation':
                raise RuntimeError('Prior request has uncertain billing; no second POST: ' + item['id'])
            if Path(item['output']).exists() or Path(item['output']).with_suffix('.json').exists():
                raise RuntimeError('Orphan audio cache without a bound receipt: ' + item['id'])
            if g.spent() + g.MAX_REQUEST_USD > ceiling:
                raise RuntimeError('Generation budget guard reached; no request sent')
            if journal.exists():
                if previous and previous['state'] == 'rejected_before_generation' and journal.stat().st_size == 0:
                    journal.unlink()
                else:
                    raise RuntimeError('Existing stream journal requires read-only recovery; no request sent')
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal_handle = journal.open('x')
            state = {'id': item['id'], 'state': 'dispatching', 'request_sha256': item['request_sha256'],
                     'reserved_usd': g.MAX_REQUEST_USD, 'reservation_token': uuid.uuid4().hex,
                     'transport': 'streamed_post'}
            g.atomic_json(attempt, state)
    if receipt.exists():
        return g.generate(item, key, ceiling)
    payload = {'model': item['model'], 'stream': True,
               'input': [{'type': 'user_input', 'content': [{'type': 'text', 'text': item['text'],
                    'annotations': [{'type': 'speech_metadata', 'style': item['style']}]}]}],
               'response_format': {'type': 'audio'},
               'generation_config': {'speech_config': [{'voice': item['voice']}]}}
    request = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/interactions',
        data=json.dumps(payload).encode(), headers={'x-goog-api-key': key, 'Content-Type': 'application/json'}, method='POST')
    chunks, counts, remote_id, terminal, output_index = [], {}, None, None, None
    started = time.monotonic()
    try:
        with journal_handle as stream, urllib.request.urlopen(request, timeout=180) as response:
            for raw in response:
                line = raw.decode().strip()
                if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                    continue
                event = json.loads(line[5:]); stream.write(json.dumps(event) + '\n'); stream.flush()
                os.fsync(stream.fileno())
                kind = event.get('event_type', 'unknown'); counts[kind] = counts.get(kind, 0) + 1
                if kind == 'interaction.created':
                    created_id = event['interaction']['id']
                    if remote_id and remote_id != created_id:
                        raise RuntimeError('Stream interaction ID changed')
                    remote_id = created_id
                    with g.LOCK:
                        state.update({'interaction_id': remote_id, 'state': 'streaming'})
                        g.atomic_json(attempt, state)
                        g.atomic_json(receipt, {'id': remote_id, 'status': 'in_progress',
                            'model': item['model'], '_request_sha256': item['request_sha256'],
                            '_stream_journal': str(journal)})
                if kind == 'step.delta' and event.get('delta', {}).get('type') == 'audio':
                    if not remote_id:
                        raise RuntimeError('Audio arrived before a durable interaction ID')
                    block = event['delta']; index = event.get('index')
                    if output_index is not None and index != output_index:
                        raise RuntimeError('Unexpected multiple audio outputs')
                    output_index = index
                    if block.get('mime_type', 'audio/l16') != 'audio/l16' or block.get('sample_rate', 24000) != 24000 or block.get('channels', 1) != 1:
                        raise RuntimeError('Unexpected audio stream format')
                    chunks.append(base64.b64decode(block.get('data', ''), validate=True))
                if kind == 'interaction.completed':
                    terminal = event.get('interaction')
                    break
            stream.flush(); os.fsync(stream.fileno())
        if not remote_id or not terminal or terminal.get('id') != remote_id or terminal.get('status') != 'completed':
            raise RuntimeError('Incomplete stream; remote ID retained, no repeat POST')
        audio = b''.join(chunks); seconds = len(audio) / 48000
        if len(audio) % 2 or not seconds or item['words'] > 50 and item['words'] / seconds * 60 > 250:
            raise RuntimeError('Incomplete or implausibly short streamed audio')
        tokens = sum(t.get('tokens', 0) for t in terminal.get('usage', {}).get('output_tokens_by_modality', []) if t.get('modality') == 'audio')
        if tokens > 125 and abs(seconds - tokens / 25) > max(1, tokens / 25 * .2):
            raise RuntimeError('Stream duration differs from reported audio tokens')
        data = {**terminal, 'model': item['model'], '_request_sha256': item['request_sha256'],
                '_assembled_from_stream': True, '_stream_event_counts': counts,
                '_stream_elapsed_seconds': round(time.monotonic() - started, 2),
                'steps': [{'type': 'model_output', 'content': [{'type': 'audio',
                    'data': base64.b64encode(audio).decode(), 'mime_type': 'audio/l16',
                    'sample_rate': 24000, 'channels': 1}]}]}
        with g.LOCK:
            g.atomic_json(receipt, data)
            state['state'] = 'response_received'; g.atomic_json(attempt, state)
        return g.generate(item, key, ceiling)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors='replace').replace(key, '[REDACTED]')
        with g.LOCK:
            if not remote_id and exc.code in (400, 401, 403, 404, 429):
                state.update({'state': 'rejected_before_generation', 'reserved_usd': 0, 'http_status': exc.code})
            else:
                state['state'] = 'uncertain'
            g.atomic_json(attempt, state)
        raise RuntimeError(f'HTTP {exc.code}: {body[:700]}') from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--budget-usd', type=float, default=15)
    args = parser.parse_args()
    if args.workers < 1 or not math.isfinite(args.budget_usd) or args.budget_usd <= 0:
        parser.error('workers and budget must be positive and budget finite')
    items = json.loads((g.JOB / 'generation-manifest.json').read_text())['items']
    key = g.key_from_project(); errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = {executor.submit(generate, item, key, args.budget_usd): item for item in items}
        for future in as_completed(pending):
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as exc:
                errors.append({'id': pending[future]['id'], 'error': str(exc).replace(key, '[REDACTED]')})
                print(json.dumps(errors[-1]), flush=True)
                for work in pending:
                    work.cancel()
                break
    g.atomic_json(g.JOB / 'generation-errors.json', errors)
    print(json.dumps({'errors': len(errors), 'estimated_spent_usd': round(g.spent(), 4)}), flush=True)
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
