#!/usr/bin/env python3
"""Encode the existing paintings as WebP without changing their composition."""
from pathlib import Path
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'dist/assets'
encoder = shutil.which('cwebp')
if not encoder:
    raise SystemExit('cwebp is required to prepare the optimized artwork.')

records = []
for source in sorted(ASSETS.glob('*.png')):
    target = source.with_suffix('.webp')
    subprocess.run([encoder, '-quiet', '-q', '86', '-m', '6', str(source), '-o', str(target)], check=True)
    records.append({'id': source.stem, 'sourceBytes': source.stat().st_size, 'webpBytes': target.stat().st_size})

before = sum(record['sourceBytes'] for record in records)
after = sum(record['webpBytes'] for record in records)
report = {'encoder': 'cwebp', 'quality': 86, 'paintings': records, 'sourceBytes': before,
          'webpBytes': after, 'reductionPercent': round(100 * (1 - after / before), 1)}
(ROOT / 'data/artwork-optimization.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'paintings': len(records), 'sourceBytes': before, 'webpBytes': after,
                  'reductionPercent': report['reductionPercent']}))
