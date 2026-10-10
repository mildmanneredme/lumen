#!/usr/bin/env python3
"""Finalize measured local ASR clocks with explicit gaps and technical proof.

This never runs inference, calls paid services, changes audio, uploads, or
invents human alignment approval. It uses the existing source-bound author
decision and freshly checks selected files and cached ASR evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import book_content as content
import prepare_book_audio as audio


def author_content_approval(approval, decision_hash, track, recording):
    content.require(type(approval.get("schemaVersion")) is int and approval["schemaVersion"] == 1 and isinstance(approval.get("reviewer"), str)
                    and approval["reviewer"].strip() and isinstance(approval.get("statements"), list)
                    and audio.AUTHOR_APPROVAL in approval["statements"]
                    and audio.AUTHOR_NO_RETAKES in approval["statements"], "Missing explicit source-bound author as-is decision")
    result = {"status": "approved", "reviewer": approval["reviewer"], "reviewedAt": approval.get("reviewedAt"),
              "audioSha256": recording["sha256"], "textSha256": track["textSha256"],
              "basis": "explicit-author-as-is-decision", "authorDecisionSha256": decision_hash,
              "note": "Existing recording accepted as-is by the author. Technical ASR verification does not claim a full human alignment review."}
    content.require(content.approval_valid(result, recording, track), "Invalid author decision timestamp or source binding")
    return result


def gap_timing(track, recording, report, content_approval):
    """Merge observed cues and unavailable records; recheck bounds are not cues."""
    canonical = content.sentences(track)
    ids = [sentence["id"] for sentence in canonical]
    measured = report.get("measuredSentences")
    content.require(isinstance(measured, list) and all(isinstance(cue, dict) for cue in measured), "Missing measured ASR sentence evidence")
    by_id = {cue.get("sentenceId"): cue for cue in measured}
    content.require(len(by_id) == len(measured) and all(key in ids for key in by_id)
                    and [cue["sentenceId"] for cue in measured] == [key for key in ids if key in by_id],
                    "Measured ASR sentence evidence is duplicated, unknown or out of order")
    evidence = report.get("evidence", [])
    selected_ids = [clip["id"] for clip in recording["selectedClips"]]
    content.require(isinstance(evidence, list) and [row.get("id") for row in evidence] == selected_ids,
                    "Missing selected ASR evidence for the finalized map")
    gaps = report.get("syncGaps", [])
    content.require(isinstance(gaps, list) and all(isinstance(row, dict) for row in gaps), "Malformed ASR sync gap evidence")
    gap_by_id = {row.get("sentenceId"): row for row in gaps}
    content.require(len(gap_by_id) == len(gaps) and all(key in ids for key in gap_by_id), "Unknown or duplicate sync gap")
    missing = set(report.get("unanchoredSentences", []))
    overlaps = set(report.get("overlappingSentences", []))
    low_rows = report.get("lowConfidenceSentences", [])
    content.require(isinstance(low_rows, list) and all(isinstance(row, dict) for row in low_rows),
                    "Malformed low-confidence ASR sentence evidence")
    low = {row.get("sentenceId") for row in low_rows}
    content.require(len(low) == len(low_rows) and low.issubset(ids)
                    and missing.issubset(ids) and overlaps.issubset(ids)
                    and set(by_id) | missing | low == set(ids), "ASR sentence coverage is incomplete")
    cues, previous = [], 0.0
    for sentence_id in ids:
        observed = by_id.get(sentence_id)
        reason = gap_by_id.get(sentence_id, {}).get("reason")
        if sentence_id in low:
            reason = "low-confidence-ASR-anchor"
        elif sentence_id in overlaps:
            reason = "overlapping-ASR-interval"
        elif sentence_id in missing:
            reason = "no-positive-duration-ASR-anchor"
        elif observed:
            start, end = observed.get("start"), observed.get("end")
            content.require(content.finite_number(start) and content.finite_number(end)
                            and 0 <= start <= end <= recording["decodedDuration"], "ASR measured clock is out of bounds")
            if end <= start:
                reason = "no-positive-duration-ASR-anchor"
            elif start < previous:
                reason = "overlapping-ASR-interval"
        if reason:
            content.require(reason in content.SYNC_GAP_REASONS, "Unknown non-neutral ASR gap reason")
            evidence_ids = gap_by_id.get(sentence_id, {}).get("evidenceIds") or selected_ids
            content.require(isinstance(evidence_ids, list) and all(key in selected_ids for key in evidence_ids), "Unknown gap source evidence")
            cues.append({"sentenceId": sentence_id, "syncStatus": "unavailable", "start": None, "end": None,
                         "reason": reason, "evidenceIds": list(dict.fromkeys(evidence_ids))})
        else:
            content.require(observed is not None, "Sentence has neither measured evidence nor an explicit gap")
            cues.append({"sentenceId": sentence_id, "syncStatus": "measured", "start": observed["start"], "end": observed["end"]})
            previous = observed["end"]
    for index, cue in enumerate(cues):
        if cue["syncStatus"] != "unavailable":
            continue
        before = next((row for row in reversed(cues[:index]) if row["syncStatus"] == "measured"), None)
        after = next((row for row in cues[index + 1:] if row["syncStatus"] == "measured"), None)
        cue["recheckWindow"] = {"start": before["end"] if before else 0,
            "end": after["start"] if after else recording["decodedDuration"],
            "beforeSentenceId": before["sentenceId"] if before else None,
            "afterSentenceId": after["sentenceId"] if after else None, "scope": "neighbor-recheck-only"}
    positive = [cue for cue in cues if cue["syncStatus"] == "measured"]
    result = {"schemaVersion": 2, "trackId": track["id"], "manuscriptVersion": track["manuscriptVersion"],
              "textSha256": track["textSha256"], "narratorId": recording["narratorId"], "audioSha256": recording["sha256"],
              "duration": recording["decodedDuration"], "sentences": cues,
              "coverage": {"introEnd": positive[0]["start"] if positive else 0,
                           "tailStart": positive[-1]["end"] if positive else recording["decodedDuration"]},
              "alignmentMethod": report["method"], "evidence": evidence, "sourceBindings": report["sourceBindings"],
              "approvals": {"content": content_approval}}
    content.validate_timing_map(track, recording, result)
    return result


def checked_cache_words(root, track, recording, evidence, source_bindings):
    """Read exact cache paths and validate their bytes, identity and master clock."""
    edition = recording.get("productionEdition")
    content.require(edition in {"v7", "v8"}, "Invalid technical proof edition")
    manifest_path = Path("Audiobook") / edition / "generation-manifest.json"
    content.require(content.project_file_hash(root, manifest_path) == recording["generationManifestSha256"], "Technical source generation manifest changed")
    manifest = content.read_project_json(root, manifest_path)
    selected = {item["id"]: item for item in manifest["items"]}
    qa_path = Path(recording["sourcePath"]).with_suffix(".qa.json")
    qa = content.read_project_json(root, qa_path)
    content.require(content.project_file_hash(root, qa_path) == source_bindings.get("sourceQaSha256"), "Technical source QA hash changed")
    checkpoint = content.read_project_json(root, Path(recording["sourcePath"]).with_suffix(".checkpoint.json"))
    content.require(content.production_object_hash(checkpoint.get("identity")) == recording["masterIdentitySha256"]
                    and checkpoint.get("identity_sha256") == recording["masterIdentitySha256"]
                    and checkpoint.get("output_sha256", {}).get("mp3") == recording["sha256"]
                    and checkpoint.get("output_sha256", {}).get("qa") == content.project_file_hash(root, qa_path),
                    "Technical master/checkpoint lineage changed")
    clips = recording["selectedClips"]
    content.require(isinstance(evidence, list) and len(evidence) == len(clips)
                    and all(isinstance(row, dict) for row in evidence), "Missing complete selected ASR cache evidence")
    words = []
    for row, clip in zip(evidence, clips):
        item = selected.get(clip["id"])
        content.require(item is not None and item.get("request_sha256") == content.selected_request_hash(item)
                        and row.get("id") == item["id"] == clip["id"], "Technical selected request/source identity differs")
        cache_name = row.get("sourcePath")
        content.require(isinstance(cache_name, str) and not Path(cache_name).is_absolute()
                        and ".." not in Path(cache_name).parts and Path(cache_name).suffix == ".json",
                        "Technical ASR cache must use a selected-root relative JSON path")
        path = content.project_file(root, cache_name)
        content.require(content.project_file_hash(root, path) == row.get("cacheSha256"), "Technical ASR cache bytes changed")
        record = content.read_project_json(root, path)
        identity = record.get("cache_identity", {})
        expected = hashlib.sha256(item.get("inputtext", item["text"]).encode()).hexdigest()
        content.require(row.get("rawAudioSha256") == clip["rawAudioSha256"]
                        and row.get("requestSha256") == item["request_sha256"] == clip["requestSha256"]
                        and row.get("expectedTextSha256") == expected == clip["textSha256"]
                        and row.get("decoderIdentitySha256") == content.object_hash(identity)
                        and row.get("refinementAttempts", []) == record.get("refinementAttempts", []),
                        "Technical ASR cache source/decoder identity changed")
        words.extend(audio.cache_words(record, item, clip, qa))
        content.require(content.project_file_hash(root, path) == row["cacheSha256"], "Technical ASR cache changed during verification")
    return words


def verification_report(track, recording, timing, checked_at):
    positive = sum(cue["syncStatus"] == "measured" for cue in timing["sentences"])
    return {"schemaVersion": 1, "kind": "technical-alignment-verification", "status": "verified",
            "method": content.TECHNICAL_ALIGNMENT_METHOD, "checkedAt": checked_at,
            "trackId": track["id"], "narratorId": recording["narratorId"], "audioSha256": recording["sha256"],
            "textSha256": track["textSha256"], "timingSha256": content.timing_content_hash(timing),
            **{key: recording[key] for key in ["selectionSha256", "masterIdentitySha256", "generationManifestSha256"]},
            "cacheEvidence": timing["evidence"], "sourceBindings": timing["sourceBindings"],
            "canonicalSentenceCount": len(timing["sentences"]), "measuredSentenceCount": positive,
            "unavailableSentenceCount": len(timing["sentences"]) - positive, "humanAlignmentApproval": "not-claimed",
            "checks": {key: True for key in ["masterBytesVerified", "rawSourceBytesVerified", "cacheBytesVerified", "sourceLineageVerified", "mapRecomputed"]}}


def report_from_words(track, recording, words, evidence, source_bindings):
    _, report = audio.align_sentences(track, recording, words)
    report.update(evidence=evidence, sourceBindings=source_bindings, syncGaps=[])
    # The same observed aligner supplies these classifications. Its neighbor
    # token intervals remain diagnostic; finalized gap bounds use retained cues.
    report["syncGaps"] = [{"sentenceId": sentence_id, "reason": "no-positive-duration-ASR-anchor",
                          "evidenceIds": [row["id"] for row in evidence]}
                         for sentence_id in report["unanchoredSentences"]]
    low_ids = {row["sentenceId"] for row in report["lowConfidenceSentences"]}
    report["syncGaps"].extend({"sentenceId": sentence_id, "reason": "overlapping-ASR-interval",
                             "evidenceIds": [row["id"] for row in evidence]}
                            for sentence_id in report["overlappingSentences"] if sentence_id not in low_ids)
    report["syncGaps"].extend({"sentenceId": row["sentenceId"], "reason": "low-confidence-ASR-anchor",
                             "evidenceIds": [entry["id"] for entry in evidence]}
                            for row in report["lowConfidenceSentences"])
    return report


def verify_technical_alignment(root, track, recording, timing):
    """Independently recheck actual report, audio/cache bytes and measured map."""
    root = Path(root).resolve()
    alignment = timing.get("approvals", {}).get("alignment", {})
    path_value = alignment.get("verificationReportPath")
    content.require(isinstance(path_value, str) and not Path(path_value).is_absolute()
                    and ".." not in Path(path_value).parts, "Missing private technical verification report path")
    path = content.project_file(root, path_value)
    report = content.read_project_json(root, path)
    content.require(content.project_file_hash(root, path) == alignment.get("verificationReportSha256"), "Technical verification report current bytes changed")
    content.validate_timing_map(track, recording, timing)
    content.validate_technical_alignment(track, recording, timing, report)
    verified = content.verify_recording_sources(content.verify_recording_file(recording, root), root)
    words = checked_cache_words(root, track, verified, report["cacheEvidence"], report["sourceBindings"])
    rebuilt_report = report_from_words(track, verified, words, report["cacheEvidence"], report["sourceBindings"])
    rebuilt = gap_timing(track, verified, rebuilt_report, timing["approvals"]["content"])
    content.require(content.timing_content_hash(rebuilt) == content.timing_content_hash(timing), "Technical map differs from recomputed measured ASR evidence")
    content.require(verification_report(track, verified, rebuilt, report["checkedAt"]) == report,
                    "Technical verification report differs from actual checks")
    content.validate_timing_map(track, verified, timing, require_approved=True, alignment_verification=report)
    return verified, report


def load_inputs(root, destination, registry=None):
    import release_export
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    public = (root / "web/dist").resolve()
    content.require(root in destination.parents and destination != public and public not in destination.parents,
                    "Timing inputs must remain private inside the selected root")
    registry = registry or content.load_project_registry(root)
    inventory = content.read_project_json(root, destination / "release-inventory.json")
    prior_report = content.read_project_json(root, destination / "audio-release-report.json")
    fresh = release_export.current_inventory(root, registry, inventory)
    recordings = [dict(row, sourceAudioPath=row["sourcePath"], sourceAudioSha256=row["sha256"]) for row in fresh.values()]
    expected = {(row["trackId"], row["narratorId"]) for row in recordings}
    report_rows = prior_report.get("alignmentReports", [])
    content.require(type(prior_report.get("schemaVersion")) is int and prior_report["schemaVersion"] == 1
                    and prior_report.get("registrySha256") == registry["registrySha256"]
                    and prior_report.get("recordings") == len(recordings)
                    and isinstance(report_rows, list) and len(report_rows) == len(expected)
                    and {(row.get("trackId"), row.get("narratorId")) for row in report_rows} == expected,
                    "Input audio report/registry/recording scope differs")
    approval = content.read_project_json(root, destination / "author-approval.json")
    author_bindings = [{key: row[key] for key in ["trackId", "narratorId", "sha256", "textSha256", "selectionSha256"]}
                       for row in recordings]
    content.require(approval.get("registrySha256") == registry["registrySha256"]
                    and approval.get("recordings") == author_bindings, "Author audio approval is stale for the selected release")
    decision_hash = content.project_file_hash(root, destination / "author-approval.json")
    return root, destination, registry, recordings, prior_report, approval, decision_hash


def dry_run(root, destination, *, registry=None):
    """Rebuild report-bound cached evidence without writes or bulk audio reads."""
    root, destination, registry, recordings, prior_report, approval, decision_hash = load_inputs(root, destination, registry)
    tracks = {track["id"]: track for track in registry["tracks"]}
    reports = {(row["trackId"], row["narratorId"]): row for row in prior_report["alignmentReports"]}
    rows = []
    for recording in recordings:
        track = tracks[recording["trackId"]]
        source = reports[(track["id"], recording["narratorId"])]
        content.require(not source.get("missingTimestampChunks"), "Missing selected ASR caches; cannot finalize this recording")
        words = checked_cache_words(root, track, recording, source.get("evidence"), source.get("sourceBindings", {}))
        report = report_from_words(track, recording, words, source["evidence"], source["sourceBindings"])
        timing = gap_timing(track, recording, report, author_content_approval(approval, decision_hash, track, recording))
        measured = sum(cue["syncStatus"] == "measured" for cue in timing["sentences"])
        rows.append({"trackId": track["id"], "narratorId": recording["narratorId"], "sentences": len(timing["sentences"]),
                     "measured": measured, "unavailable": len(timing["sentences"]) - measured})
    return {"schemaVersion": 1, "registrySha256": registry["registrySha256"], "recordings": len(rows),
            "sentenceRecords": sum(row["sentences"] for row in rows), "measuredSentences": sum(row["measured"] for row in rows),
            "unavailableSentences": sum(row["unavailable"] for row in rows), "physicalSourcesVerified": False,
            "publicationStatus": "dry-run", "artifactsWritten": False, "records": rows}


def finalize_release(root, destination, *, registry=None):
    root, destination, registry, recordings, prior_report, approval, decision_hash = load_inputs(root, destination, registry)
    destination = audio.private_destination(root, destination)
    expected = {(row["trackId"], row["narratorId"]) for row in recordings}
    tracks = {track["id"]: track for track in registry["tracks"]}
    # Existing preparation APIs recompute all source-bound word evidence. They
    # create private measured artifacts only and never execute inference here.
    _, reports = audio.build_timings(root, destination, registry, recordings, approval)
    maps, records, gaps = {}, [], 0
    for recording, source_report in zip(recordings, reports):
        content.require(not source_report.get("missingTimestampChunks"), "Missing selected ASR caches; cannot finalize this recording")
        track = tracks[recording["trackId"]]
        content_approval = author_content_approval(approval, decision_hash, track, recording)
        verified = content.verify_recording_sources(content.verify_recording_file(recording, root), root)
        words = checked_cache_words(root, track, verified, source_report.get("evidence"), source_report.get("sourceBindings", {}))
        rebuilt_report = report_from_words(track, verified, words, source_report["evidence"], source_report["sourceBindings"])
        timing = gap_timing(track, verified, rebuilt_report, content_approval)
        proof = verification_report(track, verified, timing, datetime.now(timezone.utc).isoformat())
        proof_asset = audio.private_export(destination, ("technical-alignment",), track["id"], recording["narratorId"], proof)
        alignment = {key: proof[key] for key in ["method", "trackId", "narratorId", "audioSha256", "textSha256", "timingSha256",
                                                "selectionSha256", "masterIdentitySha256", "generationManifestSha256"]}
        alignment.update(status="verified", verifiedAt=proof["checkedAt"], verificationReportSha256=proof_asset["sha256"],
                         verificationReportPath=(destination / proof_asset["path"]).relative_to(root).as_posix())
        timing["approvals"]["alignment"] = alignment
        content.validate_timing_map(track, verified, timing, require_approved=True, alignment_verification=proof)
        asset = audio.private_export(destination, ("finalized-timings",), track["id"], recording["narratorId"], timing)
        maps.setdefault(track["id"], {})[recording["narratorId"]] = timing
        gaps += proof["unavailableSentenceCount"]
        records.append({"trackId": track["id"], "narratorId": recording["narratorId"],
                        "timingPath": (destination / asset["path"]).relative_to(root).as_posix(),
                        "timingSha256": asset["sha256"], "verificationReportPath": alignment["verificationReportPath"],
                        "verificationReportSha256": proof_asset["sha256"], "unavailableSentences": proof["unavailableSentenceCount"]})
        print(f"Verified {track['id']}/{recording['narratorId']}: {proof['measuredSentenceCount']} measured, {proof['unavailableSentenceCount']} unavailable", flush=True)
    content.require(len(records) == len(expected) and {(row["trackId"], row["narratorId"]) for row in records} == expected,
                    "Finalized timing recording scope is incomplete")
    audio.write_pointer(destination, "finalized-timings.json", maps)
    result = {"schemaVersion": 1, "registrySha256": registry["registrySha256"], "tracks": len(registry["tracks"]),
              "recordings": len(records), "sentenceRecords": sum(len(timing["sentences"]) for voices in maps.values() for timing in voices.values()),
              "unavailableSentences": gaps, "humanAlignmentApproval": "not-claimed", "technicalAlignmentStatus": "verified",
              "publicationStatus": "local-finalized", "authorDecisionSha256": decision_hash,
              "inputAudioReportSha256": content.project_file_hash(root, destination / "audio-release-report.json"),
              "finalizedTimingsSha256": content.project_file_hash(root, destination / "finalized-timings.json"), "artifacts": records}
    audio.write_pointer(destination, "timing-finalization-report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Rebuild report-bound ASR maps without writes or bulk audio reads")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        operation = dry_run if args.dry_run else finalize_release
        result = operation(root, args.destination or root / "Audiobook/author-audit/web-release")
    except (content.ContentError, OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"Timing finalization blocked: {exc}\n")
    print({key: value for key, value in result.items() if key not in {"artifacts", "records"}})


if __name__ == "__main__":
    main()
