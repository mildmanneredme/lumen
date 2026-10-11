#!/usr/bin/env python3
"""Measure missing painting onsets without estimating sentence ends.

The manuscript, narration, baseline timings and ASR caches are preserved.
Optional recognition uses only the installed offline medium model, without a
manuscript prompt. Automatic cues need an exact three-to-six-token prefix
unique across the chapter and inside the baseline measured-neighbor window.
An explicit, source-bound narrative review may instead select the measured end
of a unique canonical phrase within the unavailable sentence.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import book_content as content
import finalize_release_timings as finalize
import prepare_book_audio as audio


METHOD = "unique-measured-ASR-prefix"
PHRASE_METHOD = "unique-measured-ASR-phrase-end"
PREFIX_LENGTH = 6
MIN_PREFIX_LENGTH = 3
PHRASE_REQUEST_FIELDS = {"schemaVersion", "kind", "trackId", "narratorId", "sceneId",
                        "sourceManifestPath", "sourceManifestSha256", "sourceSha256", "phoneSha256",
                        "sentenceId", "sentenceTextSha256", "tokenStart", "tokenEnd", "boundary", "review"}


def reviewed_phrase_hash(request):
    return content.object_hash({key: value for key, value in request.items() if key != "review"})


def checked_phrase_request(root, track, recording, baseline, reference):
    """Read an explicit narrative choice, bound to canonical prose and selected bytes."""
    content.require(isinstance(reference, dict) and set(reference) == {"path", "sha256"}
                    and isinstance(reference["path"], str) and not Path(reference["path"]).is_absolute()
                    and ".." not in Path(reference["path"]).parts and content.valid_hash(reference["sha256"]),
                    "Reviewed scene phrase requires a contained hash-bound request")
    path = content.project_file(root, reference["path"])
    public = Path(root).resolve() / "web/dist"
    content.require(path != public and public not in path.parents
                    and content.file_hash(path) == reference["sha256"], "Reviewed scene phrase request bytes changed")
    request = content.read_project_json(root, path)
    content.require(isinstance(request, dict) and set(request) == PHRASE_REQUEST_FIELDS
                    and type(request["schemaVersion"]) is int and request["schemaVersion"] == 1
                    and request["kind"] == "reviewed-scene-phrase-end" and request["boundary"] == "end"
                    and request["trackId"] == track["id"] and request["narratorId"] == recording["narratorId"]
                    and isinstance(request["sceneId"], str) and bool(request["sceneId"].strip()),
                    "Reviewed scene phrase identity differs")
    canonical = content.sentences(track)
    sentence = next((row for row in canonical if row["id"] == request["sentenceId"]), None)
    content.require(sentence is not None and hashlib.sha256(sentence["text"].encode("utf-8")).hexdigest()
                    == request["sentenceTextSha256"], "Reviewed scene phrase canonical text differs")
    cue = next((row for row in baseline["sentences"] if row["sentenceId"] == request["sentenceId"]), None)
    content.require(cue is not None and cue.get("syncStatus") == "unavailable",
                    "Reviewed scene phrase can only target an unavailable sentence")
    first, last = request["tokenStart"], request["tokenEnd"]
    local = [token for token, _, _ in audio.token_spans(content.spoken_comparison(sentence["text"]))]
    content.require(type(first) is int and type(last) is int and 0 <= first < last <= len(local)
                    and MIN_PREFIX_LENGTH <= last - first <= PREFIX_LENGTH,
                    "Reviewed scene phrase requires an exact three-to-six-token span within its sentence")
    review = request["review"]
    content.require(isinstance(review, dict) and review.get("status") == "accepted-with-note"
                    and isinstance(review.get("reviewer"), str) and review["reviewer"].strip()
                    and isinstance(review.get("note"), str) and review["note"].strip()
                    and review.get("requestSha256") == reviewed_phrase_hash(request),
                    "Reviewed scene phrase requires a current hash-bound narrative note")
    try:
        reviewed = datetime.fromisoformat(review.get("reviewedAt", "").replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise content.ContentError("Reviewed scene phrase requires a review timestamp") from exc
    content.require(reviewed.tzinfo is not None, "Reviewed scene phrase review requires a timezone")
    manifest_path = request["sourceManifestPath"]
    content.require(isinstance(manifest_path, str) and not Path(manifest_path).is_absolute()
                    and ".." not in Path(manifest_path).parts and content.valid_hash(request["sourceManifestSha256"])
                    and content.project_file_hash(root, manifest_path) == request["sourceManifestSha256"],
                    "Reviewed scene phrase selected manifest bytes changed")
    manifest = content.read_project_json(root, manifest_path)
    content.require(isinstance(manifest, dict) and isinstance(manifest.get("scenes"), list)
                    and all(isinstance(row, dict) for row in manifest["scenes"]),
                    "Reviewed scene phrase selected manifest is invalid")
    selected = [row for row in manifest.get("scenes", []) if row.get("id") == request["sceneId"]]
    content.require(manifest.get("chapterId") == track["id"] and len(selected) == 1,
                    "Reviewed scene phrase painting is absent or duplicated in selected manifest")
    source = selected[0]
    phone = source.get("phone")
    content.require(phone is None or isinstance(phone, dict),
                    "Reviewed scene phrase selected phone binding is invalid")
    content.require(source.get("trackId") == track["id"] and source.get("sentenceId") == request["sentenceId"]
                    and source.get("sentenceTextSha256") == request["sentenceTextSha256"]
                    and source.get("sha256") == request["sourceSha256"]
                    and (phone.get("sha256") if phone is not None else None) == request["phoneSha256"],
                    "Reviewed scene phrase selected painting binding differs")
    images = [(source.get("sourcePath"), request["sourceSha256"])]
    if phone is not None:
        images.append((phone.get("path"), request["phoneSha256"]))
    for image_path, digest in images:
        content.require(isinstance(image_path, str) and content.valid_hash(digest),
                        "Reviewed scene phrase requires each selected image path and hash")
        image = content.project_file(root, image_path)
        content.require(image.suffix.lower() == ".webp" and content.file_hash(image) == digest,
                        "Reviewed scene phrase selected image bytes changed")
    return request


def measured_phrase_cue(track, baseline, words, request, reference):
    """Measure only an explicitly reviewed phrase endpoint; never a prose clock."""
    canonical = content.sentences(track)
    flattened, sentence_first = [], None
    for sentence in canonical:
        if sentence["id"] == request["sentenceId"]:
            sentence_first = len(flattened)
        flattened.extend(token for token, _, _ in audio.token_spans(content.spoken_comparison(sentence["text"])))
    first = sentence_first + request["tokenStart"]
    length = request["tokenEnd"] - request["tokenStart"]
    phrase = flattened[first:first + length]
    options = [position for position in range(len(flattened) - length + 1)
               if flattened[position:position + length] == phrase]
    content.require(options == [first], "Reviewed scene phrase is not unique across its canonical chapter")
    heard, times = token_clock(words, baseline["duration"])
    window = next(row for row in baseline["sentences"] if row["sentenceId"] == request["sentenceId"])["recheckWindow"]
    observed = [position for position in range(len(heard) - length + 1)
                if heard[position:position + length] == phrase
                and all(window["start"] <= start < end <= window["end"]
                        and coverage_end <= window["end"] and coverage_end <= times[position + length - 1][1]
                        for start, end, coverage_end in times[position:position + length])]
    if len(observed) != 1:
        return None, {"sentenceId": request["sentenceId"],
                      "reason": "missing-exact-reviewed-phrase" if not observed else "ambiguous-observed-reviewed-phrase"}
    position = observed[0]
    end = times[position + length - 1][1]
    content.require(end < baseline["duration"], "Reviewed scene phrase ends after the last usable reveal clock")
    return {"sentenceId": request["sentenceId"], "sceneId": request["sceneId"],
            "sourceSha256": request["sourceSha256"], "phoneSha256": request["phoneSha256"],
            "start": end, "method": PHRASE_METHOD, "phraseTokens": phrase,
            "tokenStart": request["tokenStart"], "tokenEnd": request["tokenEnd"], "boundary": "end",
            "observedTokenIndex": position, "phraseStart": times[position][0], "phraseEnd": end,
            "requestPath": reference["path"], "requestSha256": reference["sha256"]}, None


def retain_monotone_cues(cues, unavailable, target_ids):
    order = {sentence_id: index for index, sentence_id in enumerate(target_ids)}
    cues = sorted(cues, key=lambda cue: order[cue["sentenceId"]])
    conflicts = set()
    while True:
        current = set()
        for before, after in zip(cues, cues[1:]):
            if after["start"] <= before["start"]:
                current.update([before["sentenceId"], after["sentenceId"]])
        if not current:
            break
        conflicts.update(current)
        cues = [cue for cue in cues if cue["sentenceId"] not in current]
    unavailable = [gap for gap in unavailable if gap["sentenceId"] not in conflicts]
    unavailable.extend({"sentenceId": sentence_id, "reason": "nonmonotone-measured-scene-onsets"}
                       for sentence_id in target_ids if sentence_id in conflicts)
    unavailable.sort(key=lambda gap: order[gap["sentenceId"]])
    return cues, unavailable


def token_clock(words, duration):
    pieces, spans, cursor, previous = [], [], 0, 0.0
    for word, start, end in words:
        content.require(isinstance(word, str) and content.finite_number(start) and content.finite_number(end)
                        and previous <= start < end <= duration, "Scene word clock is unordered or outside recording")
        pieces.append(word + " ")
        spans.append((cursor, cursor + len(word), start, end))
        cursor += len(word) + 1
        previous = start
    starts = [span[0] for span in spans]
    tokens, times = [], []
    for token, first, last in audio.token_spans("".join(pieces)):
        left = max(0, bisect_right(starts, first) - 1)
        right = max(left, bisect_left(starts, last) - 1)
        tokens.append(token)
        # Numeric normalization can merge several spoken words into one token.
        # Keep its actual final-word endpoint separate from all contributing
        # measured coverage, so an earlier word's overrun cannot disappear.
        times.append((spans[left][2], spans[right][3], max(span[3] for span in spans[left:right + 1])))
    return tokens, times


def measured_prefix_cues(track, baseline, words, target_ids):
    """Reproduce exact onsets; a missing sentence end remains unavailable."""
    canonical = content.sentences(track)
    ids = [sentence["id"] for sentence in canonical]
    content.require(isinstance(target_ids, list) and bool(target_ids)
                    and all(isinstance(value, str) and value in ids for value in target_ids)
                    and target_ids == [value for value in ids if value in target_ids],
                    "Scene cue targets are missing, duplicated, unknown or out of canonical order")
    cues = {cue["sentenceId"]: cue for cue in baseline["sentences"]}
    tokens, offsets = [], []
    for sentence in canonical:
        first = len(tokens)
        tokens.extend(token for token, _, _ in audio.token_spans(content.spoken_comparison(sentence["text"])))
        offsets.append((first, len(tokens)))
    heard, times = token_clock(words, baseline["duration"])
    measured, unavailable = [], []
    indices = {sentence_id: index for index, sentence_id in enumerate(ids)}
    for sentence_id in target_ids:
        cue = cues[sentence_id]
        content.require(cue.get("syncStatus") == "unavailable", "Scene fallback can only target unavailable sentences")
        window = cue["recheckWindow"]
        index = indices[sentence_id]
        first, last = offsets[index]
        maximum = min(PREFIX_LENGTH, len(tokens) - first)
        if maximum < MIN_PREFIX_LENGTH:
            unavailable.append({"sentenceId": sentence_id, "reason": "insufficient-exact-prefix-context"})
            continue
        after = window["afterSentenceId"]
        reason, found = "ambiguous-canonical-prefix", None
        # Prefer the longest exact prefix. Shorter prefixes require unique
        # occurrence across the entire chapter, not just this recheck gap.
        for length in range(maximum, MIN_PREFIX_LENGTH - 1, -1):
            prefix = tokens[first:first + length]
            canonical_options = [position for position in range(len(tokens) - length + 1)
                                 if tokens[position:position + length] == prefix]
            if canonical_options != [first]:
                continue
            # Short sentences need following canonical speech as corroboration;
            # the onset remains inside its original neighbor gap. The next
            # sentence's clock never substitutes for the target's first word.
            support_end = cues[after]["end"] if last - first < length and after else window["end"]
            options = [position for position in range(len(heard) - length + 1)
                       if heard[position:position + length] == prefix
                       and window["start"] - 1e-6 <= times[position][0] < window["end"]
                       and all(window["start"] - 1e-6 <= start < end
                               and coverage_end <= (window["end"] if offset < last - first else support_end) + 1e-6
                               and coverage_end <= times[position + length - 1][1] + 1e-6
                               for offset, (start, end, coverage_end)
                               in enumerate(times[position:position + length]))]
            if len(options) != 1:
                reason = "missing-exact-prefix" if not options else "ambiguous-observed-prefix"
                continue
            observed = options[0]
            found = {"sentenceId": sentence_id, "start": times[observed][0], "method": METHOD,
                     "prefixTokens": prefix, "observedTokenIndex": observed,
                     "prefixEnd": times[observed + length - 1][1]}
            break
        if found is None:
            unavailable.append({"sentenceId": sentence_id, "reason": reason})
        else:
            measured.append(found)
    return retain_monotone_cues(measured, unavailable, target_ids)


def scene_clock_map(track, recording, baseline, words, targets, evidence, baseline_path, baseline_hash,
                    baseline_words=None, reviewed_phrase_requests=None, root=None):
    cues, unavailable = measured_prefix_cues(track, baseline, words, targets)
    cues = [dict(cue, wordSource="selected-ASR") for cue in cues]
    if baseline_words is not None and unavailable:
        missing_ids = {gap["sentenceId"] for gap in unavailable}
        original, _ = measured_prefix_cues(track, baseline, baseline_words,
                                          [value for value in targets if value in missing_ids])
        cues.extend(dict(cue, wordSource="baseline-ASR") for cue in original)
        recovered_ids = {cue["sentenceId"] for cue in original}
        unavailable = [gap for gap in unavailable if gap["sentenceId"] not in recovered_ids]
    if reviewed_phrase_requests is not None:
        content.require(isinstance(reviewed_phrase_requests, list) and bool(reviewed_phrase_requests) and root is not None,
                        "Reviewed scene phrases require explicit private request references")
        requested = set()
        for reference in reviewed_phrase_requests:
            request = checked_phrase_request(root, track, recording, baseline, reference)
            sentence_id = request["sentenceId"]
            content.require(sentence_id in targets and sentence_id not in requested
                            and all(cue["sentenceId"] != sentence_id for cue in cues),
                            "Reviewed scene phrase must target a distinct unresolved scene anchor")
            requested.add(sentence_id)
            cue, gap = measured_phrase_cue(track, baseline, words, request, reference)
            word_source = "selected-ASR"
            if cue is None and baseline_words is not None:
                cue, gap = measured_phrase_cue(track, baseline, baseline_words, request, reference)
                word_source = "baseline-ASR"
            unavailable = [row for row in unavailable if row["sentenceId"] != sentence_id]
            if cue is not None:
                cues.append(dict(cue, wordSource=word_source))
            else:
                unavailable.append(gap)
    cues, unavailable = retain_monotone_cues(cues, unavailable, targets)
    result = {"schemaVersion": 1, "kind": "measured-scene-cues", "trackId": track["id"],
            "narratorId": recording["narratorId"], "manuscriptVersion": track["manuscriptVersion"],
            "textSha256": track["textSha256"], "audioSha256": recording["sha256"],
            "duration": recording["decodedDuration"], "baselineTimingPath": baseline_path,
            "baselineTimingSha256": baseline_hash, "targetSentenceIds": list(targets),
            "evidence": evidence, "sourceBindings": copy.deepcopy(baseline["sourceBindings"]),
            "cues": cues, "unavailable": unavailable}
    if reviewed_phrase_requests is not None:
        result["reviewedPhraseRequests"] = copy.deepcopy(reviewed_phrase_requests)
    return result


def checked_scene_words(root, track, recording, baseline, evidence):
    """Rebuild refined cache words from retained, hash-bound recognizer output."""
    prior_by_id = {row["id"]: row for row in baseline["evidence"]}
    qa = content.read_project_json(root, Path(recording["sourcePath"]).with_suffix(".qa.json"))
    selected = {item["id"]: item for item in content.read_project_json(
        root, Path("Audiobook") / recording["productionEdition"] / "generation-manifest.json")["items"]}
    for row, clip in zip(evidence, recording["selectedClips"]):
        prior = prior_by_id[clip["id"]]
        if row == prior:
            continue
        path = content.project_file(root, row["sourcePath"])
        content.require(content.file_hash(path) == row["cacheSha256"], "Scene recognizer cache bytes changed")
        record = content.read_project_json(root, path)
        lineage = record.get("cache_identity", {}).get("sceneRefinement", {})
        content.require(lineage.get("method") == METHOD and lineage.get("baseCacheSha256") == prior["cacheSha256"],
                        "Scene recognition refinement lineage differs")
        base = content.read_project_json(root, prior["sourcePath"])
        model = lineage.get("modelIdentity", {})
        model_path = root / "Audiobook/v6/asr-model-medium"
        content.require(model.get("path") == str(model_path) and isinstance(model.get("files_sha256"), dict)
                        and set(model["files_sha256"]) in [{"config.json", "weights.npz"},
                                                           {"config.json", "weights.safetensors"}],
                        "Scene recognition requires the existing offline medium model")
        for name, digest in model["files_sha256"].items():
            content.require(content.valid_hash(digest) and content.project_file_hash(root, model_path / name) == digest,
                            "Scene recognition model bytes changed")
        outcomes = lineage.get("recognitionEvidence")
        content.require(isinstance(outcomes, list) and bool(outcomes)
                        and len(outcomes) == len(lineage.get("decodeCalls", [])), "Missing scene recognizer output evidence")
        content.require(lineage.get("windows") == [[row.get("start"), row.get("end")] for row in outcomes]
                        and all(content.finite_number(row.get("start")) and content.finite_number(row.get("end"))
                                and 0 <= row["start"] < row["end"] <= base["duration_seconds"] for row in outcomes),
                        "Scene recognizer recheck window is invalid or outside the raw recording")
        refined = []
        for outcome, call in zip(outcomes, lineage["decodeCalls"]):
            content.require(set(call) == {"language", "temperature", "condition_on_previous_text", "verbose",
                                          "word_timestamps", "clip_timestamps"}
                            and call["language"] == "en" and call["temperature"] == 0.0
                            and call["condition_on_previous_text"] is False and call["verbose"] is None
                            and call["word_timestamps"] is True
                            and call["clip_timestamps"] == f"{outcome['start']},{outcome['end']}"
                            and outcome.get("status") == "measured"
                            and outcome.get("modelSha256") == model["files_sha256"],
                            "Scene recognition settings/clock evidence differ")
            result_path = content.project_file(root, outcome["resultPath"])
            content.require(content.file_hash(result_path) == outcome["resultSha256"],
                            "Scene recognizer output bytes changed")
            result_record = content.read_project_json(root, result_path)
            identity = {"rawAudioSha256": clip["rawAudioSha256"], "baseCacheSha256": prior["cacheSha256"],
                        "modelIdentity": model, "decodeSettings": call}
            content.require(result_record.get("identity") == identity, "Scene recognizer output source identity differs")
            candidate = dict(record, segments=result_record["result"].get("segments", []))
            audio.cache_words(candidate, selected[clip["id"]], clip, qa)
            refined.extend(word for segment in candidate["segments"] for word in segment.get("words", []))
        original = [word for segment in base.get("segments", []) for word in segment.get("words", [])]
        merged = audio.merge_refinement_words(original, refined,
                    [(outcome["start"], outcome["end"]) for outcome in outcomes])
        content.require(record.get("segments") == [{"words": merged}],
                        "Scene cache differs from retained measured recognizer output")
        content.require(record.get("cache_identity") == dict(base["cache_identity"], sceneRefinement=lineage)
                        and record.get("refinementAttempts") == base.get("refinementAttempts", []) + outcomes,
                        "Scene refinement changed baseline recognition identity or provenance")
    return finalize.checked_cache_words(root, track, recording, evidence, baseline["sourceBindings"])


def verify_scene_clock(root, track, recording, baseline_timing, scene_map):
    """Freshly verify audio/source/cache bytes and reproduce every fallback cue."""
    root = Path(root).resolve()
    content.require(isinstance(scene_map, dict) and type(scene_map.get("schemaVersion")) is int
                    and scene_map.get("schemaVersion") == 1
                    and scene_map.get("kind") == "measured-scene-cues", "Invalid measured scene clock identity")
    path = scene_map.get("baselineTimingPath")
    content.require(isinstance(path, str) and not Path(path).is_absolute() and ".." not in Path(path).parts,
                    "Scene baseline must use a contained private project path")
    content.require(content.project_file_hash(root, path) == scene_map.get("baselineTimingSha256")
                    and content.read_project_json(root, path) == baseline_timing,
                    "Measured scene clock baseline bytes differ")
    verified, _ = finalize.verify_technical_alignment(root, track, recording, baseline_timing)
    content.require(scene_map.get("sourceBindings") == baseline_timing.get("sourceBindings"),
                    "Measured scene clock mastering sources differ")
    evidence = scene_map.get("evidence")
    content.require(isinstance(evidence, list) and len(evidence) == len(verified["selectedClips"]),
                    "Measured scene clock selected source evidence is incomplete")
    words = checked_scene_words(root, track, verified, baseline_timing, evidence)
    baseline_words = finalize.checked_cache_words(root, track, verified, baseline_timing["evidence"],
                                                  baseline_timing["sourceBindings"])
    rebuilt = scene_clock_map(track, verified, baseline_timing, words, scene_map.get("targetSentenceIds"),
                              scene_map["evidence"], path, scene_map["baselineTimingSha256"], baseline_words,
                              scene_map.get("reviewedPhraseRequests"), root)
    content.require(content.object_hash(scene_map) == content.object_hash(rebuilt),
                    "Measured scene clock differs from reproducible word evidence")
    return verified, {"status": "verified", "method": METHOD, "audioSha256": verified["sha256"],
                      "sceneClockSha256": content.object_hash(rebuilt), "measuredCues": len(rebuilt["cues"]),
                      "unavailableCues": len(rebuilt["unavailable"]), "humanAlignmentApproval": "not-claimed"}


def private_output(root, output):
    root = Path(root).resolve()
    output = Path(output)
    output = (output if output.is_absolute() else root / output).resolve()
    public = (root / "web/dist").resolve()
    content.require(root in output.parents and output != public and public not in output.parents,
                    "Scene clock output must be private inside the project outside web/dist")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    return output


def contained_recognition_root(root, requested):
    root = Path(root).resolve()
    requested = Path(requested)
    try:
        directory = (requested if requested.is_absolute() else root / requested).resolve()
        public = (root / "web/dist").resolve()
        content.require(root in directory.parents and directory.is_dir()
                        and directory != public and public not in directory.parents,
                        "Retained recognition results must remain private inside the project")
    except (OSError, RuntimeError) as exc:
        raise content.ContentError(f"Cannot resolve private retained recognition results: {exc}") from exc
    return directory


def evidence_row(root, path, record, clip):
    identity = record["cache_identity"]
    return {"id": clip["id"], "sourcePath": path.relative_to(root).as_posix(),
            "cacheSha256": content.file_hash(path), "decoderIdentitySha256": content.object_hash(identity),
            "rawAudioSha256": clip["rawAudioSha256"], "requestSha256": clip["requestSha256"],
            "expectedTextSha256": clip["textSha256"], "refinementAttempts": record.get("refinementAttempts", []),
            "refinementFailures": record.get("refinementFailures", [])}


def refine_recording(root, output, track, recording, baseline, targets, model_identity, transcriber,
                     max_windows=None, recognition_root=None):
    qa = content.read_project_json(root, Path(recording["sourcePath"]).with_suffix(".qa.json"))
    selected = {item["id"]: item for item in content.read_project_json(
        root, Path("Audiobook") / recording["productionEdition"] / "generation-manifest.json")["items"]}
    cues = {cue["sentenceId"]: cue for cue in baseline["sentences"]}
    intervals = [cues[sentence_id]["recheckWindow"] for sentence_id in targets]
    windows = audio.refinement_windows(qa, intervals, context=3.0)
    evidence, events, used = [], [], 0
    for prior, clip in zip(baseline["evidence"], recording["selectedClips"]):
        base_path = content.project_file(root, prior["sourcePath"])
        content.require(content.file_hash(base_path) == prior["cacheSha256"], "Baseline recognizer output bytes changed")
        base = content.read_project_json(root, base_path)
        item = selected[clip["id"]]
        raw_path = content.project_file(root, clip["sourcePath"])
        content.require(content.file_hash(raw_path) == clip["rawAudioSha256"], "Scene recognizer raw recording bytes changed")
        needed = windows.get(clip["id"], [])
        if max_windows is not None:
            needed = needed[:max(0, max_windows - used)]
        if not needed:
            evidence.append(copy.deepcopy(prior))
            continue
        settings = {"language": "en", "temperature": 0.0, "condition_on_previous_text": False,
                    "verbose": None, "word_timestamps": True}
        calls, refined, outcomes, warnings = [], [], [], []
        for start, end in needed:
            call = dict(settings, clip_timestamps=f"{start},{end}")
            identity = {"rawAudioSha256": clip["rawAudioSha256"], "baseCacheSha256": prior["cacheSha256"],
                        "modelIdentity": model_identity, "decodeSettings": call}
            cache_asset = content.export_asset(output, track["id"], "scene-recognition-request", identity)
            cache_name = "scene-recognition-result." + cache_asset["sha256"] + ".json"
            cached = (recognition_root or output) / "tracks" / track["id"] / cache_name
            started = time.monotonic()
            if cached.is_file():
                result_record = content.read_project_json(root, cached)
                content.require(result_record.get("identity") == identity, "Scene recognition cache identity differs")
                result = result_record["result"]
                call_warnings = result_record.get("warnings", [])
            else:
                result = transcriber(str(raw_path), path_or_hf_repo=model_identity["path"], **call)
                result, call_warnings = audio.COMPARISON.sanitize_backend_confidence(result)
                result = json.loads(content.json_bytes(result))
                result_record = {"identity": identity, "result": result, "warnings": call_warnings}
                result_asset = content.export_asset(output, track["id"], "scene-recognition-result", result_record)
                cached = output / "tracks" / track["id"] / cache_name
                # Immutable request->result index. All recognition bytes also
                # retain their independently content-addressed result file.
                try:
                    os.link(output / result_asset["path"], cached, follow_symlinks=False)
                except FileExistsError:
                    content.require(content.read_project_json(root, cached) == result_record, "Concurrent ASR result differs")
            candidate = dict(base, segments=result.get("segments", []))
            try:
                audio.cache_words(candidate, item, clip, qa)
            except (content.ContentError, KeyError, TypeError, ValueError) as exc:
                events.append({"clipId": clip["id"], "start": start, "end": end,
                               "status": "rejected-invalid-clock", "detail": str(exc)[:300]})
                used += 1
                continue
            refined.extend(word for segment in candidate["segments"] for word in segment.get("words", []))
            calls.append(call)
            outcomes.append({"start": start, "end": end, "modelSha256": model_identity["files_sha256"],
                             "status": "measured", "resultPath": cached.relative_to(root).as_posix(),
                             "resultSha256": content.file_hash(cached)})
            warnings.extend(call_warnings)
            used += 1
            event = {"trackId": track["id"], "narratorId": recording["narratorId"], "clipId": clip["id"],
                     "start": start, "end": end, "status": "measured", "seconds": round(time.monotonic() - started, 2)}
            events.append(event)
            print(json.dumps(event), flush=True)
        content.require(content.file_hash(raw_path) == clip["rawAudioSha256"], "Raw recording changed during scene recognition")
        if not outcomes:
            evidence.append(copy.deepcopy(prior))
            continue
        original = [word for segment in base.get("segments", []) for word in segment.get("words", [])]
        merged = audio.merge_refinement_words(original, refined, [(row["start"], row["end"]) for row in outcomes])
        lineage = {"baseCacheSha256": prior["cacheSha256"], "modelIdentity": model_identity,
                   "decodeCalls": calls, "windows": [[row["start"], row["end"]] for row in outcomes],
                   "method": METHOD, "recognitionEvidence": outcomes}
        identity = dict(base["cache_identity"], sceneRefinement=lineage)
        record = dict(base, cache_identity=identity, cache_key=content.object_hash(identity),
                      refinementAttempts=base.get("refinementAttempts", []) + outcomes,
                      segments=[{"words": merged}], backend_nonfinite_confidence_fields=warnings)
        audio.cache_words(record, item, clip, qa)
        asset = content.export_asset(output, track["id"], "scene-asr-" + recording["narratorId"], record)
        evidence.append(evidence_row(root, output / asset["path"], record, clip))
    return evidence, events, used


def targets_from_audit(registry, timing_maps, audit):
    targets = {}
    for row in audit["tracks"]:
        for scene in row["scenes"]:
            for narrator, clock in scene["narrators"].items():
                if clock["status"] == "blocked":
                    targets.setdefault((row["trackId"], narrator), set()).add(scene["sentenceId"])
    ordered = {}
    for track in registry["tracks"]:
        for narrator in sorted(timing_maps.get(track["id"], {})):
            wanted = targets.get((track["id"], narrator), set())
            if wanted:
                ordered[(track["id"], narrator)] = [sentence["id"] for sentence in content.sentences(track)
                                                     if sentence["id"] in wanted]
    content.require(sum(len(values) for values in ordered.values()) == sum(len(values) for values in targets.values()),
                    "Scene audit targets do not match canonical recordings")
    return ordered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    for name in ["registry", "inventory", "timings", "audit", "out"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--transcribe", action="store_true", help="Use the installed offline medium model without text prompt")
    parser.add_argument("--reuse-recognition", action="store_true", help="Rebuild measured cues from existing bounded results without inference")
    parser.add_argument("--recognition-root", type=Path, help="Owned private directory containing retained recognition results")
    parser.add_argument("--max-windows", type=int, help="Limit measured ASR windows for an initial probe")
    parser.add_argument("--max-recordings", type=int, help="Limit selected recordings for an initial probe")
    args = parser.parse_args()
    root, output = args.root.resolve(), private_output(args.root, args.out)
    recognition_root = None
    if args.recognition_root is not None:
        recognition_root = contained_recognition_root(root, args.recognition_root)
    registry, inventory, maps, audit = [content.read_project_json(root, path)
                                       for path in [args.registry, args.inventory, args.timings, args.audit]]
    content.validate_registry(registry)
    content.require(args.max_windows is None or args.max_windows > 0, "Window limit must be positive")
    import release_export
    recordings = release_export.current_inventory(root, registry, inventory)
    tracks = {track["id"]: track for track in registry["tracks"]}
    targets = targets_from_audit(registry, maps, audit)
    if args.max_recordings is not None:
        content.require(args.max_recordings > 0, "Recording limit must be positive")
        targets = dict(list(targets.items())[:args.max_recordings])
    transcriber, model_identity = None, None
    content.require(not (args.transcribe and args.reuse_recognition), "Choose recognition or cache-only rebuild")
    if args.transcribe or args.reuse_recognition:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        if args.transcribe:
            import mlx_whisper
            transcriber = mlx_whisper.transcribe
        else:
            def transcriber(*_args, **_kwargs):
                raise content.ContentError("Missing bounded scene recognition result; cache-only rebuild cannot infer it")
        model_identity = audio.COMPARISON.model_identity(root / "Audiobook/v6/asr-model-medium")
    prepared, events, used = {}, [], 0
    for (track_id, narrator), target_ids in targets.items():
        track, recording, baseline = tracks[track_id], recordings[(track_id, narrator)], maps[track_id][narrator]
        verified, _ = finalize.verify_technical_alignment(root, track, recording, baseline)
        baseline_asset = content.export_asset(output, track_id, "scene-baseline-" + narrator, baseline)
        baseline_path = (output / baseline_asset["path"]).relative_to(root).as_posix()
        evidence = copy.deepcopy(baseline["evidence"])
        if transcriber is not None and (args.max_windows is None or used < args.max_windows):
            evidence, recording_events, count = refine_recording(
                root, output, track, verified, baseline, target_ids, model_identity, transcriber,
                None if args.max_windows is None else args.max_windows - used, recognition_root)
            events.extend(recording_events)
            used += count
        words = finalize.checked_cache_words(root, track, verified, evidence, baseline["sourceBindings"])
        original_words = finalize.checked_cache_words(root, track, verified, baseline["evidence"], baseline["sourceBindings"])
        scene_map = scene_clock_map(track, verified, baseline, words, target_ids, evidence,
                                    baseline_path, baseline_asset["sha256"], original_words)
        # Reproduction happens before any map is accepted as prepared.
        _, proof = verify_scene_clock(root, track, verified, baseline, scene_map)
        map_asset = content.export_asset(output, track_id, "scene-cues-" + narrator, scene_map)
        prepared.setdefault(track_id, {})[narrator] = scene_map
        print(json.dumps({"trackId": track_id, "narratorId": narrator, "measured": proof["measuredCues"],
                          "unavailable": proof["unavailableCues"], "mapPath": map_asset["path"]}), flush=True)
    index_asset = content.export_asset(output, "chapter-000", "scene-clock-maps", prepared)
    report = {"schemaVersion": 1, "method": METHOD, "checkedAt": datetime.now(timezone.utc).isoformat(),
              "recognitionMode": ("offline-medium" if args.transcribe else "reuse-retained-output"
                                  if args.reuse_recognition else "existing-baseline-cache"),
              "targetAnchors": sum(len(values) for values in targets.values()),
              "measuredAnchors": sum(len(m["cues"]) for voices in prepared.values() for m in voices.values()),
              "unavailableAnchors": sum(len(m["unavailable"]) for voices in prepared.values() for m in voices.values()),
              "recognitionWindows": used, "recognitionEvents": events,
              "sceneClockMapsPath": (output / index_asset["path"]).relative_to(root).as_posix(),
              "sceneClockMapsSha256": index_asset["sha256"], "sentenceMapsChanged": False,
              "humanAlignmentApproval": "not-claimed"}
    report_asset = content.export_asset(output, "chapter-000", "scene-clock-recovery-report", report)
    print(json.dumps({key: value for key, value in report.items() if key != "recognitionEvents"}
                     | {"reportPath": str(output / report_asset["path"])}), flush=True)


if __name__ == "__main__":
    main()
