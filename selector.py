"""Výběr nejlepších momentů z přepisu pomocí Claude API."""
import math

from transcriber import all_words

TOOL = {
    "name": "submit_clips",
    "description": "Odešli seznam vybraných momentů vhodných na krátká videa.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number", "description": "Začátek klipu v sekundách (podle časů v přepisu)."},
                        "end": {"type": "number", "description": "Konec klipu v sekundách."},
                        "title": {"type": "string", "description": "Krátký chytlavý název klipu (max 70 znaků), česky."},
                        "hook": {"type": "string", "description": "Úvodní věta/teaser, který zaujme v prvních vteřinách."},
                        "score": {"type": "integer", "description": "Virální potenciál 1-100."},
                        "reason": {"type": "string", "description": "Proč je moment silný (jedna věta)."},
                    },
                    "required": ["start", "end", "title", "hook", "score", "reason"],
                },
            }
        },
        "required": ["clips"],
    },
}

SYSTEM = """Jsi zkušený střihač krátkých vertikálních videí (TikTok, YouTube Shorts, Instagram Reels) \
z podcastů. Z přepisu vybíráš úseky, které fungují samostatně i bez znalosti zbytku epizody.

Pravidla výběru:
- Silný začátek: v prvních 3 sekundách musí být zajímavé tvrzení, otázka, překvapení nebo konflikt. \
Nezačínej uprostřed myšlenky ani slovy typu "no a tak..." či odpovědí bez kontextu.
- Uzavřená myšlenka: klip končí dokončenou větou/pointou, ne uprostřed.
- Samostatnost: divák nesmí potřebovat předchozí kontext. Pokud je kontext nutný, začni dříve.
- Hodnota: silný příběh, kontroverzní názor, konkrétní rada, vtip, emoce nebo zajímavé číslo/fakt.
- Délka přesně v zadaném rozmezí, časy `start` a `end` v sekundách podle časových značek v přepisu.
- Klipy se nesmí překrývat.
- Nevymýšlej nic, co v přepisu není. Název a hook piš česky, přirozeně, bez clickbaitových lží.
Vrať výsledek voláním nástroje submit_clips."""


def _chunks(segments, chunk_s, overlap_s=60.0):
    if not segments:
        return []
    end_all = segments[-1]["end"]
    out, start = [], 0.0
    while start < end_all:
        stop = start + chunk_s + overlap_s
        part = [s for s in segments if s["start"] >= start and s["start"] < stop]
        if part:
            out.append(part)
        start += chunk_s
    return out


def _format(part):
    return "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in part)


def _ask(client, cfg, text, n):
    prompt = (
        f"Délka klipu: {cfg['min_clip_seconds']}–{cfg['max_clip_seconds']} sekund.\n"
        f"Vyber nejvýše {n} nejlepších momentů z následujícího přepisu "
        f"(časové značky jsou v sekundách od začátku celého videa).\n\n{text}"
    )
    resp = client.messages.create(
        model=cfg["claude_model"],
        max_tokens=4096,
        system=SYSTEM,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "submit_clips"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input.get("clips", [])
    return []


def _snap(clip, words, duration):
    """Přichytí začátek/konec na hranice slov a přidá drobnou rezervu."""
    if not words:
        return clip
    s = min(words, key=lambda w: abs(w["start"] - clip["start"]))
    e = min(words, key=lambda w: abs(w["end"] - clip["end"]))
    start = max(0.0, s["start"] - 0.15)
    end = min(duration, e["end"] + 0.35)
    return {**clip, "start": round(start, 2), "end": round(end, 2)}


def _overlap(a, b):
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / max(1e-6, min(a["end"] - a["start"], b["end"] - b["start"]))


def select_moments(transcript, cfg):
    segments = transcript["segments"]
    words = all_words(transcript)
    duration = transcript.get("duration") or (segments[-1]["end"] if segments else 0)
    chunks = _chunks(segments, cfg["chunk_minutes"] * 60)
    if not chunks:
        return []

    import anthropic  # import až tady, aby aplikace naběhla i bez balíčku a ohlásila srozumitelnou chybu

    client = anthropic.Anthropic()  # čte ANTHROPIC_API_KEY z prostředí
    per_chunk = max(3, math.ceil(cfg["clips_per_video"] * 2 / len(chunks)))

    candidates = []
    for part in chunks:
        for c in _ask(client, cfg, _format(part), per_chunk):
            try:
                c = {**c, "start": float(c["start"]), "end": float(c["end"])}
            except (KeyError, TypeError, ValueError):
                continue
            c = _snap(c, words, duration)
            length = c["end"] - c["start"]
            if cfg["min_clip_seconds"] - 3 <= length <= cfg["max_clip_seconds"] + 5:
                candidates.append(c)

    candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
    chosen = []
    for c in candidates:
        if all(_overlap(c, k) < 0.3 for k in chosen):
            chosen.append(c)
        if len(chosen) >= cfg["clips_per_video"]:
            break
    chosen.sort(key=lambda c: c["start"])
    return chosen
