"""Automatický výběr momentů bez API klíče: počítá se z přepisu a hlasitosti přímo v počítači.

Není to tak chytré jako Claude (nerozumí obsahu), ale hledá to, co obvykle funguje: živé, hlasité, rychlé úseky
s otázkami, emocemi a silnými slovy, které začínají zajímavou větou a končí dokončenou myšlenkou.
"""
import re

from selector import _sentence_units, finalize

# silná slova (česky a anglicky), hledají se jako začátky slov
_STRONG = (
    "nikdy|vždy|vždycky|problém|tajemství|pravda|pravdu|šílen|neuvěř|nejhorš|nejlepš|obrovsk|peníz|miliard|milion|"
    "zbohat|chyba|chyb|pozor|překvap|úplně|naprost|zabil|zničil|smrt|láska|nenávid|podvod|lež|lže|skandál|"
    "never|always|secret|truth|crazy|insane|worst|best|money|million|billion|mistake|shocking|wrong|love|hate|"
    "kill|died|scam|lie|incredible|unbelievable"
)
_STRONG_RE = re.compile(rf"\b(?:{_STRONG})", re.I)
_FILLER_START = re.compile(r"^(a|no|tak|takže|jo|ehm|hm|ale|protože|který|která|které|and|so|well|um|uh|but|because)\b", re.I)
_NUM_RE = re.compile(r"\d")


def _score_window(units, stream=False):
    text = " ".join(u["text"] for u in units)
    dur = max(1.0, units[-1]["end"] - units[0]["start"])
    words = len(text.split())
    s = 0.0
    s += (60 if stream else 35) * sum(1 for u in units if u.get("loud")) / len(units)   # hlasitost, emoce (u streamu nejdůležitější)
    s += 6 * min(4, text.count("?"))                                        # otázky
    s += 5 * min(4, text.count("!"))
    s += 4 * min(5, len(_STRONG_RE.findall(text)))                          # silná slova
    s += 2 * min(5, len(_NUM_RE.findall(text)) > 0 and 3 or 0)              # čísla a částky
    rate = words / dur
    s += 12 * min(1.0, rate / 3.2)                                          # svižná řeč
    gaps = sum(max(0.0, b["start"] - a["end"]) for a, b in zip(units, units[1:]))
    s -= 40 * gaps / dur                                                    # dlouhé pauzy
    first = units[0]["text"].strip()
    if _FILLER_START.match(first) and not stream:
        s -= 8                                                              # začíná uprostřed myšlenky
    if "?" in first or _STRONG_RE.search(first):
        s += 8                                                              # silný začátek (hook)
    if units[-1]["text"].rstrip().endswith((".", "!", "?")):
        s += 4
    return s


def _clean(t, n):
    t = re.sub(r"\s+", " ", t).strip().strip("-–,;: ")
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0].rstrip(",;:-– ")
    return cut + "…"


def select_local(transcript, cfg):
    units = _sentence_units(transcript)
    if not units:
        return []
    lo, hi = cfg["min_clip_seconds"], cfg["max_clip_seconds"]
    target = (lo + hi) / 2
    cands = []
    for i in range(len(units)):
        j, dur = i, 0.0
        while j < len(units):
            dur = units[j]["end"] - units[i]["start"]
            if dur >= target:
                break
            j += 1
        j = min(j, len(units) - 1)
        win = units[i:j + 1]
        dur = win[-1]["end"] - win[0]["start"]
        if dur < lo - 3 or dur > hi + 5:
            continue
        cands.append((_score_window(win, cfg.get("clip_style") == "stream"), win, dur))
    if not cands:
        return []
    raw = sorted(c[0] for c in cands)
    out = []
    for sc, win, dur in cands:
        rank = raw.index(sc) / max(1, len(raw) - 1)
        best = max(win, key=lambda u: (("?" in u["text"]) * 2 + len(_STRONG_RE.findall(u["text"])) + bool(u.get("loud"))))
        hook = _clean(best["text"], 90)
        why = []
        if sum(1 for u in win if u.get("loud")) / len(win) > 0.3:
            why.append("hlasitý, živý úsek")
        if "?" in " ".join(u["text"] for u in win):
            why.append("obsahuje otázky")
        if _STRONG_RE.search(" ".join(u["text"] for u in win)):
            why.append("silná slova")
        out.append({"start": win[0]["start"], "end": win[-1]["end"], "title": _clean(win[0]["text"], 60),
                    "hook": hook, "score": int(40 + 55 * rank),
                    "reason": ("Automatický výběr: " + ", ".join(why)) if why else "Automatický výběr: svižná řeč"})
    return finalize(out, transcript, cfg)
