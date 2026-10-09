"""Compact local progress snapshot; never reads the API key."""
import hashlib, json, time
from pathlib import Path
import generate_audio as g

def status():
    items = json.loads((g.JOB / 'generation-manifest.json').read_text())['items']
    saved = []
    for item in items:
        meta = Path(item['output']).with_suffix('.json')
        if meta.exists() and Path(item['output']).exists():
            record = json.loads(meta.read_text())
            if record.get('request_sha256') == item['request_sha256']:
                saved.append(record)
    qa_path = g.JOB / 'local-checks/summary.json'
    qa = json.loads(qa_path.read_text()) if qa_path.exists() else {}
    saved_by_id = {record['id']: record for record in saved}
    checked = set()
    for path in (g.JOB / 'local-checks/cache').glob('*.json'):
        record = json.loads(path.read_text())
        identity = record.get('cache_identity', {})
        item_id = path.name.split('.')[0]
        current = saved_by_id.get(item_id)
        if current and identity.get('request_sha256') == current['request_sha256'] and identity.get('audio_sha256') == current.get('audio_sha256'):
            checked.add(item_id)
    mastering = json.loads((g.JOB / 'mastered/mastering-report.json').read_text())
    report_current = mastering.get('generation_manifest_sha256') == hashlib.sha256((g.JOB / 'generation-manifest.json').read_bytes()).hexdigest()
    chapters = mastering.get('chapters', [])
    states = {}
    for path in (g.JOB / 'attempts').glob('*.json'):
        record = json.loads(path.read_text())
        states[record['state']] = states.get(record['state'], 0) + 1
    result = {'updated_at_unix': time.time(), 'selected_clips_saved': len(saved),
              'clips_expected': len(items),
              'saved_audio_hours': round(sum(m['duration_seconds'] for m in saved) / 3600, 3),
              'reported_rate_equivalent_and_reservations_usd': round(g.spent(), 4),
              'attempt_states': states, 'local_asr_checked': len(checked),
              'local_asr_errors': qa.get('errors', 0),
              'mastered_chapter_files': len(list((g.JOB / 'mastered').glob('chapter-*.checkpoint.json'))),
              'mastering_chapters_verified_in_current_scan': len(chapters),
              'mastering_errors': mastering.get('errors', []),
              'mastering_report_current': report_current,
              'mastering_complete': mastering.get('complete', False) and report_current}
    g.atomic_json(g.JOB / 'progress-status.json', result)
    cost_path = g.JOB / 'cost-and-progress.json'
    cost = json.loads(cost_path.read_text())
    cost.update({'actual_invoice_status': 'Not verified; updated key belongs to Prototypes, Paid Tier 3/Postpay, Batch-1',
                 'generated_clips': len(saved), 'generated_minutes': round(sum(m['duration_seconds'] for m in saved) / 60, 2),
                 'generated_paid_rate_equivalent_usd': round(sum(m.get('cost_usd_from_reported_tokens', m.get('cost_usd_duration_based_estimate', 0)) for m in saved), 4),
                 'remaining_clips': len(items) - len(saved), 'production_blocker': None,
                 'billing_diagnosis': 'User supplied a key for an already paid Batch-1 project; no billing settings changed by agent.'})
    g.atomic_json(cost_path, cost)
    return result

if __name__ == '__main__':
    print(json.dumps(status()))
