#!/usr/bin/env python3
"""Verify generated selections and prepare private reader candidates and audit.

This command does not approve artwork, invent narrator clocks, upload assets,
or change selected source manifests. Reader labels are neutral until explicit
metadata is supplied; original production labels stay in the private audit.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import book_content as content
import release_export as release


SELECTION_NAME = re.compile(r"(chapter-[0-9]{3})-selected-scenes\.json")
DEFAULT_NARRATORS = ("autonoe", "charon")
CORRECTION_FIELDS = {"sourceSentenceId", "sourceSentenceTextSha256", "sourceSha256", "phoneSha256",
                     "sentenceId", "sentenceTextSha256", "review"}


def anchor_correction_hash(scene_id, correction):
    return content.object_hash({"id": scene_id, **{key: correction.get(key)
                               for key in sorted(CORRECTION_FIELDS - {"review"})}})


def apply_anchor_correction(source, sentence_by_id, correction):
    """Apply an explicitly reviewed, source-bound canonical edit, never seconds."""
    if correction is None:
        return source["sentenceId"], source["sentenceTextSha256"]
    content.require(isinstance(correction, dict) and set(correction) == CORRECTION_FIELDS,
                    "Anchor correction requires exact source, target and review fields")
    bindings = {"sourceSentenceId": source["sentenceId"],
                "sourceSentenceTextSha256": source["sentenceTextSha256"],
                "sourceSha256": source["sha256"], "phoneSha256": (source.get("phone") or {}).get("sha256")}
    content.require(all(correction.get(key) == value for key, value in bindings.items()),
                    "Anchor correction selected source changed")
    sentence_id = correction.get("sentenceId")
    content.require(isinstance(sentence_id, str) and sentence_id in sentence_by_id,
                    "Anchor correction target must belong to the same canonical chapter")
    digest = hashlib.sha256(sentence_by_id[sentence_id]["text"].encode("utf-8")).hexdigest()
    content.require(correction.get("sentenceTextSha256") == digest,
                    "Anchor correction canonical target text differs")
    review = correction.get("review")
    content.require(isinstance(review, dict) and review.get("status") == "accepted-with-note"
                    and isinstance(review.get("reviewer"), str) and review["reviewer"].strip()
                    and isinstance(review.get("note"), str) and review["note"].strip()
                    and review.get("correctionSha256") == anchor_correction_hash(source["id"], correction),
                    "Anchor correction requires a current hash-bound narrative review")
    try:
        reviewed = datetime.fromisoformat(review.get("reviewedAt", "").replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise content.ContentError("Anchor correction review requires a timestamp") from exc
    content.require(reviewed.tzinfo is not None, "Anchor correction review requires a timezone")
    return sentence_id, digest


def selected_tracks(root, registry, selections_dir, track_ids=None):
    """Select exact manifest filenames, never backup/refinement manifests."""
    root = Path(root).resolve()
    directory = Path(selections_dir)
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.resolve()
    content.require(root in directory.parents and directory.is_dir(),
                    "Selection directory must remain inside the owned project root")
    canonical = [track["id"] for track in registry["tracks"]]
    present = {match.group(1) for path in directory.iterdir()
               if (match := SELECTION_NAME.fullmatch(path.name))}
    content.require(present.issubset(canonical), "Selected manifest has an unknown canonical track")
    ids = list(track_ids) if track_ids is not None else [track_id for track_id in canonical if track_id in present]
    content.require(bool(ids) and all(isinstance(track_id, str) and track_id in canonical for track_id in ids)
                    and ids == [track_id for track_id in canonical if track_id in ids],
                    "Explicit scene tracks are unknown, duplicated, or out of canonical order")
    content.require(set(ids).issubset(present), "Explicit scene track has no selected manifest")
    return directory, ids


def verified_image(root, source_path, digest, label):
    content.require(isinstance(source_path, str) and bool(source_path) and content.valid_hash(digest),
                    f"Invalid {label} image path/hash")
    path = content.project_file(root, source_path)
    content.require(path.suffix.lower() == ".webp", f"{label} image requires optimized WebP")
    size = path.stat().st_size
    content.require(size > 0 and content.file_hash(path) == digest, f"{label} image hash differs")
    content.require(path.stat().st_size == size, f"{label} image changed during verification")
    return path.relative_to(Path(root).resolve()).as_posix(), size


def timing_cues(track, narrator_id, timing):
    """Validate clocks for a diagnostic audit, preserving explicitly absent cues.

    Partial maps are reported as blocked. The release exporter still requires
    its complete, recording-verified map before it can publish these scenes.
    """
    content.require(isinstance(timing, dict) and type(timing.get("schemaVersion")) is int
                    and timing["schemaVersion"] in {1, 2}
                    and timing.get("trackId") == track["id"] and timing.get("narratorId") == narrator_id,
                    "Scene timing track/narrator identity differs")
    content.require(timing.get("manuscriptVersion") == track["manuscriptVersion"]
                    and timing.get("textSha256") == track["textSha256"], "Scene timing canonical text differs")
    content.require(content.valid_hash(timing.get("audioSha256"))
                    and content.finite_number(timing.get("duration")) and timing["duration"] > 0,
                    "Scene timing requires final recording hash and duration")
    rows = timing.get("sentences")
    ids = [sentence["id"] for sentence in content.sentences(track)]
    content.require(isinstance(rows, list) and all(isinstance(cue, dict) for cue in rows),
                    "Invalid scene timing cue list")
    cue_ids = [cue.get("sentenceId") for cue in rows]
    content.require(all(isinstance(sentence_id, str) and sentence_id in ids for sentence_id in cue_ids)
                    and cue_ids == [sentence_id for sentence_id in ids if sentence_id in cue_ids],
                    "Scene timing cues are unknown, duplicated, or out of canonical order")
    previous_end = 0.0
    for cue in rows:
        if timing["schemaVersion"] == 2 and cue.get("syncStatus") == "unavailable":
            content.require("start" in cue and "end" in cue and cue["start"] is None and cue["end"] is None
                            and cue.get("reason") in content.SYNC_GAP_REASONS,
                            "Unavailable scene timing cannot contain an estimated clock")
            continue
        content.require(timing["schemaVersion"] == 1 or cue.get("syncStatus") == "measured",
                        "Scene timing cue must be measured or explicitly unavailable")
        start, end = cue.get("start"), cue.get("end")
        content.require(content.finite_number(start) and content.finite_number(end)
                        and previous_end <= start < end <= timing["duration"],
                        "Scene timing measured cues overlap or exceed recording bounds")
        previous_end = end
    return {cue["sentenceId"]: cue for cue in rows}


def scene_clock_cues(track, narrator_id, timing, scene_map):
    """Check diagnostic map bindings; publication freshly reproduces its proof."""
    if scene_map is None:
        return {}
    content.require(isinstance(timing, dict) and isinstance(scene_map, dict)
                    and type(scene_map.get("schemaVersion")) is int and scene_map["schemaVersion"] == 1
                    and scene_map.get("kind") == "measured-scene-cues"
                    and scene_map.get("trackId") == track["id"] and scene_map.get("narratorId") == narrator_id
                    and scene_map.get("manuscriptVersion") == track["manuscriptVersion"]
                    and scene_map.get("textSha256") == track["textSha256"]
                    and scene_map.get("audioSha256") == timing["audioSha256"]
                    and scene_map.get("duration") == timing["duration"],
                    "Measured scene clock identity differs from the selected recording")
    rows = scene_map.get("cues")
    ids = [sentence["id"] for sentence in content.sentences(track)]
    content.require(isinstance(rows, list) and all(isinstance(cue, dict) for cue in rows),
                    "Invalid measured scene clock cues")
    cue_ids = [cue.get("sentenceId") for cue in rows]
    content.require(all(isinstance(value, str) and value in ids for value in cue_ids)
                    and cue_ids == [value for value in ids if value in cue_ids]
                    and all(isinstance(cue.get("method"), str) and cue["method"] in content.SCENE_ONSET_METHODS
                            and content.finite_number(cue.get("start"))
                            and 0 <= cue["start"] < timing["duration"] for cue in rows),
                    "Measured scene clock requires ordered distinct canonical onsets")
    content.require(all(after["start"] > before["start"] for before, after in zip(rows, rows[1:])),
                    "Measured scene clock onsets must be strictly increasing")
    return {cue["sentenceId"]: cue for cue in rows}


def prepare_scenes(root, registry, selections_dir, timing_maps, *, track_ids=None,
                   narrator_ids=DEFAULT_NARRATORS, metadata=None, corrections=None, scene_timing_maps=None):
    """Return normalized scenesByTrack and a private source/clock/review audit."""
    root = Path(root).resolve()
    content.validate_registry(registry)
    directory, ids = selected_tracks(root, registry, selections_dir, track_ids)
    content.require(isinstance(timing_maps, dict), "Invalid scene timing mapping")
    scene_timing_maps = {} if scene_timing_maps is None else scene_timing_maps
    content.require(isinstance(scene_timing_maps, dict), "Invalid measured scene clock mapping")
    narrators = list(narrator_ids)
    content.require(bool(narrators) and all(release.safe_id(narrator) for narrator in narrators)
                    and len(set(narrators)) == len(narrators), "Invalid explicit scene narrator IDs")
    metadata = {} if metadata is None else metadata
    metadata_fields = {"title", "alt", "approval", "sourceSha256", "phoneSha256", "sourceManifestSha256"}
    content.require(isinstance(metadata, dict) and all(release.safe_id(scene_id) and isinstance(value, dict)
                    and set(value).issubset(metadata_fields) for scene_id, value in metadata.items()),
                    "Invalid explicit reader scene metadata")
    corrections = {} if corrections is None else corrections
    content.require(isinstance(corrections, dict) and all(release.safe_id(scene_id)
                    and isinstance(value, dict) for scene_id, value in corrections.items()),
                    "Invalid reviewed anchor correction mapping")
    tracks = {track["id"]: track for track in registry["tracks"]}
    scenes_by_track, audit_tracks, seen, unique_images = {}, [], set(), {}
    selection_sources, timing_sources = [], []
    totals = {"tracks": len(ids), "scenes": 0, "phoneImages": 0, "approvedScenes": 0,
              "pendingApprovals": 0, "measuredNarratorAnchors": 0, "blockedNarratorAnchors": 0,
              "scenesMeasuredForAllNarrators": 0, "scenesWithBlockedNarratorAnchors": 0,
              "duplicateTriggerGroups": 0, "scenesWithDuplicateTrigger": 0,
              "correctedSceneAnchors": 0,
              "measuredSceneOnlyAnchors": 0,
              "referencedImageBytes": 0, "fullImageBytes": 0, "phoneImageBytes": 0}
    for track_id in ids:
        track = tracks[track_id]
        path = content.project_file(root, directory / (track_id + "-selected-scenes.json"))
        manifest_hash = content.file_hash(path)
        selection = content.read_project_json(root, path)
        content.require(content.file_hash(path) == manifest_hash, "Selected scene manifest changed during verification")
        content.require(type(selection.get("schemaVersion")) is int and selection["schemaVersion"] == 1
                        and selection.get("bookId") == registry["bookId"]
                        and selection.get("chapterId") == track_id
                        and selection.get("manuscriptVersion") == registry["manuscriptVersion"],
                        "Selected scene manifest canonical identity differs")
        content.require(selection.get("sharedAcrossNarrators") is True
                        and selection.get("narratorTimestamps") is None,
                        "Selected scenes must share canonical anchors without narrator timestamps")
        source_rows = selection.get("scenes")
        content.require(isinstance(source_rows, list) and bool(source_rows)
                        and all(isinstance(scene, dict) for scene in source_rows), "Invalid selected scene list")
        selection_sources.append({"trackId": track_id, "sourcePath": path.relative_to(root).as_posix(),
                                  "sha256": manifest_hash, "bytes": path.stat().st_size})
        canonical = content.sentences(track)
        sentence_indices = {sentence["id"]: index for index, sentence in enumerate(canonical)}
        sentence_by_id = {sentence["id"]: sentence for sentence in canonical}
        track_maps = timing_maps.get(track_id, {})
        content.require(isinstance(track_maps, dict), "Invalid selected track timing mapping")
        cues_by_narrator = {}
        scene_cues_by_narrator = {}
        scene_maps = scene_timing_maps.get(track_id, {})
        content.require(isinstance(scene_maps, dict), "Invalid selected track scene clock mapping")
        for narrator_id in narrators:
            timing = track_maps.get(narrator_id)
            scene_cues_by_narrator[narrator_id] = scene_clock_cues(
                track, narrator_id, timing, scene_maps.get(narrator_id))
            if timing is None:
                cues_by_narrator[narrator_id] = None
                continue
            cues_by_narrator[narrator_id] = timing_cues(track, narrator_id, timing)
            timing_sources.append({"trackId": track_id, "narratorId": narrator_id,
                                   "audioSha256": timing["audioSha256"],
                                   "timingSha256": content.timing_content_hash(timing),
                                   "timingMapSha256": content.object_hash(timing)})
        prepared_rows, audit_rows, previous_index = [], [], -1
        for index, source in enumerate(source_rows, 1):
            scene_id, sentence_id = source.get("id"), source.get("sentenceId")
            content.require(release.safe_id(scene_id) and scene_id not in seen,
                            "Selected scene ID is unsafe or duplicated")
            seen.add(scene_id)
            content.require(source.get("trackId") == track_id and isinstance(sentence_id, str)
                            and sentence_id in sentence_by_id, "Selected scene canonical sentence is unknown")
            content.require(source.get("phone") is None or isinstance(source["phone"], dict),
                            "Invalid selected phone image")
            sentence_digest = hashlib.sha256(sentence_by_id[sentence_id]["text"].encode("utf-8")).hexdigest()
            content.require(source.get("sentenceTextSha256") == sentence_digest,
                            "Selected scene canonical sentence text hash differs")
            original_sentence_id = sentence_id
            sentence_id, sentence_digest = apply_anchor_correction(source, sentence_by_id, corrections.get(scene_id))
            totals["correctedSceneAnchors"] += int(scene_id in corrections)
            content.require(sentence_indices[sentence_id] >= previous_index,
                            "Selected scene canonical triggers are out of order")
            previous_index = sentence_indices[sentence_id]
            content.require("start" not in source and "end" not in source,
                            "Shared selected scene must not contain a narrator clock")
            source_path, image_bytes = verified_image(root, source.get("sourcePath"), source.get("sha256"), "Full")
            overrides = metadata.get(scene_id, {})
            metadata_bindings = {"sourceSha256": source["sha256"],
                                 "phoneSha256": (source.get("phone") or {}).get("sha256"),
                                 "sourceManifestSha256": manifest_hash}
            content.require(all(overrides[key] == expected for key, expected in metadata_bindings.items()
                                if key in overrides), "Reader metadata selected source changed")
            title = release.visible_text(overrides.get("title", f"Illustration {index:02d}"), "scene title")
            alt = release.visible_text(overrides.get("alt", "Painted illustration for the current passage."), "scene alt")
            scene = {"id": scene_id, "sentenceId": sentence_id, "sentenceTextSha256": sentence_digest,
                     "sourcePath": source_path, "sha256": source["sha256"], "title": title, "alt": alt,
                     "approval": copy.deepcopy(overrides.get("approval", source.get("approval")))}
            image_records = [{"variant": "full", "sourcePath": source_path,
                              "sha256": scene["sha256"], "bytes": image_bytes}]
            phone = source.get("phone")
            if phone is not None:
                content.require(isinstance(phone, dict), "Invalid selected phone image")
                phone_path, phone_bytes = verified_image(root, phone.get("path"), phone.get("sha256"), "Phone")
                scene.update(phoneSourcePath=phone_path, phoneSha256=phone["sha256"])
                image_records.append({"variant": "phone", "sourcePath": phone_path,
                                      "sha256": phone["sha256"], "bytes": phone_bytes})
                totals["phoneImages"] += 1
            valid_approval = release.scene_approval_valid(scene)
            approval_status = scene["approval"]["status"] if valid_approval else "pending"
            if not valid_approval:
                scene["approval"] = None
            totals["approvedScenes"] += int(valid_approval)
            totals["pendingApprovals"] += int(not valid_approval)
            clocks = {}
            for narrator_id in narrators:
                timing = track_maps.get(narrator_id)
                cues = cues_by_narrator[narrator_id]
                cue = cues.get(sentence_id) if cues is not None else None
                clock = {"audioSha256": timing["audioSha256"] if timing is not None else None,
                         "status": "blocked", "startSeconds": None}
                if timing is None:
                    clock["reason"] = "missing-timing-map"
                elif cue is None:
                    clock["reason"] = "missing-sentence-cue"
                elif cue.get("syncStatus") == "unavailable":
                    clock["reason"] = cue["reason"]
                else:
                    clock.update(status="measured", startSeconds=cue["start"], source="measured-sentence")
                scene_cue = scene_cues_by_narrator[narrator_id].get(sentence_id)
                if clock["status"] == "blocked" and scene_cue is not None:
                    content.validate_scene_onset_binding(scene, scene_cue, scene)
                    clock.pop("reason", None)
                    clock.update(status="measured", startSeconds=scene_cue["start"], source="measured-scene-cue")
                    totals["measuredSceneOnlyAnchors"] += 1
                clocks[narrator_id] = clock
                totals["measuredNarratorAnchors"] += int(clock["status"] == "measured")
                totals["blockedNarratorAnchors"] += int(clock["status"] == "blocked")
            all_measured = all(clock["status"] == "measured" for clock in clocks.values())
            totals["scenesMeasuredForAllNarrators"] += int(all_measured)
            totals["scenesWithBlockedNarratorAnchors"] += int(not all_measured)
            publication_blockers = []
            if not valid_approval:
                publication_blockers.append("pending-scene-approval")
            if not all_measured:
                publication_blockers.append("unavailable-narrator-anchor")
            for image in image_records:
                totals["referencedImageBytes"] += image["bytes"]
                totals["fullImageBytes" if image["variant"] == "full" else "phoneImageBytes"] += image["bytes"]
                unique_images[(image["sourcePath"], image["sha256"])] = image["bytes"]
            audit_rows.append({"id": scene_id, "sentenceId": sentence_id,
                               "sourceSentenceId": original_sentence_id,
                               "anchorCorrection": copy.deepcopy(corrections.get(scene_id)),
                               "sentenceTextSha256": sentence_digest,
                               "sourceTitle": copy.deepcopy(source.get("title")),
                               "sourceAlt": copy.deepcopy(source.get("alt")),
                               "readerTitle": title, "readerAlt": alt, "images": image_records,
                               "sceneContentSha256": release.scene_content_hash(scene),
                               "approvalStatus": approval_status,
                               "existingApprovalPreserved": valid_approval,
                               "priorApprovalPresent": source.get("approval") is not None,
                               "metadataSupplied": scene_id in metadata, "narrators": clocks,
                               "publicationBlockers": publication_blockers})
            prepared_rows.append(scene)
            totals["scenes"] += 1
        scenes_by_track[track_id] = prepared_rows
        trigger_scenes = {}
        for scene in audit_rows:
            trigger_scenes.setdefault(scene["sentenceId"], []).append(scene["id"])
        collisions = [{"sentenceId": sentence_id, "sceneIds": scene_ids}
                      for sentence_id, scene_ids in trigger_scenes.items() if len(scene_ids) > 1]
        collided_ids = {scene_id for group in collisions for scene_id in group["sceneIds"]}
        for scene in audit_rows:
            if scene["id"] in collided_ids:
                scene["publicationBlockers"].append("duplicate-canonical-trigger")
        totals["duplicateTriggerGroups"] += len(collisions)
        totals["scenesWithDuplicateTrigger"] += len(collided_ids)
        audit_tracks.append({"trackId": track_id, "duplicateTriggerGroups": collisions, "scenes": audit_rows})
    content.require(set(metadata).issubset(seen), "Reader metadata contains an unselected scene ID")
    content.require(set(corrections).issubset(seen), "Anchor corrections contain an unselected scene ID")
    totals.update(uniqueImageFiles=len(unique_images), uniqueImageBytes=sum(unique_images.values()))
    audit = {"schemaVersion": 1, "kind": "private-illustration-preparation", "bookId": registry["bookId"],
             "manuscriptVersion": registry["manuscriptVersion"],
             "registrySha256": registry.get("registrySha256", content.object_hash(registry)),
             "trackIds": ids, "narratorIds": narrators, "selectionSources": selection_sources,
             "timingSources": timing_sources, "readerMetadataSha256": content.object_hash(metadata),
             "anchorCorrectionsSha256": content.object_hash(corrections),
             "sceneTimingMapsSha256": content.object_hash(scene_timing_maps),
             "scenesSha256": content.object_hash(scenes_by_track), "totals": totals, "tracks": audit_tracks,
             "publicationStatus": "private-candidates-review-pending"}
    return {"scenesByTrack": scenes_by_track, "audit": audit}


def write_prepared(root, prepared, destination):
    """Write private content-addressed candidates without replacing prior work."""
    root = Path(root).resolve()
    destination = Path(destination)
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve()
    public = (root / "web/dist").resolve()
    content.require(root in destination.parents and destination != public and public not in destination.parents,
                    "Illustration output must be private inside the owned root outside web/dist")
    # Both objects are validated/serialized before the first output is created.
    content.json_bytes(prepared["scenesByTrack"])
    content.json_bytes(prepared["audit"])
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    scenes_asset = content.export_asset(destination, "chapter-000", "illustrated-scenes", prepared["scenesByTrack"])
    audit_asset = content.export_asset(destination, "chapter-000", "illustration-audit", prepared["audit"])
    return {"summary": prepared["audit"]["totals"],
            "scenesPath": str(destination / scenes_asset["path"]),
            "auditPath": str(destination / audit_asset["path"]),
            "publicationStatus": prepared["audit"]["publicationStatus"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--selections", type=Path, required=True, help="Private selected-scene manifest directory")
    parser.add_argument("--timings", type=Path, required=True, help="Track ID -> narrator ID -> finalized timing map")
    parser.add_argument("--out", type=Path, required=True, help="Private directory inside --root, outside web/dist")
    parser.add_argument("--tracks", nargs="+", help="Explicit canonical track subset; otherwise select exact manifests")
    parser.add_argument("--narrators", nargs="+", default=list(DEFAULT_NARRATORS))
    parser.add_argument("--metadata", type=Path, help="Explicit scene ID -> title/alt/approval overrides")
    parser.add_argument("--corrections", type=Path, help="Explicit source-bound reviewed canonical trigger corrections")
    parser.add_argument("--scene-timings", type=Path, help="Track -> narrator -> measured scene-onset proof; sentence gaps stay null")
    args = parser.parse_args()
    root = args.root.resolve()
    values, bindings = {}, []
    for kind, source in [("registry", args.registry), ("timings", args.timings), ("metadata", args.metadata),
                         ("corrections", args.corrections), ("scene-timings", args.scene_timings)]:
        if source is None:
            continue
        path = content.project_file(root, source)
        digest = content.file_hash(path)
        values[kind] = content.read_project_json(root, path)
        content.require(content.file_hash(path) == digest, f"Scene {kind} input changed while reading")
        bindings.append({"kind": kind, "sourcePath": path.relative_to(root).as_posix(), "sha256": digest})
    registry, timings, metadata = values["registry"], values["timings"], values.get("metadata")
    prepared = prepare_scenes(root, registry, args.selections, timings, track_ids=args.tracks,
                              narrator_ids=args.narrators, metadata=metadata, corrections=values.get("corrections"),
                              scene_timing_maps=values.get("scene-timings"))
    for binding in bindings:
        content.require(content.project_file_hash(root, binding["sourcePath"]) == binding["sha256"],
                        f"Scene {binding['kind']} input changed during verification")
    prepared["audit"]["inputFiles"] = bindings
    print(json.dumps(write_prepared(root, prepared, args.out), indent=2))


if __name__ == "__main__":
    main()
