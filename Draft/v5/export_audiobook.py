"""Export narration text, bounded TTS chunks, and a reproducible cost estimate.

This prepares local inputs only. It makes no API calls and incurs no charges.
"""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "audiobook"
MAX_WORDS = 700
PARTS = [
    ("part1.md", "Part One. The Surface."),
    ("part2.md", "Part Two. The Descent."),
    ("part3.md", "Part Three. The Descent."),
    ("part4.md", "Part Four. The Fall."),
    ("part5.md", "Part Five. The Forty Eight Hours."),
]
SPOKEN_FORMS = {
    "HEARTBEAT_FINAL": "Heartbeat Final",
    "sinoatrial_node": "sinoatrial node",
    "sanity_check.py": "sanity check dot pie",
    "CTRL+ALT+DELETE": "Control, Alt, Delete",
    "501(c)(4)": "five oh one, c four",
    ".onion": "dot onion",
}

def number_words(n):
    small = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    if n < 20:
        return small[n]
    if n < 100:
        return tens[n // 10] + (" " + small[n % 10] if n % 10 else "")
    return str(n)

def remaining(match):
    h, m = map(int, match.groups())
    words = []
    if h:
        words.append(number_words(h) + (" hour" if h == 1 else " hours"))
    if m:
        words.append(number_words(m) + (" minute" if m == 1 else " minutes"))
    return (" and ".join(words) + " remaining") if words else "The trigger moment"

def spoken(text):
    text = re.sub(r"\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*(.+?)\*|_([^_]+?)_", lambda m: next(g for g in m.groups() if g is not None), text)
    text = re.sub(r"\bT-(\d{1,2}):(\d{2})\b", remaining, text)
    text = re.sub(r"\bT-minus (\d+) hours\b", lambda m: number_words(int(m.group(1))) + " hours remaining", text)
    for old, new in SPOKEN_FORMS.items():
        text = text.replace(old, new)
    return text.strip()

def word_count(text):
    text = re.sub(r"<[^>]+>", "", text)
    return len(re.findall(r"\b[\w]+(?:['’\-][\w]+)*\b", text))

def main():
    OUT.mkdir(exist_ok=True)
    chapters = []
    current = None
    for filename, part_heading in PARTS:
        pending = [part_heading]
        for line_no, line in enumerate((ROOT / filename).read_text().splitlines(), 1):
            text = line.strip()
            if text.startswith("# EPILOGUE"):
                pending.append("Epilogue. Daylight.")
                continue
            match = re.fullmatch(r"## Chapter (\d+)", text)
            if match:
                n = int(match.group(1))
                current = {"chapter": n, "source": filename, "source_line": line_no,
                           "paragraphs": [*pending, "Chapter " + number_words(n).title() + "."]}
                pending = []
                chapters.append(current)
                continue
            if current is None or text.startswith("# LUMEN") or text == "### A Novel":
                continue
            # Metadata preceding the first chapter of a new part belongs to that part.
            if current["source"] != filename:
                continue
            if not text:
                continue
            if text == "---":
                current["paragraphs"].append("<short pause>")
            elif text.startswith("### "):
                current["paragraphs"].append(spoken(text[4:]) + ".")
            elif text.startswith("## "):
                raise ValueError(f"Unnumbered chapter heading at {filename}:{line_no}: {text}")
            elif not text.startswith("#"):
                current["paragraphs"].append(spoken(text))

    source_front = (ROOT / "part1.md").read_text().split("## Chapter", 1)[0]
    epigraph = next((spoken(line) for line in source_front.splitlines()
                     if line.startswith("*The technology")), "")
    front = "Lumen. A novel." + ("\n\n" + epigraph if epigraph else "")
    (OUT / "front-matter.txt").write_text(front + "\n")
    chunks = [make_chunk({"chapter": 0, "source": "part1.md", "source_line": 1}, [front], 1)]
    book = [front]
    chapter_manifest = []
    for chapter in chapters:
        paragraphs = chapter.pop("paragraphs")
        # A trailing scene divider requires no separate pause before the next chapter.
        while paragraphs and paragraphs[-1] == "<short pause>":
            paragraphs.pop()
        content = "\n\n".join(paragraphs)
        book.append(content)
        (OUT / f"chapter-{chapter['chapter']:03d}.txt").write_text(content + "\n")
        group, total, index = [], 0, 0
        for paragraph in paragraphs:
            size = word_count(paragraph)
            if size > MAX_WORDS:
                raise ValueError("A paragraph exceeds the narration chunk budget")
            if group and total + size > MAX_WORDS:
                index += 1
                chunks.append(make_chunk(chapter, group, index))
                group, total = [], 0
            group.append(paragraph)
            total += size
        if group:
            index += 1
            chunks.append(make_chunk(chapter, group, index))
        chapter_manifest.append({**chapter, "words": word_count(content), "chunks": index})

    narration = "\n\n\n".join(book) + "\n"
    (OUT / "LUMEN-v5-narration.txt").write_text(narration)
    (OUT / "tts-chunks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks))
    words = word_count(narration.replace("<short pause>", ""))
    # Input tokenization has not been measured by Google's countTokens endpoint.
    input_token_estimate = len(narration) / 4 + len(chunks) * 150
    scenarios = []
    for pace in (130, 150, 170):
        seconds = words / pace * 60
        audio_tokens = seconds * 25
        standard = input_token_estimate / 1e6 * 0.50 + audio_tokens / 1e6 * 6.0
        scenarios.append({"words_per_minute": pace, "hours_before_extra_pauses": round(seconds / 3600, 3),
                          "audio_tokens": round(audio_tokens), "standard_usd": round(standard, 2),
                          "batch_usd": round(standard / 2, 2),
                          "standard_with_30_percent_retakes_usd": round(standard * 1.3, 2),
                          "batch_with_30_percent_retakes_usd": round(standard * 0.65, 2)})
    estimate = {"checked_on": "2026-10-09", "model": "gemini-3.8-flash-lite-tts",
                "narration_words": words, "characters": len(narration), "chunks": len(chunks),
                "input_tokens_estimated_not_measured": round(input_token_estimate),
                "rates_usd_per_million_tokens": {"standard_text_input": 0.50, "standard_audio_output": 6.0,
                                                 "batch_text_input": 0.25, "batch_audio_output": 3.0},
                "audio_tokens_per_second": 25,
                "rates_expire": "2026-12-31; published rates double on 2027-01-01",
                "sources": ["https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-flash-lite-tts",
                            "https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-lite-tts"],
                "assumptions": ["Single English narrator; one initial pass", "Duration calculated from narration words and assumed pace",
                                "Text tokens approximated as characters divided by four plus 150 instruction tokens per chunk",
                                "Retake scenarios represent 30 percent additional billed generation",
                                "Excluded: tax, editing, mastering, distribution, additional purchased services",
                                "No audio was generated and no API usage was incurred"],
                "scenarios": scenarios}
    (OUT / "cost-estimate.json").write_text(json.dumps(estimate, indent=2) + "\n")
    (OUT / "manifest.json").write_text(json.dumps({"chapters": chapter_manifest,
        "chunk_word_limit": MAX_WORDS, "total_words": words,
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name, _ in PARTS}}, indent=2) + "\n")
    print(json.dumps({"chapters": len(chapters), "chunks": len(chunks), "words": words, "scenarios": scenarios}, indent=2))

def make_chunk(chapter, paragraphs, index):
    text = "\n\n".join(paragraphs)
    return {"id": f"chapter-{chapter['chapter']:03d}-{index:03d}", **chapter,
            "chunk": index, "model": "gemini-3.8-flash-lite-tts", "text": text,
            "words": word_count(text),
            "speech_metadata": {"speaker": "narrator", "style": "Measured English literary narration. Preserve the wording. Distinguish dialogue through modest changes of delivery; keep one consistent narrator voice."}}

if __name__ == "__main__":
    main()
