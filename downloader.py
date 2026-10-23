"""Stahování videí a výpis videí z kanálu pomocí yt-dlp (jako knihovna)."""
import re
from pathlib import Path

from platform_utils import bin_dir, setup_environment

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class DownloadFailed(RuntimeError):
    """Srozumitelná chyba stahování."""


def _friendly(exc):
    msg = _ANSI.sub("", str(exc)).replace("ERROR: ", "").strip()
    low = msg.lower()
    if "sign in" in low or "confirm you" in low or "bot" in low:
        return "YouTube vyžaduje přihlášení nebo blokuje stahování z této sítě. Zkus to později nebo z jiné sítě."
    if "private" in low or "unavailable" in low:
        return "Video je soukromé nebo nedostupné."
    if "getaddrinfo" in low or "urlopen" in low or "timed out" in low or "network" in low:
        return "Nepodařilo se připojit k internetu."
    return msg.splitlines()[-1][:300] if msg else "Stahování selhalo."


def _base_opts():
    setup_environment()
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": None,
            "retries": 8, "fragment_retries": 8, "extractor_retries": 3, "socket_timeout": 30}
    b = bin_dir()
    if b:
        opts["ffmpeg_location"] = str(b)
    return opts


def list_channel_videos(channel_url, limit=10):
    """Vrátí nejnovější videa kanálu: [{id, title, duration, live_status}]."""
    import yt_dlp

    url = channel_url.rstrip("/")
    if ("/@" in url or "/channel/" in url or "/c/" in url or "/user/" in url) and not url.endswith(
        ("/videos", "/streams", "/shorts")
    ):
        url += "/videos"
    opts = _base_opts() | {"extract_flat": "in_playlist", "playlistend": limit, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadFailed(_friendly(e)) from e

    videos = []
    for d in (info or {}).get("entries") or []:
        if not d or not d.get("id"):
            continue
        videos.append({
            "id": d["id"],
            "title": d.get("title") or d["id"],
            "duration": d.get("duration"),
            "live_status": d.get("live_status"),
        })
    return videos[:limit]


def download_video(url, work_dir, max_height=1080, on_progress=None):
    """Stáhne video do work_dir jako mp4. Vrátí {id, title, duration, path, url}."""
    import yt_dlp

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    def hook(d):
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = _base_opts() | {
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "format_sort": ["res", "vcodec:h264", "acodec:aac"],  # při stejném rozlišení raději h264 (rychlejší a spolehlivé čtení)
        "outtmpl": str(work / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadFailed(_friendly(e)) from e

    vid = info["id"]
    path = work / f"{vid}.mp4"
    if not path.exists():
        matches = [m for m in work.glob(f"{vid}.*") if m.suffix in (".mp4", ".mkv", ".webm")]
        if not matches:
            raise DownloadFailed(f"Stažené video {vid} nenalezeno v {work}")
        path = matches[0]
    return {
        "id": vid,
        "title": info.get("title") or vid,
        "duration": info.get("duration"),
        "path": str(path),
        "url": info.get("webpage_url") or url,
    }
