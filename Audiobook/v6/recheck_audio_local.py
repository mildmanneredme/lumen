#!/usr/bin/env python3
"""Offline excerpt rechecks using the existing local Whisper model.

Run only after the full QA worker exits, with local-asr-env/bin/python:
  python recheck_audio_local.py --priority high
  python recheck_audio_local.py --all
  python recheck_audio_local.py --self-test

Uses the same local-checks/.worker.lock as the full checker. No keys, API calls,
uploads, model downloads, or expected-passage decoder prompts. ASR evidence can
resolve a prior transcription artifact; it never certifies auditory quality.
"""
from __future__ import annotations

import argparse
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
import wave

import check_audio_local as comparison

JOB = Path(__file__).resolve().parent
VERSION = "local-excerpt-recheck-1"
SCOPE = "Synthetic local crop/alignment tests; no inference, production audio, keys or network."
SILENT_CONTROLS = ("<shortpause>", "<short pause>")
SILENT_CONTROL = re.compile("|".join(re.escape(value) for value in SILENT_CONTROLS))
LOADED_CHECKER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
LOADED_NORMALIZER_SHA256 = hashlib.sha256(Path(comparison.__file__).read_bytes()).hexdigest()


class RecheckError(RuntimeError):
    pass


def digest(path: Path) -> str:
    return comparison.stable_hash(path)


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RecheckError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def model_identity(model: Path) -> dict:
    if not model.is_absolute() or not model.is_dir():
        raise RecheckError("Model must be an existing absolute local directory; remote repository IDs are refused")
    weights = model / "weights.npz"
    if not weights.is_file():
        weights = model / "weights.safetensors"
    files = [model / "config.json", weights]
    if any(not path.is_file() or path.stat().st_size == 0 for path in files):
        raise RecheckError("Existing local config.json and weights.npz/safetensors are required")
    return {"path": str(model.resolve()), "files_sha256": {path.name: digest(path) for path in files},
            "mlx_whisper_version": importlib.metadata.version("mlx-whisper")}


def prompt_settings(excerpt: dict) -> dict:
    prompt = excerpt.get("decoder_prompt", {})
    if not isinstance(prompt, dict) or prompt.get("contains_expected_passage") is not False:
        raise RecheckError("Decoder prompt must explicitly exclude the expected passage")
    terms = prompt.get("initial_prompt", "")
    if not isinstance(terms, str) or len(terms) > 3000 or any(ord(c) < 32 and c not in "\t" for c in terms):
        raise RecheckError("Decoder prompt must contain a short cast/terms list")
    reference = comparison.normalize(excerpt.get("expected_reference", {}).get("excerpt_text", ""))
    prompt_words = comparison.normalize(terms)
    if any(block.size >= 8 for block in difflib.SequenceMatcher(None, reference, prompt_words, autojunk=False).get_matching_blocks()):
        raise RecheckError("Decoder prompt overlaps an expected passage; use cast/terms only")
    return {"language": "en", "temperature": 0.0, "condition_on_previous_text": False,
            "verbose": None, "word_timestamps": True, "initial_prompt": terms}


def spoken_alignment(text: str) -> str:
    """Remove only an explicit silent control while retaining source coordinates."""
    return SILENT_CONTROL.sub(lambda match: " " * len(match.group()), text)


def bind_excerpt(excerpt: dict, manifest_item: dict, job: Path) -> tuple[Path, dict]:
    for field in ("excerpt_id", "clip_id", "audio_sha256", "request_sha256"):
        if not isinstance(excerpt.get(field), str) or not excerpt[field]:
            raise RecheckError(f"Excerpt has no valid {field}")
    if not re.fullmatch(r"[a-f0-9]{64}", excerpt["excerpt_id"]):
        raise RecheckError("Excerpt ID must be a SHA-256 identifier")
    if manifest_item.get("id") != excerpt["clip_id"] or manifest_item.get("request_sha256") != excerpt["request_sha256"]:
        raise RecheckError("Queued excerpt belongs to a different current manifest request")
    request_hash = hashlib.sha256((manifest_item["model"] + manifest_item["voice"] +
                                  manifest_item["style"] + manifest_item["text"]).encode()).hexdigest()
    if request_hash != manifest_item["request_sha256"]:
        raise RecheckError("Current manifest request hash does not match its actual text/style/model")
    wav = Path(manifest_item["output"])
    if not wav.is_absolute():
        wav = job / wav
    wav = wav.resolve()
    if wav.parent != (job / "raw").resolve() or wav.name != excerpt["clip_id"] + ".wav":
        raise RecheckError("Current clip WAV must be its named file directly inside raw/")
    if Path(excerpt.get("source_wav", "")).resolve() != wav:
        raise RecheckError("Queued source path differs from the current manifest")
    receipt = load(wav.with_suffix(".json"))
    audio_hash = digest(wav)
    if (audio_hash != excerpt["audio_sha256"] or receipt.get("audio_sha256") != audio_hash
            or receipt.get("request_sha256") != excerpt["request_sha256"]
            or receipt.get("id") != excerpt["clip_id"]):
        raise RecheckError("Queued audio, current WAV, or generation receipt has a stale hash")
    start, end = float(excerpt["start_seconds"]), float(excerpt["end_seconds"])
    diagnostic = excerpt.get("diagnostic", {})
    long_diagnostic = (isinstance(diagnostic, dict) and diagnostic.get("kind") == "complete_long_target"
                       and diagnostic.get("max_seconds") == 120)
    allowed_seconds = 120.01 if long_diagnostic else 30.01
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end - start > allowed_seconds:
        raise RecheckError("Excerpt must be finite and nonempty; ordinary cap30s, declared complete-target diagnostic cap120s")
    with wave.open(str(wav), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        channels, width = audio.getnchannels(), audio.getsampwidth()
        if not rate or not frames or channels != 1 or width != 2 or audio.getcomptype() != "NONE":
            raise RecheckError("Source must be nonempty mono 16-bit PCM WAV")
    if end > frames / rate + 1 / rate:
        raise RecheckError("Excerpt extends past the current WAV")
    reference = excerpt.get("expected_reference")
    if not isinstance(reference, dict):
        raise RecheckError("Expected reference is required for post-ASR comparison")
    span = reference.get("source_char_span")
    text = manifest_item["text"]
    if (not isinstance(span, list) or len(span) != 2 or any(type(v) is not int for v in span)
            or not 0 <= span[0] <= span[1] <= len(text)):
        raise RecheckError("Expected reference has invalid current source coordinates")
    source_reference = text[span[0]:span[1]]
    full_comparison_text = spoken_alignment(text)
    if reference.get("excerpt_text") not in (source_reference, SILENT_CONTROL.sub(" ", source_reference)):
        raise RecheckError("Expected reference is not an exact slice of current narration text")
    if "comparison_text" in reference and reference["comparison_text"] != full_comparison_text[span[0]:span[1]]:
        raise RecheckError("Expected comparison mask differs from the exact current source")
    candidates, focuses = excerpt.get("candidate_ids"), reference.get("focus_targets")
    if (not isinstance(candidates, list) or not candidates or len(set(candidates)) != len(candidates)
            or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in candidates)
            or not isinstance(focuses, list) or {f.get("candidate_id") for f in focuses} != set(candidates)):
        raise RecheckError("Excerpt candidate IDs and focus targets do not match")
    for focus in focuses:
        target = focus.get("source_char_span")
        if (not isinstance(target, list) or len(target) != 2 or any(type(v) is not int for v in target)
                or not 0 <= target[0] <= target[1] <= len(text)
                or text[target[0]:target[1]] != focus.get("expected_text")):
            raise RecheckError("Focus target is not an exact slice of current narration text")
        if "expected_comparison_text" in focus and focus["expected_comparison_text"] != full_comparison_text[target[0]:target[1]]:
            raise RecheckError("Focus comparison mask differs from the exact current source")
        if "window_contains_target_anchor" in focus:
            anchor = focus.get("audio_anchor", {})
            if (not isinstance(anchor, dict) or type(focus["window_contains_target_anchor"]) is not bool
                    or not isinstance(anchor.get("start_seconds"), (int, float))
                    or not isinstance(anchor.get("end_seconds"), (int, float))):
                raise RecheckError("Focus window declaration needs a valid source anchor")
            contains = start <= float(anchor["start_seconds"]) + .002 and end >= float(anchor["end_seconds"]) - .002
            if focus["window_contains_target_anchor"] != contains:
                raise RecheckError("Focus window declaration contradicts the current crop geometry")
    first, last = math.floor(start * rate), min(frames, math.ceil(end * rate))
    crop = {"first_frame": first, "last_frame_exclusive": last, "frames": last - first,
            "source_frames": frames, "source_duration_seconds": frames / rate,
            "sample_rate": rate, "channels": channels, "sample_width": width,
            "start_seconds": first / rate, "end_seconds": last / rate, "duration_seconds": (last - first) / rate}
    return wav, crop


def crop_audio(source: Path, destination: Path, crop: dict, source_hash: str) -> dict:
    with wave.open(str(source), "rb") as audio:
        audio.setpos(crop["first_frame"])
        pcm = audio.readframes(crop["frames"])
    if len(pcm) != crop["frames"] * crop["channels"] * crop["sample_width"]:
        raise RecheckError("Source WAV became truncated while cropping")
    with wave.open(str(destination), "wb") as audio:
        audio.setnchannels(crop["channels"])
        audio.setsampwidth(crop["sample_width"])
        audio.setframerate(crop["sample_rate"])
        audio.writeframes(pcm)
    if digest(source) != source_hash:
        raise RecheckError("Source WAV changed while cropping")
    return {**crop, "cropped_wav_sha256": digest(destination), "pcm_sha256": hashlib.sha256(pcm).hexdigest()}


def normalized_alignment(reference: str, transcript: str) -> tuple[list, list, dict, list]:
    expected, actual = comparison.tokens(reference), comparison.tokens(transcript)
    opcodes = difflib.SequenceMatcher(None, [t.value for t in expected], [t.value for t in actual], autojunk=False).get_opcodes()
    matched = {}
    for tag, a, b, c, d in opcodes:
        if tag == "equal":
            matched.update({a + i: c + i for i in range(b - a)})
    return expected, actual, matched, opcodes


def audio_anchor(start: int, end: int, transcript: str, segments: list, crop: dict) -> dict:
    evidence = {"differences": [{"heard_word_start": start, "heard_word_end": end}]}
    comparison.add_time_anchors(evidence, transcript, segments, crop["duration_seconds"])
    anchor = evidence["differences"][0]["audio_anchor"]
    return {key: value + crop["start_seconds"] if key.endswith("seconds") else value for key, value in anchor.items()}


def adjudicate(excerpt: dict, transcript: str, segments: list, crop: dict) -> dict:
    reference = excerpt["expected_reference"]
    reference_text = reference.get("alignment_text", reference.get("comparison_text", spoken_alignment(reference["excerpt_text"])))
    expected, actual, matched, opcodes = normalized_alignment(reference_text, transcript)
    ref_start, ref_end = reference["source_char_span"]
    outcomes = []
    for focus in reference["focus_targets"]:
        first, last = focus["source_char_span"]
        target_text = focus.get("alignment_text", focus.get("expected_comparison_text", spoken_alignment(focus["expected_text"])))
        target_tokens = comparison.tokens(target_text)
        total = len(target_tokens)
        indexes = [i for i, token in enumerate(expected) if ref_start + token.start < last and ref_start + token.end > first]
        insertion = first == last
        if insertion:
            boundary = next((i for i, token in enumerate(expected) if ref_start + token.start >= first), len(expected))
            a = b = boundary
        elif indexes:
            a, b = min(indexes), max(indexes) + 1
        else:
            a = b = 0
        inside = ref_start <= first <= last <= ref_end
        complete_window = focus.get("window_contains_target_anchor", True)
        left, right = max(0, a - 2), min(len(expected), b + 2)
        neighborhood = list(range(left, right))
        full_span = reference.get("full_source_spoken_char_span")
        true_head = (isinstance(full_span, list) and crop.get("first_frame") == 0
                     and ref_start <= full_span[0])
        true_tail = (isinstance(full_span, list) and crop.get("last_frame_exclusive") == crop.get("source_frames")
                     and crop.get("source_frames") is not None and ref_end >= full_span[1])
        head_edge = a - left < 2 and true_head
        tail_edge = right - b < 2 and true_tail
        enough_context = ((a - left >= 2 or head_edge) and (right - b >= 2 or tail_edge)
                          and (a - left >= 2 or right - b >= 2 or total >= 4))
        coherent = (bool(neighborhood) and all(i in matched for i in neighborhood)
                    and all(matched[y] == matched[x] + 1 for x, y in zip(neighborhood, neighborhood[1:])))
        if head_edge:
            coherent = coherent and matched.get(left) == 0
        if tail_edge:
            coherent = coherent and matched.get(right - 1) == len(actual) - 1
        left_flank, right_flank = list(range(left, a)), list(range(b, right))
        left_known = ((len(left_flank) >= 2 or head_edge) and all(i in matched for i in left_flank)
                      and all(matched[y] == matched[x] + 1 for x, y in zip(left_flank, left_flank[1:])))
        right_known = ((len(right_flank) >= 2 or tail_edge) and all(i in matched for i in right_flank)
                       and all(matched[y] == matched[x] + 1 for x, y in zip(right_flank, right_flank[1:])))
        if head_edge and left_flank:
            left_known = left_known and matched.get(left_flank[0]) == 0
        if tail_edge and right_flank:
            right_known = right_known and matched.get(right_flank[-1]) == len(actual) - 1
        flanks_recovered = left_known and right_known and enough_context
        focused_observed = None
        if flanks_recovered:
            observed_start = matched[left_flank[-1]] + 1 if left_flank else 0
            observed_end = matched[right_flank[0]] if right_flank else len(actual)
            focused_observed = [token.value for token in actual[observed_start:observed_end]]
        focused_expected = [token.value for token in expected[a:b]]
        # Partial long targets need exact interior coverage; never count a focus
        # outside this fragment as a missing word. Approximate edge words are
        # excluded until another overlapping excerpt can corroborate them.
        covered = []
        interior_contradictions = []
        if inside and enough_context and coherent and complete_window:
            covered = list(range(total))
        elif not insertion and indexes:
            for target_index, target in enumerate(target_tokens):
                absolute_span = (first + target.start, first + target.end)
                for i in indexes:
                    token = expected[i]
                    if (absolute_span == (ref_start + token.start, ref_start + token.end)
                            and target.value == token.value):
                        context = list(range(max(0, i - 2), min(len(expected), i + 3)))
                        at_head, at_tail = i < 2, i >= len(expected) - 2
                        if ((not at_head or true_head) and (not at_tail or true_tail)
                                and len(context) >= 3 and all(j in matched for j in context)
                                and all(matched[y] == matched[x] + 1 for x, y in zip(context, context[1:]))
                                and (not at_head or matched[context[0]] == 0)
                                and (not at_tail or matched[context[-1]] == len(actual) - 1)):
                            covered.append(target_index)
            # Coverage from an overlapping exact fragment must not erase an
            # interior contradictory decode in this fragment. Two intact source
            # words on EACH side locate changed runs; clipped edges stay unknown.
            for tag, ea, eb, ha, hb in opcodes:
                if tag == "equal" or ea < 2 or eb + 2 > len(expected):
                    continue
                if ea < eb:
                    change_start = ref_start + expected[ea].start
                    change_end = ref_start + expected[eb - 1].end
                    within_target = change_start < last and change_end > first
                else:
                    point = ref_start + expected[ea].start
                    within_target = first < point < last
                flank = [ea - 2, ea - 1, eb, eb + 1]
                if (not within_target or not all(i in matched for i in flank)
                        or matched[ea - 1] != matched[ea - 2] + 1
                        or matched[eb + 1] != matched[eb] + 1):
                    continue
                heard_start, heard_end = matched[ea - 1] + 1, matched[eb]
                observed_words = [token.value for token in actual[heard_start:heard_end]]
                expected_words = [token.value for token in expected[ea:eb]]
                if observed_words != expected_words:
                    interior_contradictions.append({"type": tag, "expected_normalized_words": expected_words,
                                                    "observed_normalized_words": observed_words,
                                                    "expected_reference_word_span": [ea, eb],
                                                    "heard_word_span": [heard_start, heard_end],
                                                    "audio_anchor": audio_anchor(heard_start, heard_end, transcript, segments, crop)})
        if inside and enough_context and coherent and complete_window and (insertion or total):
            status, reason = "resolved_asr_artifact", "Exact focal wording and contiguous source context recovered by independent local ASR; true file boundaries require exact ASR prefix/suffix"
        elif interior_contradictions:
            status, reason = "unresolved_second_pass_mismatch", "Partial long target has contradictory interior wording located by intact two-word source flanks"
        elif not indexes and not insertion or not inside or not complete_window:
            status, reason = "insufficient_context", "Target is outside or crosses this approximate excerpt reference; combine fragment coverage"
        elif not enough_context:
            status, reason = "insufficient_context", "Focal wording lacks two source context words on both sides"
        elif flanks_recovered and focused_observed != focused_expected:
            status, reason = "unresolved_second_pass_mismatch", "Recovered source flanks enclose persistent changed focal wording"
        else:
            status, reason = "insufficient_context", "Expected prefix or suffix may lie outside this crop; actual source flanks do not prove a focal discrepancy"
        heard_indexes = [matched[i] for i in range(a, b) if i in matched]
        if insertion:
            heard_indexes = [matched[i] for i in (a - 1, a) if i in matched]
        observed = " ".join(actual[i].value for i in heard_indexes)
        anchor = audio_anchor(min(heard_indexes), max(heard_indexes) + 1, transcript, segments, crop) if heard_indexes else focus.get("audio_anchor")
        outcomes.append({"candidate_id": focus["candidate_id"], "status": status, "reason": reason,
                         "expected_text": focus["expected_text"], "original_asr_text": focus.get("asr_text", ""),
                         "expected_normalized_words": [token.value for token in target_tokens],
                         "observed_matched_normalized_words": observed, "covered_expected_token_indices": sorted(set(covered)),
                         "expected_token_count": total, "insertion_candidate": insertion,
                         "source_char_span": [first, last], "source_reference_contains_full_focus": inside,
                         "window_contains_target_anchor": complete_window,
                         "context_coherent": coherent, "audio_anchor": anchor,
                         "actual_source_flanks_recovered": flanks_recovered,
                         "focused_observed_between_flanks": focused_observed,
                         "interior_target_contradictions": interior_contradictions,
                         "true_file_head_used": head_edge, "true_file_tail_used": tail_edge,
                         "retake_candidate": status == "unresolved_second_pass_mismatch",
                         "confirmed_narration_error": False, "listening_approval": False})
    comparison_record = comparison.compare(reference_text, transcript)
    comparison.add_time_anchors(comparison_record, transcript, segments, crop["duration_seconds"])
    for difference in comparison_record["differences"]:
        difference["audio_anchor"] = {key: value + crop["start_seconds"] if key.endswith("seconds") else value
                                      for key, value in difference["audio_anchor"].items()}
    return {"focus_outcomes": outcomes, "reference_alignment": comparison_record,
            "interpretation": "Exact normalized acoustic-transcript evidence only. Boundary mismatches are not omissions; ASR cannot certify hearing quality."}


def absolute_segments(segments: list, offset: float) -> list:
    output = []
    for segment in segments:
        item = dict(segment)
        for name in ("start", "end"):
            if name in item:
                item[name] = float(item[name]) + offset
        if item.get("words"):
            item["words"] = [{**word, "start": float(word["start"]) + offset, "end": float(word["end"]) + offset} for word in item["words"]]
        output.append(item)
    return output


def run_one(excerpt: dict, manifest_item: dict, model: dict, job: Path, decoder) -> dict:
    wav, crop = bind_excerpt(excerpt, manifest_item, job)
    settings = prompt_settings(excerpt)
    identity = {"version": VERSION, "checker_sha256": LOADED_CHECKER_SHA256,
                "normalizer_sha256": LOADED_NORMALIZER_SHA256, "excerpt_id": excerpt["excerpt_id"],
                "clip_id": excerpt["clip_id"], "audio_sha256": excerpt["audio_sha256"],
                "request_sha256": excerpt["request_sha256"], "crop": crop, "model_identity": model,
                "decode_settings": settings, "candidate_ids": sorted(excerpt["candidate_ids"]),
                "expected_reference_sha256": sha(excerpt["expected_reference"])}
    key = sha(identity)
    path = job / "local-checks" / "rechecks" / "cache" / f"{excerpt['excerpt_id']}.{key}.json"
    if path.is_file():
        saved = load(path)
        if saved.get("cache_identity") != identity or saved.get("cache_key") != key:
            raise RecheckError("Excerpt cache identity mismatch")
        if saved.get("evidence_sha256") != sha({k: saved[k] for k in ("transcript", "segments", "adjudication", "backend_nonfinite_confidence_fields")}):
            raise RecheckError("Excerpt cached evidence hash differs")
        return {"status": "cached", "output": str(path), **saved}
    temporary_parent = job / "local-checks" / "rechecks"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".crop-", dir=temporary_parent) as temporary:
        cropped = Path(temporary) / "excerpt.wav"
        crop_evidence = crop_audio(wav, cropped, crop, excerpt["audio_sha256"])
        result = decoder(cropped, model, settings)
        result, backend_warnings = comparison.sanitize_backend_confidence(result)
        if digest(cropped) != crop_evidence["cropped_wav_sha256"] or digest(wav) != excerpt["audio_sha256"]:
            raise RecheckError("Source or cropped WAV changed during local decoding")
        if load(wav.with_suffix(".json")).get("request_sha256") != excerpt["request_sha256"]:
            raise RecheckError("Narration request changed during local decoding")
        transcript, segments = str(result.get("text", "")), result.get("segments", [])
        aligned_reference = dict(excerpt["expected_reference"])
        reference_start, reference_end = aligned_reference["source_char_span"]
        full_comparison_text = spoken_alignment(manifest_item["text"])
        full_tokens = comparison.tokens(full_comparison_text)
        aligned_reference["alignment_text"] = full_comparison_text[reference_start:reference_end]
        aligned_reference["full_source_spoken_char_span"] = ([full_tokens[0].start, full_tokens[-1].end]
                                                              if full_tokens else [0, 0])
        aligned_reference["focus_targets"] = [{**focus, "alignment_text": full_comparison_text[focus["source_char_span"][0]:focus["source_char_span"][1]]}
                                                for focus in aligned_reference["focus_targets"]]
        aligned_excerpt = {**excerpt, "expected_reference": aligned_reference}
        adjudication = adjudicate(aligned_excerpt, transcript, segments, crop)
        evidence = {"transcript": transcript, "segments": segments, "adjudication": adjudication,
                    "backend_nonfinite_confidence_fields": backend_warnings}
        saved = {"excerpt_id": excerpt["excerpt_id"], "clip_id": excerpt["clip_id"], "candidate_ids": excerpt["candidate_ids"],
                 "cache_key": key, "cache_identity": identity, "crop_evidence": crop_evidence,
                 "source_wav": str(wav), "expected_reference": excerpt["expected_reference"],
                 "source_reference_alignment_text": aligned_reference["alignment_text"],
                 **evidence, "absolute_segments": absolute_segments(segments, crop["start_seconds"]),
                 "evidence_sha256": sha(evidence), "created_at_unix": time.time(),
                 "inference_location": "Local computer, offline existing model", "listening_approval": False}
        atomic(path, saved)
        return {"status": "checked", "output": str(path), **saved}


def aggregate(outcomes: list[dict], candidate_ids: list[str], errors: list[dict]) -> dict:
    records = {candidate: [] for candidate in candidate_ids}
    for outcome in outcomes:
        for focus in outcome["adjudication"]["focus_outcomes"]:
            records.setdefault(focus["candidate_id"], []).append({**focus, "excerpt_id": outcome["excerpt_id"],
                        "clip_id": outcome["clip_id"], "audio_sha256": outcome["cache_identity"]["audio_sha256"],
                        "request_sha256": outcome["cache_identity"]["request_sha256"], "evidence_file": outcome["output"]})
    candidates = []
    for candidate, evidence in records.items():
        resolved = any(row["status"] == "resolved_asr_artifact" for row in evidence)
        counts = {row["expected_token_count"] for row in evidence}
        identities = {sha({key: row[key] for key in ("clip_id", "audio_sha256", "request_sha256",
                                                    "source_char_span", "expected_normalized_words")}) for row in evidence}
        covered = sorted({i for row in evidence for i in row["covered_expected_token_indices"]})
        conflict = any(row["status"] == "unresolved_second_pass_mismatch" for row in evidence)
        # Contradictory independent evidence is retained even if another excerpt
        # appears exact. Long omissions require every target token across crops.
        combined = bool(evidence) and len(counts) == 1 and next(iter(counts)) > 0 and covered == list(range(next(iter(counts))))
        if len(identities) > 1:
            status = "unresolved_conflicting_source_binding"
        elif not conflict and resolved:
            status = "resolved_asr_artifact"
        elif not conflict and combined:
            status = "insufficient_ordered_full_window_evidence"
        elif conflict:
            status = "unresolved_second_pass_mismatch"
        else:
            status = "insufficient_context_or_error"
        candidates.append({"candidate_id": candidate, "status": status, "covered_expected_token_indices": covered,
                           "clip_id": evidence[0]["clip_id"] if evidence else None,
                           "audio_sha256": evidence[0]["audio_sha256"] if evidence else None,
                           "request_sha256": evidence[0]["request_sha256"] if evidence else None,
                           "combined_fragment_coverage": combined, "fragment_union_is_not_resolution": True,
                           "evidence": evidence,
                           "confirmed_narration_error": False, "listening_approval": False})
    return {"candidate_ids": candidate_ids, "resolved_candidate_ids": [r["candidate_id"] for r in candidates if r["status"] == "resolved_asr_artifact"],
            "unresolved_candidate_ids": [r["candidate_id"] for r in candidates if r["status"] != "resolved_asr_artifact"],
            "retake_candidate_ids": [r["candidate_id"] for r in candidates if r["status"] == "unresolved_second_pass_mismatch"],
            "candidate_results": candidates, "errors": errors, "confirmed_narration_errors": 0, "listening_approval": False}


def local_decoder(path: Path, model: dict, settings: dict) -> dict:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        import mlx_whisper
    except Exception as exc:
        raise comparison.BackendImportError("Local MLX import failed; stop before retrying a partial backend: " + str(exc)) from exc
    return mlx_whisper.transcribe(str(path), path_or_hf_repo=model["path"], **settings)


def execute(job: Path, queue_path: Path, model_path: Path, priority: str | None) -> dict:
    checks = job / "local-checks"
    checks.mkdir(parents=True, exist_ok=True)
    with (checks / ".worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecheckError("Local QA worker is already running; no concurrent GPU inference was started") from exc
        queue_hash = digest(queue_path)
        queue = load(queue_path)
        manifest_path = job / "generation-manifest.json"
        manifest_hash = digest(manifest_path)
        manifest = load(manifest_path)
        items = {item["id"]: item for item in manifest["items"]}
        excerpts = queue.get("items", [])
        if not isinstance(excerpts, list):
            raise RecheckError("Queue items must be an array")
        if priority:
            excerpts = [item for item in excerpts if item.get("priority") == priority]
        model = model_identity(model_path)
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        candidates = sorted({candidate for excerpt in excerpts for candidate in excerpt["candidate_ids"]})
        outcomes, errors = [], []
        report = checks / "rechecks" / (f"summary-{priority}.json" if priority else "summary.json")
        for excerpt in excerpts:
            try:
                if excerpt["clip_id"] not in items:
                    raise RecheckError("Queued clip is absent from current manifest")
                outcome = run_one(excerpt, items[excerpt["clip_id"]], model, job, local_decoder)
                outcomes.append(outcome)
                print(json.dumps({"excerpt_id": excerpt["excerpt_id"], "clip_id": excerpt["clip_id"], "status": outcome["status"],
                                  "candidate_statuses": {row["candidate_id"]: row["status"] for row in outcome["adjudication"]["focus_outcomes"]}}), flush=True)
            except (ImportError, comparison.BackendImportError) as exc:
                errors.append({"excerpt_id": excerpt.get("excerpt_id"), "clip_id": excerpt.get("clip_id"), "error": str(exc), "fatal_backend_import": True})
                break
            except (RecheckError, OSError, ValueError, KeyError, TypeError, wave.Error, EOFError, RuntimeError) as exc:
                errors.append({"excerpt_id": excerpt.get("excerpt_id"), "clip_id": excerpt.get("clip_id"), "error": str(exc)})
                print(json.dumps(errors[-1]), flush=True)
            summary = {"version": VERSION, "queue_sha256": queue_hash, "generation_manifest_sha256": manifest_hash,
                       "priority": priority, "selected_excerpts": len(excerpts), "processed_excerpts": len(outcomes),
                       "model_identity": model, "updated_at_unix": time.time(), **aggregate(outcomes, candidates, errors)}
            atomic(report, summary)
        changed = digest(queue_path) != queue_hash or digest(manifest_path) != manifest_hash
        summary = {"version": VERSION, "queue_sha256": queue_hash, "generation_manifest_sha256": manifest_hash,
                   "priority": priority, "selected_excerpts": len(excerpts), "processed_excerpts": len(outcomes),
                   "source_snapshot_changed_during_run": changed, "complete_selected_queue": len(outcomes) == len(excerpts) and not errors and not changed,
                   "model_identity": model, "updated_at_unix": time.time(), **aggregate(outcomes, candidates, errors)}
        atomic(report, summary)
        text_report = (f"Local excerpt rechecks: {len(outcomes)}/{len(excerpts)} excerpts\n"
                       f"Resolved ASR artifacts: {len(summary['resolved_candidate_ids'])}\n"
                       f"Unresolved candidates: {len(summary['unresolved_candidate_ids'])}\n"
                       f"Retake review candidates: {len(summary['retake_candidate_ids'])}\n"
                       f"Errors: {len(errors)}\n\n"
                       "ASR is evidence, not a listening or narration-quality certification.\n\n" +
                       "\n".join(f"{row['candidate_id']} {row['status']}" for row in summary["candidate_results"]) + "\n")
        report.with_suffix(".txt").write_text(text_report, encoding="utf-8")
        print(json.dumps({"selected_excerpts": len(excerpts), "processed_excerpts": len(outcomes),
                          "resolved_candidates": len(summary["resolved_candidate_ids"]), "unresolved_candidates": len(summary["unresolved_candidate_ids"]),
                          "errors": len(errors), "report": str(report)}), flush=True)
        return summary


def self_test() -> dict:
    checks = []

    def check(name, condition):
        if not condition:
            raise RecheckError("Self-test failed: " + name)
        checks.append(name)

    source = "He said she was not ready to leave the quiet room."
    start, end = source.index("not"), source.index("not") + 3
    candidate = "a" * 64
    fixture = {"expected_reference": {"source_char_span": [0, len(source)], "excerpt_text": source,
                                    "focus_targets": [{"candidate_id": candidate, "source_char_span": [start, end], "expected_text": "not", "asr_text": "now"}]}}
    crop = {"start_seconds": 2.0, "end_seconds": 5.0, "duration_seconds": 3.0}
    check("Exact interior target with coherent context resolves a prior ASR mismatch", adjudicate(fixture, source, [], crop)["focus_outcomes"][0]["status"] == "resolved_asr_artifact")
    check("Negation substitution remains unresolved", adjudicate(fixture, source.replace("not", "now"), [], crop)["focus_outcomes"][0]["status"] == "unresolved_second_pass_mismatch")
    check("Persistent focal omission remains unresolved", adjudicate(fixture, source.replace("not ", ""), [], crop)["focus_outcomes"][0]["status"] == "unresolved_second_pass_mismatch")
    check("Added wording beside exact target prevents silent acceptance", adjudicate(fixture, source.replace("not ready", "not really ready"), [], crop)["focus_outcomes"][0]["status"] == "unresolved_second_pass_mismatch")
    insertion = json.loads(json.dumps(fixture))
    point = source.index("not")
    insertion["expected_reference"]["focus_targets"][0].update({"source_char_span": [point, point], "expected_text": ""})
    check("Recovered matching flanks resolve false addition", adjudicate(insertion, source, [], crop)["focus_outcomes"][0]["status"] == "resolved_asr_artifact")
    check("Persistent insertion cannot resolve", adjudicate(insertion, source.replace("not", "really not"), [], crop)["focus_outcomes"][0]["status"] == "unresolved_second_pass_mismatch")
    boundary = json.loads(json.dumps(fixture))
    boundary["expected_reference"]["focus_targets"][0].update({"source_char_span": [0, 2], "expected_text": "He"})
    check("Boundary target without context stays ambiguous", adjudicate(boundary, source, [], crop)["focus_outcomes"][0]["status"] == "insufficient_context")
    boundary["expected_reference"]["full_source_spoken_char_span"] = [0, len(source) - 1]
    entire_crop = {**crop, "start_seconds": 0.0, "end_seconds": 3.0,
                   "first_frame": 0, "last_frame_exclusive": 3000, "source_frames": 3000}
    check("True source head allows exact prefix plus inward context", adjudicate(boundary, source, [], entire_crop)["focus_outcomes"][0]["status"] == "resolved_asr_artifact")
    check("Extra ASR words before true head remain unresolved", adjudicate(boundary, "Well " + source, [], entire_crop)["focus_outcomes"][0]["status"] == "unresolved_second_pass_mismatch")
    final_word = source.index("room")
    tail = json.loads(json.dumps(boundary))
    tail["expected_reference"]["focus_targets"][0].update({"source_char_span": [final_word, final_word + 4], "expected_text": "room"})
    check("True source tail allows exact suffix plus inward context", adjudicate(tail, source, [], entire_crop)["focus_outcomes"][0]["status"] == "resolved_asr_artifact")
    check("Extra ASR words beyond true tail remain unresolved", adjudicate(tail, source + " Again.", [], entire_crop)["focus_outcomes"][0]["status"] == "unresolved_second_pass_mismatch")
    check("Approximate crop tail is never treated as true source tail", adjudicate(tail, source, [], {**entire_crop, "last_frame_exclusive": 2000})["focus_outcomes"][0]["status"] == "insufficient_context")
    punctuation_source = "The visitor finally heard the answer."
    terminal_gap = {"expected_reference": {"source_char_span": [0, len(punctuation_source)],
                    "excerpt_text": punctuation_source, "full_source_spoken_char_span": [0, len(punctuation_source) - 1],
                    "focus_targets": [{"candidate_id": candidate, "source_char_span": [len(punctuation_source), len(punctuation_source)],
                                       "expected_text": "", "asr_text": "again"}]}}
    check("Exact true-tail transcript clears insertion point after punctuation", adjudicate(terminal_gap, punctuation_source, [], entire_crop)["focus_outcomes"][0]["status"] == "resolved_asr_artifact")
    check("Persistent added word after terminal punctuation remains unresolved", adjudicate(terminal_gap, punctuation_source + " Again.", [], entire_crop)["focus_outcomes"][0]["status"] == "unresolved_second_pass_mismatch")
    check("Terminal insertion at approximate excerpt tail remains ambiguous", adjudicate(terminal_gap, punctuation_source, [], {**entire_crop, "last_frame_exclusive": 2000})["focus_outcomes"][0]["status"] == "insufficient_context")
    outside = json.loads(json.dumps(fixture))
    outside["expected_reference"].update({"source_char_span": [end + 1, len(source)], "excerpt_text": source[end + 1:]})
    check("Focus outside fragment is not treated as an omission", adjudicate(outside, source[end + 1:], [], crop)["focus_outcomes"][0]["status"] == "insufficient_context")
    marked_source = source.replace("was ", "was <shortpause> ")
    marked = json.loads(json.dumps(fixture))
    marked_start = marked_source.index("not")
    marked["expected_reference"].update({"source_char_span": [0, len(marked_source)],
                                        "excerpt_text": marked_source.replace("<shortpause>", " "),
                                        "alignment_text": spoken_alignment(marked_source)})
    marked["expected_reference"]["focus_targets"][0]["source_char_span"] = [marked_start, marked_start + 3]
    check("Silent control removal preserves original source offsets for later focus", adjudicate(marked, source, [], crop)["focus_outcomes"][0]["status"] == "resolved_asr_artifact")
    for control in SILENT_CONTROLS:
        check(f"Exact silent control {control} is masked without coordinate drift", spoken_alignment(control) == " " * len(control))
    check("Plain story Silence and untagged short pause remain spoken words", spoken_alignment("Silence. A short pause followed.") == "Silence. A short pause followed.")
    check("Unlisted markup is not silently removed", spoken_alignment("<short  pause>") == "<short  pause>")
    prompt_fixture = {"decoder_prompt": {"initial_prompt": "Rob Xie, Lumen, Adrian Marsh", "contains_expected_passage": False}, "expected_reference": {"excerpt_text": source}}
    check("Decoder settings contain cast terms, never reference text", prompt_settings(prompt_fixture)["initial_prompt"] != source)
    try:
        prompt_settings({**prompt_fixture, "decoder_prompt": {"initial_prompt": source, "contains_expected_passage": False}})
    except RecheckError:
        rejected = True
    else:
        rejected = False
    check("Dishonestly labelled expected-passage prompt is rejected", rejected)
    long_source = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango"
    long_tokens = comparison.tokens(long_source)
    long_id = "c" * 64
    fragments = []
    for index, (lo, hi) in enumerate(((0, 12), (4, 16), (8, 20), (6, 18))):
        ca, cb = long_tokens[lo].start, long_tokens[hi - 1].end
        passage = long_source[ca:cb]
        piece = {"expected_reference": {"source_char_span": [ca, cb], "excerpt_text": passage,
                    "full_source_spoken_char_span": [0, len(long_source)], "focus_targets": [{"candidate_id": long_id,
                    "source_char_span": [0, len(long_source)], "expected_text": long_source, "asr_text": "initial omission"}]}}
        geometry = {"first_frame": lo * 1000, "last_frame_exclusive": hi * 1000, "source_frames": 20000,
                    "start_seconds": float(lo), "duration_seconds": float(hi - lo)}
        fragments.append({"excerpt_id": str(index), "clip_id": "fixture", "output": "isolated-fixture",
                          "cache_identity": {"audio_sha256": "d" * 64, "request_sha256": "e" * 64},
                          "adjudication": adjudicate(piece, passage, [], geometry)})
    partial_summary = aggregate(fragments, [long_id], [])
    check("Complete fragment token union stays ambiguous without a contiguous full-window proof",
          partial_summary["candidate_results"][0]["combined_fragment_coverage"]
          and partial_summary["unresolved_candidate_ids"] == [long_id]
          and not partial_summary["resolved_candidate_ids"])
    bad = json.loads(json.dumps(fragments[-1]))
    bad["adjudication"] = adjudicate(piece, passage.replace("kilo", "wrong"), [], geometry)
    evidence = fragments[:-1] + [bad]
    result = aggregate(evidence, [long_id], [])
    check("Complete coverage union cannot hide contradictory interior fragment", result["retake_candidate_ids"] == [long_id] and result["candidate_results"][0]["combined_fragment_coverage"])
    bad["adjudication"] = adjudicate(piece, passage.replace("kilo lima", "kilo really lima"), [], geometry)
    check("Interior addition in partial long target stays a conflict", aggregate(fragments[:-1] + [bad], [long_id], [])["retake_candidate_ids"] == [long_id])
    with tempfile.TemporaryDirectory(prefix="lumen-local-recheck-test-") as temporary:
        job = Path(temporary)
        (job / "raw").mkdir()
        wav = job / "raw" / "fixture.wav"
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(1000)
            audio.writeframes(b"\x01\x00" * 5000)
        item = {"id": "fixture", "model": "test", "voice": "Charon", "style": "cast terms", "text": source,
                "output": str(wav)}
        item["request_sha256"] = hashlib.sha256((item["model"] + item["voice"] + item["style"] + source).encode()).hexdigest()
        atomic(wav.with_suffix(".json"), {"id": "fixture", "audio_sha256": digest(wav), "request_sha256": item["request_sha256"]})
        excerpt = {**fixture, **prompt_fixture, "expected_reference": fixture["expected_reference"],
                   "excerpt_id": "b" * 64, "clip_id": "fixture", "source_wav": str(wav), "audio_sha256": digest(wav),
                   "request_sha256": item["request_sha256"], "start_seconds": 1.2345, "end_seconds": 3.3456,
                   "candidate_ids": [candidate]}
        _, planned_crop = bind_excerpt(excerpt, item, job)
        check("Crop uses outward integer frame boundaries", planned_crop["first_frame"] == 1234 and planned_crop["last_frame_exclusive"] == 3346)
        masked = json.loads(json.dumps(excerpt))
        masked["expected_reference"]["comparison_text"] = spoken_alignment(source)
        masked["expected_reference"]["focus_targets"][0]["expected_comparison_text"] = "not"
        check("Optional queue comparison fields validate against exact source mask", bind_excerpt(masked, item, job)[1] == planned_crop)
        masked["expected_reference"]["comparison_text"] = source.replace("not", "now")
        try:
            bind_excerpt(masked, item, job)
        except RecheckError:
            rejected_mask = True
        else:
            rejected_mask = False
        check("Changed comparison wording cannot hide behind an expected mask", rejected_mask)
        calls = []

        def mock_decoder(path, model, settings):
            calls.append(settings)
            with wave.open(str(path), "rb") as audio:
                check("Cropped WAV sample count and format match plan", audio.getnframes() == 2112 and audio.getsampwidth() == 2)
            return {"text": source, "segments": [{"start": 0.1, "end": 2.0, "text": source}]}

        model = {"path": str(job / "fake-existing-model"), "files_sha256": {"config": "fixture"}}
        before = digest(wav)
        result = run_one(excerpt, item, model, job, mock_decoder)
        cached = run_one(excerpt, item, model, job, mock_decoder)
        check("Hash-bound cache avoids duplicate local inference", len(calls) == 1 and cached["status"] == "cached")
        check("Expected reference never reaches decoder", all("expected_reference" not in settings and source != settings["initial_prompt"] for settings in calls))
        check("Raw source remains byte-identical", digest(wav) == before)
        check("Absolute audio anchors retain original clip offset", result["absolute_segments"][0]["start"] == planned_crop["start_seconds"] + .1)
        combined = aggregate([result], [candidate], [])
        check("Plain candidate aggregation retains resolved IDs and no auditory certification", combined["resolved_candidate_ids"] == [candidate] and not combined["listening_approval"])
        stale = dict(excerpt); stale["audio_sha256"] = "f" * 64
        try:
            run_one(stale, item, model, job, mock_decoder)
        except RecheckError:
            rejected = True
        else:
            rejected = False
        check("Stale queued audio hash is rejected before inference", rejected and len(calls) == 1)
        contradictory = json.loads(json.dumps(result))
        contradictory["adjudication"]["focus_outcomes"][0]["status"] = "unresolved_second_pass_mismatch"
        check("Conflicting excerpt evidence is never silently resolved", aggregate([result, contradictory], [candidate], [])["retake_candidate_ids"] == [candidate])
        marked_item = {**item, "text": marked_source}
        marked_item["request_sha256"] = hashlib.sha256((item["model"] + item["voice"] + item["style"] + marked_source).encode()).hexdigest()
        atomic(wav.with_suffix(".json"), {"id": "fixture", "audio_sha256": digest(wav), "request_sha256": marked_item["request_sha256"]})
        partial_marked = json.loads(json.dumps(excerpt))
        partial_marked["request_sha256"] = marked_item["request_sha256"]
        inner_start, inner_end = marked_source.index("shortpause"), marked_source.index("not") + 3
        partial_marked["expected_reference"].update({"source_char_span": [0, len(marked_source)], "excerpt_text": marked_source,
                                                     "comparison_text": spoken_alignment(marked_source)})
        partial_marked["expected_reference"]["focus_targets"][0].update({"source_char_span": [inner_start, inner_end],
                    "expected_text": marked_source[inner_start:inner_end],
                    "expected_comparison_text": spoken_alignment(marked_source)[inner_start:inner_end]})
        partial_result = run_one(partial_marked, marked_item, model, job, mock_decoder)
        check("A focus starting inside a silent tag uses global mask before slicing", partial_result["adjudication"]["focus_outcomes"][0]["status"] == "resolved_asr_artifact")
        # Structural c025001 regression: an82-word opening target needs0–34s
        # to retain its right source flanks. Expected prose is never a prompt.
        target_words = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango ".split() * 5)[:82]
        target = " ".join(target_words)
        prefix = "Chapter Twenty-Five.\n\n"
        diagnostic_source = prefix + target + " after ending."
        diagnostic_wav = job / "raw" / "long-fixture.wav"
        with wave.open(str(diagnostic_wav), "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(1000)
            audio.writeframes(b"\x01\x00" * 40000)
        diagnostic_item = {**item, "id": "long-fixture", "text": diagnostic_source, "output": str(diagnostic_wav)}
        diagnostic_item["request_sha256"] = hashlib.sha256((item["model"] + item["voice"] + item["style"] + diagnostic_source).encode()).hexdigest()
        atomic(diagnostic_wav.with_suffix(".json"), {"id": "long-fixture", "audio_sha256": digest(diagnostic_wav),
                                                    "request_sha256": diagnostic_item["request_sha256"]})
        diagnostic_excerpt = {**excerpt, "excerpt_id": "f" * 64, "clip_id": "long-fixture", "source_wav": str(diagnostic_wav),
                              "audio_sha256": digest(diagnostic_wav), "request_sha256": diagnostic_item["request_sha256"],
                              "start_seconds": 0.0, "end_seconds": 34.0, "diagnostic": {"kind": "complete_long_target", "max_seconds": 120},
                              "expected_reference": {"source_char_span": [0, len(diagnostic_source)], "excerpt_text": diagnostic_source,
                                  "comparison_text": diagnostic_source, "focus_targets": [{"candidate_id": candidate,
                                      "source_char_span": [len(prefix), len(prefix) + len(target)], "expected_text": target,
                                      "expected_comparison_text": target, "asr_text": "initial omission", "window_contains_target_anchor": True,
                                      "audio_anchor": {"start_seconds": 1.18, "end_seconds": 30.0}}]}}
        check("Declared bounded complete-target34s diagnostic passes source preflight", bind_excerpt(diagnostic_excerpt, diagnostic_item, job)[1]["frames"] == 34000)
        ordinary = dict(diagnostic_excerpt); ordinary.pop("diagnostic")
        try:
            bind_excerpt(ordinary, diagnostic_item, job)
        except RecheckError:
            ordinary_rejected = True
        else:
            ordinary_rejected = False
        check("A34s crop without explicit diagnostic declaration is rejected", ordinary_rejected)

        def mock_diagnostic(path, model, settings):
            check("Diagnostic decoder receives cast terms rather than expected target", settings["initial_prompt"] != diagnostic_source and "expected_reference" not in settings)
            return {"text": diagnostic_source, "segments": [{"start": 0.0, "end": 33.5, "text": diagnostic_source}]}

        diagnostic_result = run_one(diagnostic_excerpt, diagnostic_item, model, job, mock_diagnostic)
        check("Contiguous complete82-word target plus source flanks resolves in a single diagnostic",
              diagnostic_result["adjudication"]["focus_outcomes"][0]["status"] == "resolved_asr_artifact")
        incomplete = json.loads(json.dumps(diagnostic_excerpt))
        incomplete.pop("diagnostic")
        incomplete["end_seconds"] = 30.0
        incomplete["expected_reference"]["focus_targets"][0]["window_contains_target_anchor"] = False
        # Post-ASR expanded reference is allowed to retain words beyond a crop;
        # source prefix/suffix absent at the crop edges are not contradictions.
        full_reference = {**diagnostic_excerpt["expected_reference"], "full_source_spoken_char_span": [0, len(diagnostic_source)-1]}
        partial_focus = json.loads(json.dumps(full_reference["focus_targets"][0]))
        partial_focus["window_contains_target_anchor"] = False
        full_reference["focus_targets"] = [partial_focus]
        early_transcript = prefix + " ".join(target_words[:70])
        early_geometry = {"first_frame": 0, "last_frame_exclusive": 30000, "source_frames": 40000,
                          "start_seconds": 0.0, "duration_seconds": 30.0}
        early = adjudicate({"expected_reference": full_reference}, early_transcript, [], early_geometry)["focus_outcomes"][0]
        check("Inside but incomplete long-window reference retains local covered tokens", bool(early["covered_expected_token_indices"]) and early["status"] == "insufficient_context")
        tail_transcript = " ".join(target_words[60:]) + " after ending."
        tail_geometry = {"first_frame": 26000, "last_frame_exclusive": 34000, "source_frames": 40000,
                         "start_seconds": 26.0, "duration_seconds": 8.0}
        late = adjudicate({"expected_reference": full_reference}, tail_transcript, [], tail_geometry)["focus_outcomes"][0]
        check("Expanded late-fragment reference can recover tail target coverage", bool(late["covered_expected_token_indices"]) and late["status"] == "insufficient_context")
        check("Missing reference prefix at crop edge is not an interior contradiction", not late["interior_target_contradictions"])
        backend_fixture = {"text": source, "segments": [{"start": .1, "end": 2.0, "text": source,
            "avg_logprob": float("nan"), "no_speech_prob": float("inf"), "compression_ratio": float("-inf"),
            "words": [{"word": " He", "start": .1, "end": .3, "probability": float("nan")},
                      {"word": " said", "start": .3, "end": .5, "probability": .9}]}]}
        clean, paths = comparison.sanitize_backend_confidence(backend_fixture)
        check("Only optional nonfinite confidence is explicitly nulled with precise warning paths",
              len(paths) == 4 and clean["segments"][0]["avg_logprob"] is None
              and clean["segments"][0]["words"][0]["probability"] is None
              and "result.segments[0].words[0].probability" in paths)
        check("Confidence sanitization preserves transcript timing and finite probability",
              clean["text"] == source and clean["segments"][0]["start"] == .1
              and clean["segments"][0]["words"][0]["end"] == .3
              and clean["segments"][0]["words"][1]["probability"] == .9)
        check("Confidence sanitization leaves the original backend object unchanged", math.isnan(backend_fixture["segments"][0]["avg_logprob"]))
        check("Sanitized optional confidence serializes as strict JSON", bool(json.dumps(clean, allow_nan=False)))
        rejected_required = 0
        for required in ("start", "end", "temperature", "text"):
            try:
                comparison.sanitize_backend_confidence({required: float("nan")})
            except ValueError:
                rejected_required += 1
        check("Nonfinite timing and other required fields fail closed", rejected_required == 4)
        confidence_excerpt = json.loads(json.dumps(diagnostic_excerpt))
        confidence_excerpt["excerpt_id"] = "1" * 64

        def optional_confidence_decoder(path, model, settings):
            return {"text": diagnostic_source, "segments": [{"start": 0.0, "end": 33.5, "text": diagnostic_source,
                    "avg_logprob": float("nan"), "no_speech_prob": .01, "compression_ratio": float("inf")}]} 

        confidence_result = run_one(confidence_excerpt, diagnostic_item, model, job, optional_confidence_decoder)
        check("Executor retains optional-confidence warning paths in hash-bound cache",
              confidence_result["backend_nonfinite_confidence_fields"] == ["result.segments[0].avg_logprob", "result.segments[0].compression_ratio"]
              and confidence_result["segments"][0]["avg_logprob"] is None
              and confidence_result["absolute_segments"][0]["start"] == 0.0)
        cached_confidence = run_one(confidence_excerpt, diagnostic_item, model, job, optional_confidence_decoder)
        check("Confidence-cleaned transcript evidence remains cache-resumable", cached_confidence["status"] == "cached")
        rejected_excerpt = json.loads(json.dumps(diagnostic_excerpt)); rejected_excerpt["excerpt_id"] = "2" * 64

        def invalid_timing_decoder(path, model, settings):
            return {"text": diagnostic_source, "segments": [{"start": float("nan"), "end": 33.5, "text": diagnostic_source}]}

        try:
            run_one(rejected_excerpt, diagnostic_item, model, job, invalid_timing_decoder)
        except ValueError:
            invalid_timing_rejected = True
        else:
            invalid_timing_rejected = False
        check("Executor never saves or substitutes a nonfinite segment time", invalid_timing_rejected
              and not list((job / "local-checks" / "rechecks" / "cache").glob("2" * 64 + ".*.json")))
        worker = job / "local-checks" / ".worker.lock"
        with worker.open("a+") as first_lock, worker.open("a+") as second_lock:
            fcntl.flock(first_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.flock(second_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                blocked = True
            else:
                blocked = False
            check("Exclusive worker lock prevents concurrent GPU worker", blocked)
    report = {"passed": True, "checks": checks, "scope": SCOPE, "created_at_unix": time.time()}
    atomic(JOB / "recheck-audio-local-self-test.json", report)
    print(json.dumps({"self_test_passed": True, "checks": len(checks)}), flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--priority", choices=("high", "medium", "low"))
    parser.add_argument("--queue", type=Path, default=JOB / "qa-review" / "recheck-queue.json")
    parser.add_argument("--model", type=Path, default=JOB / "asr-model")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if not args.all and not args.priority:
            parser.error("Choose --all or --priority high/medium/low")
        summary = execute(JOB, args.queue.resolve(), args.model, args.priority)
        return 0 if summary["complete_selected_queue"] else 1
    except (RecheckError, OSError, ValueError, KeyError, TypeError, wave.Error, EOFError, RuntimeError) as exc:
        print(f"Local recheck stopped: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
