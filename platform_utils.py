"""Pomocné funkce pro běh jako samostatná aplikace (PyInstaller) i ze zdrojáků."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
SRC_DIR = Path(__file__).parent.resolve()
RES_DIR = Path(getattr(sys, "_MEIPASS", SRC_DIR))  # kde leží přibalené soubory (ui, assets, bin)
IS_WIN = sys.platform.startswith("win")


def data_dir():
    """Kam se ukládá nastavení, rozpracovaná videa a hotové klipy.
    Ze zdrojáků: složka projektu. Jako aplikace: ~/PodcastClipper."""
    d = (Path.home() / "PodcastClipper") if FROZEN else SRC_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource(*parts):
    return RES_DIR.joinpath(*parts)


def bin_dir():
    d = RES_DIR / "bin"
    return d if d.is_dir() else None


def ffmpeg_path():
    exe = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    b = bin_dir()
    if b and (b / exe).exists():
        return str(b / exe)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # záloha: ffmpeg z balíčku imageio-ffmpeg, pokud je nainstalovaný
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        raise FileNotFoundError("ffmpeg nebyl nalezen. Nainstaluj ho nebo použij hotovou aplikaci.")


def setup_environment():
    """Přidá přibalené programy (ffmpeg, deno) do PATH, aby je yt-dlp našel."""
    b = bin_dir()
    if b:
        os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")
        if not IS_WIN:  # balení někdy ztratí příznak „spustitelný“
            for f in b.iterdir():
                try:
                    f.chmod(f.stat().st_mode | 0o111)
                except OSError:
                    pass


def hidden_subprocess_kwargs():
    """Ve Windows zabrání vyskakování černých oken konzole při volání ffmpeg."""
    if IS_WIN:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def redirect_output_when_windowed(log_file):
    """Aplikace bez konzole nemá stdout/stderr; přesměrujeme je do logu."""
    if sys.stdout is None or sys.stderr is None or FROZEN:
        f = open(log_file, "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
