"""Podcast Clipper – aplikace s grafickým rozhraním (běží lokálně, otevře se v prohlížeči)."""
import argparse
import json
import logging
import multiprocessing
import os
import platform
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import config
from downloader import list_channel_videos
from pipeline import load_transcript, prepare_video, process_video, render_clips
import thisorthat
from selector import build_prompts, finalize, parse_answer
from platform_utils import FROZEN, data_dir, redirect_output_when_windowed, resource, setup_environment

DATA = data_dir()  # ~/PodcastClipper jako aplikace, složka projektu ze zdrojáků
os.chdir(DATA)

SETTINGS_FILE = DATA / "settings.json"
CHANNELS_FILE = DATA / "channels.json"
JOBS_FILE = DATA / "jobs.json"
UI_DIR = resource("ui")
APP_ID = "podcast-clipper"

app = Flask(__name__)

# ----------------------------------------------------------------- nastavení

INT_KEYS = ("clips_per_video", "min_clip_seconds", "max_clip_seconds", "check_interval_min", "stream_cam_size", "max_transcribe_minutes")
STR_KEYS = ("language", "whisper_model", "whisper_device", "selection_mode", "clip_style", "layout_mode", "stream_cam_corner")


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_settings(s):
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        SETTINGS_FILE.chmod(0o600)  # obsahuje API klíč
    except OSError:
        pass


def has_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or load_settings().get("api_key"))


def build_cfg():
    cfg = config.load(str(resource("config.yaml")))
    cfg["work_dir"] = str(DATA / "work")
    cfg["output_dir"] = str(DATA / "Klipy")
    cfg["state_file"] = str(DATA / "state.json")
    s = load_settings()
    for k in STR_KEYS + INT_KEYS[:3] + INT_KEYS[4:]:
        if s.get(k) not in (None, ""):
            cfg[k] = s[k]
    if "keep_source" in s:
        cfg["keep_source"] = bool(s["keep_source"])
    cfg["subtitles"].update(s.get("subtitles", {}))
    if cfg.get("selection_mode") not in ("manual", "api"):
        cfg["selection_mode"] = "manual"
    if cfg.get("clip_style") not in ("general", "thisorthat", "stream"):
        cfg["clip_style"] = "general"
    if cfg.get("layout_mode") not in ("auto", "stream"):
        cfg["layout_mode"] = "auto"
    if cfg.get("stream_cam_corner") not in ("br", "bl", "tr", "tl"):
        cfg["stream_cam_corner"] = "br"
    cfg["stream_cam_size"] = max(10, min(50, int(cfg.get("stream_cam_size") or 26)))
    if cfg["clip_style"] == "thisorthat":  # krátké klipy: jedna otázka a reakce
        cfg["min_clip_seconds"] = min(cfg["min_clip_seconds"], 12)
        cfg["max_clip_seconds"] = min(cfg["max_clip_seconds"], 60)
    if s.get("api_key"):
        os.environ["ANTHROPIC_API_KEY"] = s["api_key"]
    return cfg


# ----------------------------------------------------------------- úlohy

jobs = {}
job_order = []
job_queue = queue.Queue()
jobs_lock = threading.Lock()


def _persist_jobs():
    keep = []
    for i in job_order:
        j = jobs.get(i)
        if not j:
            continue
        resumable = j["status"] in ("queued", "running") and j.get("phase") == "render"
        if j["status"] not in ("done", "error", "waiting") and not resumable:
            continue
        d = {k: v for k, v in j.items() if k != "log"}
        d["log"] = j["log"][-30:]
        if j["status"] != "waiting":
            d.pop("prompts", None)
        keep.append(d)
    JOBS_FILE.write_text(json.dumps(keep[-100:], ensure_ascii=False), encoding="utf-8")


def _load_jobs():
    if JOBS_FILE.exists():
        try:
            for j in json.loads(JOBS_FILE.read_text(encoding="utf-8")):
                if j.get("status") in ("queued", "running"):
                    j.update(status="error", stage="Chyba",
                             error="Zpracování přerušilo ukončení aplikace. Klikněte na Zkusit znovu.")
                jobs[j["id"]] = j
                job_order.append(j["id"])
        except (json.JSONDecodeError, KeyError):
            pass


def add_job(url, title=None, source="ručně", start=True):
    jid = uuid.uuid4().hex[:8]
    job = {
        "id": jid, "url": url, "title": title or url, "source": source,
        "status": "queued", "stage": "Čeká ve frontě", "pct": 0,
        "log": [], "error": None, "clips": 0, "vid": None, "created": time.time(),
    }
    with jobs_lock:
        jobs[jid] = job
        job_order.append(jid)
    if start:
        job_queue.put(jid)
    return job


def worker():
    while True:
        jid = job_queue.get()
        job = jobs.get(jid)
        if not job or job["status"] == "cancelled":
            continue
        job.update(status="running", stage="Začínám", pct=1)

        def log(msg):
            job["log"].append(str(msg))
            del job["log"][:-200]

        def progress(pct, stage, **extra):
            job["pct"], job["stage"] = pct, stage
            for k in ("title", "vid"):
                if k in extra:
                    job[k] = extra[k]

        try:
            cfg = build_cfg()
            if job.get("kind") == "ton":
                if not job.get("videos"):
                    req = job["ton_request"]
                    if not has_api_key():
                        raise RuntimeError("Chybí API klíč Claude. Použijte ruční výběr (otázky z chatu) nebo klíč vyplňte v Nastavení.")
                    job.update(stage="Claude vymýšlí otázky", pct=3)
                    job["videos"] = thisorthat.questions_via_api(req["topic"], req["rounds"], req["videos"], cfg)
                results = thisorthat.generate(job["videos"], job.get("topic", ""), cfg["output_dir"],
                                              job.get("voice", "antonin"), log, progress,
                                              pexels_key=load_settings().get("pexels_key"))
                job.update(status="done", pct=100, stage="Hotovo", clips=len(results))
            elif job.get("phase") == "render":
                # klipy vybrané ručně přes chat: zbývá je vytvořit
                transcript = load_transcript(cfg, job["info"]["id"])
                results = render_clips(job["info"], transcript, job.get("pending_clips") or [], cfg, log, progress)
                job.pop("pending_clips", None)
                job.pop("prompts", None)
                job.update(status="done", pct=100, stage="Hotovo", clips=len(results))
            elif cfg["selection_mode"] == "api":
                if not has_api_key():
                    raise RuntimeError("Chybí API klíč Claude. Vyplň ho v Nastavení, nebo přepni na ruční výběr.")
                results = process_video(job["url"], cfg, log, progress)
                job.update(status="done", pct=100, stage="Hotovo", clips=len(results))
            else:
                info, transcript = prepare_video(job["url"], cfg, log, progress)
                parts = build_prompts(transcript, cfg)
                if not parts:
                    raise RuntimeError("V přepisu není žádný text. Video asi neobsahuje řeč.")
                job.update(info=info, prompts=parts, phase="select", status="waiting", pct=55,
                           stage="Čeká na vás: výběr momentů přes Claude", title=info["title"], vid=info["id"])
        except Exception as e:  # noqa: BLE001
            job["log"].append(f"{type(e).__name__}: {e}")
            job.update(status="error", stage="Chyba", error=str(e)[:400] or type(e).__name__)
        with jobs_lock:
            _persist_jobs()


# ----------------------------------------------------------------- kanály

channels_lock = threading.Lock()
wake = threading.Event()


def load_channels():
    if CHANNELS_FILE.exists():
        try:
            return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save_channels(chs):
    CHANNELS_FILE.write_text(json.dumps(chs, ensure_ascii=False, indent=2), encoding="utf-8")


def _channel_name(url):
    m = re.search(r"/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)", url)
    return m.group(1) if m else url


def _eligible(v, cfg):
    if v.get("live_status") in ("is_live", "is_upcoming"):
        return False
    dur = v.get("duration")
    return dur is None or dur >= cfg["min_video_minutes"] * 60


def scan_channel(ch, cfg):
    for v in list_channel_videos(ch["url"], 5):
        if v["id"] in ch["seen"]:
            continue
        ch["seen"].append(v["id"])
        if _eligible(v, cfg):
            add_job(f"https://www.youtube.com/watch?v={v['id']}", v["title"], source=ch["name"])


def watcher_loop():
    last = 0.0
    while True:
        interval = max(5, int(load_settings().get("check_interval_min", 60))) * 60
        if wake.wait(timeout=5) or time.time() - last >= interval:
            wake.clear()
            last = time.time()
            cfg = build_cfg()
            with channels_lock:
                chs = load_channels()
                for ch in chs:
                    if not ch.get("enabled", True):
                        continue
                    try:
                        scan_channel(ch, cfg)
                        ch["error"] = None
                    except Exception as e:  # noqa: BLE001
                        ch["error"] = str(e)[:200]
                    ch["last_check"] = time.time()
                save_channels(chs)


# ----------------------------------------------------------------- API

YT_RE = re.compile(r"^https?://([\w-]+\.)?(youtube\.com|youtu\.be)/", re.I)


_HIDDEN_JOB_KEYS = ("log", "prompts", "pending_clips", "info", "videos", "ton_request")


def _public_job(j):
    d = {k: v for k, v in j.items() if k not in _HIDDEN_JOB_KEYS}
    d["log"] = j["log"][-12:]
    d["parts"] = len(j.get("prompts") or [])
    return d


@app.get("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.get("/api/state")
def api_state():
    s = load_settings()
    return jsonify({
        "has_api_key": has_api_key(),
        "selection_mode": build_cfg()["selection_mode"],
        "jobs": [_public_job(jobs[i]) for i in reversed(job_order) if i in jobs],
        "channels": load_channels(),
        "settings": {k: v for k, v in s.items() if k not in ("api_key", "pexels_key")},
        "has_pexels_key": bool(s.get("pexels_key")),
        "defaults": build_cfg() | {"subtitles": build_cfg()["subtitles"]},
    })


@app.post("/api/jobs")
def api_add_job():
    url = (request.json or {}).get("url", "").strip()
    if not YT_RE.match(url):
        return jsonify(error="Zadej platný odkaz na YouTube video."), 400
    if re.search(r"youtube\.com/(@|channel/|c/|user/)", url) and "watch?v=" not in url:
        return jsonify(error="Tohle je odkaz na kanál. Přidej ho v záložce Kanály."), 400
    return jsonify(_public_job(add_job(url)))


@app.delete("/api/jobs/<jid>")
def api_delete_job(jid):
    job = jobs.get(jid)
    if not job:
        abort(404)
    if job["status"] == "running":
        return jsonify(error="Běžící úlohu nelze smazat."), 409
    if job["status"] in ("waiting", "error") and (job.get("info") or {}).get("path"):
        Path(job["info"]["path"]).unlink(missing_ok=True)  # stažené video zabírá hodně místa
    job["status"] = "cancelled"
    with jobs_lock:
        jobs.pop(jid, None)
        if jid in job_order:
            job_order.remove(jid)
        _persist_jobs()
    return jsonify(ok=True)


@app.post("/api/jobs/<jid>/retry")
def api_retry_job(jid):
    job = jobs.get(jid)
    if not job or job["status"] != "error":
        abort(404)
    job.update(status="queued", stage="Čeká ve frontě", pct=0, error=None, log=[])
    job_queue.put(jid)
    return jsonify(ok=True)


@app.get("/api/jobs/<jid>/prompts")
def api_job_prompts(jid):
    job = jobs.get(jid)
    if not job or not job.get("prompts"):
        abort(404)
    return jsonify(parts=job["prompts"])


@app.post("/api/jobs/<jid>/answers")
def api_job_answers(jid):
    job = jobs.get(jid)
    if not job or job["status"] != "waiting":
        abort(404)
    answers = (request.json or {}).get("answers") or []
    candidates, problems = [], []
    for i, text in enumerate(answers[: len(job["prompts"])]):
        if not str(text or "").strip():
            continue
        try:
            candidates += parse_answer(text)
        except ValueError as e:
            problems.append(f"Část {i + 1}: {e}")
    if problems:
        return jsonify(error=" ".join(problems)), 400
    if not candidates:
        return jsonify(error="Vložte alespoň jednu odpověď od Claude."), 400
    cfg = build_cfg()
    clips = finalize(candidates, load_transcript(cfg, job["info"]["id"]), cfg)
    if not clips:
        return jsonify(error=(
            f"Žádný z vybraných momentů nemá vhodnou délku ({cfg['min_clip_seconds']}–{cfg['max_clip_seconds']} s) "
            "nebo se navzájem překrývají. Požádejte Claude o jiný výběr se správnou délkou.")), 400
    with jobs_lock:
        job.update(phase="review", pending_clips=clips, status="waiting", pct=58, error=None,
                   stage="Čeká na vás: zkontrolujte vybrané klipy")
        _persist_jobs()
    return jsonify(ok=True, clips=len(clips))


@app.get("/api/jobs/<jid>/review")
def api_job_review(jid):
    job = jobs.get(jid)
    if not job or job.get("phase") != "review" or job["status"] != "waiting":
        abort(404)
    info = job.get("info") or {}
    return jsonify(clips=[{k: c.get(k) for k in ("title", "hook", "reason", "score", "start", "end")}
                          for c in job.get("pending_clips") or []],
                   has_video=bool(info.get("path")) and Path(info["path"]).exists())


@app.get("/api/jobs/<jid>/source")
def api_job_source(jid):
    job = jobs.get(jid)
    path = Path(((job or {}).get("info") or {}).get("path") or "")
    if not job or job.get("phase") != "review" or not path.is_file():
        abort(404)
    return send_file(path, conditional=True)


@app.post("/api/jobs/<jid>/render")
def api_job_render(jid):
    job = jobs.get(jid)
    if not job or job.get("phase") != "review" or job["status"] != "waiting":
        abort(404)
    base = job.get("pending_clips") or []
    duration = float((load_transcript(build_cfg(), job["info"]["id"]) or {}).get("duration") or 0)
    chosen = []
    for item in (request.json or {}).get("clips") or []:
        try:
            i = int(item["i"])
            if not item.get("keep", True):
                continue
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            return jsonify(error="Neplatné hodnoty u klipu."), 400
        if not 0 <= i < len(base):
            continue
        if duration:
            end = min(end, duration)
        if start < 0 or end - start < 3:
            return jsonify(error=f"Klip {i + 1}: konec musí být aspoň 3 sekundy za začátkem."), 400
        if end - start > 180:
            return jsonify(error=f"Klip {i + 1} je delší než 3 minuty."), 400
        chosen.append({**base[i], "start": round(start, 2), "end": round(end, 2),
                       "title": str(item.get("title") or base[i]["title"]).strip()[:120] or base[i]["title"]})
    if not chosen:
        return jsonify(error="Ponechte aspoň jeden klip, nebo úlohu odeberte."), 400
    with jobs_lock:
        job.update(phase="render", pending_clips=chosen, status="queued", pct=60, error=None,
                   stage="Čeká ve frontě (vytváření klipů)")
        _persist_jobs()
    job_queue.put(jid)
    return jsonify(ok=True, clips=len(chosen))


def _ton_params(data):
    topic = "\n".join(thisorthat.split_topics(data.get("topic")))[:600]
    n_topics = max(1, len(thisorthat.split_topics(topic)))
    try:
        rounds = max(2, min(thisorthat.MAX_ROUNDS, int(data.get("rounds") or 6)))
        videos = max(1, min(thisorthat.MAX_VIDEOS, int(data.get("videos") or 1)))
    except (TypeError, ValueError):
        rounds, videos = 6, 1
    videos = max(1, min(videos, thisorthat.MAX_VIDEOS // n_topics))  # celkem nejvýše 20 videí
    return topic, rounds, videos


@app.post("/api/ton/prompt")
def api_ton_prompt():
    topic, rounds, videos = _ton_params(request.json or {})
    if not topic:
        return jsonify(error="Napište téma, například „jídlo“ nebo „filmy“."), 400
    return jsonify(prompt=thisorthat.build_prompt(topic, rounds, videos))


@app.post("/api/ton/generate")
def api_ton_generate():
    data = request.json or {}
    topic, rounds, videos = _ton_params(data)
    answer = str(data.get("answer") or "").strip()
    if answer:
        try:
            vids = thisorthat.parse_questions(answer)
        except ValueError as e:
            return jsonify(error=str(e)), 400
    elif build_cfg()["selection_mode"] == "api":
        vids = None
    else:
        return jsonify(error="Vložte odpověď od Claude (blok s otázkami)."), 400
    if not topic and vids:
        topic = vids[0]["title"]
    if not topic:
        return jsonify(error="Napište téma."), 400
    shown = ", ".join(thisorthat.split_topics(topic))[:80]
    job = add_job("", title=f"To nebo to: {shown}", source="generátor", start=False)
    voice = data.get("voice") if data.get("voice") in thisorthat.VOICES else "antonin"
    job.update(kind="ton", topic=topic, voice=voice, videos=vids, ton_request={"topic": topic, "rounds": rounds, "videos": videos})
    job_queue.put(job["id"])
    return jsonify(_public_job(job))


@app.post("/api/channels")
def api_add_channel():
    data = request.json or {}
    url = data.get("url", "").strip()
    backfill = max(0, min(5, int(data.get("backfill", 0))))
    if not YT_RE.match(url):
        return jsonify(error="Zadej platný odkaz na kanál, např. https://www.youtube.com/@nazev."), 400
    try:
        videos = list_channel_videos(url, 5)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"Kanál se nepodařilo načíst: {e}"[:300]), 400
    if not videos:
        return jsonify(error="V kanálu jsem nenašel žádná videa. Zkontroluj odkaz."), 400
    cfg = build_cfg()
    ch = {"url": url, "name": _channel_name(url), "enabled": True, "seen": [], "last_check": time.time(), "error": None}
    for i, v in enumerate(videos):  # videa jsou od nejnovějšího
        ch["seen"].append(v["id"])
        if i < backfill and _eligible(v, cfg):
            add_job(f"https://www.youtube.com/watch?v={v['id']}", v["title"], source=ch["name"])
    with channels_lock:
        chs = [c for c in load_channels() if c["url"] != url]
        chs.append(ch)
        save_channels(chs)
    return jsonify(ok=True)


@app.post("/api/channels/toggle")
def api_toggle_channel():
    url = (request.json or {}).get("url")
    with channels_lock:
        chs = load_channels()
        for c in chs:
            if c["url"] == url:
                c["enabled"] = not c.get("enabled", True)
        save_channels(chs)
    return jsonify(ok=True)


@app.post("/api/channels/delete")
def api_delete_channel():
    url = (request.json or {}).get("url")
    with channels_lock:
        save_channels([c for c in load_channels() if c["url"] != url])
    return jsonify(ok=True)


@app.post("/api/channels/check")
def api_check_channels():
    wake.set()
    return jsonify(ok=True)


@app.post("/api/settings")
def api_save_settings():
    data = request.json or {}
    s = load_settings()
    if data.get("api_key"):
        s["api_key"] = data["api_key"].strip()
    if data.get("clear_api_key"):
        s.pop("api_key", None)
    if data.get("pexels_key") is not None and "pexels_key" in data:
        key = str(data["pexels_key"]).strip()
        if key:
            s["pexels_key"] = key
        elif data.get("clear_pexels_key"):
            s.pop("pexels_key", None)
    if "layout_mode" in data and data["layout_mode"] not in ("auto", "stream"):
        return jsonify(error="Neznámé rozložení videa."), 400
    if "stream_cam_corner" in data and data["stream_cam_corner"] not in ("br", "bl", "tr", "tl"):
        return jsonify(error="Neznámá poloha kamery."), 400
    if "clip_style" in data and data["clip_style"] not in ("general", "thisorthat", "stream"):
        return jsonify(error="Neznámý typ klipů."), 400
    if "selection_mode" in data and data["selection_mode"] not in ("manual", "api"):
        return jsonify(error="Neznámý způsob výběru momentů."), 400
    for k in STR_KEYS:
        if k in data:
            s[k] = str(data[k])
    for k in INT_KEYS:
        if k in data:
            try:
                s[k] = int(data[k])
            except (TypeError, ValueError):
                return jsonify(error=f"Hodnota {k} musí být číslo."), 400
    if "keep_source" in data:
        s["keep_source"] = bool(data["keep_source"])
    if "subtitles" in data:
        sub = s.setdefault("subtitles", {})
        d = data["subtitles"]
        if "highlight_color" in d:
            color = str(d["highlight_color"]).strip().lstrip("#")
            if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
                return jsonify(error="Barva musí být ve tvaru 6 znaků, např. FFD400."), 400
            sub["highlight_color"] = color
        for k in ("size", "words_per_line"):
            if k in d:
                sub[k] = int(d[k])
        if "uppercase" in d:
            sub["uppercase"] = bool(d["uppercase"])
        if "style" in d:
            if d["style"] not in ("karaoke", "box", "plain", "word"):
                return jsonify(error="Neznámý styl titulků."), 400
            sub["style"] = d["style"]
    save_settings(s)
    build_cfg()
    return jsonify(ok=True, has_api_key=has_api_key())


@app.get("/api/outputs")
def api_outputs():
    out = Path(build_cfg()["output_dir"])
    items = []
    for meta in out.glob("*/clips.json"):
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        d["mtime"] = meta.stat().st_mtime
        items.append(d)
    items.sort(key=lambda d: d["mtime"], reverse=True)
    return jsonify(items)


@app.get("/media/<vid>/<path:name>")
def media(vid, name):
    folder = Path(build_cfg()["output_dir"]).resolve() / vid
    return send_from_directory(folder, name, as_attachment=bool(request.args.get("dl")))


@app.delete("/api/outputs/<vid>")
def api_delete_output(vid):
    folder = (Path(build_cfg()["output_dir"]).resolve() / vid)
    if not re.fullmatch(r"[\w-]+", vid) or not folder.is_dir():
        abort(404)
    shutil.rmtree(folder)
    return jsonify(ok=True)


@app.post("/api/open-folder")
def api_open_folder():
    folder = Path(build_cfg()["output_dir"]).resolve()
    folder.mkdir(exist_ok=True)
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(folder)  # noqa: S606
        elif system == "Darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError:
        return jsonify(error="Složku se nepodařilo otevřít.", path=str(folder)), 500
    return jsonify(ok=True, path=str(folder))


@app.get("/api/ping")
def api_ping():
    return jsonify(app=APP_ID)


@app.post("/api/quit")
def api_quit():
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify(ok=True)


# ----------------------------------------------------------------- start

def _is_our_app(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == APP_ID
    except Exception:  # noqa: BLE001
        return False


def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def selftest():
    """Ověří, že je v sestavené aplikaci všechno potřebné. Výsledek jde do selftest.log."""
    import tempfile

    lines, failed = [], []

    def check(name, fn, required=True):
        try:
            lines.append(f"OK    {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"{'CHYBA' if required else 'VAROVÁNÍ'} {name}: {type(e).__name__}: {e}")
            if required:
                failed.append(name)

    def t_ffmpeg():
        from platform_utils import ffmpeg_path, hidden_subprocess_kwargs
        out = subprocess.run([ffmpeg_path(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                             **hidden_subprocess_kwargs()).stdout
        assert "libx264" in out and " aac " in out, "ffmpeg nemá libx264 nebo aac"
        return ffmpeg_path()

    def t_deno():
        found = shutil.which("deno")
        assert found, "deno nenalezen (stahování z YouTube může být omezené)"
        return found

    def t_font():
        from PIL import ImageFont
        import renderer
        ImageFont.truetype(str(renderer.FONT_PATH), 40)
        return str(renderer.FONT_PATH)

    def t_cv2():
        import cv2
        c = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        assert not c.empty(), "detektor tváří se nenačetl"
        return cv2.__version__

    def t_ytdlp():
        import yt_dlp
        import yt_dlp_ejs  # řešení výzev YouTube; bez něj stahování nefunguje
        return f"{yt_dlp.version.__version__}, ejs {getattr(yt_dlp_ejs, '__version__', 'ok')}"

    def t_whisper():
        import faster_whisper
        assets = Path(faster_whisper.__file__).parent / "assets"
        onnx = list(assets.glob("*.onnx"))
        assert onnx, f"chybí VAD model v {assets}"
        import ctranslate2
        return f"ctranslate2 {ctranslate2.__version__}, VAD {onnx[0].name}"

    def t_audio():
        from faster_whisper.audio import decode_audio
        from platform_utils import ffmpeg_path, hidden_subprocess_kwargs
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=300:duration=2", str(wav)], check=True, **hidden_subprocess_kwargs())
            audio = decode_audio(str(wav))
            assert len(audio) > 16000, "dekódování zvuku vrátilo málo dat"
            return f"{len(audio)} vzorků"

    def t_onnx():
        import faster_whisper
        import onnxruntime
        onnx = next((Path(faster_whisper.__file__).parent / "assets").glob("*.onnx"))
        onnxruntime.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
        return f"onnxruntime {onnxruntime.__version__}"

    def t_transcribe():
        from faster_whisper import WhisperModel
        from platform_utils import ffmpeg_path, hidden_subprocess_kwargs
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=300:duration=3", str(wav)], check=True, **hidden_subprocess_kwargs())
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            segs, info = model.transcribe(str(wav), language="cs", word_timestamps=True, vad_filter=True)
            return f"model tiny funguje, segmentů: {len(list(segs))}"

    def t_manual():
        import selector
        segs = [{"start": i * 5.0, "end": i * 5.0 + 4.5, "text": f"věta {i}",
                 "words": [{"w": "věta", "start": i * 5.0, "end": i * 5.0 + 2.0}]} for i in range(200)]
        tr = {"segments": segs, "duration": 1000.0}
        cfg = build_cfg()
        parts = selector.build_prompts(tr, cfg)
        assert parts and "PŘEPIS" in parts[0]["prompt"], "pokyny pro chat se nevytvořily"
        ans = '```json\n{"clips":[{"start":100,"end":150,"title":"T","hook":"h","score":80,"reason":"r"}]}\n```'
        clips = selector.finalize(selector.parse_answer(ans), tr, cfg)
        assert len(clips) == 1, "ruční výběr nevrátil klip"
        return f"{len(parts)} část, {len(clips)} klip"

    def t_ton():
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "t.mp4"
            thisorthat.render_video({"title": "Test", "rounds": [{"a": "Káva", "ea": "", "b": "Čaj", "eb": ""}]}, out, voice=None)
            assert out.exists() and out.stat().st_size > 5000, "video To nebo to je prázdné"
            return f"{out.stat().st_size} B"

    def t_voice_lib():
        import edge_tts  # noqa: F401
        import aiohttp  # noqa: F401
        return "edge-tts načten (samotný hlas potřebuje internet)"

    def t_model_choice():
        import transcriber
        m, est = transcriber.choose_model({"whisper_model": "auto", "work_dir": str(DATA / "work"), "max_transcribe_minutes": 45}, 3600)
        assert m in transcriber.AUTO_ORDER, "automatický výběr modelu selhal"
        return f"1 h záznam: model {m}, odhad {est:.0f} min, vláken {transcriber._threads()}"

    def t_anthropic():
        import anthropic
        import selector  # noqa: F401
        return anthropic.__version__

    def t_ui():
        assert (UI_DIR / "index.html").exists()
        return str(UI_DIR)

    def t_render():
        import renderer
        from platform_utils import ffmpeg_path, hidden_subprocess_kwargs
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            subprocess.run(
                [ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                 "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", str(src)],
                check=True, **hidden_subprocess_kwargs())
            words = [{"w": w, "start": 0.3 + i * 0.4, "end": 0.6 + i * 0.4} for i, w in enumerate("Zkušební titulky děkuji".split())]
            out = Path(tmp) / "out.mp4"
            renderer.render_clip(src, {"start": 0.0, "end": 2.5}, words, build_cfg(), out)
            assert out.exists() and out.stat().st_size > 5000, "výstupní klip je prázdný"
            return f"{out.stat().st_size} B"

    check("ffmpeg", t_ffmpeg)
    check("deno", t_deno, required=FROZEN)  # v sestavené aplikaci musí být, ze zdrojů jen varování
    check("písmo titulků", t_font)
    check("OpenCV a detektor tváří", t_cv2, required=FROZEN)
    check("yt-dlp", t_ytdlp)
    check("faster-whisper", t_whisper)
    check("dekódování zvuku", t_audio)
    check("ONNX runtime (VAD)", t_onnx)
    check("zkušební přepis (model tiny)", t_transcribe, required=False)  # potřebuje internet
    check("ruční výběr momentů", t_manual)
    check("video To nebo to", t_ton)
    check("hlas (edge-tts)", t_voice_lib)
    check("výběr modelu přepisu", t_model_choice)
    check("anthropic", t_anthropic)
    check("rozhraní", t_ui)
    check("zkušební render klipu", t_render)

    lines.append("VÝSLEDEK: " + ("SELHALO: " + ", ".join(failed) if failed else "vše v pořádku"))
    (DATA / "selftest.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if failed else 0


def main():
    multiprocessing.freeze_support()
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--selftest", action="store_true", help="ověří sestavenou aplikaci a skončí")
    args = p.parse_args()

    redirect_output_when_windowed(DATA / "app.log")
    setup_environment()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    if args.selftest:
        sys.exit(selftest())

    # Když už aplikace běží, jen otevřeme prohlížeč; jinak vezmeme první volný port.
    port = None
    for cand in range(args.port, args.port + 10):
        if _is_our_app(cand):
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{cand}")
            print(f"Aplikace už běží na portu {cand}.")
            return
        if _port_free(cand):
            port = cand
            break
    if port is None:
        print("Nenašel jsem volný port.")
        sys.exit(1)

    _load_jobs()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=watcher_loop, daemon=True).start()

    url = f"http://127.0.0.1:{port}"
    print(f"Podcast Clipper běží na {url}  (ukončení: tlačítko Ukončit v aplikaci nebo Ctrl+C)")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()
