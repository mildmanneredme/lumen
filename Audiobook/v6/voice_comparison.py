"""Generate short, same-passage Gemini narration auditions; never alters the book pass."""
from pathlib import Path
import hashlib, importlib.util, json, re, sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT / "Draft/v6/audiobook/tts-chunks.jsonl"
OUT = HERE / "voice-comparison"
GENERATOR = HERE / "generate_audio.py"
MODEL = "gemini-3.8-flash-lite-tts"

# Load the production request handling while redirecting all receipts, attempts,
# and WAVs into this audition-only directory.
spec = importlib.util.spec_from_file_location("lumen_tts_generator", GENERATOR)
gen = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gen
spec.loader.exec_module(gen)
gen.JOB = OUT

items = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines()]
chunk = next(i for i in items if i["id"] == "chapter-001-002")
paragraphs = [p.strip() for p in chunk["text"].split("\n\n") if p.strip()]
text = next(p for p in paragraphs if 80 <= len(re.findall(r"\b\w+(?:['’\-]\w+)*\b", p)) <= 100 and '"' in p)

style_current = gen.STYLE
style_story = ("Narrate as a literary thriller for one attentive listener. Keep the voice close and human. "
    "Shape each sentence with natural rises and falls, vary pace as the thought develops, and let key phrases land. "
    "Use subtle unease and lightly differentiated quoted speech. Engaged but restrained, never flat, announcer-like, or theatrical.")
style_suspense = ("Read with quiet suspense and forward momentum. Begin measured, then give important clauses a little more energy; "
    "slow briefly at turns and let the final thought carry weight. Use varied pitch, clear thought groups, and natural conversational emphasis. "
    "Intimate, believable literary narration; avoid a lecture cadence or melodrama.")

variants = [
    ("A-current-Charon", "Charon", style_current),
    ("B-story-Charon", "Charon", style_story),
    ("C-suspense-Charon", "Charon", style_suspense),
    ("D-story-Gacrux", "Gacrux", style_story),
    ("E-story-Sulafat", "Sulafat", style_story),
    ("F-story-Sadachbia", "Sadachbia", style_story),
]

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

OUT.mkdir(parents=True, exist_ok=True)
manifest_path = OUT / "audition-manifest.json"
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if [(v["id"], v["voice"], v["style"], v["text"]) for v in manifest["variants"]] != [
            (v[0], v[1], v[2], text) for v in variants]:
        raise SystemExit("Saved audition manifest differs; refusing to reuse its request IDs")
else:
    records = []
    for variant_id, voice, style in variants:
        request_hash = hashlib.sha256((MODEL + voice + style + text).encode()).hexdigest()
        records.append({"id": variant_id, "chapter": 1, "chunk": 2, "text": text,
                        "words": len(re.findall(r"\b\w+(?:['’\-]\w+)*\b", text)),
                        "model": MODEL, "voice": voice, "style": style,
                        "request_sha256": request_hash,
                        "output": str(OUT / "raw" / (variant_id + ".wav"))})
    atomic_json(manifest_path, {"model": MODEL, "source_chunk": chunk["id"], "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                "variants": records, "production_audio_modified": False})
    manifest = {"variants": records}

key = gen.key_from_project()
results, errors = [], []
for item in manifest["variants"]:
    try:
        result = gen.generate(item, key, 1.0)
        results.append(result)
        print(json.dumps(result), flush=True)
    except Exception as exc:
        error = {"id": item["id"], "error": str(exc).replace(key, "[REDACTED]")}
        errors.append(error)
        print(json.dumps(error), flush=True)
        if "daily" in error["error"].lower() and "quota" in error["error"].lower():
            break

completed = []
for item in manifest["variants"]:
    meta_path = Path(item["output"]).with_suffix(".json")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        completed.append({"id": item["id"], "voice": item["voice"], "duration_seconds": meta["duration_seconds"],
                          "word_count": item["words"], "pace_wpm": meta["pace_words_per_minute"],
                          "estimated_cost_usd": meta.get("cost_usd_from_reported_tokens", meta["cost_usd_duration_based_estimate"])})
atomic_json(OUT / "audition-results.json", {"completed": completed, "errors": errors,
            "total_estimated_cost_usd": round(sum(x["estimated_cost_usd"] for x in completed), 4),
            "production_audio_modified": False})
print(json.dumps({"completed_samples": len(completed), "failed_samples": len(errors),
                  "estimated_cost_usd": round(sum(x["estimated_cost_usd"] for x in completed), 4),
                  "output_dir": str(OUT)}), flush=True)
