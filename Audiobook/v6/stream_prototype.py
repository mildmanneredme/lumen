"""Bounded streaming TTS check; runtime secret is never logged."""
from pathlib import Path
import json, base64, urllib.request, urllib.error, uuid
import generate_audio as g

items = json.loads((g.JOB / 'generation-manifest.json').read_text())['items']
key = g.key_from_project()
item = next(i for i in items if not Path(i['output']).is_file())
attempt = g.JOB / 'attempts' / (item['id'] + '.json')
previous = g.bound_attempt(item, attempt)
assert previous is None or previous['state'] == 'rejected_before_generation'
assert g.spent() + g.MAX_REQUEST_USD <= 15
state = {'id': item['id'], 'state': 'dispatching', 'request_sha256': item['request_sha256'],
         'reserved_usd': g.MAX_REQUEST_USD, 'reservation_token': uuid.uuid4().hex,
         'transport': 'streamed_post'}
g.atomic_json(attempt, state)
payload = {'model': item['model'], 'stream': True,
           'input': [{'type': 'user_input', 'content': [{'type': 'text', 'text': item['text'],
               'annotations': [{'type': 'speech_metadata', 'style': item['style']}]}]}],
           'response_format': {'type': 'audio'},
           'generation_config': {'speech_config': [{'voice': item['voice']}]}}
req = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/interactions',
    data=json.dumps(payload).encode(), headers={'x-goog-api-key': key, 'Content-Type': 'application/json'}, method='POST')
chunks, counts, remote_id, terminal = [], {}, None, None
try:
    with urllib.request.urlopen(req, timeout=120) as response, (g.JOB / 'receipts' / (item['id'] + '-stream.jsonl')).open('w') as journal:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                continue
            event = json.loads(line[5:]); journal.write(json.dumps(event) + '\n'); journal.flush()
            kind = event.get('event_type', 'unknown'); counts[kind] = counts.get(kind, 0) + 1
            if kind == 'interaction.created':
                remote_id = event['interaction']['id']; state.update({'interaction_id': remote_id, 'state': 'streaming'}); g.atomic_json(attempt, state)
            if kind == 'step.delta' and event.get('delta', {}).get('type') == 'audio':
                block = event['delta']; assert block.get('mime_type', 'audio/l16') == 'audio/l16'
                assert block.get('sample_rate', 24000) == 24000 and block.get('channels', 1) == 1
                chunks.append(base64.b64decode(block['data']))
            if kind == 'interaction.completed':
                terminal = event.get('interaction')
    assert remote_id and terminal and terminal['status'] == 'completed', 'Incomplete stream'
    req = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/interactions/' + remote_id, headers={'x-goog-api-key': key})
    with urllib.request.urlopen(req, timeout=120) as response:
        data = json.load(response)
    assert data['id'] == remote_id and data['status'] == 'completed'
    audio = b''.join(chunks); seconds = len(audio) / 48000
    tokens = sum(t['tokens'] for t in data['usage'].get('output_tokens_by_modality', []) if t['modality'] == 'audio')
    assert seconds > item['words'] / 250 * 60, 'Implausibly short audio'
    assert abs(seconds - tokens / 25) < 1, 'Duration differs from audio tokens'
    data['steps'] = [s for s in data.get('steps', []) if s.get('type') != 'model_output'] + [
        {'type': 'model_output', 'content': [{'type': 'audio', 'data': base64.b64encode(audio).decode(), 'mime_type': 'audio/l16', 'sample_rate': 24000, 'channels': 1}]}]
    g.atomic_json(g.JOB / 'receipts' / (item['id'] + '.json'), {**data, '_request_sha256': item['request_sha256'], '_assembled_from_stream': True, '_stream_event_counts': counts})
    state['state'] = 'response_received'; g.atomic_json(attempt, state)
    result = g.generate(item, key, 15); result['stream_event_counts'] = counts
    print(json.dumps(result), flush=True)
except urllib.error.HTTPError as exc:
    body = exc.read().decode(errors='replace').replace(key, '[REDACTED]')
    if not remote_id and exc.code in (400, 401, 403, 404, 429):
        state.update({'state': 'rejected_before_generation', 'reserved_usd': 0, 'http_status': exc.code})
        g.atomic_json(attempt, state)
    print(json.dumps({'id': item['id'], 'http_status': exc.code, 'error': body[:900]}), flush=True)
    raise SystemExit(1)
except Exception as exc:
    print(json.dumps({'id': item['id'], 'error': str(exc).replace(key, '[REDACTED]'), 'remote_id_saved': bool(remote_id)}), flush=True)
    raise SystemExit(1)
