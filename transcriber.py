"""Přepis řeči na text s časy jednotlivých slov (faster-whisper)."""
import json
import os
import time
from pathlib import Path

# Odhad: kolik minut procesoru s 8 vlákny trvá přepis jedné minuty záznamu. Po prvním přepisu se nahradí změřenou hodnotou.
GUESS_RTF = {"turbo": 0.55, "medium": 0.40, "small": 0.15, "base": 0.06}
AUTO_ORDER = ("turbo", "medium", "small", "base")   # od nejpřesnějšího


def _perf_file(cfg):
    return Path(cfg["work_dir"]).parent / "perf.json"


def _load_perf(cfg):
    try:
        return json.loads(_perf_file(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _threads():
    return max(2, min(16, os.cpu_count() or 4))


def audio_duration(path):
    try:
        import av
        with av.open(str(path)) as c:
            return (c.duration or 0) / 1_000_000
    except Exception:  # noqa: BLE001
        return 0.0


def choose_model(cfg, duration_s):
    """Při nastavení „auto“ vybere nejpřesnější model, který se podle odhadu stihne přepsat v časovém limitu."""
    name = cfg["whisper_model"]
    if name != "auto":
        return name, None
    perf = _load_perf(cfg)
    budget = float(cfg.get("max_transcribe_minutes") or 45)
    minutes = max(1.0, (duration_s or 3600) / 60)
    scale = 8.0 / _threads()
    for m in AUTO_ORDER:
        est = minutes * (perf.get(m) or GUESS_RTF[m]) * scale
        if est <= budget:
            return m, est
    return "base", minutes * (perf.get("base") or GUESS_RTF["base"]) * scale


def _record_speed(cfg, model, audio_s, elapsed_s):
    if audio_s < 120 or elapsed_s <= 0:
        return
    rtf8 = (elapsed_s / audio_s) * (_threads() / 8.0)
    perf = _load_perf(cfg)
    old = perf.get(model)
    perf[model] = round(rtf8 if not old else 0.5 * old + 0.5 * rtf8, 4)
    try:
        _perf_file(cfg).write_text(json.dumps(perf), encoding="utf-8")
    except OSError:
        pass


def transcribe(video_path, cfg, cache_path, progress=None, duration=None, log=print):
    cache = Path(cache_path)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    from faster_whisper import WhisperModel

    duration = duration or audio_duration(video_path)
    model_name, est = choose_model(cfg, duration)
    if cfg["whisper_model"] == "auto":
        log(f"Přepis: model „{model_name}“ vybrán automaticky (odhad asi {est:.0f} min).")
    else:
        log(f"Přepis: model „{model_name}“.")
    kw = {"device": cfg["whisper_device"], "compute_type": cfg["whisper_compute_type"]}
    if cfg["whisper_device"] == "cpu":
        kw["cpu_threads"] = _threads()   # výchozí jsou jen 4 vlákna, na silnějších procesorech to zbytečně brzdí
    try:
        model = WhisperModel(model_name, **kw)
    except Exception as e:  # noqa: BLE001
        if model_name in ("turbo", "large-v3-turbo"):
            log(f"Model „{model_name}“ se nepodařilo načíst ({type(e).__name__}), použije se „medium“.")
            model_name = "medium"
            model = WhisperModel(model_name, **kw)
        else:
            raise
    started = time.time()
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

    out = {"language": info.language, "duration": info.duration, "model": model_name, "segments": []}
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
    _record_speed(cfg, model_name, info.duration or duration or 0, time.time() - started)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def all_words(transcript):
    return [w for seg in transcript["segments"] for w in seg["words"]]
