"""Nahrávání živého streamu (YouTube, Twitch, Kick) po částech. Každá hotová část se předá ke zpracování na klipy."""
import subprocess
import time
from pathlib import Path

from platform_utils import ffmpeg_path, hidden_subprocess_kwargs

MIN_PART_S = 90          # kratší zbytek na konci streamu se zahodí


def _ydl_opts():
    from downloader import _base_opts
    return _base_opts() | {"skip_download": True, "noplaylist": True, "format": "best[height<=1080]/best"}


def probe(url):
    """Vrátí {live: bool, title, id}. Nikdy nevyhodí chybu (neznámé = ne živé)."""
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
        return {"live": bool(info.get("is_live")) or info.get("live_status") == "is_live",
                "was_live": bool(info.get("was_live")) or info.get("live_status") in ("was_live", "post_live"),
                "title": info.get("title") or url, "id": info.get("id") or ""}
    except Exception:  # noqa: BLE001
        return {"live": False, "was_live": False, "title": url, "id": ""}


def _resolve(url):
    """Aktuální přímý odkaz na proud (platnost vyprší, proto se zjišťuje před každou částí)."""
    import yt_dlp
    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    if not (info.get("is_live") or info.get("live_status") == "is_live"):
        return None, None
    return info["url"], info.get("http_headers") or {}


def record(url, out_dir, session, chunk_s, on_part, should_stop, on_status, log=print):
    """Nahrává po částech dlouhých chunk_s sekund. on_part(path, index) se volá po každé hotové části."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    idx, fails = 0, 0
    while not should_stop():
        try:
            direct, headers = _resolve(url)
        except Exception as e:  # noqa: BLE001
            direct, headers = None, None
            log(f"Zjištění proudu selhalo: {e}")
        if not direct:
            fails += 1
            if fails >= 4:            # stream skončil (nebo se nedá otevřít)
                log("Stream skončil.")
                return
            on_status(f"Čekám na stream… ({fails}/4)")
            for _ in range(15):
                if should_stop():
                    return
                time.sleep(1)
            continue
        fails = 0
        idx += 1
        path = out_dir / f"{session}-{idx:03d}.mkv"
        cmd = [ffmpeg_path(), "-y", "-loglevel", "error",
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
        if headers:
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        cmd += ["-i", direct, "-t", str(chunk_s), "-c", "copy", str(path)]
        on_status(f"Nahrávám živě: část {idx}")
        started = time.time()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden_subprocess_kwargs())
        while proc.poll() is None:
            if should_stop():
                try:
                    proc.stdin.write(b"q")      # korektně dokončí soubor
                    proc.stdin.flush()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            time.sleep(1)
        length = time.time() - started
        if path.exists() and length >= MIN_PART_S and path.stat().st_size > 50_000:
            on_part(path, idx)
        else:
            path.unlink(missing_ok=True)
            if not should_stop() and length < MIN_PART_S:
                fails += 1
                if fails >= 4:
                    log("Stream skončil.")
                    return
