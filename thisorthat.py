"""Videa „To nebo to“ vyrobená od nuly: otázky -> obrazovky -> svislé video se zvukem odpočtu."""
import json
import re
import subprocess
import tempfile
import unicodedata
import uuid
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from platform_utils import ffmpeg_path, hidden_subprocess_kwargs, resource
from selector import _loads_lenient

W, H = 1080, 1920
HEADER = 190
FONT_PATH = resource("assets", "DejaVuSansCondensed-Bold.ttf")
READ_S, COUNT_S, END_S, FPS = 2.5, 3, 2.5, 30
MAX_ROUNDS = 12

PALETTES = [("#ff4d6d", "#2d7ff9"), ("#ffb703", "#7b2ff7"), ("#06d6a0", "#ef476f"), ("#f95738", "#0d6efd")]
INK = (16, 19, 26)

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
        "- Piš česky, přirozeně.\n"
        "- Každá možnost je krátká, nejvýše 4 slova, aby se vešla na obrazovku.\n"
        "- Obě možnosti mají být vyvážené, aby se divák těžko rozhodoval. Žádná nemá být zjevně lepší.\n"
        "- Otázky se v rámci videí neopakují.\n"
        "- Nic urážlivého, sexuálního, politického ani o zdraví a tragédiích.\n"
        "- Každé video dostane krátký název (nejvýše 5 slov) a otázky jdou od lehčích ke zábavnějším.\n\n"
        "Odpověz POUZE jedním blokem kódu ve formátu JSON a nic dalšího nepiš. Formát:\n"
        "```json\n"
        '{"videos": [{"title": "Jídlo", "rounds": [{"a": "Pizza", "b": "Burger"}, {"a": "Léto", "b": "Zima"}]}]}\n'
        "```"
    )


def _clean(text, limit=60):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


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
                    rounds.append({"a": a, "b": b})
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


# ------------------------------------------------------------------ obraz

def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _text_block(draw, text, cx, cy, max_w, max_h, start=150, fill=(255, 255, 255)):
    """Vycentruje text do rámečku, velikost se zmenšuje, dokud se nevejde."""
    text = text.upper()
    for size in range(start, 39, -6):
        font = _font(size)
        lines = _wrap(draw, text, font, max_w)
        if all(draw.textlength(w, font=font) <= max_w for w in text.split()) and len(lines) * size * 1.18 <= max_h:
            break
    step = size * 1.18
    y = cy - step * len(lines) / 2 + step / 2
    for ln in lines:
        draw.text((cx, y), ln, font=font, fill=fill, anchor="mm", stroke_width=max(3, size // 22), stroke_fill=(0, 0, 0))
        y += step


def _frame(rnd, idx, total, title, pal, count=None):
    img = Image.new("RGB", (W, H), _rgb(pal[0]))
    d = ImageDraw.Draw(img)
    mid = (HEADER + H) // 2
    d.rectangle([0, mid, W, H], fill=_rgb(pal[1]))
    d.rectangle([0, 0, W, HEADER], fill=INK)
    d.text((60, HEADER // 2), title.upper(), font=_font(54), fill=(255, 255, 255), anchor="lm")
    d.text((W - 60, HEADER // 2), f"{idx} / {total}", font=_font(54), fill=_rgb(pal[0]), anchor="rm")

    reg_h = mid - HEADER
    box_h = reg_h - 2 * 150
    _text_block(d, rnd["a"], W // 2, HEADER + (reg_h - 90) // 2, W - 140, box_h)
    _text_block(d, rnd["b"], W // 2, mid + 90 + (H - mid - 90) // 2, W - 140, box_h)

    r = 118
    d.ellipse([W // 2 - r - 10, mid - r - 10, W // 2 + r + 10, mid + r + 10], fill=INK)
    d.ellipse([W // 2 - r, mid - r, W // 2 + r, mid + r], fill=(255, 255, 255))
    if count is None:
        d.text((W // 2, mid), "NEBO", font=_font(62), fill=INK, anchor="mm")
    else:
        d.text((W // 2, mid - 4), str(count), font=_font(170), fill=_rgb(pal[0] if count != 1 else pal[1]), anchor="mm")
    return img


def _end_frame(title, pal):
    img = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, H // 2 - 6, W, H // 2 + 6], fill=_rgb(pal[0]))
    _text_block(d, "Co by sis vybral?", W // 2, H // 2 - 330, W - 140, 400, start=130)
    _text_block(d, "Napiš to do komentářů", W // 2, H // 2 + 250, W - 140, 300, start=90, fill=_rgb(pal[1]))
    d.text((W // 2, H - 150), title.upper(), font=_font(46), fill=(150, 156, 170), anchor="mm")
    return img


# ------------------------------------------------------------------ zvuk

SR = 44100


def _tone(buf, t0, freq, dur=0.14, vol=0.5):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t) * np.exp(-t * 14) * vol
    a = int(t0 * SR)
    buf[a:a + n] += sig[: max(0, len(buf) - a)]


def _write_audio(path, total_s, events):
    buf = np.zeros(int(total_s * SR) + SR // 2, dtype=np.float64)
    for t0, f, d, v in events:
        _tone(buf, t0, f, d, v)
    pcm = (np.clip(buf, -1, 1) * 32000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ video

def render_video(video, out_path, palette_idx=0):
    pal = PALETTES[palette_idx % len(PALETTES)]
    rounds, title = video["rounds"], video["title"]
    seq, events, t = [], [], 0.0  # seq: (obrázek, délka)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        def add(img, dur):
            p = tmp / f"f{len(seq):03d}.png"
            img.save(p)
            seq.append((p, dur))

        for i, rnd in enumerate(rounds, 1):
            events.append((t, 520, 0.18, 0.45))
            add(_frame(rnd, i, len(rounds), title, pal), READ_S)
            t += READ_S
            for n in range(COUNT_S, 0, -1):
                events.append((t, 784 if n > 1 else 1046, 0.16, 0.55))
                add(_frame(rnd, i, len(rounds), title, pal, count=n), 1.0)
                t += 1.0
        events += [(t, 784, 0.3, 0.5), (t + 0.12, 1046, 0.4, 0.5)]
        add(_end_frame(title, pal), END_S)
        t += END_S

        audio = tmp / "audio.wav"
        _write_audio(audio, t, events)
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
    return t


def _slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:40] or "video"


def generate(videos, topic, out_root, log=print, progress=None):
    """Vyrobí všechna videa do složky ton-xxxxxx a zapíše clips.json (zobrazí se v záložce Klipy)."""
    folder_id = "ton-" + uuid.uuid4().hex[:6]
    out_dir = Path(out_root) / folder_id
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, v in enumerate(videos, 1):
        name = f"{i:02d}_{_slug(v['title'])}.mp4"
        log(f"Vytvářím video {i}/{len(videos)}: {v['title']} ({len(v['rounds'])} otázek)")
        if progress:
            progress(5 + int(90 * (i - 1) / len(videos)), f"Vytvářím video {i} z {len(videos)}")
        dur = render_video(v, out_dir / name, palette_idx=i - 1)
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
