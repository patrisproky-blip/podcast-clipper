"""Celý postup pro jedno video: stažení -> přepis -> výběr momentů -> render."""
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


def process_video(url, cfg, log=print, progress=None):
    """progress(pct, stage, **extra) – volitelné hlášení průběhu pro aplikaci."""

    def prog(pct, stage, **extra):
        if progress:
            progress(pct, stage, **extra)

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
        info["path"], cfg, work / f"{vid}.transcript.json",
        progress=lambda f: prog(15 + int(40 * f), "Přepisuji řeč"),
    )

    log("Claude vybírá nejlepší momenty...")
    prog(55, "Claude vybírá nejlepší momenty")
    clips = select_moments(transcript, cfg)
    if not clips:
        log("Nenalezeny žádné vhodné momenty.")
        prog(100, "Hotovo (žádné vhodné momenty)")
        return []

    out_dir = Path(cfg["output_dir"]) / f"{vid}"
    out_dir.mkdir(parents=True, exist_ok=True)
    words = all_words(transcript)

    results = []
    for i, clip in enumerate(clips, 1):
        name = f"{i:02d}_{slugify(clip['title'])}.mp4"
        log(f"Renderuji {i}/{len(clips)}: {clip['title']} ({clip['start']:.0f}–{clip['end']:.0f} s)")
        prog(65 + int(33 * (i - 1) / len(clips)), f"Renderuji klip {i} z {len(clips)}")
        layout = render_clip(info["path"], clip, words, cfg, out_dir / name)
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
