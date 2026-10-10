#!/usr/bin/env python3
"""Local author listening audit. No uploads, TTS calls, or production mutations."""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
from http.server import ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlsplit

from serve import RangeHandler


class AuditConflict(ValueError):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix='.feedback-', delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        import os
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    temporary.replace(path)


class AuditStore:
    def __init__(self, root, *, include_local=True):
        self.root = Path(root).resolve()
        self.report_path = self.root / 'Audiobook/openai-review/review.json'
        self.feedback_path = self.root / 'Audiobook/author-audit/feedback.json'
        self.include_local = include_local
        self.lock = threading.RLock()
        self.hashes = {}

    def audio_path(self, path, edition, kind):
        path = Path(path).resolve()
        allowed = self.root / 'Audiobook' / edition / kind
        if (edition not in {'v7', 'v8'} or path.parent != allowed
                or path.suffix != ('.mp3' if kind == 'mastered' else '.wav')):
            raise ValueError('Audio path is outside the selected production directory')
        return path

    def verify(self, path, expected):
        try:
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
            cached = self.hashes.get(path)
            if not cached or cached[0] != signature:
                h = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024*1024), b''):
                        h.update(block)
                self.hashes[path] = (signature, h.hexdigest())
            if self.hashes[path][1] != expected:
                raise AuditConflict('Recording changed. Rebuild the review and listen to the new take.')
        except OSError as error:
            raise AuditConflict('The bound recording is unavailable locally.') from error

    def catalog(self):
        report = read(self.report_path)
        candidates = []
        for c in report['candidates']:
            window = c.get('whisper_anchor') or c['listening_window']
            path = self.audio_path(c['mastered_mp3'], c['edition'], 'mastered')
            item = dict(candidateId=c['candidate_id'], edition=c['edition'], narrator=c['narrator'],
                chapter=c['chapter'], title=c['chapter_title'], chunkId=c['id'],
                audioHash=c['mastered_mp3_sha256'], rawHash=c['raw_audio_sha256'],
                requestHash=c['request_sha256'], expected=c['expected'], heard=c['heard'],
                expectedContext=c.get('expected_context',''), category=c['category'], severity=c['severity'],
                start=window.get('suggested_clip_start_seconds',window['start_seconds']),
                end=window.get('suggested_clip_end_seconds',window['end_seconds']),
                precision=window['precision'], scope=window['scope'], source='OpenAI / mastered chapter',
                _path=path)
            item['binding'] = digest({k: v for k,v in item.items() if k != '_path'})
            candidates.append(item)
        if self.include_local:
            candidates.extend(self.local_candidates())
        ids = set()
        for c in candidates:
            if (c['candidateId'] in ids or c['severity'] not in {'high','medium','low'}
                    or not all(isinstance(c[k], (int,float)) and not isinstance(c[k],bool)
                               and math.isfinite(c[k]) for k in ['start','end'])
                    or not 0 <= c['start'] < c['end']):
                raise ValueError('Invalid or duplicate listening candidate')
            ids.add(c['candidateId'])
            c['audioUrl'] = '/api/audio?id=' + quote(c['candidateId'], safe='')
        return report, {c['candidateId']: c for c in candidates}

    def local_candidates(self):
        base = self.root / 'Audiobook/v8'
        summary_path = base / 'local-checks/rechecks/summary.json'
        ledger_path = base / 'qa-review/candidate-ledger.json'
        if not summary_path.exists() or not ledger_path.exists():
            return []
        summary, ledger = read(summary_path), read(ledger_path)
        selected = {c['id']: c for c in read(base / 'generation-manifest.json')['items']}
        rows = []
        for result in summary['candidate_results']:
            if result['status'] not in {'unresolved_second_pass_mismatch','insufficient_context_or_error'}:
                continue
            c = ledger['candidates'].get(result['candidate_id'])
            if not c or not c.get('active_in_current_sources'):
                continue
            if any(c[k] != result[k] for k in ['clip_id','audio_sha256','request_sha256']):
                raise AuditConflict('Local review sources changed; rebuild the recheck report.')
            source = selected.get(c['clip_id'])
            if not source or source['request_sha256'] != c['request_sha256']:
                raise AuditConflict('Local review no longer matches the selected source take.')
            window = c['audio_anchor']
            # Local ASR times belong to the raw WAV; never seek the mastered MP3 with them.
            path = self.audio_path(source['output'], 'v8', 'raw')
            span = c['source_char_span']
            item = dict(candidateId='local:'+c['candidate_id'], edition='v8', narrator='Charon',
                chapter=source['chapter'], title=f"Chapter {source['chapter']}", chunkId=c['clip_id'],
                audioHash=c['audio_sha256'], rawHash=c['audio_sha256'], requestHash=c['request_sha256'],
                expected=c['expected'], heard=c['heard'],
                expectedContext=source['text'][max(0,span[0]-180):span[1]+180],
                category=c['classification'], severity=c['priority'],
                start=window.get('suggested_clip_start_seconds', window['start_seconds']),
                end=window.get('suggested_clip_end_seconds', window['end_seconds']),
                precision='local_whisper_' + window['precision'],
                scope='Local Whisper estimate in the original source take, before mastering. ' + result['status'],
                source='Local ASR / source take', _path=path)
            item['binding'] = digest({k:v for k,v in item.items() if k != '_path'})
            rows.append(item)
        return rows

    def ledger(self):
        if not self.feedback_path.exists():
            return {'schemaVersion':1, 'decisions':{}, 'history':[]}
        result = read(self.feedback_path)
        if (result.get('schemaVersion') != 1 or not isinstance(result.get('decisions'),dict)
                or not isinstance(result.get('history'),list)):
            raise ValueError('Feedback ledger is invalid. Restore it from a backup before saving.')
        return result

    def decision(self, c, ledger):
        previous = ledger['decisions'].get(c['candidateId'])
        if not previous:
            return dict(status='pending', notes='', revision=0)
        if previous['binding'] != c['binding']:
            return dict(status='pending', notes='', revision=previous['revision'], stale=True)
        return {k: previous[k] for k in ['status','notes','revision']}

    def snapshot(self):
        with self.lock:
            report, candidates = self.catalog()
            ledger = self.ledger()
            rows = []
            for c in candidates.values():
                rows.append({**{k:v for k,v in c.items() if k not in {'_path','binding','rawHash','requestHash'}},
                             'decision':self.decision(c,ledger)})
            return dict(schemaVersion=1,
                coverage=dict(complete=report['complete'], selected=report['selected_chunks'], checked=report['transcribed_chunks']),
                candidates=rows)

    def save(self, value):
        with self.lock:
            _, candidates = self.catalog()
            c = candidates.get(value.get('candidateId'))
            if not c:
                raise AuditConflict('This flag is no longer in the current review.')
            if value.get('audioHash') != c['audioHash']:
                raise AuditConflict('Recording identity changed. Listen again before saving.')
            self.verify(c['_path'],c['audioHash'])
            status, notes, revision = value.get('status'), value.get('notes'), value.get('revision')
            if (status not in {'pending','accepted','regenerate','unsure'} or not isinstance(notes,str)
                    or len(notes)>10000 or status=='regenerate' and not notes.strip()
                    or not isinstance(revision,int) or isinstance(revision,bool)):
                raise ValueError('Choose a valid decision; regeneration needs a specific correction note.')
            ledger = self.ledger()
            current = self.decision(c, ledger)
            if revision != current['revision']:
                raise AuditConflict('Newer feedback was saved in another tab. Review it before saving again.')
            saved = dict(status=status, notes=notes, revision=revision+1,
                candidateId=c['candidateId'], binding=c['binding'], audioHash=c['audioHash'],
                rawHash=c['rawHash'], requestHash=c['requestHash'], edition=c['edition'], chunkId=c['chunkId'],
                reviewedAt=datetime.now(timezone.utc).isoformat())
            ledger['decisions'][c['candidateId']] = saved
            ledger['history'].append(saved)
            atomic(self.feedback_path, ledger)
            return self.decision(c, ledger)

    def audio(self, candidate_id):
        with self.lock:
            _, candidates = self.catalog()
            c = candidates.get(candidate_id)
            if not c:
                raise KeyError('Unknown audio ID')
            self.verify(c['_path'], c['audioHash'])
            return c['_path']

    def queue(self):
        with self.lock:
            _, candidates = self.catalog()
            ledger = self.ledger()
            groups, stale = {}, []
            manifests = {}
            for c in candidates.values():
                verdict = self.decision(c, ledger)
                if verdict.get('stale'):
                    stale.append(c['candidateId'])
                if verdict['status'] != 'regenerate':
                    continue
                self.verify(c['_path'],c['audioHash'])
                if c['edition'] not in manifests:
                    manifests[c['edition']] = {i['id']:i for i in read(
                        self.root/'Audiobook'/c['edition']/'generation-manifest.json')['items']}
                source = manifests[c['edition']].get(c['chunkId'])
                if not source or source['request_sha256'] != c['requestHash']:
                    raise AuditConflict('Repair plan no longer matches the selected generation request.')
                raw = self.audio_path(source['output'], c['edition'], 'raw')
                self.verify(raw, c['rawHash'])
                key = (c['edition'],c['chunkId'],c['requestHash'],c['rawHash'])
                if key not in groups:
                    groups[key] = dict(edition=c['edition'], sourceId=c['chunkId'],
                        sourceRequestHash=c['requestHash'], sourceAudioHash=c['rawHash'],
                        text=source['text'], model=source['model'], voice=source['voice'], style=source['style'],
                        findings=[], action='create_new_take_then_reaudit',
                        selection='retain_current_take_until_replacement_approved')
                groups[key]['findings'].append(dict(candidateId=c['candidateId'], notes=verdict['notes'],
                    revision=verdict['revision'], audioHash=c['audioHash'], start=c['start'], end=c['end'],
                    clock=c['source']))
            return dict(schemaVersion=1, items=list(groups.values()), staleDecisionIds=stale,
                        production_selection_modified=False, paid_requests_dispatched=False)


class AuditHandler(RangeHandler):
    def log_message(self, format, *args):
        pass

    def permitted(self):
        port = self.server.server_address[1]
        allowed = {f'127.0.0.1:{port}',f'localhost:{port}'}
        return (self.headers.get('Host') in allowed
                and self.headers.get('Sec-Fetch-Site') != 'cross-site')

    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
        self.send_header('Referrer-Policy','no-referrer')
        super().end_headers()

    def respond(self, status, value, *, download=False, head=False):
        data = json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        if download:
            self.send_header('Content-Disposition','attachment; filename="lumen-regeneration-queue.json"')
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def translate_path(self, path):
        return str(self._serve_path)

    def do_HEAD(self):
        self.route(head=True)

    def do_GET(self):
        self.route()

    def route(self, head=False):
        if not self.permitted():
            return self.respond(403,dict(error='Local same-origin access required.'),head=head)
        url = urlsplit(self.path)
        try:
            if url.path == '/api/audit':
                return self.respond(200,{**self.server.store.snapshot(), 'csrfToken':self.server.csrf_token},head=head)
            if url.path == '/api/queue':
                return self.respond(200,self.server.store.queue(),download=True,head=head)
            if url.path == '/api/audio':
                candidate = parse_qs(url.query).get('id',[''])[0]
                self._serve_path = self.server.store.audio(candidate)
            else:
                paths = {'/audit':'index.html','/audit/':'index.html','/':'index.html',
                         '/audit.html':'index.html','/audit.css':'audit.css','/audit.js':'audit.js',
                         '/audit/audit.css':'audit.css','/audit/audit.js':'audit.js'}
                name = paths.get(url.path)
                if not name:
                    return self.respond(404,dict(error='Not found'),head=head)
                self._serve_path = self.server.ui / name
            if head:
                return super().do_HEAD()
            return super().do_GET()
        except AuditConflict as error:
            self.respond(409,dict(error=str(error)),head=head)
        except KeyError:
            self.respond(404,dict(error='Unknown recording'),head=head)
        except (ValueError,OSError) as error:
            # Never send arbitrary filesystem paths or tracebacks to the browser.
            self.respond(503,dict(error='Audit source is unavailable or invalid. '+
                                 ('Rebuild the local review.' if isinstance(error,OSError) else 'Check the server source data.')),head=head)

    def do_POST(self):
        origin = 'http://' + self.headers.get('Host','')
        if (not self.permitted() or self.headers.get('Origin') != origin
                or not secrets.compare_digest(self.headers.get('X-Audit-Token',''), self.server.csrf_token)):
            return self.respond(403,dict(error='Local same-origin access and audit token required.'))
        if urlsplit(self.path).path != '/api/decisions':
            return self.respond(404,dict(error='Not found'))
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 65536 or self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                raise ValueError('Expected a small JSON decision')
            self.connection.settimeout(5)
            value = json.loads(self.rfile.read(size))
            if not isinstance(value,dict):
                raise ValueError('Expected a JSON object')
            self.respond(200,dict(decision=self.server.store.save(value)))
        except AuditConflict as error:
            self.respond(409,dict(error=str(error)))
        except (ValueError,OSError) as error:
            self.respond(400,dict(error='Could not save decision: '+str(error) if isinstance(error,ValueError)
                                 else 'Could not save feedback locally.'))


def server(root, port=8766):
    instance = ThreadingHTTPServer(('127.0.0.1',port), AuditHandler)
    instance.store = AuditStore(root)
    instance.csrf_token = secrets.token_urlsafe(32)
    instance.ui = Path(root)/'web/audit'
    return instance


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    instance=server(root,args.port)
    print(f'Lumen author audit: http://127.0.0.1:{args.port}/audit',flush=True)
    try:
        instance.serve_forever()
    except KeyboardInterrupt:
        instance.server_close()
