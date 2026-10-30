"""Render klipu: střih, svislý formát 9:16 se sledováním tváří a vypálené titulky."""
import bisect
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from platform_utils import ffmpeg_path, hidden_subprocess_kwargs, resource

OUT_W, OUT_H = 1080, 1920
FONT_PATH = resource("assets", "DejaVuSansCondensed-Bold.ttf")  # přibalené písmo, umí češtinu


def _ffmpeg(args):
    res = subprocess.run(
        [ffmpeg_path(), "-y", "-loglevel", "error", *args],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        **hidden_subprocess_kwargs(),
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg selhal: {res.stderr.strip()[-300:]}")


def cut_segment(src, start, end, dst):
    _ffmpeg([
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
        "-c:a", "aac", "-b:a", "192k", str(dst),
    ])


# ---------------------------------------------------------------- detekce tváří

DETECTOR = "žádný"          # který detektor se naposledy použil (do podrobností úlohy)
YUNET_MODEL = resource("assets", "face_detection_yunet_2023mar.onnx")


def _yunet():
    """Přesnější detektor tváří YuNet (model se stáhne při sestavení). Když chybí nebo selže, použije se Haar."""
    try:
        if YUNET_MODEL.exists() and hasattr(cv2, "FaceDetectorYN") and YUNET_MODEL.stat().st_size > 50_000:
            return cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (640, 360), 0.6, 0.3, 500)
    except Exception:  # noqa: BLE001
        pass
    return None


def analyze_faces(cap, fps, n_frames, step_s=0.5):
    global DETECTOR
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    det = _yunet()
    if det is not None:
        scale = 640.0 / W
        sh = int(H * scale)
        step = max(1, int(fps * step_s))
        times, faces_per = [], []
        try:
            det.setInputSize((640, sh))
            gap = max(1, int(fps * 0.12))        # druhý snímek pro měření pohybu úst
            for i in range(0, n_frames, step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                ok, fr = cap.read()
                if not ok:
                    break
                small = cv2.resize(fr, (640, sh))
                _, found = det.detect(small)
                acts = [0.0] * (0 if found is None else len(found))
                if found is not None and len(found) >= 2:
                    # kdo mluví: u kterého obličeje se nejvíc mění spodní polovina (ústa) mezi dvěma snímky
                    ok2, fr2 = cap.read() if gap == 1 else (cap.set(cv2.CAP_PROP_POS_FRAMES, i + gap) and cap.read())
                    if ok2:
                        g1 = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
                        g2 = cv2.cvtColor(cv2.resize(fr2, (640, sh)), cv2.COLOR_BGR2GRAY).astype(np.float32)
                        for k, f in enumerate(found):
                            x0, y0, fw, fh = [int(max(0, v)) for v in f[:4]]
                            ya = y0 + int(fh * 0.55)
                            if fw > 8 and fh > 8:
                                acts[k] = float(np.abs(g1[ya:y0 + fh, x0:x0 + fw] - g2[ya:y0 + fh, x0:x0 + fw]).mean())
                faces_per.append([] if found is None else
                                 [((f[0] + f[2] / 2) / scale, (f[1] + f[3] / 2) / scale, f[2] / scale, acts[k])
                                  for k, f in enumerate(found)])
                times.append(i / fps)
            DETECTOR = "YuNet"
            return times, faces_per
        except Exception:  # noqa: BLE001
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # selhalo: pokračuje se klasickým detektorem
    DETECTOR = "Haar"
    if not hasattr(cv2, "CascadeClassifier"):
        # OpenCV 5 detektor tváří nemá; střih se udělá na střed obrazu.
        print("Upozornění: tato verze OpenCV nemá detektor tváří (nainstaluj opencv-python-headless<5). "
              "Klip bude oříznut na střed.")
        return [], []
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cascade.empty():
        print("Upozornění: soubor detektoru tváří se nenačetl. Klip bude oříznut na střed.")
        return [], []
    scale = 640.0 / W
    step = max(1, int(fps * step_s))
    times, faces_per = [], []
    for i in range(0, n_frames, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            break
        small = cv2.resize(fr, (640, int(H * scale)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        found = cascade.detectMultiScale(gray, 1.1, 6, minSize=(40, 40))
        faces_per.append([((x + w / 2) / scale, (y + h / 2) / scale, w / scale) for x, y, w, h in found])
        times.append(i / fps)
    return times, faces_per


def choose_layout(faces_per, W):
    if not faces_per:
        return "single"
    two = 0
    for f in faces_per:
        if len(f) >= 2:
            xs = sorted(c[0] for c in f)
            if xs[-1] - xs[0] > 0.25 * W:
                two += 1
    return "split" if two / len(faces_per) > 0.5 else "single"


def _smooth(vals, default):
    v = np.array([np.nan if x is None else x for x in vals], dtype=float)
    if len(v) == 0 or np.all(np.isnan(v)):
        return np.full(max(len(v), 1), float(default))
    idx = np.where(~np.isnan(v))[0]
    v = np.interp(np.arange(len(v)), idx, v[idx])
    k = 5
    pad = np.pad(v, (k // 2, k // 2), mode="edge")
    med = np.array([np.median(pad[i:i + k]) for i in range(len(v))])
    out = np.empty_like(med)
    out[0] = med[0]
    for i in range(1, len(med)):
        out[i] = out[i - 1] + 0.35 * (med[i] - out[i - 1])
    return out


def _follow_speaker(faces_per, W):
    """Střed záběru pro každý vzorek. Když jsou v záběru dva lidé, kamera zůstává u toho, kdo mluví.
    Přepne se až když druhý mluví výrazně víc po dobu aspoň dvou vzorků (žádné poskakování)."""
    out, cur, streak = [], None, 0
    for f in faces_per:
        if not f:
            out.append(None)
            continue
        biggest = max(f, key=lambda c: c[2])
        if len(f) < 2 or all(len(c) < 4 for c in f):
            cur, streak = biggest[0], 0
            out.append(cur)
            continue
        act = lambda c: c[3] if len(c) > 3 else 0.0  # noqa: E731
        best = max(f, key=act)
        here = min(f, key=lambda c: abs(c[0] - cur)) if cur is not None else None
        if here is None or abs(here[0] - cur) > 0.2 * W:
            cur, streak = (best if act(best) > 0 else biggest)[0], 0
        elif best is not here and act(best) > 1.6 * act(here) + 0.5:
            streak += 1
            if streak >= 2:
                cur, streak = best[0], 0
            else:
                cur = here[0]
        else:
            cur, streak = here[0], 0
        out.append(cur)
    return out


def build_tracks(times, faces_per, W, layout):
    """Vrátí funkci t -> (cx,) pro single, resp. (cx_left, cx_right) pro split."""
    t = np.array(times if times else [0.0])
    if layout == "single":
        raw = _follow_speaker(faces_per, W)
        s = _smooth(raw, W / 2)
        return lambda x: (float(np.interp(x, t[: len(s)], s)),)
    left, right = [], []
    for f in faces_per:
        xs = sorted(c[0] for c in f)
        if len(xs) >= 2 and xs[-1] - xs[0] > 0.25 * W:
            left.append(xs[0]); right.append(xs[-1])
        elif len(xs) == 1:
            (left if xs[0] < W / 2 else right).append(xs[0])
            (right if xs[0] < W / 2 else left).append(None)
        else:
            left.append(None); right.append(None)
    sl, sr = _smooth(left, W * 0.27), _smooth(right, W * 0.73)
    return lambda x: (float(np.interp(x, t[: len(sl)], sl)), float(np.interp(x, t[: len(sr)], sr)))


def _crop(frame, cx, cw):
    W = frame.shape[1]
    x = int(min(max(cx - cw / 2, 0), W - cw))
    return frame[:, x:x + cw]


def _compose(frame, layout, centers):
    H, W = frame.shape[:2]
    if layout == "single":
        cw = min(W, int(H * 9 / 16))
        return cv2.resize(_crop(frame, centers[0], cw), (OUT_W, OUT_H), interpolation=cv2.INTER_CUBIC)
    cw = min(W, int(H * 9 / 8))
    half = OUT_H // 2
    top = cv2.resize(_crop(frame, centers[0], cw), (OUT_W, half), interpolation=cv2.INTER_CUBIC)
    bot = cv2.resize(_crop(frame, centers[1], cw), (OUT_W, half), interpolation=cv2.INTER_CUBIC)
    return np.vstack([top, bot])


STREAM_SPLIT = 1000   # výška, kde končí hra a začíná kamera


def _cam_box(W, H, cfg):
    """Obdélník s kamerou ve zdrojovém obrazu podle nastavení (roh a šířka v procentech)."""
    cw = int(W * max(10, min(50, int(cfg.get("stream_cam_size", 26)))) / 100)
    ch = int(cw * 0.75)
    corner = cfg.get("stream_cam_corner", "br")
    x = W - cw if corner in ("br", "tr") else 0
    y = H - ch if corner in ("br", "bl") else 0
    return x, y, cw, min(ch, H)


def _compose_stream(frame, cfg):
    """Hra nahoře (na šířku, s rozmazaným pozadím), kamera dole (oříznutý výřez ze zdrojového obrazu)."""
    H, W = frame.shape[:2]
    cw = min(W, int(H * 9 / 16))
    x0 = (W - cw) // 2
    small = cv2.resize(frame[:, x0:x0 + cw], (54, 96), interpolation=cv2.INTER_AREA)
    bg = cv2.resize(cv2.GaussianBlur(small, (0, 0), 3), (OUT_W, OUT_H), interpolation=cv2.INTER_LINEAR)
    bg = (bg * 0.55).astype(np.uint8)

    gh = int(OUT_W * H / W)
    game = cv2.resize(frame, (OUT_W, gh), interpolation=cv2.INTER_AREA)
    gy = max(0, (STREAM_SPLIT - gh) // 2)
    bg[gy:gy + gh] = game[: OUT_H - gy]

    cx, cy, bw, bh = _cam_box(W, H, cfg)
    cam = frame[cy:cy + bh, cx:cx + bw]
    area_h = OUT_H - STREAM_SPLIT
    k = max(OUT_W / cam.shape[1], area_h / cam.shape[0])
    cam = cv2.resize(cam, (int(cam.shape[1] * k) + 1, int(cam.shape[0] * k) + 1), interpolation=cv2.INTER_CUBIC)
    ox, oy = (cam.shape[1] - OUT_W) // 2, (cam.shape[0] - area_h) // 2
    bg[STREAM_SPLIT:] = cam[oy:oy + area_h, ox:ox + OUT_W]
    cv2.line(bg, (0, STREAM_SPLIT), (OUT_W, STREAM_SPLIT), (0, 0, 0), 6)
    return bg


# ---------------------------------------------------------------- titulky (kreslené Pillow)

def build_caption_states(words, clip_start, clip_end, sc):
    """Vrátí seřazený seznam (start, end, [slova skupiny], index zvýrazněného slova) v čase klipu."""
    ws = [w for w in words if w["start"] >= clip_start - 0.05 and w["end"] <= clip_end + 0.05]
    per = 1 if sc.get("style") == "word" else max(1, int(sc["words_per_line"]))

    groups, cur = [], []
    for w in ws:
        cur.append(w)
        if len(cur) >= per or w["w"].endswith((".", "?", "!")) or sum(len(x["w"]) for x in cur) > 22:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    def clean(txt):
        return txt.upper() if sc["uppercase"] else txt

    states = []
    for g in groups:
        texts = [clean(x["w"]) for x in g]
        for j, w in enumerate(g):
            start = w["start"] - clip_start
            end = (g[j + 1]["start"] if j + 1 < len(g) else w["end"] + 0.15) - clip_start
            if end <= start:
                end = start + 0.1
            states.append((start, end, texts, j))
    return states


def _hex_to_rgba(h):
    h = str(h).lstrip("#")
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    except (ValueError, IndexError):
        return (255, 212, 0, 255)


_font_cache = {}


def _font(size):
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(str(FONT_PATH), size)
    return _font_cache[size]


def render_caption(texts, active, sc):
    """Nakreslí jeden řádek titulků. Vrátí (BGR*alfa jako uint16, 255-alfa jako uint16)."""
    style = sc.get("style", "karaoke")
    size = int(sc["size"] * (1.45 if style == "word" else 1))
    max_w = OUT_W - 120
    while True:
        font = _font(size)
        space = font.getlength(" ")
        widths = [font.getlength(t) for t in texts]
        total = sum(widths) + space * (len(texts) - 1)
        if total <= max_w or size <= 30:
            break
        size -= 4
    stroke = max(2, round(size * 0.05)) if style == "box" else max(3, round(size * 0.09))
    pad = stroke + (22 if style == "box" else 8)
    ascent, descent = font.getmetrics()
    img = Image.new("RGBA", (int(total) + 2 * pad, ascent + descent + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if style == "box":
        draw.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=30, fill=(0, 0, 0, 175))
    hi = (255, 255, 255, 255) if style == "plain" else _hex_to_rgba(sc["highlight_color"])
    x = pad
    for k, t in enumerate(texts):
        draw.text((x, pad), t, font=font, fill=hi if k == active else (255, 255, 255, 255),
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        x += widths[k] + space
    arr = np.asarray(img)
    alpha = arr[..., 3:4].astype(np.uint16)
    bgr = arr[..., 2::-1].astype(np.uint16)
    return bgr * alpha, 255 - alpha


def blend_caption(frame, premul, inv_alpha, layout):
    h, w = premul.shape[:2]
    x0 = (OUT_W - w) // 2
    if layout == "stream":
        y0 = STREAM_SPLIT - h // 2
    else:
        y0 = (OUT_H // 2 - h // 2) if layout == "split" else (OUT_H - 430 - h)
    xs, ys = max(0, x0), max(0, y0)
    xe, ye = min(OUT_W, x0 + w), min(OUT_H, y0 + h)
    if xe <= xs or ye <= ys:
        return
    p = premul[ys - y0:ye - y0, xs - x0:xe - x0]
    ia = inv_alpha[ys - y0:ye - y0, xs - x0:xe - x0]
    region = frame[ys:ye, xs:xe].astype(np.uint16)
    frame[ys:ye, xs:xe] = ((region * ia + p) // 255).astype(np.uint8)


# ---------------------------------------------------------------- hlavní funkce

def render_clip(src, clip, words, cfg, out_path):
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        seg = tmp / "seg.mp4"
        cut_segment(src, clip["start"], clip["end"], seg)

        cap = cv2.VideoCapture(str(seg))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

        if cfg.get("layout_mode") == "stream":
            layout, track = "stream", None
        else:
            times, faces_per = analyze_faces(cap, fps, n_frames)
            layout = choose_layout(faces_per, W)
            if cfg.get("layout_mode") == "speaker":
                layout = "single"       # jeden velký záběr, kamera přejíždí na toho, kdo mluví
            track = build_tracks(times, faces_per, W, layout)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        states = build_caption_states(words, clip["start"], clip["end"], cfg["subtitles"])
        starts = [s[0] for s in states]

        cmd = [
            ffmpeg_path(), "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}", "-r", f"{fps:.5f}", "-i", "-",
            "-i", str(seg),
            "-map", "0:v", "-map", "1:a?",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",   # vyrovnaná hlasitost jako na sociálních sítích
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart",
            str(out_path),
        ]
        errlog = open(tmp / "ffmpeg.log", "w+", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errlog, **hidden_subprocess_kwargs())
        i = 0
        cur_idx, cur_img = -1, None
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                t = i / fps
                if layout == "stream":
                    composed = np.ascontiguousarray(_compose_stream(frame, cfg))
                else:
                    composed = np.ascontiguousarray(_compose(frame, layout, track(t)))
                k = bisect.bisect_right(starts, t) - 1
                if k >= 0 and t < states[k][1]:
                    if k != cur_idx:
                        cur_img = render_caption(states[k][2], states[k][3], cfg["subtitles"])
                        cur_idx = k
                    blend_caption(composed, cur_img[0], cur_img[1], layout)
                proc.stdin.write(composed.tobytes())
                i += 1
        except BrokenPipeError:
            pass  # ffmpeg skončil předčasně, důvod vyčteme z logu níž
        finally:
            cap.release()
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            code = proc.wait()
        if code != 0:
            errlog.seek(0)
            raise RuntimeError(f"ffmpeg selhal: {errlog.read().strip()[-300:]}")
        errlog.close()
    return layout
