"""Přepis řeči na text s časy jednotlivých slov (faster-whisper)."""
import json
from pathlib import Path


def transcribe(video_path, cfg, cache_path, progress=None):
    cache = Path(cache_path)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    from faster_whisper import WhisperModel

    model = WhisperModel(
        cfg["whisper_model"],
        device=cfg["whisper_device"],
        compute_type=cfg["whisper_compute_type"],
    )
    lang = cfg["language"] if cfg["language"] not in (None, "", "auto") else None
    segments, info = model.transcribe(
        str(video_path),
        language=lang,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )

    out = {"language": info.language, "duration": info.duration, "segments": []}
    for s in segments:
        if progress and info.duration:
            progress(min(1.0, s.end / info.duration))
        words = [
            {"w": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2)}
            for w in (s.words or [])
            if w.word.strip()
        ]
        out["segments"].append(
            {
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": s.text.strip(),
                "words": words,
            }
        )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def all_words(transcript):
    return [w for seg in transcript["segments"] for w in seg["words"]]
