#!/usr/bin/env python3
"""Local-only, resumable narration QA. Differences are ASR review candidates.

Run with the project-local Python after the model has been installed. This file
never imports the cloud generator, uploads audio, or downloads a model. The
--selftest path exercises comparison only and does not import MLX Whisper.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import difflib
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
import wave

JOB = Path(__file__).resolve().parent
VERSION = "local-qa-2"
COMPARISON_VERSION = "local-comparison-3"
THRESHOLD = 0.035
VOCABULARY = (
    "Rob Xie, Lumen, Adrian Marsh, Sebastian Hale, Kai Nakamura, Nadia Osei, "
    "Ólafur Sigurdsson, Tomás Ferreira, Ines Brandt, Andrei Lepp, Sophie Richter, "
    "Priya Chandrasekaran, Daniel Yoon, James Morrow, Harrison Polk, Elias Strutt, "
    "Arthur Venn, Magnus Cole, Geoffrey Laine, Clive Barrington, NovaMind, NovaTok"
)
TOKEN_RE = re.compile(r"(?<!\w)[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:st|nd|rd|th)?|[^\W\d_]+(?:['’‘][^\W\d_]+)*|%", re.UNICODE)
SMALL = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
SCALES = {"thousand": 1000, "million": 10**6, "billion": 10**9, "trillion": 10**12}
ORDINALS = dict(zip("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split(), range(1, 21)))
ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "jr", "sr", "e.g", "i.e", "a.m", "p.m", "ph.d"}
DIRECTOR_PATTERNS = (
    r"\b(?:open|opening|close|closing) (?:square )?bracket\b",
    r"\b(?:director s note|directors note|stage direction|speech metadata|input text)\b",
    r"\b(?:pause|pausing) (?:for )?(?:\d+|one|two|three|four|five|ten) seconds?\b",
    r"\b(?:read aloud|preserve the wording|literary narration|narrator voice|speaker one)\b",
    r"\b(?:in a|with a) (?:whispered|whispering|dramatic|calm|narrator) voice\b",
    r"\b(?:end quote|begin quote|start quote|end of input|end of narration)\b",
)


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int


class BackendImportError(RuntimeError):
    """An import failure must stop this worker before a partially loaded retry."""


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
    return "".join(c for c in text if not unicodedata.combining(c)).replace("’", "'").replace("‘", "'").replace("'", "")


def _integer(words: list[str], start: int, raw: list[Token], text: str) -> tuple[int, int] | None:
    """Conservative English cardinal parser; adjacent small numbers stay distinct."""
    total = current = 0
    i, last, found = start, "", False
    while i < len(words):
        if i > start and re.search(r"[.,!?;:]", text[raw[i - 1].end:raw[i].start]):
            break
        w = words[i]
        if w in SMALL:
            if last in {"small", "unit"} or (last == "tens" and SMALL[w] >= 10):
                break
            current += SMALL[w]
            last = "unit" if last == "tens" else "small"
        elif w in TENS:
            if last in {"small", "unit", "tens"}:
                break
            current += TENS[w]
            last = "tens"
        elif w == "hundred":
            if not found or last not in {"small", "unit"} or current >= 100:
                break
            current *= 100
            last = "hundred"
        elif w in SCALES:
            if not found or current == 0:
                break
            total += current * SCALES[w]
            current, last = 0, "scale"
        elif w == "and":
            if last not in {"hundred", "scale"} or i + 1 >= len(words) or words[i + 1] not in SMALL | TENS:
                break
            i += 1
            continue
        else:
            break
        found = True
        i += 1
    return (total + current, i) if found else None


def tokens(text: str) -> list[Token]:
    raw = [Token(fold(m.group()), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
    values = [t.value for t in raw]
    result: list[Token] = []
    i = 0
    while i < len(raw):
        token, w = raw[i], values[i]
        # Exact fictional-brand spelling aliases only, including possessives.
        # Do not join across punctuation or accept phonetic substitutes like Talk.
        if w == "nova" and i + 1 < len(raw) and values[i + 1] in {"mind", "minds", "tok", "toks"} and re.fullmatch(r"[\s\-‐‑–]+", text[token.end:raw[i + 1].start]):
            result.append(Token("nova" + values[i + 1], token.start, raw[i + 1].end))
            i += 2
            continue
        negative_prefix = False
        if w in {"minus", "negative", "plus"} and i + 1 < len(raw) and (values[i + 1] in SMALL | TENS or values[i + 1].isdigit()):
            negative_prefix = w != "plus"
            i += 1
            w = values[i]
            token = Token(w, token.start, raw[i].end)
        # Spaced/dotted initialisms commonly produced by ASR, with original spans.
        joined = None
        for abbreviation in ("c e t", "e s t", "p s t", "w e t", "e e t", "a i", "u s", "u k", "f b i", "c e o", "p h d", "a m", "p m"):
            parts = abbreviation.split()
            if values[i:i + len(parts)] == parts:
                joined = ("".join(parts), i + len(parts))
                break
        if joined:
            result.append(Token(joined[0], token.start, raw[joined[1] - 1].end))
            i = joined[1]
            continue
        if w == "%":
            result.append(Token("percent", token.start, token.end))
            i += 1
            continue
        if re.fullmatch(r"[+-]?\d[\d,]*(?:\.\d+)?(?:st|nd|rd|th)?", w):
            ordinal = re.search(r"(st|nd|rd|th)$", w)
            number = re.sub(r"(st|nd|rd|th)$", "", w).replace(",", "")
            whole, dot, decimal = number.partition(".")
            value = str(int(whole)) + ("." + decimal.rstrip("0") if dot and decimal.rstrip("0") else "")
            if (w.startswith("-") or negative_prefix) and not value.startswith("-"):
                value = "-" + value
            result.append(Token(("ord:" if ordinal else "num:") + value, token.start, token.end))
            i += 1
            continue
        if w in ORDINALS:
            result.append(Token("ord:" + str(ORDINALS[w]), token.start, token.end))
            i += 1
            continue
        parsed = _integer(values, i, raw, text)
        if parsed:
            number, end = parsed
            # Familiar century/year readings: nineteen ninety-nine, twenty nineteen.
            if number in {19, 20} and end == i + 1 and end < len(raw) and not re.search(r"[.,!?;:]", text[raw[end - 1].end:raw[end].start]):
                following = _integer(values, end, raw, text)
                if following and 10 <= following[0] < 100 and following[1] <= end + 2:
                    number, end = number * 100 + following[0], following[1]
            ordinal_at = end + 1 if end < len(raw) and values[end] == "and" else end
            if number >= 20 and number % 10 == 0 and ordinal_at < len(raw) and values[ordinal_at] in ORDINALS and not re.search(r"[.,!?;:]", text[raw[end - 1].end:raw[ordinal_at].start]):
                result.append(Token("ord:" + str(number + ORDINALS[values[ordinal_at]]), token.start, raw[ordinal_at].end))
                i = ordinal_at + 1
                continue
            decimal = []
            if end < len(raw) and values[end] == "point" and not re.search(r"[.,!?;:]", text[raw[end - 1].end:raw[end].start]):
                j = end + 1
                while j < len(raw) and (values[j] in SMALL and SMALL[values[j]] < 10 or values[j].isdigit() or values[j] == "oh"):
                    if re.search(r"[.,!?;:]", text[raw[j - 1].end:raw[j].start]):
                        break
                    decimal.append(str(SMALL[values[j]]) if values[j] in SMALL else "0" if values[j] == "oh" else values[j])
                    j += 1
                if decimal:
                    end = j
            value = str(number) + ("." + "".join(decimal).rstrip("0") if decimal and "".join(decimal).rstrip("0") else "")
            if negative_prefix:
                value = "-" + value
            result.append(Token("num:" + value, token.start, raw[end - 1].end))
            i = end
            continue
        result.append(token)
        i += 1
    return result


def normalize(text: str) -> list[str]:
    return [t.value for t in tokens(text)]


def sentence_spans(text: str, ts: list[Token]) -> list[tuple[int, int]]:
    boundaries = [0]
    for match in re.finditer(r"[.!?]+[\"'”’]*\s+|[.!?]+[\"'”’]*$", text):
        prefix = text[:match.start()]
        word = re.search(r"([\w.]+)$", prefix)
        if word and (word.group(1).casefold() in ABBREVIATIONS or re.fullmatch(r"[A-Z]", word.group(1))):
            continue
        boundaries.append(match.end())
    if boundaries[-1] != len(text):
        boundaries.append(len(text))
    starts = [t.start for t in ts]
    return [(bisect_left(starts, a), bisect_left(starts, b)) for a, b in zip(boundaries, boundaries[1:]) if b > a]


def _exact_span(text: str, ts: list[Token], start: int, end: int) -> str:
    return text[ts[start].start:ts[end - 1].end] if start < end else ""


def compare(expected: str, heard: str) -> dict:
    source, actual = tokens(expected), tokens(heard)
    left, right = [t.value for t in source], [t.value for t in actual]
    opcodes = difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes()
    differences, reasons = [], []
    missing, added = set(), set()
    changed = 0
    for tag, a, b, c, d in opcodes:
        if tag == "equal":
            continue
        size = max(b - a, d - c)
        changed += size
        missing.update(range(a, b))
        added.update(range(c, d))
        difference = {
            "type": tag, "expected_word_start": a, "expected_word_end": b,
            "heard_word_start": c, "heard_word_end": d,
            "expected": _exact_span(expected, source, a, b),
            "heard": _exact_span(heard, actual, c, d), "changed_words": size,
            "review_candidate": True, "interpretation": "ASR mismatch; listen before deciding whether narration is wrong",
            "reasons": [],
        }
        if b - a >= 8:
            difference["reasons"].append("sentence_sized_deletion_or_replacement")
        if d - c >= 8:
            difference["reasons"].append("sentence_sized_insertion_or_replacement")
        # Cue detection is confined to unmatched additions, so quoted story cues
        # already present in the exact input do not become director-note alarms.
        cue_text = " ".join(fold(difference["heard"]).replace("-", " ").split())
        if any(re.search(pattern, cue_text) for pattern in DIRECTOR_PATTERNS):
            difference["reasons"].append("possible_spoken_markup_or_director_note")
        if b == len(source) and b - a >= 1 and tag == "delete":
            difference["reasons"].append("possible_tail_truncation")
        differences.append(difference)
    for label, spans, changed_indices, start_field, end_field in (
        ("possible_sentence_deletion", sentence_spans(expected, source), missing, "expected_word_start", "expected_word_end"),
        ("possible_sentence_insertion", sentence_spans(heard, actual), added, "heard_word_start", "heard_word_end"),
    ):
        for a, b in spans:
            if b - a >= 1 and sum(i in changed_indices for i in range(a, b)) / (b - a) >= .8:
                for difference in differences:
                    if difference[start_field] < b and difference[end_field] > a:
                        difference["reasons"].append(label)
    fraction = changed / max(1, len(source))
    if fraction > THRESHOLD:
        reasons.append("worddiff_above_3_5_percent")
    if source and not actual:
        reasons.append("empty_asr_transcript")
    if not source:
        reasons.append("empty_expected_input")
    for difference in differences:
        difference["reasons"] = sorted(set(difference["reasons"]))
        reasons.extend(difference["reasons"])
    tail_size = min(25, len(source))
    tail_similarity = difflib.SequenceMatcher(None, left[-tail_size:] if tail_size else [], right[-tail_size:] if tail_size else [], autojunk=False).ratio()
    return {
        "comparison_version": COMPARISON_VERSION,
        "source_words_normalized": len(source), "transcribed_words_normalized": len(actual),
        "changed_words": changed, "changed_word_fraction": fraction,
        "metric": "SequenceMatcher aligned blocks; a replacement counts max(expected words, heard words), not validated WER",
        "threshold": THRESHOLD, "tail_similarity": tail_similarity,
        "differences": differences, "review_reasons": sorted(set(reasons)),
        "needs_review": bool(reasons) and bool(source or actual),
        "verdict": "review_candidate" if reasons and (source or actual) else "no_flagged_discrepancy",
        "confirmed_narration_error": False,
    }


def add_time_anchors(comparison: dict, transcript: str, segments: list[dict], duration: float) -> None:
    parts, spans, cursor = [], [], 0
    for segment in segments:
        entries = segment.get("words") or [segment]
        for entry in entries:
            text = str(entry.get("word", entry.get("text", "")))
            parts.append(text + " ")
            spans.append((cursor, cursor + len(text), float(entry.get("start", segment.get("start", 0))), float(entry.get("end", segment.get("end", duration)))))
            cursor += len(text) + 1
    anchored = tokens("".join(parts))
    timestamps = []
    span_starts = [s[0] for s in spans]
    for token in anchored:
        lo = max(0, bisect_right(span_starts, token.start) - 1)
        hi = max(lo, bisect_left(span_starts, token.end) - 1)
        timestamps.append((spans[lo][2], spans[hi][3]))
    heard = normalize(transcript)
    mapped: dict[int, tuple[float, float]] = {}
    for tag, a, b, c, d in difflib.SequenceMatcher(None, heard, [t.value for t in anchored], autojunk=False).get_opcodes():
        if tag == "equal":
            for i in range(b - a):
                mapped[a + i] = timestamps[c + i]
    for difference in comparison["differences"]:
        a, b = difference["heard_word_start"], difference["heard_word_end"]
        matched = [mapped[i] for i in range(a, b) if i in mapped]
        if matched:
            start, end = min(t[0] for t in matched), max(t[1] for t in matched)
            precision = "word_timestamps" if any(s.get("words") for s in segments) else "segment_timestamps"
        else:
            before = [i for i in mapped if i < a]
            after = [i for i in mapped if i >= b]
            start = mapped[max(before)][1] if before else 0.0
            end = mapped[min(after)][0] if after else duration
            precision = "neighboring_asr_anchor"
        if end < start:
            start, end = end, start
        difference["audio_anchor"] = {
            "start_seconds": start, "end_seconds": end, "precision": precision,
            "suggested_clip_start_seconds": max(0.0, start - 2),
            "suggested_clip_end_seconds": min(duration, end + 2),
        }
        related = [s for s in segments if float(s.get("end", 0)) >= start and float(s.get("start", duration)) <= end]
        difference["asr_segment_quality"] = [{k: s[k] for k in ("start", "end", "avg_logprob", "no_speech_prob", "compression_ratio") if k in s} for s in related]


def sanitize_backend_confidence(value, warnings=None, path="result", field=""):
    """Retain text/timing; explicitly null non-finite optional confidence values."""
    if warnings is None:
        warnings = []
    if isinstance(value, float) and not math.isfinite(value):
        if field not in {"probability", "avg_logprob", "no_speech_prob", "compression_ratio"}:
            raise ValueError("Non-finite required backend value: " + path)
        warnings.append(path)
        return None, warnings
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            cleaned[key], warnings = sanitize_backend_confidence(child, warnings, path + "." + key, key)
        return cleaned, warnings
    if isinstance(value, list):
        cleaned = []
        for index, child in enumerate(value):
            result, warnings = sanitize_backend_confidence(child, warnings, path + f"[{index}]", field)
            cleaned.append(result)
        return cleaned, warnings
    return value, warnings


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    os.replace(temporary, path)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(path: Path) -> str:
    before = path.stat()
    digest = file_hash(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("File changed while hashing: " + str(path))
    return digest


def model_identity(model: Path) -> dict:
    if not model.is_absolute() or not model.is_dir():
        raise ValueError("--model must be an existing absolute local model directory; repository IDs are refused")
    weights = model / "weights.safetensors"
    if not weights.exists():
        weights = model / "weights.npz"
    required = [model / "config.json", weights]
    if not all(p.is_file() and p.stat().st_size > 0 for p in required):
        raise ValueError("Local model needs nonempty config.json and weights.npz or weights.safetensors")
    return {"path": str(model.resolve()), "files_sha256": {p.name: stable_hash(p) for p in required},
            "mlx_whisper_version": importlib.metadata.version("mlx-whisper")}


def input_text(item: dict) -> str:
    value = item.get("inputtext", item.get("text"))
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Manifest item has no exact nonempty inputtext/text")
    return value


def prepare(item: dict, identity: dict) -> tuple[Path, str, dict, float]:
    wav = Path(item["output"])
    if not wav.is_absolute():
        wav = JOB / wav
    # Receipt is written after generation; its absence prevents partial-WAV reads.
    receipt_path = wav.with_suffix(".json")
    if not wav.exists() or not receipt_path.exists():
        raise FileNotFoundError("Waiting for WAV and matching generation receipt")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("request_sha256") != item["request_sha256"]:
        raise ValueError("Generation receipt belongs to a different request")
    audio_hash = stable_hash(wav)
    if receipt.get("audio_sha256") and receipt["audio_sha256"] != audio_hash:
        raise ValueError("WAV bytes do not match generation receipt audio_sha256")
    with wave.open(str(wav), "rb") as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
        if not frames or not rate:
            raise ValueError("Empty WAV")
        duration = frames / rate
    settings = {"language": "en", "temperature": 0.0, "condition_on_previous_text": False,
                "verbose": None, "word_timestamps": True, "initial_prompt": VOCABULARY}
    cache_identity = {"audio_sha256": audio_hash, "request_sha256": item["request_sha256"],
                      "inputtext_sha256": hashlib.sha256(input_text(item).encode()).hexdigest(),
                      "model_identity": identity, "decode_settings": settings, "checker_version": VERSION}
    key = hashlib.sha256(json.dumps(cache_identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return wav, key, cache_identity, duration


def one(item: dict, identity: dict, prepared: tuple, checks: Path) -> dict:
    wav, key, cache_identity, duration = prepared
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(item["id"]))
    output = checks / "cache" / f"{safe_id}.{key}.json"
    if output.exists():
        saved = json.loads(output.read_text(encoding="utf-8"))
        if saved.get("cache_key") == key and saved.get("cache_identity") == cache_identity:
            status = "cached"
            if saved.get("comparison_version") != COMPARISON_VERSION:
                refresh_record(saved, input_text(item))
                atomic_json(output, saved)
                status = "comparison_refreshed"
            return {"id": item["id"], "status": status, "cache_key": key, "output": str(output),
                    "needs_review": saved["comparison"]["needs_review"], "changed_word_fraction": saved["comparison"]["changed_word_fraction"]}
        raise ValueError("QA cache identity mismatch: " + str(output))
    # Never let MLX's fallback model loader resolve an HF repository or contact it.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        import mlx_whisper
    except Exception as exc:
        raise BackendImportError("MLX Whisper backend import failed; stopping without reimporting: " + str(exc)) from exc
    settings = cache_identity["decode_settings"]
    result = mlx_whisper.transcribe(str(wav), path_or_hf_repo=identity["path"], **settings)
    result, backend_warnings = sanitize_backend_confidence(result)
    if stable_hash(wav) != cache_identity["audio_sha256"]:
        raise RuntimeError("WAV changed during local transcription; result was not cached")
    transcript = str(result.get("text", ""))
    segments = result.get("segments", [])
    comparison = compare(input_text(item), transcript)
    add_time_anchors(comparison, transcript, segments, duration)
    saved = {"id": item["id"], "chapter": item.get("chapter"), "chunk": item.get("chunk"),
             "cache_key": key, "cache_identity": cache_identity, "wav": str(wav),
             "duration_seconds": duration, "expected_inputtext": input_text(item),
             "transcript": transcript, "segments": segments, "language": result.get("language"),
             "comparison": comparison, "created_at_unix": time.time(),
             "backend_nonfinite_confidence_fields": backend_warnings}
    saved["comparison_version"] = COMPARISON_VERSION
    atomic_json(output, saved)
    return {"id": item["id"], "status": "checked", "cache_key": key, "output": str(output),
            "needs_review": comparison["needs_review"], "changed_word_fraction": comparison["changed_word_fraction"]}


def refresh_record(saved: dict, expected: str) -> None:
    """Recompare cached text and timestamps without opening audio or the model."""
    expected_hash = hashlib.sha256(expected.encode()).hexdigest()
    if saved["cache_identity"]["inputtext_sha256"] != expected_hash:
        raise ValueError("Exact inputtext changed; this transcript does not belong to the current request")
    comparison = compare(expected, saved["transcript"])
    add_time_anchors(comparison, saved["transcript"], saved.get("segments", []), saved["duration_seconds"])
    saved["expected_inputtext"] = expected
    saved["comparison"] = comparison
    saved["comparison_version"] = COMPARISON_VERSION
    saved["comparison_updated_at_unix"] = time.time()


def refresh_comparisons(checks: Path, pilot: bool = False) -> int:
    """Refresh only current-request transcript caches; never access WAV/model bytes."""
    manifest = json.loads((JOB / "generation-manifest.json").read_text(encoding="utf-8"))
    selected = manifest["items"]
    if pilot:
        selected = [selected[0], next(i for i in selected if i["chapter"] == 1)]
    items = {i["id"]: i for i in selected}
    outcomes = []
    for output in sorted((checks / "cache").glob("*.json")):
        saved = json.loads(output.read_text(encoding="utf-8"))
        item = items.get(saved.get("id"))
        if item is None or saved["cache_identity"].get("request_sha256") != item["request_sha256"]:
            continue
        try:
            refresh_record(saved, input_text(item))
            atomic_json(output, saved)
            outcome = {"id": saved["id"], "cache_key": saved["cache_key"], "status": "comparison_refreshed",
                       "output": str(output), "needs_review": saved["comparison"]["needs_review"],
                       "changed_word_fraction": saved["comparison"]["changed_word_fraction"]}
        except ValueError as exc:
            outcome = {"id": saved["id"], "cache_key": saved["cache_key"], "status": "comparison_error", "error": str(exc)}
        outcomes.append(outcome)
        print(json.dumps(outcome), flush=True)
    summary = {"comparison_version": COMPARISON_VERSION, "updated_at_unix": time.time(),
               "refreshed": sum(o["status"] == "comparison_refreshed" for o in outcomes),
               "errors": sum(o["status"] == "comparison_error" for o in outcomes),
               "review_ids": sorted({o["id"] for o in outcomes if o.get("needs_review")}),
               "outcomes": outcomes, "audio_accessed": False, "inference_performed": False,
               "interpretation": "ASR differences require local listening; flags are not confirmed narration errors"}
    atomic_json(checks / "comparison-summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("refreshed", "errors", "review_ids")}), flush=True)
    return 1 if summary["errors"] else 0


def selftest() -> None:
    equivalent = [
        ("José’s well-being—improved.", "Joses well being improved"),
        ("21, 1,000, and 3.5.", "twenty-one, one thousand, and three point five"),
        ("In 2019 he saw 17% at 12:17 a.m.", "In twenty nineteen he saw seventeen percent at twelve seventeen a m"),
        ("One hundred and twenty-one.", "121"),
        ("Ólafur’s 2nd test.", "Olafurs second test"),
        ("The 21st and 101st attempts.", "The twenty-first and one hundred and first attempts"),
        ("Minus five degrees.", "-5 degrees"),
        ("He said, ‘Cut!’ Then he paused.", "He said Cut Then he paused"),
        ("NovaMind. NovaMind’s records reached NovaTok’s platform.", "Nova Mind. Nova Mind’s records reached Nova Tok’s platform."),
    ]
    for a, b in equivalent:
        assert normalize(a) == normalize(b), (a, b, normalize(a), normalize(b))
        assert not compare(a, b)["needs_review"]
    for a, b in (("15", "fifty"), ("0.5", "five"), ("cannot", "can"), ("-5", "5"), ("Oh, he left.", "Zero, he left."), ("One point five. Three.", "1.53")):
        assert normalize(a) != normalize(b)
    assert normalize("NovaTok") != normalize("Nova Talk")
    assert normalize("NovaMind") != normalize("Nova. Mind.")
    assert normalize("NovaMind's") != normalize("Nova Mind")
    brand_text = "Before Nova Mind’s arrival."
    brand_token = tokens(brand_text)[1]
    assert brand_token.value == "novaminds"
    assert brand_text[brand_token.start:brand_token.end] == "Nova Mind’s"
    expected_brand = "NovaMind."
    saved_brand = {"cache_identity": {"inputtext_sha256": hashlib.sha256(expected_brand.encode()).hexdigest()},
                   "transcript": "Nova Mind.", "segments": [], "duration_seconds": 1.0}
    refresh_record(saved_brand, expected_brand)
    assert saved_brand["comparison_version"] == COMPARISON_VERSION
    assert not saved_brand["comparison"]["needs_review"]
    background = " ".join(f"ordinary{i} word" for i in range(180)) + ". "
    lost = compare(background + "She closed the door. " + background, background + background)
    assert lost["changed_word_fraction"] < THRESHOLD
    assert "possible_sentence_deletion" in lost["review_reasons"]
    inserted = compare(background + background, background + "She closed the door. " + background)
    assert inserted["changed_word_fraction"] < THRESHOLD
    assert "possible_sentence_insertion" in inserted["review_reasons"]
    tail = compare(background + "He would never return.", background)
    assert "possible_tail_truncation" in tail["review_reasons"]
    assert "possible_sentence_deletion" in compare(background + "He left. " + background, background + background)["review_reasons"]
    assert "possible_tail_truncation" in compare(background + "He finally escaped.", background + "He finally.")["review_reasons"]
    for addition in ("Pause two seconds.", "Open bracket whisper close bracket.", "Preserve the wording."):
        assert "possible_spoken_markup_or_director_note" in compare("The door opened.", "The door opened. " + addition)["review_reasons"]
    repeated = compare("She waited. He nodded. She waited. He left.", "She waited. He nodded. He left.")
    assert repeated["changed_words"] == 2
    assert "possible_tail_truncation" not in repeated["review_reasons"]
    assert len(sentence_spans("Dr. Rivera arrived at 3.5 p.m. Then she left.", tokens("Dr. Rivera arrived at 3.5 p.m. Then she left."))) <= 2
    source = " ".join(f"word{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(200))
    actual = source.split()
    for i in range(0, 140, 20):
        actual[i] = "different" + actual[i]
    assert compare(source, " ".join(actual))["changed_word_fraction"] == THRESHOLD
    assert "worddiff_above_3_5_percent" not in compare(source, " ".join(actual))["review_reasons"]
    actual[160] = "different" + actual[160]
    assert "worddiff_above_3_5_percent" in compare(source, " ".join(actual))["review_reasons"]
    assert compare("Expected narration.", "")["needs_review"]
    assert not compare("", "")["needs_review"]
    for report in (lost, inserted, tail, repeated):
        assert report["confirmed_narration_error"] is False
    anchored = compare("The door opened. He was gone.", "The door opened.")
    add_time_anchors(anchored, "The door opened.", [{"start": 0, "end": 2, "text": "The door opened.", "words": [{"word": "The", "start": 0, "end": .4}, {"word": "door", "start": .4, "end": 1}, {"word": "opened.", "start": 1, "end": 2}]}], 4)
    assert anchored["differences"][-1]["audio_anchor"]["end_seconds"] == 4
    print("Comparison selftest passed; no audio, MLX import, model loading, or inference performed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--pilot", action="store_true", help="opening credits plus first Chapter 1 chunk")
    modes.add_argument("--all", action="store_true", help="check all available matching WAVs")
    parser.add_argument("--watch", action="store_true", help="process arrivals immediately; stop when all selected items have an outcome")
    parser.add_argument("--model", type=Path, default=JOB / "asr-model", help="existing absolute local MLX Whisper model directory")
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--selftest", action="store_true", help="comparison-only tests; no model or audio access")
    parser.add_argument("--refresh-comparisons", action="store_true", help="recompare saved transcripts/timestamps only; no WAV/model access or inference")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not (args.pilot or args.all or args.watch or args.refresh_comparisons):
        parser.error("choose --pilot, --all, --watch, or --refresh-comparisons")
    if args.refresh_comparisons and args.watch:
        parser.error("--refresh-comparisons cannot be combined with --watch")
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    checks = JOB / "local-checks"
    checks.mkdir(parents=True, exist_ok=True)
    with (checks / ".worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("a local QA worker is already running; only one GPU worker is allowed")
        if args.refresh_comparisons:
            return refresh_comparisons(checks, args.pilot)
        identity = model_identity(args.model)
        known, failed = {}, set()
        while True:
            manifest = json.loads((JOB / "generation-manifest.json").read_text(encoding="utf-8"))
            items = manifest["items"]
            if args.pilot:
                items = [items[0], next(i for i in items if i["chapter"] == 1)]
            outcomes, pending = [], 0
            for item in items:
                try:
                    wav = Path(item["output"])
                    if not wav.is_absolute():
                        wav = JOB / wav
                    receipt = wav.with_suffix(".json")
                    signature = (item["request_sha256"], hashlib.sha256(input_text(item).encode()).hexdigest(),
                                 wav.stat().st_size, wav.stat().st_mtime_ns,
                                 receipt.stat().st_size, receipt.stat().st_mtime_ns)
                    if item["id"] in known and known[item["id"]][0] == signature:
                        outcomes.append(known[item["id"]][1])
                        continue
                    prepared = prepare(item, identity)
                    if prepared[1] in failed:
                        outcomes.append({"id": item["id"], "status": "error", "cache_key": prepared[1]})
                        continue
                    try:
                        outcome = one(item, identity, prepared, checks)
                    except Exception as exc:
                        failed.add(prepared[1])
                        outcome = {"id": item["id"], "status": "error", "cache_key": prepared[1], "error": str(exc)}
                        atomic_json(checks / "errors" / f"{item['id']}.{prepared[1]}.json",
                                    {**outcome, "cache_identity": prepared[2], "created_at_unix": time.time()})
                        if isinstance(exc, BackendImportError):
                            atomic_json(checks / "summary.json", {"checker_version": VERSION, "comparison_version": COMPARISON_VERSION,
                                        "model_identity": identity, "status": "failed_backend_import", "errors": 1,
                                        "outcomes": outcomes + [outcome], "updated_at_unix": time.time()})
                            print(json.dumps(outcome), flush=True)
                            return 1
                    known[item["id"]] = (signature, outcome)
                    outcomes.append(outcome)
                    print(json.dumps(outcome), flush=True)
                except (FileNotFoundError, ValueError, json.JSONDecodeError, wave.Error, EOFError, RuntimeError) as exc:
                    pending += 1
                    outcomes.append({"id": item["id"], "status": "pending_audio", "reason": str(exc)})
            summary = {"checker_version": VERSION, "comparison_version": COMPARISON_VERSION, "model_identity": identity, "updated_at_unix": time.time(),
                       "selected": len(items), "pending_audio": pending,
                       "checked": sum(o["status"] in {"cached", "checked", "comparison_refreshed"} for o in outcomes),
                       "errors": sum(o["status"] == "error" for o in outcomes),
                       "review_ids": [o["id"] for o in outcomes if o.get("needs_review")],
                       "outcomes": outcomes, "confirmed_narration_errors": 0,
                       "interpretation": "ASR differences require local listening; flags are not confirmed narration errors"}
            atomic_json(checks / "summary.json", summary)
            print(json.dumps({k: summary[k] for k in ("selected", "pending_audio", "checked", "errors", "review_ids")}), flush=True)
            if not args.watch or pending == 0:
                return 1 if summary["errors"] else 0
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
