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
    kwargs = dict(
        language=lang,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 600, "speech_pad_ms": 250},
        beam_size=5,
        condition_on_previous_text=False,   # u dlouhých záznamů brání zacyklení a vymyšleným větám
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.2,
    )
    if lang == "cs":
        kwargs["initial_prompt"] = "Dobrý den, vítejte u dalšího dílu. Dneska si řekneme, co je nového, a také o tom, jak to celé funguje."
    # novější volby se použijí jen tehdy, když je nainstalovaná verze zná
    import inspect
    supported = set(inspect.signature(model.transcribe).parameters)
    if "hallucination_silence_threshold" in supported:
        kwargs["hallucination_silence_threshold"] = 2.0
    kwargs = {k: v for k, v in kwargs.items() if k in supported}
    segments, info = model.transcribe(str(video_path), **kwargs)

    out = {"language": info.language, "duration": info.duration, "segments": []}
    last_text, repeats = None, 0
    for s in segments:
        # typická chyba přepisu: stále dokola stejná věta. Víc než dvě za sebou se zahodí.
        norm = " ".join(s.text.lower().split())
        repeats = repeats + 1 if norm == last_text else 0
        last_text = norm
        if repeats >= 2:
            continue
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
