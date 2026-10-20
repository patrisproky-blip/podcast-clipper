"""Videa „To nebo to“ vyrobená od nuly: otázky -> obrazovky s obrázky -> hlas, odpočet, procenta."""
import asyncio
import json
import random
import re
import ssl
import subprocess
import tempfile
import unicodedata
import urllib.request
import uuid
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from platform_utils import ffmpeg_path, hidden_subprocess_kwargs, resource
from selector import _loads_lenient

W, H = 1080, 1920
FONT_PATH = resource("assets", "DejaVuSansCondensed-Bold.ttf")
FPS, COUNT_S, MAX_ROUNDS = 30, 3, 12
MIN_READ_S, MIN_REVEAL_S, END_S = 2.5, 2.4, 3.0

RED, BLUE = (250, 40, 40), (10, 50, 250)   # horní půlka červená, spodní modrá (podle vzoru)
INK, GREEN = (16, 19, 26), (56, 224, 120)
VOICES = {"antonin": "cs-CZ-AntoninNeural", "vlasta": "cs-CZ-VlastaNeural", "off": None}
EMOJI_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/2D/png/512/emoji_u{code}.png"

_fonts = {}


def _font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(str(FONT_PATH), size)
    return _fonts[size]


# ------------------------------------------------------------------ otázky

def build_prompt(topic, rounds, videos):
    topic = (topic or "").strip() or "všeobecné"
    return (
        "Vymysli otázky do krátkých videí ve hře „To nebo to“ (anglicky „This or that“). "
        "V každé otázce si divák vybírá jednu ze dvou možností.\n\n"
        f"Téma: {topic}\n"
        f"Počet videí: {videos}\n"
        f"Počet otázek v každém videu: {rounds}\n\n"
        "Pravidla:\n"
        "- Piš česky, přirozeně. Každá možnost je v 1. pádu (nominativu), protože ji bude číst hlas.\n"
        "- Každá možnost je krátká, nejvýše 4 slova, aby se vešla na obrazovku.\n"
        "- Obě možnosti mají být vyvážené, aby se divák těžko rozhodoval. Žádná nemá být zjevně lepší.\n"
        "- Ke každé možnosti přidej jeden emoji, který ji nejlépe vystihuje (bude z něj obrázek). "
        "Když žádný nesedí, dej prázdný řetězec.\n"
        "- Otázky se v rámci videí neopakují.\n"
        "- Nic urážlivého, sexuálního, politického ani o zdraví a tragédiích.\n"
        "- Každé video dostane krátký název (nejvýše 5 slov) a otázky jdou od lehčích ke zábavnějším.\n\n"
        "Odpověz POUZE jedním blokem kódu ve formátu JSON a nic dalšího nepiš. Formát:\n"
        "```json\n"
        '{"videos": [{"title": "Jídlo", "rounds": [{"a": "Pizza", "ea": "🍕", "b": "Burger", "eb": "🍔"}, '
        '{"a": "Léto", "ea": "☀️", "b": "Zima", "eb": "❄️"}]}]}\n'
        "```"
    )


def _clean(text, limit=60):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _emoji(text):
    return re.sub(r"\s+", "", str(text or ""))[:16]


def parse_questions(text):
    """Vytáhne videa z odpovědi chatu. Při problému vyvolá ValueError se srozumitelnou zprávou."""
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
        raise ValueError("V odpovědi jsem nenašel otázky ve formátu JSON. "
                         "Požádejte Claude: „Pošli to znovu jako jeden blok JSON.“")

    if isinstance(data, dict) and "videos" in data:
        items = data["videos"]
    elif isinstance(data, dict) and "rounds" in data:
        items = [data]
    elif isinstance(data, list) and data and isinstance(data[0], dict) and "a" in data[0]:
        items = [{"title": "", "rounds": data}]
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("V odpovědi chybí seznam videí („videos“).")

    videos = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rounds = []
        for r in it.get("rounds") or []:
            if isinstance(r, dict):
                a, b = _clean(r.get("a")), _clean(r.get("b"))
                if a and b:
                    rounds.append({"a": a, "b": b, "ea": _emoji(r.get("ea")), "eb": _emoji(r.get("eb"))})
        if rounds:
            videos.append({"title": _clean(it.get("title"), 50) or "To nebo to", "rounds": rounds[:MAX_ROUNDS]})
    if not videos:
        raise ValueError("V odpovědi nejsou žádné použitelné otázky (každá potřebuje „a“ a „b“).")
    return videos


def questions_via_api(topic, rounds, videos, cfg):
    """Otázky přes Claude API (potřebuje API klíč s kreditem)."""
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=cfg["claude_model"], max_tokens=4096,
        messages=[{"role": "user", "content": build_prompt(topic, rounds, videos)}],
    )
    return parse_questions("".join(b.text for b in resp.content if b.type == "text"))


# ------------------------------------------------------------------ procenta

_BUCKETS = [(51, 57, 0.20, "close"), (58, 72, 0.38, "mid"), (73, 88, 0.30, "strong"), (89, 96, 0.12, "huge")]
_PHRASES = {
    "close": ["Těsně vyhrává: {w}", "Skoro remíza, ale vede: {w}", "Hlasy se dělí skoro napůl, těsně vede: {w}"],
    "mid": ["Většina lidí volí: {w}", "Vyhrává: {w}", "Líp dopadlo: {w}"],
    "strong": ["Jasně vede: {w}", "Tady má navrch: {w}", "Většina je pro: {w}"],
    "huge": ["Drtivě vyhrává: {w}", "Tohle bylo jasné, vyhrává: {w}", "Skoro všichni volí: {w}"],
}


def make_results(n, rng=None):
    """Smyšlená procenta pro zábavu: různorodá, bez opakování stejných čísel za sebou, vítěz se střídá."""
    rng = rng or random.Random()
    out, used, last_tone, sides = [], [], None, []
    for _ in range(n):
        for _try in range(10):
            lo, hi, _, tone = rng.choices(_BUCKETS, weights=[b[2] for b in _BUCKETS])[0]
            if tone != last_tone or rng.random() < 0.15:
                break
        win = rng.randint(lo, hi)
        if win % 5 == 0 and rng.random() < 0.85:
            win = max(lo, min(hi, win + rng.choice([-2, -1, 1, 2])))
        while win in used[-3:]:
            win = win + 1 if win < hi else win - 1
        side = rng.choice("ab")
        if len(sides) >= 2 and sides[-1] == sides[-2] == side:
            side = "b" if side == "a" else "a"
        used.append(win)
        sides.append(side)
        last_tone = tone
        out.append({"win": side, "pct_win": win, "pct_lose": 100 - win, "tone": tone})
    return out


# ------------------------------------------------------------------ obrázky (Noto Emoji, licence Apache 2.0)

def _emoji_image(emoji, cache_dir, failed):
    if not emoji or not cache_dir:
        return None
    code = "_".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)
    path = Path(cache_dir) / f"{code}.png"
    if not path.exists() and code not in failed:
        try:
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:  # noqa: BLE001
                ctx = ssl.create_default_context()
            with urllib.request.urlopen(EMOJI_URL.format(code=code), timeout=15, context=ctx) as r:
                data = r.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except Exception:  # noqa: BLE001
            failed.add(code)
            return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ vykreslení

SX, SY = 0.78, 1.1   # písmo se zúží a prodlouží, aby působilo jako úzké tučné titulkové písmo
MID = H // 2
BAND = 16            # polovina výšky černého proužku mezi půlkami
PIC = 430            # velikost obrázku


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) * SX <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _line_layer(text, size, fill):
    font = _font(size)
    sw = max(4, size // 8)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    w = int(probe.textlength(text, font=font)) + 2 * sw + 8
    h = int(size * 1.35) + 2 * sw
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((sw + 4, h // 2), text, font=font, fill=fill, anchor="lm",
                               stroke_width=sw, stroke_fill=(0, 0, 0))
    return layer.resize((max(1, int(w * SX)), max(1, int(h * SY))), Image.LANCZOS)


def _text_block(img, text, cx, cy, max_w, max_h, start=170, fill=(255, 255, 255)):
    """Vycentruje text do rámečku, velikost se zmenšuje, dokud se nevejde."""
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    for size in range(start, 47, -6):
        font = _font(size)
        lines = _wrap(probe, text, font, max_w)
        step = size * SY * 1.12
        if all(probe.textlength(w, font=font) * SX <= max_w for w in text.split()) and len(lines) * step <= max_h:
            break
    y = cy - step * len(lines) / 2 + step / 2
    for ln in lines:
        layer = _line_layer(ln, size, fill)
        img.paste(layer, (int(cx - layer.width / 2), int(y - layer.height / 2)), layer)
        y += step


def _half(img, top_half, text, pic, pct, winner):
    """Jedna půlka podle vzoru: obrázek na kraji, text blíž ke středu, procenta mezi textem a středem."""
    cx = W // 2
    sign = 1 if top_half else -1                     # směr od okraje ke středu
    edge = 40 if top_half else H - 40
    if pic is not None:
        p = pic.resize((PIC, PIC), Image.LANCZOS)
        y0 = edge if top_half else edge - PIC
        img.paste(p, (cx - PIC // 2, y0), p)
        text_c = edge + sign * (PIC + 110)
        _text_block(img, text, cx, text_c, W - 120, 200, start=170)
    else:
        text_c = edge + sign * 330
        _text_block(img, text, cx, text_c, W - 120, 520, start=210)
    if pct is not None:
        color = GREEN if winner else (255, 255, 255)
        _text_block(img, f"{pct} %", cx, MID - sign * 185, W - 200, 175, start=170, fill=color)


def _circle(img, label, size):
    d = ImageDraw.Draw(img)
    r = 84
    d.ellipse([W // 2 - r, MID - r, W // 2 + r, MID + r], fill=(30, 30, 30))
    layer = _line_layer(label, size, (255, 255, 255))
    img.paste(layer, (W // 2 - layer.width // 2, MID - layer.height // 2 - 2), layer)


def _frame(rnd, idx, total, title, pics, count=None, reveal=None):
    img = Image.new("RGB", (W, H), RED)
    d = ImageDraw.Draw(img)
    d.rectangle([0, MID, W, H], fill=BLUE)
    d.rectangle([0, MID - BAND, W, MID + BAND], fill=(0, 0, 0))
    pa = pb = None
    if reveal:
        pa = reveal["pct_win"] if reveal["win"] == "a" else reveal["pct_lose"]
        pb = reveal["pct_win"] if reveal["win"] == "b" else reveal["pct_lose"]
    _half(img, True, rnd["a"], pics[0], pa, bool(reveal) and reveal["win"] == "a")
    _half(img, False, rnd["b"], pics[1], pb, bool(reveal) and reveal["win"] == "b")
    if count is None:
        _circle(img, "nebo", 70)
    else:
        _circle(img, str(count), 120)
    return img


def _end_frame(title):
    img = Image.new("RGB", (W, H), RED)
    d = ImageDraw.Draw(img)
    d.rectangle([0, MID, W, H], fill=BLUE)
    d.rectangle([0, MID - BAND, W, MID + BAND], fill=(0, 0, 0))
    _text_block(img, "A co ty?", W // 2, MID - 330, W - 120, 400, start=230)
    _text_block(img, "Napiš to do komentářů", W // 2, MID + 330, W - 120, 400, start=150)
    return img


# ------------------------------------------------------------------ zvuk

SR = 44100


def _tone(buf, t0, freq, dur=0.14, vol=0.5):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t) * np.exp(-t * 14) * vol
    a = int(t0 * SR)
    buf[a:a + n] += sig[: max(0, len(buf) - a)]


def synth(text, voice, tmp, name):
    """Namluví text (Microsoft Edge TTS, zdarma, potřebuje internet). Vrátí pole vzorků 44,1 kHz mono."""
    import edge_tts

    mp3 = Path(tmp) / f"{name}.mp3"

    async def run():
        await edge_tts.Communicate(text, voice, rate="+6%").save(str(mp3))

    asyncio.run(run())
    res = subprocess.run(
        [ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **hidden_subprocess_kwargs())
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError("hlas se nepodařilo převést")
    return np.frombuffer(res.stdout, dtype="<i2").astype(np.float64) / 32768.0


def _mix_and_write(path, total_s, events, voices):
    buf = np.zeros(int(total_s * SR) + SR, dtype=np.float64)
    for t0, f, d, v in events:
        _tone(buf, t0, f, d, v)
    for t0, arr in voices:
        a = int(t0 * SR)
        seg = arr[: max(0, len(buf) - a)]
        buf[a:a + len(seg)] += seg * 0.95
    peak = np.abs(buf).max()
    if peak > 0.98:
        buf *= 0.98 / peak
    pcm = (buf * 32000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ video

def render_video(video, out_path, voice=None, cache_dir=None, log=print):
    """voice: kód hlasu z VOICES nebo None. Hlas a obrázky jsou vždy volitelné, bez nich video vznikne také."""
    rounds, title = video["rounds"], video["title"]
    results = make_results(len(rounds))
    seq, events, voices = [], [], []
    failed_img = set()
    voice_name = VOICES.get(voice or "off")
    voice_ok = [bool(voice_name)]
    t = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        def say(text, name):
            """Vrátí (pole vzorků, délka) nebo (None, 0), když hlas není nebo selhal."""
            if not voice_ok[0]:
                return None, 0.0
            try:
                arr = synth(text, voice_name, tmp, name)
                return arr, len(arr) / SR
            except Exception as e:  # noqa: BLE001
                voice_ok[0] = False
                log(f"Upozornění: hlas se nepodařilo vytvořit, video bude bez namluvení ({type(e).__name__}: {e}). "
                    "Hlas potřebuje připojení k internetu.")
                return None, 0.0

        def add(img, dur):
            p = tmp / f"f{len(seq):03d}.png"
            img.save(p)
            seq.append((p, dur))

        used_phrases = []
        for i, (rnd, res) in enumerate(zip(rounds, results), 1):
            pics = (_emoji_image(rnd.get("ea"), cache_dir, failed_img), _emoji_image(rnd.get("eb"), cache_dir, failed_img))
            question = f"{rnd['a']}, nebo {rnd['b']}?"
            if i == 1:
                question = "To nebo to? " + question
            arr, dur = say(question, f"q{i}")
            events.append((t, 520, 0.18, 0.35))
            if arr is not None:
                voices.append((t + 0.25, arr))
            read = max(MIN_READ_S, dur + 0.7)
            add(_frame(rnd, i, len(rounds), title, pics), read)
            t += read

            for n in range(COUNT_S, 0, -1):
                events.append((t, 784 if n > 1 else 1046, 0.16, 0.4))
                add(_frame(rnd, i, len(rounds), title, pics, count=n), 1.0)
                t += 1.0

            winner = rnd["a"] if res["win"] == "a" else rnd["b"]
            options = [p for p in _PHRASES[res["tone"]] if p not in used_phrases] or _PHRASES[res["tone"]]
            phrase = random.choice(options)
            used_phrases.append(phrase)
            arr, dur = say(phrase.format(w=winner), f"r{i}")
            events += [(t, 880, 0.3, 0.4), (t + 0.1, 1318, 0.35, 0.35)]
            if arr is not None:
                voices.append((t + 0.2, arr))
            reveal_s = max(MIN_REVEAL_S, dur + 0.8)
            add(_frame(rnd, i, len(rounds), title, pics, reveal=res), reveal_s)
            t += reveal_s

        arr, dur = say("A co ty? Napiš to do komentářů.", "end")
        if arr is not None:
            voices.append((t + 0.2, arr))
        events += [(t, 784, 0.3, 0.4), (t + 0.12, 1046, 0.4, 0.4)]
        add(_end_frame(title), max(END_S, dur + 0.8))
        t += max(END_S, dur + 0.8)

        audio = tmp / "audio.wav"
        _mix_and_write(audio, t, events, voices)
        listing = []
        for p, dur in seq:
            safe = p.as_posix().replace("'", "'\\''")
            listing += [f"file '{safe}'", f"duration {dur}"]
        listing.append(listing[-2])  # poslední obrázek se musí uvést ještě jednou
        (tmp / "list.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")

        res = subprocess.run(
            [ffmpeg_path(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
             "-i", str(audio), "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "20", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            **hidden_subprocess_kwargs(),
        )
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg selhal: {res.stderr.strip()[-300:]}")
    if failed_img:
        log(f"Upozornění: {len(failed_img)} obrázků se nepodařilo stáhnout (potřebují internet), místo nich je jen text.")
    return t


def _slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:40] or "video"


def generate(videos, topic, out_root, voice="antonin", log=print, progress=None):
    """Vyrobí všechna videa do složky ton-xxxxxx a zapíše clips.json (zobrazí se v záložce Klipy)."""
    folder_id = "ton-" + uuid.uuid4().hex[:6]
    out_dir = Path(out_root) / folder_id
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(out_root).parent / "work" / "emoji"
    results = []
    for i, v in enumerate(videos, 1):
        name = f"{i:02d}_{_slug(v['title'])}.mp4"
        log(f"Vytvářím video {i}/{len(videos)}: {v['title']} ({len(v['rounds'])} otázek)")
        if progress:
            progress(5 + int(90 * (i - 1) / len(videos)), f"Vytvářím video {i} z {len(videos)}")
        dur = render_video(v, out_dir / name, voice=voice, cache_dir=cache_dir, log=log)
        first = v["rounds"][0]
        results.append({
            "file": name, "title": v["title"], "score": 0,
            "hook": f"{first['a']} nebo {first['b']}?",
            "reason": f"{len(v['rounds'])} otázek, {int(dur)} s",
        })
    (out_dir / "clips.json").write_text(
        json.dumps({"video": f"To nebo to: {topic or videos[0]['title']}", "id": folder_id, "clips": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress(100, "Hotovo")
    log(f"Hotovo, videa jsou v: {out_dir}")
    return results
