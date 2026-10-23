"""Postup pro jedno video: příprava (stažení + přepis) -> výběr momentů -> vytvoření klipů."""
import json
import re
import unicodedata
from pathlib import Path

from downloader import download_video
from renderer import render_clip
from selector import select_moments
from transcriber import all_words, transcribe


def slugify(text, maxlen=50):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen] or "klip"


def _prog(progress):
    def prog(pct, stage, **extra):
        if progress:
            progress(pct, stage, **extra)
    return prog


def transcript_path(cfg, vid):
    return Path(cfg["work_dir"]) / f"{vid}.transcript.json"


def load_transcript(cfg, vid):
    return json.loads(transcript_path(cfg, vid).read_text(encoding="utf-8"))


def prepare_video(url, cfg, log=print, progress=None):
    """Stáhne video a přepíše řeč. Vrátí (info, transcript)."""
    prog = _prog(progress)
    work = Path(cfg["work_dir"])
    log(f"Stahuji: {url}")
    prog(3, "Stahuji video")
    info = download_video(
        url, work, cfg["video_max_height"],
        on_progress=lambda f: prog(3 + int(12 * f), "Stahuji video"),
    )
    vid = info["id"]
    log(f"Staženo: {info['title']}")
    prog(15, "Přepisuji řeč (první spuštění stáhne model, může to chvíli trvat)", title=info["title"], vid=vid)

    log("Přepisuji řeč (první spuštění stáhne model Whisper)...")
    transcript = transcribe(
        info["path"], cfg, transcript_path(cfg, vid),
        progress=lambda f: prog(15 + int(40 * f), "Přepisuji řeč"),
        duration=info.get("duration"), log=log,
    )
    if "loud" not in transcript:
        try:
            from audioenergy import loud_seconds, loudness_per_second
            prog(55, "Hledám hlasitá místa")
            transcript["loud"] = loud_seconds(loudness_per_second(info["path"]))
            transcript_path(cfg, vid).write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
            log(f"Hlasitá místa: {len(transcript['loud'])} s")
        except Exception as e:  # noqa: BLE001
            transcript["loud"] = []
            log(f"Hlasitost zvuku se nepodařilo změřit ({type(e).__name__}), výběr poběží jen podle textu.")
    return info, transcript


def render_clips(info, transcript, clips, cfg, log=print, progress=None):
    """Z vybraných momentů vytvoří hotové klipy a zapíše clips.json."""
    prog = _prog(progress)
    vid = info["id"]
    if not Path(info["path"]).exists():
        raise FileNotFoundError("Stažené video už neexistuje. Smažte tuto úlohu a zadejte video znovu.")

    out_dir = Path(cfg["output_dir"]) / f"{vid}"
    out_dir.mkdir(parents=True, exist_ok=True)
    words = all_words(transcript)

    results = []
    for i, clip in enumerate(clips, 1):
        name = f"{i:02d}_{slugify(clip['title'])}.mp4"
        log(f"Renderuji {i}/{len(clips)}: {clip['title']} ({clip['start']:.0f}–{clip['end']:.0f} s)")
        prog(65 + int(33 * (i - 1) / len(clips)), f"Vytvářím klip {i} z {len(clips)}")
        layout = render_clip(info["path"], clip, words, cfg, out_dir / name)
        if i == 1:
            import renderer
            log(f"Rozložení: {layout}, detekce tváří: {renderer.DETECTOR}")
        results.append({
            **clip,
            "file": name,
            "layout": layout,
            "source_link": f"https://youtu.be/{vid}?t={int(clip['start'])}",
        })

    (out_dir / "clips.json").write_text(
        json.dumps({"video": info["title"], "id": vid, "clips": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not cfg["keep_source"]:
        Path(info["path"]).unlink(missing_ok=True)

    log(f"Hotovo, klipy jsou v: {out_dir}")
    prog(100, "Hotovo")
    return results


def process_video(url, cfg, log=print, progress=None):
    """Celý automatický postup (výběr momentů přes Claude API)."""
    prog = _prog(progress)
    info, transcript = prepare_video(url, cfg, log, progress)

    log("Claude vybírá nejlepší momenty...")
    prog(55, "Claude vybírá nejlepší momenty")
    clips = select_moments(transcript, cfg)
    if not clips:
        log("Nenalezeny žádné vhodné momenty.")
        prog(100, "Hotovo (žádné vhodné momenty)")
        return []
    return render_clips(info, transcript, clips, cfg, log, progress)
