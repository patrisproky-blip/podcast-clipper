"""Hlasitost zvuku po vteřinách: najde místa se zvýšenou hlasitostí (smích, výkřiky, důraz)."""
import subprocess

import numpy as np

from platform_utils import ffmpeg_path, hidden_subprocess_kwargs


def loudness_per_second(path, sr=8000):
    """Efektivní hodnota hlasitosti pro každou vteřinu zvuku. Zpracovává se po částech, takže stačí málo paměti."""
    proc = subprocess.Popen(
        [ffmpeg_path(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden_subprocess_kwargs())
    sec_bytes = sr * 2
    out, buf = [], b""
    try:
        while True:
            data = proc.stdout.read(sec_bytes * 30)
            if not data:
                break
            buf += data
            full = len(buf) // sec_bytes
            if full:
                x = np.frombuffer(buf[: full * sec_bytes], dtype="<i2").astype(np.float32) / 32768.0
                out.extend(np.sqrt((x.reshape(full, sr) ** 2).mean(axis=1)))
                buf = buf[full * sec_bytes:]
        if len(buf) > sec_bytes // 2:
            x = np.frombuffer(buf[: len(buf) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
            out.append(float(np.sqrt((x ** 2).mean())))
    finally:
        proc.stdout.close()
        proc.wait()
    return np.array(out, dtype=np.float32)


def loud_seconds(rms):
    """Vteřiny výrazně hlasitější než běžná řeč v daném záznamu (nejhlasitější desetina a zároveň aspoň 1,4× typická hodnota)."""
    if len(rms) < 20:
        return []
    speech = rms[rms >= np.percentile(rms, 30)]
    if len(speech) < 10:
        return []
    thr = max(np.percentile(speech, 90), 1.4 * np.median(speech))
    return [int(i) for i in np.nonzero(rms >= thr)[0]]
