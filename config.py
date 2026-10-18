"""Načítání konfigurace z config.yaml (s výchozími hodnotami)."""
from pathlib import Path

import yaml

DEFAULTS = {
    "language": "cs",              # "cs", "en", ... nebo "auto"
    "whisper_model": "small",      # "large-v3" je nejpřesnější, ale bez grafické karty velmi pomalý
    "whisper_device": "cpu",       # sestavená aplikace neobsahuje ovladače grafické karty
    "whisper_compute_type": "int8",
    "selection_mode": "manual",    # "manual" = ručně přes chat Claude (zdarma), "api" = automaticky přes API
    "claude_model": "claude-sonnet-5-5",
    "clips_per_video": 6,
    "min_clip_seconds": 25,
    "max_clip_seconds": 75,
    "chunk_minutes": 30,           # po kolika minutách se přepis posílá Claudovi
    "video_max_height": 1080,
    "min_video_minutes": 10,       # kratší videa (shorts, trailery) se přeskočí
    "keep_source": False,          # ponechat stažené původní video
    "output_dir": "output",
    "work_dir": "work",
    "state_file": "state.json",
    "subtitles": {
        "size": 78,
        "uppercase": False,
        "words_per_line": 3,
        "highlight_color": "FFD400",  # RGB hex aktuálně vyslovovaného slova
    },
}


def load(path="config.yaml"):
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    p = Path(path)
    if p.exists():
        user = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg
