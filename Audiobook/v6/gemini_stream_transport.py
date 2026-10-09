"""Durable, single-output Gemini SSE PCM transport. No credential discovery.

The caller owns billing reservations and must merge update_attempt(fields) into
its durable attempt record. Only a new journal may POST. Interrupted streams
without a durable cursor fail closed; they never trigger another paid request.
"""
import base64
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

URL = 'https://generativelanguage.googleapis.com/v1beta/interactions'

class StreamError(RuntimeError):
    pass

class StreamReadError(StreamError):
    pass

def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def iter_sse(response):
    """Yield JSON events and optional SSE IDs, preserving multiline data frames."""
    fields, data = {}, []
    iterator = iter(response)
    while True:
        try:
            raw = next(iterator)
        except StopIteration:
            break
        except (OSError, urllib.error.URLError, http.client.IncompleteRead):
            raise StreamReadError('SSE response read interrupted') from None
        line = raw.decode('utf-8', errors='strict').rstrip('\r\n') if isinstance(raw, bytes) else raw.rstrip('\r\n')
        if not line:
            if data:
                value = json.loads('\n'.join(data))
                if not isinstance(value, dict):
                    raise StreamError('SSE data must be an object')
                if fields.get('event') and value.get('event_type') != fields['event']:
                    raise StreamError('SSE event name disagrees with JSON event_type')
                cursor = value.get('event_id') or fields.get('id')
                if value.get('event_id') and fields.get('id') and value['event_id'] != fields['id']:
                    raise StreamError('SSE cursor disagreement')
                yield value, cursor
            fields, data = {}, []
        elif not line.startswith(':'):
            name, _, value = line.partition(':')
            value = value[1:] if value.startswith(' ') else value
            if name == 'data':
                data.append(value)
            elif name in ('id', 'event'):
                fields[name] = value
    if data:
        raise StreamError('SSE ended inside an unfinished frame')

class _Audio:
    def __init__(self, update_attempt, request_hash):
        self.update, self.request_hash = update_attempt, request_hash
        self.remote_id = self.cursor = self.terminal = None
        self.cursor_safe = False
        self.audio_index = None
        self.model_indices, self.seen = set(), {}
        self.pcm = bytearray()
        self.counts = {}

    def duplicate(self, event, cursor):
        if cursor and cursor in self.seen:
            if self.seen[cursor] != _hash(event):
                raise StreamError('Repeated event ID has conflicting data')
            return True
        return False

    def accept(self, event, cursor):
        kind = event.get('event_type')
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if cursor:
            self.seen[cursor] = _hash(event)
            self.cursor, self.cursor_safe = cursor, True
        interaction = event.get('interaction', {})
        remote = interaction.get('id') or event.get('interaction_id')
        if remote:
            if self.remote_id and remote != self.remote_id:
                raise StreamError('Stream changed remote interaction ID')
            self.remote_id = remote
            self.update({'state':'streaming', 'request_sha256':self.request_hash,
                         'interaction_id':remote, 'last_event_id':self.cursor})
        if kind == 'error':
            raise StreamError('Gemini emitted a stream error')
        if kind == 'step.start' and event.get('step', {}).get('type') == 'model_output':
            self.model_indices.add(event.get('index'))
            if len(self.model_indices) > 1:
                raise StreamError('Multiple model outputs are unsupported')
        if kind == 'step.delta':
            delta = event.get('delta', {})
            if delta.get('type') == 'audio':
                if not self.remote_id:
                    raise StreamError('Audio arrived before a durable remote ID')
                if (delta.get('mime_type', 'audio/l16') != 'audio/l16' or
                        delta.get('sample_rate', 24000) != 24000 or delta.get('channels', 1) != 1):
                    raise StreamError('Expected native 24 kHz mono audio/l16 PCM')
                index = event.get('index')
                if not isinstance(index, int) or isinstance(index, bool):
                    raise StreamError('Audio delta lacks an integer output index')
                if self.audio_index is not None and index != self.audio_index:
                    raise StreamError('Multiple audio outputs are unsupported')
                self.audio_index = index
                chunk = base64.b64decode(delta.get('data', ''), validate=True)
                if len(chunk) % 2:
                    raise StreamError('PCM delta contains an incomplete sample')
                self.pcm.extend(chunk)
                if chunk and not cursor:
                    self.cursor_safe = False
            elif delta.get('type') == 'text' and delta.get('text'):
                raise StreamError('Unexpected text output in audio-only stream')
        if kind == 'interaction.completed':
            if interaction.get('status') != 'completed':
                raise StreamError('Interaction did not complete successfully')
            if self.terminal is not None:
                raise StreamError('Multiple terminal interactions')
            self.terminal = interaction
        if kind == 'interaction.status_update' and event.get('status') in ('failed','incomplete','cancelled','budget_exceeded'):
            raise StreamError('Interaction entered unsuccessful terminal status')

    def finish(self, expected_words):
        if not self.terminal or not self.remote_id or not self.pcm:
            raise StreamError('Stream has no complete interaction and full PCM audio')
        usage = self.terminal.get('usage', {})
        audio_usage = [x for x in usage.get('output_tokens_by_modality', []) if x.get('modality') == 'audio']
        tokens = sum(x.get('tokens', 0) for x in audio_usage) if audio_usage else usage.get('total_output_tokens')
        if not isinstance(tokens, (int, float)) or tokens <= 0:
            raise StreamError('Completed stream has no positive audio token usage')
        seconds = len(self.pcm) / 48000
        if abs(seconds - tokens / 25) > max(1.0, tokens / 25 * .20):
            raise StreamError('Assembled audio duration disagrees with reported audio tokens')
        if expected_words and expected_words > 50 and expected_words / seconds * 60 > 250:
            raise StreamError('Assembled audio is too short for the transcript')
        terminal_audio = [part for step in self.terminal.get('steps', []) if step.get('type') == 'model_output'
                          for part in step.get('content', []) if part.get('type') == 'audio']
        if len(terminal_audio) > 1:
            raise StreamError('Terminal response contains multiple audio outputs')
        # Lifecycle audio can contain only a tail. Never append it to the deltas.
        result = {**self.terminal, 'id':self.remote_id, 'steps':[{'type':'model_output', 'content':[
            {'type':'audio', 'mime_type':'audio/l16', 'sample_rate':24000, 'channels':1,
             'data':base64.b64encode(self.pcm).decode()}]}], '_assembled_from_stream':True,
            '_stream_event_counts':self.counts, '_request_sha256':self.request_hash,
            '_stream_terminal_interaction':self.terminal}
        self.update({'state':'response_received', 'request_sha256':self.request_hash,
                     'interaction_id':self.remote_id, 'last_event_id':self.cursor})
        return result

def stream_interaction(payload, key, *, request_sha256, journal_path, update_attempt,
                       opener=urllib.request.urlopen, timeout=180, max_resume_requests=1,
                       expected_words=None):
    """Return one completed Interaction; opener and ledger callback are injectable.

    Completed journals reconstruct locally. Partial no-cursor journals refuse
    ambiguous recovery. Cursor recovery sends only GET, never a replacement POST.
    Known pre-creation 400/401/403/404/429 errors may be retried on a later call.
    """
    body = {**payload, 'stream':True}
    body.pop('background', None)  # default false; keep the tested minimal schema
    if body.get('response_format') != {'type':'audio'}:
        raise StreamError('Use the tested minimal audio response format')
    path = Path(journal_path); path.parent.mkdir(parents=True, exist_ok=True)
    state = _Audio(update_attempt, request_sha256)
    with path.open('a+b') as journal:
        try:
            fcntl.flock(journal.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StreamError('Another writer owns this stream journal') from None
        journal.seek(0); records = []; previous_hash = None
        for line in journal:
            if not line.endswith(b'\n'):
                raise StreamError('Stream journal is truncated; refusing network access')
            row = json.loads(line)
            digest = row.pop('record_sha256', None)
            if (row.get('sequence') != len(records)+1 or row.get('request_sha256') != request_sha256 or
                    row.get('previous_sha256') != previous_hash or _hash(row) != digest):
                raise StreamError('Stream journal identity/order/hash verification failed')
            previous_hash = digest; records.append(row)
        def append(kind, **fields):
            nonlocal previous_hash
            row = {'sequence':len(records)+1, 'kind':kind, 'request_sha256':request_sha256,
                   'previous_sha256':previous_hash, **fields}
            digest = _hash(row)
            journal.seek(0, 2)
            journal.write((json.dumps({**row,'record_sha256':digest}, ensure_ascii=False)+'\n').encode())
            journal.flush(); os.fsync(journal.fileno())
            directory = os.open(path.parent, os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
            try: os.fsync(directory)
            finally: os.close(directory)
            previous_hash = digest; records.append(row)
        if records:
            if records[0].get('kind') != 'header' or records[0].get('payload_sha256') != _hash(body):
                raise StreamError('Journal payload binding mismatch')
        else:
            append('header', payload_sha256=_hash(body))
        for row in records:
            if row['kind'] == 'event':
                event, cursor = row['event'], row.get('event_id')
                if state.duplicate(event, cursor):
                    raise StreamError('Journal contains duplicate event IDs')
                state.accept(event, cursor)
        if state.terminal:
            return state.finish(expected_words)
        dispatched = any(r['kind']=='dispatch' for r in records)
        last_kind = records[-1]['kind']
        new_post = not dispatched or (last_kind == 'rejected_before_creation' and not state.remote_id)
        if not new_post and (not state.remote_id or not state.cursor or not state.cursor_safe):
            raise StreamError('Interrupted stream has no safe cursor; no duplicate POST or ambiguous tail recovery')
        resumes = 0
        while True:
            if new_post:
                append('dispatch')
                update_attempt({'state':'dispatching','request_sha256':request_sha256,'stream_journal':str(path)})
                request = urllib.request.Request(URL, data=json.dumps(body).encode(), method='POST',
                    headers={'x-goog-api-key':key,'Content-Type':'application/json','Accept':'text/event-stream'})
            else:
                if resumes >= max_resume_requests:
                    raise StreamError('Read-only stream recovery limit reached')
                resumes += 1
                query = urllib.parse.urlencode({'stream':'true','last_event_id':state.cursor})
                request = urllib.request.Request(URL+'/'+urllib.parse.quote(state.remote_id, safe='')+'?'+query,
                    method='GET', headers={'x-goog-api-key':key,'Accept':'text/event-stream'})
            try:
                try:
                    opened = opener(request, timeout=timeout)
                except urllib.error.HTTPError:
                    raise
                except (OSError, urllib.error.URLError, http.client.IncompleteRead):
                    raise StreamReadError('SSE connection interrupted') from None
                with opened as response:
                    for event, cursor in iter_sse(response):
                        if state.duplicate(event, cursor):
                            continue
                        append('event', event=event, event_id=cursor, interaction_id=state.remote_id)
                        state.accept(event, cursor)
                        if state.terminal:
                            return state.finish(expected_words)
                    if state.terminal:
                        return state.finish(expected_words)
                raise StreamReadError('Stream ended without a terminal event')
            except urllib.error.HTTPError as exc:
                if new_post and not state.remote_id and exc.code in (400,401,403,404,429):
                    append('rejected_before_creation', http_status=exc.code)
                    update_attempt({'state':'rejected_before_generation','request_sha256':request_sha256,'reserved_usd':0})
                raise
            except StreamReadError:
                update_attempt({'state':'uncertain','request_sha256':request_sha256,
                                'interaction_id':state.remote_id,'last_event_id':state.cursor})
                if not state.remote_id or not state.cursor or not state.cursor_safe or resumes >= max_resume_requests:
                    raise StreamError('Stream interrupted; preserved journal and no duplicate POST sent') from None
                new_post = False
