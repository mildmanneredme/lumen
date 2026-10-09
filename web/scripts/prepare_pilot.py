#!/usr/bin/env python3
"""Prepare the opening Lumen v6 listening pilot from existing approved sources.

No network calls, narration generation, or changes to audiobook source files.
Timings are approximate ASR word timestamps, mapped onto the mastered timeline.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
JOB = ROOT / "Audiobook/v6"
CANONICAL = ROOT / "Draft/v6/part1.md"
DATA = WEB / "data/chapter-001.json"
REPORT = WEB / "data/alignment-report.json"
AUDIO = WEB / "assets/chapter-001-pilot.mp3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def number_words(number: int) -> str:
    small = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
    tens = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()
    if number < 20:
        return small[number]
    if number < 100:
        return tens[number // 10] + (" " + small[number % 10] if number % 10 else "")
    if number < 1000:
        return small[number // 100] + " hundred" + (" " + number_words(number % 100) if number % 100 else "")
    for scale, label in [(10**9, "billion"), (10**6, "million"), (1000, "thousand")]:
        if number >= scale:
            return number_words(number // scale) + " " + label + (" " + number_words(number % scale) if number % scale else "")
    return str(number)


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).lower()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("’", "'").replace("'", "")
    text = re.sub(r"\d+", lambda match: number_words(int(match.group())), text)
    return re.findall(r"\w+", text)


def display_markdown(markdown: str):
    """Keep canonical prose while representing emphasis as character ranges."""
    parts = re.split(r"(\*[^*]+\*)", markdown)
    plain = ""
    emphasis = []
    for part in parts:
        if part.startswith("*") and part.endswith("*"):
            start = len(plain)
            plain += part[1:-1]
            emphasis.append({"start": start, "end": len(plain)})
        else:
            plain += part
    return plain, emphasis


def sentence_ranges(text: str):
    cursor = 0
    for match in re.finditer(r"[.!?][\"”']?(?=\s+|$)", text):
        ending = match.end()
        candidate = text[cursor:ending].strip()
        # Periods in these abbreviations are not sentence boundaries.
        if re.search(r"\b(?:Mr|Mrs|Ms|Dr|Prof|et al)\.$", candidate):
            continue
        if candidate:
            leading = len(text[cursor:ending]) - len(text[cursor:ending].lstrip())
            yield cursor + leading, ending
        cursor = ending
    if text[cursor:].strip():
        leading = len(text[cursor:]) - len(text[cursor:].lstrip())
        yield cursor + leading, len(text.rstrip())


def locate_cache(item, checkpoint_input):
    matches = []
    for path in (JOB / "local-checks/cache").glob(item["id"] + ".*.json"):
        cache = read_json(path)
        identity = cache["cache_identity"]
        if (identity["audio_sha256"] == checkpoint_input["audio_sha256"]
                and identity["request_sha256"] == item["request_sha256"]
                and cache["expected_inputtext"] == item["text"]):
            matches.append((path, cache))
    if len(matches) != 1:
        raise RuntimeError(f"Need exactly one matching ASR cache for {item['id']}; found {len(matches)}")
    return matches[0]


def align(expected, heard, heard_times):
    """Map exact word runs, then locally interpolate recognition differences."""
    mapped = [None] * len(expected)
    exact = [False] * len(expected)
    differences = []
    matcher = difflib.SequenceMatcher(None, expected, heard, autojunk=False)
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(b - a):
                mapped[a + offset] = heard_times[c + offset]
                exact[a + offset] = True
        elif tag == "replace":
            # A replaced name/spelling inherits that locally recognized phrase's
            # time span; canonical display wording is never changed.
            start = heard_times[c][0]
            end = heard_times[d - 1][1]
            for offset in range(b - a):
                mapped[a + offset] = (
                    start + (end - start) * offset / (b - a),
                    start + (end - start) * (offset + 1) / (b - a),
                )
        if tag != "equal":
            differences.append({"type": tag, "expectedIndex": a, "expectedCount": b - a,
                                "recognizedIndex": c, "recognizedCount": d - c})
    for index, timing in enumerate(mapped):
        if timing is not None:
            continue
        before = next((mapped[previous] for previous in range(index - 1, -1, -1) if mapped[previous] is not None), None)
        after = next((mapped[following] for following in range(index + 1, len(mapped)) if mapped[following] is not None), None)
        if before and after:
            mapped[index] = (before[1], max(before[1], after[0]))
        elif before:
            mapped[index] = (before[1], before[1])
        elif after:
            mapped[index] = (after[0], after[0])
        else:
            raise RuntimeError("No recognized anchors for narration")
    return mapped, exact, differences


def make_scenes(paragraphs):
    """Use existing sentence anchors for every artwork change."""
    by_id = {paragraph["id"]: paragraph for paragraph in paragraphs}
    definitions = [
        ("opening-room", "p001", 1, "Berkeley · Monday evening", "present",
         "Adrian watches breaking news in his modest craftsman living room.",
         "Establish the room and the news broadcast before the narration begins."),
        ("boardroom-dismissal", "p004", 3, "Growth advisors", "memory",
         "Adrian recalls the growth advisors arriving and treating users as markets and ethics as brand risk.",
         "The arrival of the growth advisors turns his memory into a specific conflict."),
        ("kitchen-calculation", "p005", 3, "The later stakes", "memory",
         "Adrian remembers being alone in his kitchen, calculating what his later equity stakes might have been worth.",
         "The narration moves from his departure to the private cost of being pushed out."),
        ("unnamed-suspect", "p006", 1, "The broadcast", "broadcast",
         "Television footage shows an unnamed, slight East Asian man in his mid-twenties, escorted in handcuffs, with a faint unsettling smile.",
         "The footage explicitly cuts from the tower to the perp walk."),
        ("personal-connection", "p009", 1, "NovaMind", "present",
         "Adrian recognizes NovaMind. His hand hesitates near the remote, the glass tower broadcast reflected in the room. His name has not appeared on television yet.",
         "Recognizing the company makes the distant broadcast personal to Adrian."),
        ("cognitive-grid", "p009", 5, "The cognitive age", "symbolic",
         "Symbolic imagery interprets foundation models as the electrical grid of the cognitive age; it does not depict a literal story event.",
         "The narrator's infrastructure metaphor invites a visual change from the room to the scale of the technology."),
        ("classroom-lever", "p011", 1, "Teaching", "reflection",
         "An illustrative view of Adrian teaching his next generation of students accompanies his reflection on education as a way of fighting back.",
         "His answer to being pushed out is teaching, and the paragraph turns toward his students."),
        ("people-as-clay", "p011", 7, "People became clay", "symbolic",
         "Symbolic imagery interprets Adrian's argument that prediction systems shape people like clay; no literal transformation occurs in the story.",
         "The short sentence crystallizes his ethical argument into its strongest image."),
    ]
    scenes = []
    for scene_id, paragraph_id, sentence_number, title, kind, description, reason in definitions:
        sentence = by_id[paragraph_id]["sentences"][sentence_number - 1]
        scenes.append({"id": scene_id, "src": f"assets/{scene_id}.webp", "start": 0 if not scenes else sentence["start"],
                       "paragraphId": paragraph_id, "sentenceId": sentence["id"],
                       "title": title, "kind": kind, "description": description,
                       "narrationTrigger": sentence["text"], "reason": reason})
    return scenes


def verify(payload, canonical_paragraphs):
    paragraphs = payload["paragraphs"]
    assert paragraphs and payload["duration"] > paragraphs[-1]["end"]
    assert 240 <= payload["duration"] <= 360, "Pilot should end at a full paragraph in four to six minutes"
    assert len(paragraphs) == len(canonical_paragraphs)
    previous_end = 0.0
    sentence_count = 0
    for paragraph, canonical in zip(paragraphs, canonical_paragraphs):
        assert paragraph["markdown"] == canonical, "Canonical manuscript text changed"
        assert paragraph["text"] == display_markdown(canonical)[0]
        assert paragraph["start"] >= previous_end - 0.05
        assert paragraph["start"] < paragraph["end"] <= payload["duration"]
        assert " ".join(sentence["text"] for sentence in paragraph["sentences"]) == paragraph["text"]
        previous_sentence_end = paragraph["start"]
        for sentence in paragraph["sentences"]:
            assert paragraph["start"] - 0.001 <= sentence["start"] < sentence["end"] <= paragraph["end"] + 0.001
            assert sentence["start"] >= previous_sentence_end - 0.05
            previous_sentence_end = sentence["end"]
            sentence_count += 1
        for span in paragraph["emphasis"]:
            assert 0 <= span["start"] < span["end"] <= len(paragraph["text"])
        previous_end = paragraph["end"]
    assert payload["scenes"][0]["start"] == 0
    assert [scene["start"] for scene in payload["scenes"]] == sorted(scene["start"] for scene in payload["scenes"])
    assert len({scene["id"] for scene in payload["scenes"]}) == len(payload["scenes"])
    by_id = {paragraph["id"]: paragraph for paragraph in paragraphs}
    for scene in payload["scenes"]:
        assert scene["paragraphId"] in by_id
        sentence_by_id = {sentence["id"]: sentence for sentence in by_id[scene["paragraphId"]]["sentences"]}
        assert scene["sentenceId"] in sentence_by_id
        sentence = sentence_by_id[scene["sentenceId"]]
        assert scene["narrationTrigger"] == sentence["text"]
        if scene["start"]:
            assert scene["start"] == sentence["start"]
        assert scene["start"] < payload["duration"]
        if scene["id"] in {"cognitive-grid", "people-as-clay"}:
            assert scene["kind"] == "symbolic" and "Symbolic" in scene["description"]
    return {"canonicalParagraphsPreserved": True, "monotonicParagraphTimings": True,
            "sentenceTextReconstructsParagraphs": True, "sentenceBoundsWithinParagraphs": True,
            "monotonicSentenceTimings": True,
            "sceneCuesOnCanonicalSentenceStarts": True, "symbolicImageryClearlyIdentified": True,
            "endsAfterWholeParagraph": True, "paragraphsChecked": len(paragraphs),
            "sentencesChecked": sentence_count, "scenesChecked": len(payload["scenes"])}


def build(encode=True):
    source_text = CANONICAL.read_text(encoding="utf-8")
    chapter = source_text.split("## Chapter 1\n", 1)[1].split("\n---", 1)[0].strip()
    canonical = chapter.split("\n\n")
    manifest_path = JOB / "generation-manifest.json"
    manifest = read_json(manifest_path)
    qa_path = JOB / "mastered/chapter-001.qa.json"
    checkpoint_path = JOB / "mastered/chapter-001.checkpoint.json"
    qa, checkpoint = read_json(qa_path), read_json(checkpoint_path)
    items = sorted((item for item in manifest["items"] if item["chapter"] == 1), key=lambda item: item["chunk"])
    inputs = checkpoint["identity"]["inputs"]
    assert [item["id"] for item in items] == [item["id"] for item in inputs]
    timeline = qa["coordinator_checks"]["chunk_timeline"]
    assert [item["id"] for item in items] == [chunk["id"] for chunk in timeline]
    expected, heard, heard_times = [], [], []
    cache_sources, chunk_diagnostics = [], []
    rate = qa["coordinator_checks"]["sample_rate"]
    trimmed_start = qa["assembly"]["leading_source_samples_trimmed"] / rate
    for item, input_record, chunk in zip(items, inputs, timeline):
        assert item["request_sha256"] == input_record["request_sha256"]
        path, cache = locate_cache(item, input_record)
        assert sha256(Path(item["output"])) == input_record["audio_sha256"]
        offset = chunk["master_start_seconds"] - (trimmed_start if item["chunk"] == 1 else 0)
        expected += tokens(item["text"])
        for segment in cache["segments"]:
            for word in segment["words"]:
                normalized = tokens(word["word"])
                heard += normalized
                heard_times += [(word["start"] + offset, word["end"] + offset)] * len(normalized)
        cache_sources.append({"path": str(path.relative_to(ROOT)), "sha256": sha256(path),
                              "requestSha256": item["request_sha256"], "audioSha256": input_record["audio_sha256"]})
        chunk_diagnostics.append({"id": item["id"], "masterOffset": round(offset, 6),
                                  "sourceDuration": input_record["duration_seconds"]})

    intro_texts = ["Part One. The Surface.", "Chapter One."]
    all_display = intro_texts + [display_markdown(paragraph)[0] for paragraph in canonical]
    display_tokens = [token for text in all_display for token in tokens(text)]
    assert display_tokens == expected, "Selected narration text no longer agrees with canonical chapter"
    timings, exact, differences = align(expected, heard, heard_times)
    intro, paragraphs, cursor = [], [], 0
    for index, text in enumerate(all_display):
        count = len(tokens(text))
        start = round(timings[cursor][0], 3)
        end = round(timings[cursor + count - 1][1], 3)
        if index < len(intro_texts):
            intro.append({"id": "part-one" if index == 0 else "chapter-one", "text": text, "start": start, "end": end})
        else:
            paragraph_number = index - len(intro_texts) + 1
            paragraph_id = f"p{paragraph_number:03d}"
            sentence_cursor, sentences = cursor, []
            for sentence_index, (a, b) in enumerate(sentence_ranges(text), 1):
                sentence_text = text[a:b]
                sentence_count = len(tokens(sentence_text))
                sentences.append({"id": f"{paragraph_id}-s{sentence_index:02d}", "text": sentence_text,
                                  "start": round(timings[sentence_cursor][0], 3),
                                  "end": round(timings[sentence_cursor + sentence_count - 1][1], 3),
                                  "textStart": a, "textEnd": b})
                sentence_cursor += sentence_count
            assert sentence_cursor == cursor + count
            plain, emphasis = display_markdown(canonical[paragraph_number - 1])
            paragraphs.append({"id": paragraph_id, "text": plain, "markdown": canonical[paragraph_number - 1],
                               "emphasis": emphasis, "start": start, "end": end, "sentences": sentences,
                               "alignment": {"exactTokenFraction": round(sum(exact[cursor:cursor + count]) / count, 4)}})
        cursor += count

    # A complete paragraph nearest five minutes. Keep the following paragraph
    # solely to place the cut in a verified gap rather than on the last syllable.
    candidates = [(abs(paragraph["end"] - 300), index) for index, paragraph in enumerate(paragraphs[:-1])
                  if 240 <= paragraph["end"] <= 359]
    _, ending_index = min(candidates)
    selected = paragraphs[:ending_index + 1]
    last_speech = selected[-1]["end"]
    next_speech = paragraphs[ending_index + 1]["start"]
    assert next_speech - last_speech >= 0.2, "Natural end needs a pause without clipping speech"
    duration = round(last_speech + min(0.65, (next_speech - last_speech) / 2), 3)
    master_wav = JOB / "mastered/chapter-001.wav"
    master_mp3 = JOB / "mastered/chapter-001.mp3"
    assert sha256(master_wav) == checkpoint["output_sha256"]["lossless_wav"]
    assert sha256(master_mp3) == checkpoint["output_sha256"]["mp3"]
    audio_source = "assets/chapter-001-pilot.mp3"
    payload = {"schemaVersion": 1, "id": "chapter-001-pilot", "bookId": "lumen", "manuscriptVersion": "v6",
               "chapterId": "chapter-001", "book": "LUMEN", "title": "Chapter 1",
               "part": "Part One · The Surface", "author": "Rob Xie", "edition": "v6", "chapter": 1,
               "narration": "AI-generated Gemini voice, Charon", "duration": duration,
               "fullChapterDuration": qa["master_pcm_qa"]["duration_seconds"], "audioSrc": audio_source,
               "audio": {"src": audio_source, "duration": duration, "format": "audio/mpeg"},
               "intro": intro, "paragraphs": selected,
               "scenes": make_scenes(selected),
               "sourceHashes": {"canonicalManuscript": sha256(CANONICAL), "generationManifest": sha256(manifest_path),
                                "masteredWav": checkpoint["output_sha256"]["lossless_wav"],
                                "masteredMp3": checkpoint["output_sha256"]["mp3"], "masteringQa": sha256(qa_path)},
               "timingNote": "Sentence cues use existing ASR word timestamps mapped to the mastered recording. Canonical manuscript wording is preserved. Cues have not received human listening approval."}

    checks = verify(payload, canonical[:len(selected)])
    if encode:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("ffmpeg and ffprobe are required for the pilot audio export")
        # Verify that the export and its brief fade land inside a measured quiet
        # gap. ASR endpoints alone are not sufficient evidence for a clean cut.
        pause_window_start = max(0, last_speech - 0.25)
        pause_window_end = next_speech + 0.25
        pause_probe = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-ss", str(pause_window_start),
                                      "-t", str(pause_window_end - pause_window_start), "-i", str(master_wav),
                                      "-af", "silencedetect=noise=-45dB:d=0.08", "-f", "null", "-"],
                                     check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        quiet_regions, quiet_start = [], None
        for event, value in re.findall(r"silence_(start|end): ([\d.]+)", pause_probe.stderr):
            if event == "start":
                quiet_start = pause_window_start + float(value)
            elif quiet_start is not None:
                quiet_regions.append((quiet_start, pause_window_start + float(value)))
                quiet_start = None
        if quiet_start is not None:
            quiet_regions.append((quiet_start, pause_window_end))
        cut_pause = next((region for region in quiet_regions if region[0] <= duration - 0.10 and duration <= region[1]), None)
        assert cut_pause, "Export and fade must lie in a measured quiet gap"
        checks["cutAndFadeInsideMeasuredQuietGap"] = True
        AUDIO.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(master_wav),
                        "-t", str(duration), "-af", f"afade=t=out:st={duration - 0.10:.3f}:d=0.10",
                        "-c:a", "libmp3lame", "-b:a", "128k", "-map_metadata", "-1",
                        "-metadata", "title=Lumen — Chapter 1 pilot", "-metadata", "artist=Rob Xie", str(AUDIO)], check=True)
        probe = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_entries",
                                                   "format=duration,size:stream=codec_name,sample_rate,channels", "-of", "json", str(AUDIO)]))
        decoded = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(AUDIO),
                                  "-f", "f32le", "-ac", "1", "-ar", str(rate), "pipe:1"],
                                 check=True, stdout=subprocess.PIPE).stdout
        decoded_duration = len(decoded) / 4 / rate
        assert abs(decoded_duration - duration) <= 1 / rate
        checks["exportDecodedDurationMatchesCut"] = True
        payload["audio"]["sha256"] = sha256(AUDIO)
        payload["audio"]["bytes"] = AUDIO.stat().st_size
        payload["audio"]["decodedDuration"] = round(decoded_duration, 6)
    else:
        # A scene-only refresh must preserve the existing MP3 byte for byte.
        # Reuse its verified export facts only when all source identities and
        # the calculated paragraph end still match the saved pilot.
        saved_payload, saved_report = read_json(DATA), read_json(REPORT)
        assert saved_payload["sourceHashes"] == payload["sourceHashes"], "Audio sources changed; a scene-only refresh cannot re-export audio"
        assert saved_payload["duration"] == duration
        assert saved_payload["audio"]["sha256"] == sha256(AUDIO)
        assert saved_payload["audio"]["bytes"] == AUDIO.stat().st_size
        payload["audio"] = saved_payload["audio"]
        probe = saved_report["audioProbe"]
        cut_pause = saved_report["cut"]["measuredQuietRegion"]
        for key in ["cutAndFadeInsideMeasuredQuietGap", "exportDecodedDurationMatchesCut"]:
            assert saved_report["checks"][key]
            checks[key] = True
        checks["audioPreservedWithoutReencoding"] = True
        checks["audioHashMatches"] = True

    included_tokens = len(tokens(" ".join(intro_texts + [paragraph["text"] for paragraph in selected])))
    report = {"method": "Canonical text → selected narration tokens → ASR matching word runs → mastered chunk timeline",
              "edition": "v6", "chapter": 1, "checks": checks, "duration": duration,
              "paragraphCount": len(selected), "sentenceCount": sum(len(paragraph["sentences"]) for paragraph in selected),
              "sceneCount": len(payload["scenes"]),
              "sceneCues": [{key: scene[key] for key in ["id", "start", "paragraphId", "sentenceId", "kind", "narrationTrigger"]}
                            for scene in payload["scenes"]],
              "exactNormalizedTokens": sum(exact[:included_tokens]), "canonicalNormalizedTokens": included_tokens,
              "exactTokenFraction": round(sum(exact[:included_tokens]) / included_tokens, 6),
              "interpolatedCanonicalTokens": included_tokens - sum(exact[:included_tokens]),
              "cut": {"lastCanonicalSpeechEnd": last_speech, "nextCanonicalSpeechStart": next_speech,
                      "cutAt": duration, "silentTailAfterLastRecognizedWord": round(duration - last_speech, 3),
                      "fadeOutSeconds": 0.10, "finalParagraphId": selected[-1]["id"],
                      "measuredQuietRegion": ([round(value, 6) for value in cut_pause] if cut_pause else None),
                      "quietThresholdDbfs": -45,
                      "finalWords": selected[-1]["text"][-150:]},
              "masteredTimeline": chunk_diagnostics, "recognitionDifferences": differences,
              "asrSources": cache_sources, "audioProbe": probe,
              "limitations": ["ASR word timestamps are approximate; there is no measured human-reviewed timing error bound.",
                              "Recognition spelling/name differences use local interpolation; canonical display text is not rewritten.",
                              "Existing content QA and these timing checks do not constitute human listening approval."],
              "listeningApproved": False}
    payload["audio"]["narratorId"] = "charon"
    write_json(DATA, payload)
    browser_data = WEB / "dist/data/chapter-001.js"
    browser_data.write_text("window.LUMEN_CHAPTER = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")
    write_json(REPORT, report)
    return payload, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify-only", action="store_true", help="Verify saved text, cues, and audio hash without rebuilding")
    modes.add_argument("--refresh-data", action="store_true", help="Refresh text and scene cues while preserving the verified MP3 exactly")
    args = parser.parse_args()
    if args.verify_only:
        payload = read_json(DATA)
        chapter = CANONICAL.read_text(encoding="utf-8").split("## Chapter 1\n", 1)[1].split("\n---", 1)[0].strip()
        checks = verify(payload, chapter.split("\n\n")[:len(payload["paragraphs"])])
        assert sha256(AUDIO) == payload["audio"]["sha256"]
        checks["audioHashMatches"] = True
        browser_source = (WEB / "dist/data/chapter-001.js").read_text(encoding="utf-8")
        assert browser_source == "window.LUMEN_CHAPTER = " + json.dumps(payload, ensure_ascii=False) + ";\n"
        checks["browserDataMatchesPreparedData"] = True
        print(json.dumps(checks, indent=2))
    else:
        payload, report = build(encode=not args.refresh_data)
        print(json.dumps({"data": str(DATA.relative_to(ROOT)), "audio": str(AUDIO.relative_to(ROOT)),
                          "duration": payload["duration"], "paragraphs": len(payload["paragraphs"]),
                          "scenes": len(payload["scenes"]),
                          "exactTokenFraction": report["exactTokenFraction"], "checks": report["checks"]}, indent=2))


if __name__ == "__main__":
    main()
