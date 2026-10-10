"""Author decisions must survive restarts and never repair a different take."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import unittest

SCRIPT = Path(__file__).parents[1] / 'scripts' / 'audit_server.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('audit_server', SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuthorAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / 'Audiobook/v7/mastered/chapter-018.mp3'
        self.audio.parent.mkdir(parents=True)
        self.audio.write_bytes(b'original mastered recording')
        self.raw = self.root / 'Audiobook/v7/raw/chapter-018-002.wav'
        self.raw.parent.mkdir(parents=True)
        self.raw.write_bytes(b'original source take')
        self.digest = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        raw_hash = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.report = self.root / 'Audiobook/openai-review/review.json'
        self.report.parent.mkdir(parents=True)
        self.candidate = dict(candidate_id='a'*64, edition='v7', narrator='Autonoe',
            id='chapter-018-002', chapter=18, chapter_title='Chapter 18', chunk=2,
            mastered_mp3=str(self.audio), mastered_mp3_sha256=self.digest,
            raw_audio_sha256=raw_hash, request_sha256='c'*64,
            expected='', heard='unexpected passage', expected_context='The last faithful sentence.',
            severity='high', category='passage_addition_candidate',
            listening_window=dict(start_seconds=10, end_seconds=30, precision='exact_mastered_chunk_sample_interval', scope='Whole transcription chunk'),
            whisper_anchor=dict(start_seconds=22, end_seconds=27, suggested_clip_start_seconds=20,
                                suggested_clip_end_seconds=29, precision='independent_whisper_word_alignment', scope='Estimated phrase'))
        self.write_report()
        self.manifest = self.root / 'Audiobook/v7/generation-manifest.json'
        self.manifest.write_text(json.dumps(dict(model='tts-model', voice='Autonoe', items=[dict(
            id='chapter-018-002', output=str(self.raw), request_sha256='c'*64,
            text='The last faithful sentence.', model='tts-model', voice='Autonoe', style='Restrained')])) )
        self.store = audit.AuditStore(self.root, include_local=False)

    def tearDown(self):
        self.temp.cleanup()

    def write_report(self):
        self.report.write_text(json.dumps(dict(complete=False, selected_chunks=496,
            transcribed_chunks=142, candidates=[self.candidate])))

    def save(self, status='regenerate', notes='Remove the added passage', revision=0):
        return self.store.save(dict(candidateId='a'*64, audioHash=self.digest,
            status=status, notes=notes, revision=revision))

    def test_snapshot_uses_estimated_master_clock_and_partial_coverage(self):
        result = self.store.snapshot()
        self.assertEqual(result['coverage'], dict(complete=False, selected=496, checked=142))
        c = result['candidates'][0]
        self.assertEqual((c['start'], c['end']), (20,29))
        self.assertEqual(c['precision'], 'independent_whisper_word_alignment')
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_decision_persists_and_queue_preserves_exact_source(self):
        self.assertEqual(self.save()['revision'], 1)
        fresh = audit.AuditStore(self.root, include_local=False)
        self.assertEqual(fresh.snapshot()['candidates'][0]['decision']['status'], 'regenerate')
        queue = fresh.queue()
        self.assertEqual(len(queue['items']),1)
        self.assertEqual(queue['items'][0]['text'],'The last faithful sentence.')
        self.assertEqual(queue['items'][0]['voice'],'Autonoe')
        self.assertFalse(queue['production_selection_modified'])
        self.assertEqual(json.loads(self.manifest.read_text())['items'][0]['id'], 'chapter-018-002')

    def test_audio_revision_prevents_save_and_queue(self):
        self.save()
        self.audio.write_bytes(b'regenerated recording')
        with self.assertRaises(audit.AuditConflict): self.save(revision=1)
        with self.assertRaises(audit.AuditConflict): self.store.queue()

    def test_source_revision_prevents_repair_plan(self):
        self.save()
        self.raw.write_bytes(b'different source take')
        with self.assertRaises(audit.AuditConflict): self.store.queue()

    def test_report_binding_changes_make_previous_feedback_stale(self):
        self.save()
        self.candidate['request_sha256'] = 'd'*64
        self.write_report()
        c = self.store.snapshot()['candidates'][0]
        self.assertEqual(c['decision']['status'], 'pending')
        self.assertTrue(c['decision']['stale'])

    def test_two_tabs_cannot_overwrite_newer_feedback(self):
        self.save('accepted','Sounds faithful')
        with self.assertRaises(audit.AuditConflict): self.save()
        self.save('unsure','Check pronunciation',1)
        ledger=json.loads(self.store.feedback_path.read_text())
        self.assertEqual(len(ledger['history']),2)

    def test_only_explicit_regeneration_is_queued(self):
        for status in ['pending','accepted','unsure']:
            revision=self.store.snapshot()['candidates'][0]['decision']['revision']
            self.save(status,'Listening note',revision)
            self.assertEqual(self.store.queue()['items'],[])

    def test_regeneration_requires_actionable_notes_and_valid_status(self):
        for status, notes in [('regenerate',' '),('approved','foo')]:
            with self.assertRaises(ValueError): self.save(status,notes)

    def test_paths_outside_production_audio_are_rejected(self):
        self.candidate['mastered_mp3']=str(self.root / '.env')
        self.write_report()
        with self.assertRaises(ValueError): self.store.snapshot()

    def test_feedback_corruption_is_reported_not_overwritten(self):
        self.store.feedback_path.parent.mkdir(parents=True,exist_ok=True)
        self.store.feedback_path.write_text('{broken')
        with self.assertRaises(ValueError): self.save()
        self.assertEqual(self.store.feedback_path.read_text(),'{broken')

    def test_repeated_candidates_group_into_one_source_retake(self):
        c={**self.candidate,'candidate_id':'b'*64,'expected':'word','heard':'wrong'}
        report=json.loads(self.report.read_text()); report['candidates'].append(c)
        self.report.write_text(json.dumps(report))
        self.save()
        self.store.save(dict(candidateId='b'*64,audioHash=self.digest,status='regenerate',notes='Fix word',revision=0))
        q=self.store.queue()['items']
        self.assertEqual(len(q),1)
        self.assertEqual(len(q[0]['findings']),2)

    def start_http(self):
        instance = audit.server(self.root,0)
        instance.store = self.store
        instance.ui = SCRIPT.parents[1] / 'audit'
        thread=threading.Thread(target=instance.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(instance.server_close)
        self.addCleanup(instance.shutdown)
        self.origin=f'http://127.0.0.1:{instance.server_address[1]}'
        self.http=instance

    def request(self,path,headers=None,data=None):
        return urlopen(Request(self.origin+path,headers=headers or {},data=data),timeout=5)

    def test_http_serves_only_allowlisted_audio_with_byte_ranges(self):
        self.start_http()
        with self.request('/api/audio?id='+'a'*64, {'Range':'bytes=2-7'}) as response:
            self.assertEqual(response.status,206)
            self.assertEqual(response.read(),b'iginal')
            self.assertEqual(response.headers['Content-Range'],'bytes 2-7/27')
        for path in ['/Audiobook/v7/mastered/chapter-018.mp3','/../.env','/api/audio?id=../../.env']:
            with self.assertRaises(HTTPError) as context:self.request(path)
            self.assertEqual(context.exception.code,404)

    def test_http_rejects_cross_site_and_rebinding_and_missing_token(self):
        self.start_http()
        for headers in [{'Host':'attacker.test'}, {'Sec-Fetch-Site':'cross-site'}]:
            with self.assertRaises(HTTPError) as context:self.request('/api/audit',headers)
            self.assertEqual(context.exception.code,403)
        for headers in [{}, {'Origin':'https://attacker.test','X-Audit-Token':self.http.csrf_token}]:
            with self.assertRaises(HTTPError) as context:self.request('/api/decisions',headers,b'{}')
            self.assertEqual(context.exception.code,403)
        self.assertFalse(self.store.feedback_path.exists())

    def test_http_saves_decisions_and_serves_ui_and_queue(self):
        self.start_http()
        with self.request('/api/audit') as response:token=json.load(response)['csrfToken']
        headers={'Origin':self.origin,'Content-Type':'application/json','X-Audit-Token':token}
        data=json.dumps(dict(candidateId='a'*64,audioHash=self.digest,status='regenerate',notes='Remove added words',revision=0)).encode()
        with self.request('/api/decisions',headers,data) as response:
            self.assertEqual(json.load(response)['decision']['revision'],1)
        with self.request('/api/queue') as response:
            self.assertIn('attachment',response.headers['Content-Disposition'])
            self.assertEqual(len(json.load(response)['items']),1)
        for path in ['/audit','/audit/','/audit/audit.js','/audit/audit.css']:
            with self.request(path) as response:
                self.assertEqual(response.status,200)
                self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])

    def test_http_range_edge_cases_and_stale_recordings(self):
        self.start_http()
        path='/api/audio?id='+'a'*64
        with self.request(path,{'Range':'bytes=-4'}) as response:
            self.assertEqual(response.read(),b'ding')
        with self.assertRaises(HTTPError) as context:self.request(path,{'Range':'bytes=999-'})
        self.assertEqual(context.exception.code,416)
        self.audio.write_bytes(b'new take')
        with self.assertRaises(HTTPError) as context:self.request(path)
        self.assertEqual(context.exception.code,409)


if __name__ == '__main__': unittest.main()
