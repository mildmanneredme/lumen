#!/usr/bin/env python3
"""Build a source-bound LOCAL excerpt QA queue from saved ASR, without audio I/O.

No model, network, key, decoder, or audio APIs are used. Expected excerpt text is
reference material only; the decoder prompt is exclusively cast vocabulary.
Adjudications must identify the same audio SHA and request SHA as the candidate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import time

import check_audio_local as lexical  # Pure-text functions; MLX is imported lazily elsewhere.

VERSION = "local-recheck-queue-1"
JOB = Path(__file__).resolve().parent
SILENT_CONTROLS = ("<shortpause>", "<short pause>")
DIAGNOSTIC_MAX_SECONDS = 120
RESOLVED = {"resolved_asr_artifact", "confirmed_correct", "accepted_acoustic_equivalent", "accepted_transcript_representation", "repaired"}
NEGATION = {"not", "no", "never", "neither", "nor", "none", "cannot", "cant", "wont", "dont", "didnt", "doesnt", "isnt", "wasnt", "werent", "wouldnt", "couldnt", "shouldnt", "mustnt", "havent", "hasnt", "hadnt"}
MODAL = {"can", "could", "may", "might", "must", "shall", "should", "will", "would"}
SPELLING_PAIRS = {
    ("moulding", "molding"), ("behaviour", "behavior"), ("behavioural", "behavioral"),
    ("practising", "practicing"), ("practised", "practiced"),
    ("recognise", "recognize"), ("recognised", "recognized"), ("recognising", "recognizing"),
    ("realise", "realize"), ("realised", "realized"), ("realising", "realizing"),
    ("organise", "organize"), ("organised", "organized"), ("organising", "organizing"),
    ("optimise", "optimize"), ("optimised", "optimized"), ("optimising", "optimizing"),
    ("colour", "color"), ("colours", "colors"), ("centre", "center"), ("centres", "centers"),
    ("catalogue", "catalog"), ("cataloguing", "cataloging"), ("grey", "gray"),
    ("favour", "favor"), ("favourite", "favorite"), ("neighbour", "neighbor"),
    ("sceptical", "skeptical"), ("travelled", "traveled"), ("travelling", "traveling"),
    ("labelled", "labeled"), ("labelling", "labeling"),
    ("pre-trial", "pretrial"), ("counterargument", "counter-argument"),
    ("thinkpad", "think pad"), ("timeframe", "time frame"),
    ("meagre", "meager"), ("nonprofit", "non-profit"),
    ("firsthand", "first-hand"), ("inoffensiveness", "inoffensive-ness"),
    ("microexpressions", "micro-expressions"), ("microexpression", "micro expression"),
    ("boardrooms", "board rooms"), ("dev-ops", "devops"),
    ("cyberterrorism", "cyber-terrorism"), ("toolset", "tool set"),
    ("workmanlike", "workman-like"), ("safehouse", "safe house"), ("d.c", "dc"),
    ("backup", "back-up"), ("lock picks", "lockpicks"), ("sightlines", "sight lines"),
    ("keycard", "key card"), ("brainstem", "brain stem"),
    ("un-ring", "unring"), ("inter-company", "intercompany"),
    ("porthole", "port hole"), ("hardcopy", "hard copy"), ("printout", "print out"),
    ("loopback", "loop back"), ("corkboard", "cork board"), ("chokepoints", "choke points"),
    ("pre-loaded", "preloaded"), ("backend", "back end"), ("setup", "set up"),
    ("land mine", "landmine"), ("codenames", "code names"),
}
SPELLING_PAIRS |= {(b, a) for a, b in tuple(SPELLING_PAIRS)}
HOMOPHONES = {("tied", "tide"), ("male", "mail"), ("aisle", "isle"), ("patience", "patients")}
HOMOPHONES |= {(b, a) for a, b in tuple(HOMOPHONES)}
NAMES = {lexical.fold(t) for t in re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)*", lexical.VOCABULARY)} | {"priti", "chen", "bryant", "alpaydin", "novaminds", "novatoks"}
DECADES = dict(zip("twenties thirties forties fifties sixties seventies eighties nineties".split(), range(20, 100, 10)))
LARGE_ORDINALS = dict(zip("thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth thousandth millionth".split(), [30, 40, 50, 60, 70, 80, 90, 100, 1000, 1000000]))


def exact_numeric_representation(text: str) -> tuple[str, str] | None:
    """Strict numeric/decade forms only; no fuzzy or clock value inference."""
    value = text.casefold().strip()
    if value in DECADES:
        return "decade", str(DECADES[value])
    if re.fullmatch(r"(?:20|30|40|50|60|70|80|90)s", value):
        return "decade", value[:-1]
    if value in LARGE_ORDINALS:
        return "ordinal", str(LARGE_ORDINALS[value])
    words = lexical.normalize(value)
    if len(words) == 1 and words[0].startswith("ord:"):
        return "ordinal", words[0][4:]
    if not words or not words[0].startswith("num:"):
        return None
    try:
        number = Decimal(words[0][4:])
        if len(words) == 2 and words[1] in lexical.SCALES:
            number *= lexical.SCALES[words[1]]
        elif len(words) != 1:
            return None
        return "number", str(number.normalize())
    except InvalidOperation:
        return None


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        name = stream.name
    Path(name).replace(path)


def comparison_text(text: str) -> str:
    """Mask only the explicit silent control, retaining source coordinates."""
    for control in SILENT_CONTROLS:
        text = text.replace(control, " " * len(control))
    return text


def char_span(text: str, words: list, start: int, end: int) -> tuple[int, int]:
    if start < end:
        return words[start].start, words[end - 1].end
    point = words[start].start if start < len(words) else len(text)
    return point, point


def classify(diff: dict, source: str, source_span: tuple[int, int]) -> tuple[str, str, str, bool]:
    a, b = diff["expected"].casefold(), diff["heard"].casefold()
    left, right = lexical.normalize(a), lexical.normalize(b)
    around = source[max(0, source_span[0] - 1):source_span[1] + 1]
    if a in {"shortpause", "short pause"} and not b and around in SILENT_CONTROLS:
        return "nonspeech_control_marker", "low", "Exact explicit silent input control is not spoken narration.", False
    if (a, b) in SPELLING_PAIRS:
        return "orthographic_equivalent", "low", "Explicit spelling or compound-spacing pair; no audible wording difference is implied.", False
    if (a, b) in HOMOPHONES:
        return "homophonic_transcript_variant", "low", "Explicit acoustically equivalent pair; transcript spelling alone cannot establish spoken error.", False
    numeric_a, numeric_b = exact_numeric_representation(a), exact_numeric_representation(b)
    if numeric_a is not None and numeric_a == numeric_b:
        return "exact_numeric_representation_equivalent", "low", "Strict word/numeral conversion gives the same number, ordinal, or decade; no changed value is hidden.", False
    # Clock punctuation is kept as a low-priority candidate: a transcript alone
    # cannot prove that a clock time was not voiced as a decimal.
    if ":" in a and a.replace(":", ".") == b:
        return "clock_format_candidate", "low", "Digits match; local recheck can confirm the spoken clock reading.", True
    reasons = set(diff.get("reasons", []))
    if "possible_spoken_markup_or_director_note" in reasons:
        return "spoken_instruction_candidate", "high", "Unmatched ASR addition resembles a direction or control cue.", True
    if "possible_sentence_deletion" in reasons and len(left) > len(right):
        return "whole_utterance_omission_candidate", "high", "A complete source utterance loses words; a neighboring name spelling cannot hide the missing utterance.", True
    if (set(left) & NEGATION) != (set(right) & NEGATION):
        return "negation_candidate", "high", "Negation changes or disappears, even below the clip-wide threshold.", True
    if (set(left) & MODAL) != (set(right) & MODAL):
        return "modality_candidate", "high", "A modal word changes or disappears.", True
    if any(t.startswith(("num:", "ord:")) for t in left + right):
        return "numeric_value_candidate", "high", "Unmatched numeric value needs verification; no fuzzy numeric alias is applied.", True
    if max(len(left), len(right)) >= 6 or "possible_tail_truncation" in reasons:
        return "passage_omission_or_addition_candidate", "high", "Multiword change or missing tail needs focused verification.", True
    if left and all(t in NAMES or t.endswith("s") and t[:-1] in NAMES for t in left) and right:
        return "proper_name_pronunciation_candidate", "low", "Nonexact name rendering remains a pronunciation candidate; no phonetic alias is auto-accepted.", True
    if diff["type"] in {"insert", "delete"}:
        return "word_addition_or_omission_candidate", "medium", "Added or omitted word is retained regardless of aggregate mismatch rate.", True
    return "wording_candidate", "medium", "Non-equivalent wording mismatch needs focused verification.", True


def mapped_times(record: dict) -> dict[int, tuple[float, float]]:
    """Map normalized transcript words to their existing ASR timestamps."""
    pieces, spans, cursor = [], [], 0
    for segment in record.get("segments", []):
        for entry in segment.get("words") or [segment]:
            text = str(entry.get("word", entry.get("text", "")))
            pieces.append(text + " ")
            spans.append((cursor, cursor + len(text), float(entry.get("start", segment.get("start", 0))), float(entry.get("end", segment.get("end", 0)))))
            cursor += len(text) + 1
    if not spans:
        return {}
    joined = lexical.tokens("".join(pieces))
    times = []
    j = 0
    for token in joined:
        while j + 1 < len(spans) and spans[j][1] < token.start:
            j += 1
        k = j
        while k + 1 < len(spans) and spans[k][1] < token.end:
            k += 1
        times.append((spans[j][2], spans[k][3]))
    result = {}
    heard = lexical.normalize(record.get("transcript", ""))
    for tag, a, b, c, d in difflib.SequenceMatcher(None, heard, [t.value for t in joined], autojunk=False).get_opcodes():
        if tag == "equal":
            for i in range(b - a):
                result[a + i] = times[c + i]
    return result


def source_times(record: dict, times: dict) -> dict[int, tuple[float, float]]:
    result = {}
    left = lexical.normalize(comparison_text(record["expected_inputtext"]))
    right = lexical.normalize(record.get("transcript", ""))
    for tag, a, b, c, d in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if tag == "equal":
            for i in range(b - a):
                if c + i in times:
                    result[a + i] = times[c + i]
    return result


def read_adjudications(review: Path) -> dict:
    result = {}
    path = review / "adjudications.json"
    if path.exists():
        data = json.loads(path.read_text())
        rows = data.get("items", []) if isinstance(data, dict) else data
        if isinstance(rows, dict):
            rows = [dict(value, candidate_id=key) for key, value in rows.items()]
        for row in rows:
            if row.get("candidate_id"):
                result[row["candidate_id"]] = row
    return result


def legacy_resolution(record: dict, diff: dict, review: Path) -> dict | None:
    """Import the previous chapter-2 assessment only with its original hashes."""
    if record["id"] != "chapter-002-003" or diff["type"] != "delete" or len(lexical.normalize(diff["expected"])) < 30:
        return None
    initial = review / "initial-11-candidate-review.json"
    assessment = review.parent / "local-checks/review-assessment.json"
    if not initial.exists() or not assessment.exists():
        return None
    for clip in json.loads(initial.read_text()).get("clips", []):
        identity = record["cache_identity"]
        if clip.get("id") != record["id"] or any(clip.get(k) != identity.get(k) for k in ("audio_sha256", "request_sha256")):
            continue
        if any(x.get("classification") == "resolved_asr_artifact" for x in clip.get("differences", [])):
            # The prior assessment covered the first passage only, not every
            # later long deletion that a different decode could report.
            start, end = diff["audio_anchor"]["start_seconds"], diff["audio_anchor"]["end_seconds"]
            if diff["expected_word_start"] > 6 or start > 4 or end > 36:
                continue
            prior = json.loads(assessment.read_text())
            if prior.get("retake_required_for_this_flag") is False and prior.get("normalized_changed_words") == 0:
                return {"status": "resolved_asr_artifact", "audio_sha256": identity["audio_sha256"], "request_sha256": identity["request_sha256"], "evidence": str(assessment), "evidence_sha256": hashlib.sha256(assessment.read_bytes()).hexdigest(), "rationale": prior.get("finding")}
    return None


def make_candidates(record: dict, review: Path, adjudications: dict, previous: dict) -> list[dict]:
    source, heard = record["expected_inputtext"], record.get("transcript", "")
    source_comparison = comparison_text(source)
    sw, hw = lexical.tokens(source_comparison), lexical.tokens(heard)
    # Do not reuse cached indices even if a version string matches. A normalizer
    # repair can change index geometry. Build fresh opcodes and source spans from
    # this exact manifest-bound text and the saved ASR transcript every refresh.
    comparison = lexical.compare(source_comparison, heard)
    lexical.add_time_anchors(comparison, heard, record.get("segments", []), record["duration_seconds"])
    identity = record["cache_identity"]
    result = []
    for diff in comparison["differences"]:
        ss = char_span(source, sw, diff["expected_word_start"], diff["expected_word_end"])
        hs = char_span(heard, hw, diff["heard_word_start"], diff["heard_word_end"])
        binding = {"clip_id": record["id"], "audio_sha256": identity["audio_sha256"], "request_sha256": identity["request_sha256"], "type": diff["type"], "source_char_span": list(ss), "expected": source[ss[0]:ss[1]], "heard": diff["heard"]}
        key = digest(binding)
        category, priority, rationale, queueable = classify(diff, source, ss)
        near_gap = source[max(0, ss[0] - 35):min(len(source), ss[1] + 35)]
        if not diff["expected"] and lexical.normalize(diff["heard"]) in [["shortpause"], ["short", "pause"], ["pause"]] and any(control in near_gap for control in SILENT_CONTROLS):
            category, priority, rationale, queueable = "spoken_control_candidate", "high", "Unmatched ASR words resemble the nearby explicit silent scene-break control.", True
        spoken_focus = source_comparison[ss[0]:ss[1]]
        row = {"candidate_id": key, **binding, "asr_char_span": list(hs), "source_word_span": [diff["expected_word_start"], diff["expected_word_end"]], "asr_word_span": [diff["heard_word_start"], diff["heard_word_end"]], "expected_comparison_text": spoken_focus, "expected_normalized_words": lexical.normalize(spoken_focus), "asr_normalized_words": lexical.normalize(diff["heard"]), "classification": category, "priority": priority, "rationale": rationale, "queueable": queueable, "status": "open" if queueable else "no_audible_difference_indicated", "audio_anchor": diff["audio_anchor"], "first_seen_unix": previous.get(key, {}).get("first_seen_unix", time.time()), "last_seen_unix": time.time(), "confirmed_narration_error": False}
        adjudication = adjudications.get(key) or previous.get(key, {}).get("adjudication") or legacy_resolution(record, diff, review)
        if adjudication and all(adjudication.get(k) == row[k] for k in ("audio_sha256", "request_sha256")):
            row["adjudication"] = adjudication
            row["status"] = adjudication.get("status", "open")
            row["confirmed_narration_error"] = row["status"] == "confirmed_narration_error"
            if row["status"] in RESOLVED:
                row["queueable"] = False
            elif row["confirmed_narration_error"]:
                row["queueable"] = True
                row["priority"] = "high"
        result.append(row)
    return result


def make_excerpts(record: dict, candidates: list[dict], context: float, max_seconds: float) -> list[dict]:
    duration = record["duration_seconds"]
    open_rows = [x for x in candidates if x["queueable"] and x["status"] not in RESOLVED]
    if not open_rows:
        return []
    ranges = []
    for row in open_rows:
        anchor = row["audio_anchor"]
        start, end = max(0.0, anchor["start_seconds"] - context), min(duration, anchor["end_seconds"] + context)
        # A neighboring ASR anchor can collapse a long omission to a point,
        # especially at the file tail. An 80-word missing tail must not become
        # a four-second recheck. Widen only long deletion diagnostics, using a
        # conservative 90-wpm context estimate; this is search coverage, not a
        # claim about where omitted source words were actually voiced.
        focus_words = len(row["expected_normalized_words"])
        if row["type"] == "delete" and focus_words >= 30:
            desired = min(duration, DIAGNOSTIC_MAX_SECONDS, focus_words / 1.5 + 2 * context)
            if end - start < desired:
                if anchor["start_seconds"] <= context:
                    start, end = 0.0, desired
                elif duration - anchor["end_seconds"] <= context:
                    start, end = duration - desired, duration
                else:
                    midpoint = (anchor["start_seconds"] + anchor["end_seconds"]) / 2
                    start = max(0.0, min(duration - desired, midpoint - desired / 2))
                    end = start + desired
        ranges.append([start, end, [row]])
    ranges.sort(key=lambda x: (x[0], x[1]))
    groups = []
    for start, end, rows in ranges:
        if groups and start <= groups[-1][1] + 1 and end - groups[-1][0] <= max_seconds:
            groups[-1][1] = max(groups[-1][1], end)
            groups[-1][2].extend(rows)
        else:
            groups.append([start, end, rows])
    split_groups = []
    for start, end, rows in groups:
        if end - start <= max_seconds:
            split_groups.append((start, end, rows, None))
            continue
        if end - start <= DIAGNOSTIC_MAX_SECONDS:
            split_groups.append((start, end, rows, {"kind": "complete_long_target", "max_seconds": DIAGNOSTIC_MAX_SECONDS}))
            continue
        # Overlap long-anchor fragments; every fragment remains a standalone
        # local utterance recheck. Focus passage is not assumed absent from any
        # individual fragment.
        step = max_seconds - min(4.0, max_seconds / 4)
        parts = max(1, math.ceil(max(0, end - start - max_seconds) / step) + 1)
        for i in range(parts):
            a = start + i * step
            b = min(end, a + max_seconds)
            split_groups.append((a, b, rows, None))
    sw = lexical.tokens(comparison_text(record["expected_inputtext"]))
    heard_times = mapped_times(record)
    st = source_times(record, heard_times)
    excerpts = []
    for start, end, rows, diagnostic in split_groups:
        source_indices = [i for i, (a, b) in st.items() if b >= start and a <= end]
        # Include focus ranges in references. Long passages are separated from
        # time-selected excerpt text to avoid pretending the entire passage fits.
        focus_indices = [i for row in rows if start <= row["audio_anchor"]["start_seconds"] and end >= row["audio_anchor"]["end_seconds"] for i in range(*row["source_word_span"])]
        indices = source_indices + focus_indices
        a, b = (min(indices), max(indices) + 1) if indices else (max(0, min(row["source_word_span"][0] for row in rows) - 4), min(len(sw), max(row["source_word_span"][1] for row in rows) + 4))
        # Unrecognized passage words have no source-to-ASR timestamps. Include
        # the whole focus plus explicit source flanks for post-ASR alignment in
        # every diagnostic/fragment; these words are NEVER a decoder prompt.
        for row in rows:
            first, last = row["source_word_span"]
            a = min(a, max(0, first - 2))
            b = max(b, min(len(sw), last + 2))
        ca, cb = char_span(record["expected_inputtext"], sw, a, b)
        # A tail insertion point is after trailing source punctuation, beyond
        # the final token end. Include that exact gap in its reference so the
        # executor can apply strict true-file-tail proof.
        for row in rows:
            anchor = row["audio_anchor"]
            ca = min(ca, row["source_char_span"][0])
            cb = max(cb, row["source_char_span"][1])
        reference = record["expected_inputtext"][ca:cb]
        spoken = comparison_text(record["expected_inputtext"])[ca:cb]
        ids = sorted({row["candidate_id"] for row in rows})
        binding = {"clip_id": record["id"], "audio_sha256": record["cache_identity"]["audio_sha256"], "request_sha256": record["cache_identity"]["request_sha256"], "start_seconds": round(start, 3), "end_seconds": round(end, 3), "candidate_ids": ids}
        excerpt = {"excerpt_id": digest(binding), **binding, "source_wav": record["wav"], "priority": min((row["priority"] for row in rows), key={"high": 0, "medium": 1, "low": 2}.get), "expected_reference": {"source_char_span": [ca, cb], "source_word_span": [a, b], "excerpt_text": reference, "comparison_text": spoken, "normalized_words": lexical.normalize(spoken), "boundary_policy": "Exact source alignment reference includes focus and flanks and may extend beyond crop. Verify interior target wording; boundary words may be clipped.", "focus_targets": [{"candidate_id": row["candidate_id"], "expected_text": row["expected"], "expected_comparison_text": row["expected_comparison_text"], "asr_text": row["heard"], "expected_normalized_words": row["expected_normalized_words"], "asr_normalized_words": row["asr_normalized_words"], "source_char_span": row["source_char_span"], "source_word_span": row["source_word_span"], "audio_anchor": row["audio_anchor"], "window_contains_target_anchor": start <= row["audio_anchor"]["start_seconds"] and end >= row["audio_anchor"]["end_seconds"]} for row in rows]}, "decoder_prompt": {"initial_prompt": lexical.VOCABULARY, "contains_expected_passage": False, "prohibition": "Never feed expected_reference or candidate wording to the ASR decoder."}, "adjudication_policy": "Complete diagnostics require contiguous focal words and context. Split long targets remain unresolved without separate ordered/temporal evidence; token-union existence is insufficient."}
        if diagnostic:
            excerpt["diagnostic"] = diagnostic
        excerpts.append(excerpt)
    return excerpts


def refresh(job: Path, context: float = 4, max_seconds: float = 30) -> dict:
    review = job / "qa-review"
    ledger_path = review / "candidate-ledger.json"
    previous = json.loads(ledger_path.read_text()).get("candidates", {}) if ledger_path.exists() else {}
    adjudications = read_adjudications(review)
    manifest = json.loads((job / "generation-manifest.json").read_text())
    items = {x["id"]: x for x in manifest["items"]}
    selected, rejected, retired = {}, [], []
    for path in sorted((job / "local-checks/cache").glob("*.json")):
        try:
            record = json.loads(path.read_text())
            if record.get("id") not in items:
                retired.append({"path": str(path), "clip_id": record.get("id"), "reason": "Clip was replaced or removed from the current manifest; kept as audit evidence only."})
                continue
            item = items[record["id"]]
            identity = record["cache_identity"]
            expected = lexical.input_text(item)
            if record.get("expected_inputtext") != expected or identity.get("inputtext_sha256") != hashlib.sha256(expected.encode()).hexdigest():
                raise ValueError("cache text does not match the current exact narration input")
            if identity.get("request_sha256") != item["request_sha256"]:
                raise ValueError("cache request hash is stale")
            output = Path(item["output"])
            if not output.is_absolute():
                output = job / output
            metadata = json.loads(output.with_suffix(".json").read_text())
            if any(metadata.get(k) != identity.get(k) for k in ("audio_sha256", "request_sha256")):
                raise ValueError("cache audio/request differs from current raw metadata")
            record["wav"] = str(output)
            record["selected_cache_path"] = str(path)
            old = selected.get(record["id"])
            if old is None or record.get("created_at_unix", 0) > old.get("created_at_unix", 0):
                selected[record["id"]] = record
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rejected.append({"path": str(path), "reason": str(exc)})
    candidates, excerpts, sources, active = {}, [], [], set()
    for clip_id in sorted(selected):
        record = selected[clip_id]
        rows = make_candidates(record, review, adjudications, previous)
        candidates.update({row["candidate_id"]: row for row in rows})
        active.update(row["candidate_id"] for row in rows)
        excerpts.extend(make_excerpts(record, rows, context, max_seconds))
        sources.append({"clip_id": clip_id, "cache": record["selected_cache_path"], "cache_key": record["cache_key"], "audio_sha256": record["cache_identity"]["audio_sha256"], "request_sha256": record["cache_identity"]["request_sha256"]})
    # Keep prior identities for audit, but never queue stale or absent audio.
    for key, old in previous.items():
        if key not in candidates:
            old = dict(old, active_in_current_sources=False)
            candidates[key] = old
    for key, row in candidates.items():
        row["active_in_current_sources"] = key in active
    excerpts.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["priority"]], x["clip_id"], x["start_seconds"]))
    counts = Counter(row["classification"] for key, row in candidates.items() if key in active)
    statuses = Counter(row["status"] for key, row in candidates.items() if key in active)
    summary = {"bound_clips": len(selected), "manifest_clips": len(items), "pending_cache_clips": len(items) - len(selected), "active_candidates": len(active), "classifications": dict(counts), "statuses": dict(statuses), "queued_excerpts": len(excerpts), "queued_seconds": round(sum(x["end_seconds"] - x["start_seconds"] for x in excerpts), 2), "queue_priorities": dict(Counter(x["priority"] for x in excerpts)), "rejected_caches": len(rejected), "retired_caches": len(retired), "new_candidate_ids": sorted(active - set(previous))}
    header = {"version": VERSION, "updated_at_unix": time.time(), "audio_accessed": False, "inference_performed": False, "network_used": False, "summary": summary}
    atomic_json(ledger_path, {**header, "candidates": candidates, "source_bindings": sources, "rejected_caches": rejected, "retired_caches": retired})
    atomic_json(review / "recheck-queue.json", {**header, "context_seconds": context, "max_excerpt_seconds": max_seconds, "max_complete_diagnostic_seconds": DIAGNOSTIC_MAX_SECONDS, "source_word_span_normalization": "Current lexical.tokens on globally masked explicit silent controls; character spans always refer to original manifest text.", "preflight": "The executor must verify current WAV bytes and request SHA before extraction. This builder reads metadata only.", "gpu_worker_policy": "Root runs one local GPU worker after the complete first pass; this queue builder never runs ASR.", "items": excerpts})
    atomic_json(review / "queue-summary.json", {**header, "sources": sources, "rejected_caches": rejected, "retired_caches": retired})
    return summary


def selftest() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        job = Path(temporary)
        (job / "local-checks/cache").mkdir(parents=True)
        (job / "raw").mkdir()
        (job / "qa-review").mkdir()
        source = "She opened the door. She did not trust the report. She hid the complete record from the inspector. She closed the door."
        heard = "She opened the door. She did trust the report. She closed the door. A stranger whispered behind her."
        item = {"id": "test-001", "text": source, "request_sha256": "request-1", "output": "raw/test-001.wav"}
        identity = {"audio_sha256": "audio-1", "request_sha256": "request-1", "inputtext_sha256": hashlib.sha256(source.encode()).hexdigest()}
        record = {"id": item["id"], "cache_key": "test-key", "cache_identity": identity, "wav": "raw/test-001.wav", "expected_inputtext": source, "transcript": heard, "duration_seconds": 60.0, "segments": [{"start": 1, "end": 58, "text": heard}], "created_at_unix": 1}
        (job / "generation-manifest.json").write_text(json.dumps({"items": [item]}))
        (job / "raw/test-001.json").write_text(json.dumps(identity))
        (job / "local-checks/cache/test-001.json").write_text(json.dumps(record))
        first = refresh(job)
        assert first["bound_clips"] == 1 and first["queued_excerpts"] >= 1
        ledger = json.loads((job / "qa-review/candidate-ledger.json").read_text())["candidates"]
        assert any(x["classification"] == "negation_candidate" for x in ledger.values())
        assert any(x["classification"] in {"passage_omission_or_addition_candidate", "whole_utterance_omission_candidate"} for x in ledger.values())
        assert any(x["type"] == "insert" for x in ledger.values())
        queue = json.loads((job / "qa-review/recheck-queue.json").read_text())["items"]
        assert all(x["end_seconds"] - x["start_seconds"] <= (DIAGNOSTIC_MAX_SECONDS if x.get("diagnostic", {}).get("kind") == "complete_long_target" else 30) + .001 for x in queue)
        assert all(x["decoder_prompt"]["initial_prompt"] == lexical.VOCABULARY and not x["decoder_prompt"]["contains_expected_passage"] for x in queue)
        key = next(k for k, row in ledger.items() if row["classification"] == "negation_candidate")
        adjudication = {"candidate_id": key, "audio_sha256": "audio-1", "request_sha256": "request-1", "status": "resolved_asr_artifact", "evidence": "mock focused excerpt"}
        (job / "qa-review/adjudications.json").write_text(json.dumps({"items": [adjudication]}))
        refresh(job)
        assert json.loads((job / "qa-review/candidate-ledger.json").read_text())["candidates"][key]["queueable"] is False
        assert all(key not in x["candidate_ids"] for x in json.loads((job / "qa-review/recheck-queue.json").read_text())["items"])
        alias_record = dict(record, expected_inputtext="moulding", transcript="molding")
        alias = make_candidates(alias_record, job / "qa-review", {}, {})[0]
        explicit_error = {"status": "confirmed_narration_error", "audio_sha256": "audio-1", "request_sha256": "request-1", "evidence": "mock independent confirmation"}
        overridden = make_candidates(alias_record, job / "qa-review", {alias["candidate_id"]: explicit_error}, {})[0]
        assert overridden["queueable"] and overridden["confirmed_narration_error"] and overridden["priority"] == "high"
        # A stale hash-bound assessment cannot clear the same words in new audio.
        record["cache_identity"]["audio_sha256"] = "audio-2"
        (job / "raw/test-001.json").write_text(json.dumps(record["cache_identity"]))
        (job / "local-checks/cache/test-001.json").write_text(json.dumps(record))
        refresh(job)
        current = json.loads((job / "qa-review/candidate-ledger.json").read_text())["candidates"]
        assert any(x["classification"] == "negation_candidate" and x["queueable"] and x["active_in_current_sources"] and x["audio_sha256"] == "audio-2" for x in current.values())
        assert current[key]["active_in_current_sources"] is False
        # Exact compound aliases are preserved by the lexical matcher; a
        # nonexact brand/name or negation must remain visible.
        assert not lexical.compare("NovaMind's report", "Nova Mind’s report")["differences"]
        assert lexical.compare("NovaTok was not ready", "Nova Talk was now ready")["differences"]
        diff = {"type": "replace", "expected": "NovaTok", "heard": "Nova Talk", "reasons": []}
        assert classify(diff, "NovaTok", (0, 7))[0] == "proper_name_pronunciation_candidate"
        mixed = {"type": "replace", "expected": "Morrow opened", "heard": "Maro closed", "reasons": []}
        assert classify(mixed, mixed["expected"], (0, len(mixed["expected"])))[0] == "wording_candidate"
        utterance_source = "Agreed, Polk said. Geoffrey? Laine looked at him."
        utterance_diff = lexical.compare(utterance_source, "Agreed, Polk said. Lane looked at him.")["differences"][0]
        assert classify(utterance_diff, utterance_source, (0, 0))[0] == "whole_utterance_omission_candidate"
        assert classify({"type": "replace", "expected": "Geoffrey", "heard": "Jeffrey", "reasons": ["possible_sentence_deletion"]}, "Geoffrey?", (0, 8))[0] == "proper_name_pronunciation_candidate"
        assert classify({"type": "replace", "expected": "residence", "heard": "residents", "reasons": []}, "residence", (0, 9))[3] is True
        assert classify({"type": "replace", "expected": "firsthand", "heard": "first-hand", "reasons": []}, "firsthand", (0, 9))[0] == "orthographic_equivalent"
        assert classify({"type": "replace", "expected": "toolset", "heard": "tool reset", "reasons": []}, "toolset", (0, 7))[3] is True
        assert exact_numeric_representation("sixty million") == exact_numeric_representation("60 million")
        assert exact_numeric_representation("forties") == exact_numeric_representation("40s")
        assert exact_numeric_representation("fiftieth") == exact_numeric_representation("50th")
        assert exact_numeric_representation("sixty million") != exact_numeric_representation("600 million")
        assert exact_numeric_representation("8:14") is None
        assert classify({"type": "replace", "expected": "sixty million", "heard": "600 million", "reasons": []}, "sixty million", (0, 13))[1] == "high"
        long_prefix = "The archive remained open while the clerk reviewed each record. " * 20
        sparse = lexical.compare(long_prefix + "He did not sign.", long_prefix + "He did sign.")
        assert sparse["changed_word_fraction"] < lexical.THRESHOLD
        assert any(classify(x, long_prefix + "He did not sign.", (0, 0))[0] == "negation_candidate" for x in sparse["differences"])
        assert classify({"type": "delete", "expected": "shortpause", "heard": "", "reasons": []}, "<shortpause>", (1, 11))[3] is False
        poisoned = dict(record, comparison={"comparison_version": lexical.COMPARISON_VERSION, "differences": [{"type": "delete", "expected_word_start": 0, "expected_word_end": 1, "heard_word_start": 0, "heard_word_end": 0, "expected": "incorrect cached index", "heard": "", "audio_anchor": {"start_seconds": 0, "end_seconds": 1}}]})
        refreshed = make_candidates(poisoned, job / "qa-review", {}, {})
        assert any(x["expected"] == "not" for x in refreshed)
        assert all(source[x["source_char_span"][0]:x["source_char_span"][1]] == x["expected"] for x in refreshed)
        marked_source = "<shortpause> She did not sign the record. After that she waited."
        marked = dict(record, expected_inputtext=marked_source, transcript="did not sign the record. After that she waited.")
        marked["segments"] = [{"start": 1, "end": 10, "text": marked["transcript"]}]
        marked["duration_seconds"] = 12
        marked_rows = make_candidates(marked, job / "qa-review", {}, {})
        assert any(x["expected"] == "She" for x in marked_rows)
        assert all("shortpause" not in x["expected_normalized_words"] for x in marked_rows)
        for excerpt in make_excerpts(marked, marked_rows, 4, 30):
            ref = excerpt["expected_reference"]
            a, b = ref["source_char_span"]
            assert ref["excerpt_text"] == marked_source[a:b]
            assert ref["comparison_text"] == comparison_text(marked_source)[a:b]
            for target in ref["focus_targets"]:
                a, b = target["source_char_span"]
                assert target["expected_text"] == marked_source[a:b]
                assert target["expected_comparison_text"] == comparison_text(marked_source)[a:b]
        for control in SILENT_CONTROLS:
            controlled_source = "The door closed.\n" + control + "\nHe kept waiting."
            controlled = dict(record, expected_inputtext=controlled_source, transcript="The door closed. He kept waiting.")
            controlled["segments"] = [{"start": 0, "end": 10, "text": controlled["transcript"]}]
            assert not make_candidates(controlled, job / "qa-review", {}, {})
            controlled["transcript"] = "The door closed. Short pause. He kept waiting."
            controlled["segments"] = [{"start": 0, "end": 10, "text": controlled["transcript"]}]
            control_rows = make_candidates(controlled, job / "qa-review", {}, {})
            assert any(x["classification"] == "spoken_control_candidate" and x["queueable"] for x in control_rows)
            assert len(comparison_text(controlled_source)) == len(controlled_source)
        plain_source = "They took a short pause before walking."
        assert comparison_text(plain_source) == plain_source
        assert comparison_text("Silence.") == "Silence."
        terminal_source = "She waited for the answer."
        terminal = dict(record, expected_inputtext=terminal_source, transcript=terminal_source + " Again.")
        terminal["segments"] = [{"start": 0, "end": 10, "text": terminal["transcript"]}]
        terminal["duration_seconds"] = 10
        terminal_rows = make_candidates(terminal, job / "qa-review", {}, {})
        end_row = next(x for x in terminal_rows if x["type"] == "insert")
        assert end_row["source_char_span"] == [len(terminal_source), len(terminal_source)]
        tail_excerpts = make_excerpts(terminal, terminal_rows, 4, 30)
        assert any(x["expected_reference"]["source_char_span"][1] == len(terminal_source) for x in tail_excerpts)
        broad = make_excerpts(record, make_candidates(record, job / "qa-review", {}, {}), 4, 30)
        assert any(x.get("diagnostic", {}).get("kind") == "complete_long_target" for x in broad)
        assert all(t["window_contains_target_anchor"] for x in broad if x.get("diagnostic") for t in x["expected_reference"]["focus_targets"])
        tail_source = "The visitor reached the gate and asked for directions. " + "They waited for the answer before continuing. " * 12
        tail_record = dict(record, expected_inputtext=tail_source, transcript="The visitor reached the gate and asked for directions.", duration_seconds=223.2, segments=[{"start": 210, "end": 222.7, "text": "The visitor reached the gate and asked for directions."}])
        tail_rows = make_candidates(tail_record, job / "qa-review", {}, {})
        tail_diagnostic = make_excerpts(tail_record, tail_rows, 4, 30)[0]
        assert tail_diagnostic["end_seconds"] == 223.2
        assert tail_diagnostic["start_seconds"] < 170
        assert tail_diagnostic["diagnostic"]["kind"] == "complete_long_target"
        longer = dict(record, duration_seconds=180, segments=[{"start": 0, "end": 180, "text": record["transcript"]}])
        pieces = make_excerpts(longer, make_candidates(longer, job / "qa-review", {}, {}), 4, 30)
        assert len(pieces) > 1 and all(x["end_seconds"] - x["start_seconds"] <= 30.001 for x in pieces)
        assert all(x["expected_reference"]["focus_targets"] for x in pieces)
        old_count = len(current)
        repeat = refresh(job)
        assert len(json.loads((job / "qa-review/candidate-ledger.json").read_text())["candidates"]) == old_count
        assert not repeat["new_candidate_ids"]
    print(json.dumps({"selftest": "passed", "cases": ["negation below aggregate threshold", "whole utterance lost across name replacement", "name spelling alone not promoted", "sentence omission and insertion", "30-second normal or explicit 120-second diagnostic cap", "split references retain complete long focus", "cast-only decoder prompt", "hash-bound adjudication preservation", "stale audio adjudication isolation", "exact alias preservation without fuzzy alias", "name-plus-verb change retained", "exact numeric representation without value substitution", "plural differences retained", "explicit compound representation with extra-word contradiction retained", "both explicit silent controls excluded", "spoken silent controls remain additions", "plain narrative pause and Silence retained", "stale normalized cache indices ignored", "global marker mask preserves source coordinates", "terminal insertion gap included", "collapsed long-tail anchor receives bounded backward context", "idempotent refresh"]}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, default=JOB)
    parser.add_argument("--context", type=float, default=4)
    parser.add_argument("--max-seconds", type=float, default=30)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
    else:
        if not 3 <= args.context <= 5 or not 10 <= args.max_seconds <= 30:
            parser.error("context must be 3–5 seconds; max-seconds must be 10–30")
        result = refresh(args.job.resolve(), args.context, args.max_seconds)
        print(json.dumps({k: v for k, v in result.items() if k != "new_candidate_ids"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
