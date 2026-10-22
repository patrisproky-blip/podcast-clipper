"""Výběr nejlepších momentů z přepisu: přes Claude API, nebo ručně přes chat."""
import json
import math
import re

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

RULES = """Jsi zkušený střihač krátkých vertikálních videí (TikTok, YouTube Shorts, Instagram Reels) \
z podcastů. Z přepisu vybíráš úseky, které fungují samostatně i bez znalosti zbytku epizody.

Pravidla výběru:
- Silný začátek: v prvních 3 sekundách musí být zajímavé tvrzení, otázka, překvapení nebo konflikt. \
Nezačínej uprostřed myšlenky ani slovy typu "no a tak..." či odpovědí bez kontextu.
- Uzavřená myšlenka: klip končí dokončenou větou/pointou, ne uprostřed.
- Samostatnost: divák nesmí potřebovat předchozí kontext. Pokud je kontext nutný, začni dříve.
- Hodnota: silný příběh, kontroverzní názor, konkrétní rada, vtip, emoce nebo zajímavé číslo/fakt.
- Délka přesně v zadaném rozmezí, časy `start` a `end` v sekundách podle časových značek v přepisu.
- Klipy se nesmí překrývat.
- Nevymýšlej nic, co v přepisu není. Název a hook piš česky, přirozeně, bez clickbaitových lží."""

RULES_TON = """Jsi zkušený střihač krátkých vertikálních videí (TikTok, YouTube Shorts, Instagram Reels) \
z podcastů. Hledáš výhradně úseky, kde se hraje hra „To nebo to“ (anglicky „This or that“ / „Would you rather“): \
někdo nahlas přečte nebo vysloví dvě možnosti a ostatní si vybírají jednu z nich, často s krátkou reakcí nebo vtipem.

Pravidla výběru:
- Klip začíná těsně před otázkou, tedy v okamžiku, kdy zazní první možnost. Nezačínej dřív, žádný úvod ani povídání před hrou.
- Obě možnosti musí být v klipu slyšet celé.
- Klip pokračuje odpověďmi a vtipnými nebo překvapivými reakcemi a končí po dokončené reakci na poslední odpověď, ne uprostřed věty.
- Jedna otázka = jeden klip. Když hra pokračuje další otázkou, začni nový klip. Jen když jsou otázky velmi krátké, můžeš spojit dvě až tři za sebou.
- Vyber nejzábavnější otázky: kontroverzní volby, silné reakce, neshoda mezi hosty, překvapivé nebo vtipné odpovědi. Nudné nebo bez reakce vynech.
- Pokud v přepisu žádná taková hra není, vrať prázdný seznam. Nevymýšlej nic, co v přepisu není.
- Časy `start` a `end` v sekundách podle časových značek v přepisu. Klipy se nesmí překrývat. Název piš česky, krátký, ve tvaru dvou možností (například „Pizza nebo burger?“)."""


def rules(cfg):
    return RULES_TON if cfg.get("clip_style") == "thisorthat" else RULES


SYSTEM = RULES + "\nVrať výsledek voláním nástroje submit_clips."


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
        system=rules(cfg) + "\nVrať výsledek voláním nástroje submit_clips.",
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


def _per_chunk(cfg, n_chunks):
    return max(3, math.ceil(cfg["clips_per_video"] * 2 / max(1, n_chunks)))


def finalize(candidates, transcript, cfg):
    """Přichytí klipy na slova, vyřadí příliš krátké/dlouhé a překrývající se, vybere nejlepší."""
    segments = transcript["segments"]
    words = all_words(transcript)
    duration = transcript.get("duration") or (segments[-1]["end"] if segments else 0)

    ok = []
    for c in candidates:
        c = _snap(c, words, duration)
        length = c["end"] - c["start"]
        if cfg["min_clip_seconds"] - 3 <= length <= cfg["max_clip_seconds"] + 5:
            ok.append(c)

    ok.sort(key=lambda c: c.get("score", 0), reverse=True)
    chosen = []
    for c in ok:
        if all(_overlap(c, k) < 0.3 for k in chosen):
            chosen.append(c)
        if len(chosen) >= cfg["clips_per_video"]:
            break
    chosen.sort(key=lambda c: c["start"])
    return chosen


def select_moments(transcript, cfg):
    """Automatický výběr přes Claude API (potřebuje API klíč s kreditem)."""
    chunks = _chunks(transcript["segments"], cfg["chunk_minutes"] * 60)
    if not chunks:
        return []

    import anthropic  # import až tady, aby aplikace naběhla i bez balíčku a ohlásila srozumitelnou chybu

    client = anthropic.Anthropic()  # čte ANTHROPIC_API_KEY z prostředí
    per_chunk = _per_chunk(cfg, len(chunks))

    candidates = []
    for part in chunks:
        for c in _ask(client, cfg, _format(part), per_chunk):
            try:
                candidates.append({**c, "start": float(c["start"]), "end": float(c["end"])})
            except (KeyError, TypeError, ValueError):
                continue
    return finalize(candidates, transcript, cfg)


# ---------------------------------------------------------------- ruční režim (chat na claude.ai)

def _fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def build_prompts(transcript, cfg):
    """Připraví texty, které uživatel vloží do chatu s Claude. Jeden text na každou část přepisu."""
    chunks = _chunks(transcript["segments"], cfg["chunk_minutes"] * 60)
    n = _per_chunk(cfg, len(chunks))
    out = []
    for part in chunks:
        text = _format(part)
        prompt = (
            f"{rules(cfg)}\n\n"
            f"Délka klipu: {cfg['min_clip_seconds']}–{cfg['max_clip_seconds']} sekund.\n"
            f"Vyber nejvýše {n} nejlepších momentů z následujícího přepisu "
            f"(časové značky jsou v sekundách od začátku celého videa).\n\n"
            "Odpověz POUZE jedním blokem kódu ve formátu JSON a nic dalšího nepiš. Formát:\n"
            "```json\n"
            '{"clips": [{"start": 123.4, "end": 170.2, "title": "Název česky", "hook": "Úvodní věta", '
            '"score": 85, "reason": "Proč je moment silný"}]}\n'
            "```\n"
            "(start a end jsou čísla v sekundách, score je 1 až 100. Pokud nic nevyhovuje, odpověz {\"clips\": []}.)\n\n"
            f"PŘEPIS:\n{text}"
        )
        out.append({
            "label": f"{_fmt_time(part[0]['start'])}–{_fmt_time(part[-1]['end'])}",
            "prompt": prompt,
            "words": len(text.split()),
        })
    return out


def _to_seconds(v):
    if isinstance(v, bool):
        raise ValueError("neplatný čas")
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(",", ".")
    if ":" in t:
        secs = 0.0
        for p in t.split(":"):
            secs = secs * 60 + float(p)
        return secs
    return float(t)


def _loads_lenient(blob):
    """Přečte JSON i s drobnými nepřesnostmi, které chat občas udělá."""
    def attempt(t):
        try:
            return json.loads(t)
        except ValueError:
            return None

    t = blob.strip()
    for cand in (t,):
        r = attempt(cand)
        if r is not None:
            return r
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    for opener, closer in (("{", "}"), ("[", "]")):
        a, b = t.find(opener), t.rfind(closer)
        if a != -1 and b > a:
            piece = t[a:b + 1]
            for cand in (piece, re.sub(r",\s*([}\]])", r"\1", piece)):
                r = attempt(cand)
                if r is not None:
                    return r
    return None


def parse_answer(text):
    """Vytáhne seznam klipů z odpovědi chatu. Při problému vyvolá ValueError se srozumitelnou zprávou."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Odpověď je prázdná.")
    blobs = [m.group(1) for m in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", text, re.S)] + [text]
    data = None
    for b in blobs:
        data = _loads_lenient(b)
        if data is not None:
            break
    if data is None:
        raise ValueError("V odpovědi jsem nenašel seznam klipů ve formátu JSON. "
                         "Požádejte Claude: „Pošli to znovu jako jeden blok JSON.“")
    items = data.get("clips") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("V odpovědi chybí položka „clips“ se seznamem klipů.")

    clips = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            start, end = _to_seconds(it["start"]), _to_seconds(it["end"])
        except (KeyError, ValueError, TypeError):
            continue
        if end <= start:
            continue
        try:
            score = int(float(it.get("score", 50)))
        except (TypeError, ValueError):
            score = 50
        clips.append({
            "start": start, "end": end,
            "title": str(it.get("title") or "Klip")[:120],
            "hook": str(it.get("hook") or ""),
            "score": max(1, min(100, score)),
            "reason": str(it.get("reason") or ""),
        })
    if not clips:
        if items == [] or (isinstance(items, list) and not any(isinstance(i, dict) for i in items)):
            raise ValueError("Claude v této části nenašel nic vhodného (prázdný seznam). Nechte pole prázdné a pokračujte dalšími částmi.")
        raise ValueError("V odpovědi nejsou žádné použitelné klipy (chybí start a end v sekundách).")
    return clips
