"""Sestaví samostatnou aplikaci (bez nutnosti instalovat Python) pomocí PyInstalleru.

Spouští se na cílovém systému (Windows nebo macOS), obvykle automaticky přes GitHub Actions.
Potřebuje: pip install -r requirements.txt pyinstaller imageio-ffmpeg
"""
import importlib.util
import io
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).parent.resolve()
BUILD_BIN = ROOT / "build_bin"
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
NAME = "PodcastClipper"
REQUIRED = {"faster_whisper", "ctranslate2", "yt_dlp", "yt_dlp_ejs", "anthropic", "edge_tts"}


def make_executable(p):
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def prepare_ffmpeg():
    import imageio_ffmpeg

    src = Path(imageio_ffmpeg.get_ffmpeg_exe())
    dst = BUILD_BIN / ("ffmpeg.exe" if IS_WIN else "ffmpeg")
    shutil.copy2(src, dst)
    make_executable(dst)
    enc = subprocess.run([str(dst), "-hide_banner", "-encoders"], capture_output=True, text=True, check=True).stdout
    if "libx264" not in enc or " aac " not in enc:
        sys.exit("Bundled ffmpeg lacks required codecs (libx264, aac).")
    print(f"ffmpeg: {src.name} -> {dst}")


def prepare_deno():
    """yt-dlp potřebuje pro YouTube běhové prostředí JavaScriptu; přibalíme deno."""
    machine = platform.machine().lower()
    if IS_WIN:
        target = "x86_64-pc-windows-msvc"
    elif IS_MAC:
        target = "aarch64-apple-darwin" if machine in ("arm64", "aarch64") else "x86_64-apple-darwin"
    else:
        target = "x86_64-unknown-linux-gnu"
    url = f"https://github.com/denoland/deno/releases/latest/download/deno-{target}.zip"
    print("Downloading deno:", url)
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(BUILD_BIN)
    exe = BUILD_BIN / ("deno.exe" if IS_WIN else "deno")
    make_executable(exe)
    print(subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0])


def prepare_font():
    """Úzké tučné písmo Anton (licence SIL OFL) pro videa To nebo to. Když se nestáhne, použije se záložní písmo."""
    base = "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/"
    try:
        for name in ("Anton-Regular.ttf", "OFL.txt"):
            with urllib.request.urlopen(base + name, timeout=60) as r:
                data = r.read()
            dst = ROOT / "assets" / ("Anton-Regular.ttf" if name.endswith("ttf") else "Anton-OFL.txt")
            dst.write_bytes(data)
        print("Anton font downloaded.")
    except Exception as e:  # noqa: BLE001
        print(f"Warning: Anton font not downloaded, fallback font will be used ({e}).")


def has(mod):
    return importlib.util.find_spec(mod) is not None


def main():
    if BUILD_BIN.exists():
        shutil.rmtree(BUILD_BIN)
    BUILD_BIN.mkdir()
    prepare_ffmpeg()
    prepare_deno()
    prepare_font()

    sep = os.pathsep
    args = [
        "app.py", "--name", NAME, "--noconfirm", "--clean", "--onedir", "--windowed",
        "--add-data", f"ui{sep}ui",
        "--add-data", f"config.yaml{sep}.",
        "--add-data", f"assets{sep}assets",
        "--add-binary", f"{BUILD_BIN / ('ffmpeg.exe' if IS_WIN else 'ffmpeg')}{sep}bin",
        "--add-binary", f"{BUILD_BIN / ('deno.exe' if IS_WIN else 'deno')}{sep}bin",
        "--collect-data", "cv2",
    ]
    for mod in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "yt_dlp", "yt_dlp_ejs",
                "anthropic", "certifi", "edge_tts", "aiohttp"):
        if has(mod):
            args += ["--collect-all", mod]
        elif mod in REQUIRED:
            sys.exit(f"Required package {mod} is not installed.")
        else:
            print(f"Warning: package {mod} is not installed, skipping.")
    if IS_MAC:
        args += ["--osx-bundle-identifier", "cz.podcastclipper.app"]

    import PyInstaller.__main__

    PyInstaller.__main__.run(args)
    print("Done:", ROOT / "dist")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
