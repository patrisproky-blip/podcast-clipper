"""Výběr momentů přes Google Gemini s bezplatným klíčem (aistudio.google.com). Bez dalších balíčků, jen HTTPS."""
import json
import ssl
import time
import urllib.error
import urllib.request

from selector import build_prompts, finalize, parse_answer

MODELS = ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash")     # zkouší se popořadě, názvy se časem mění
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


class Unavailable(RuntimeError):
    pass


def _call(model, key, prompt):
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"}}
    req = urllib.request.Request(URL.format(model=model), data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=120, context=_ctx()) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def ask(key, prompt, log=print):
    """Pošle dotaz; při vyčerpané kvótě chvíli počká, při neexistujícím modelu zkusí další."""
    last = None
    for model in MODELS:
        for attempt in range(3):
            try:
                return _call(model, key, prompt)
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:
                    time.sleep(25 * (attempt + 1))
                    continue
                if e.code in (404, 400):
                    break                      # špatný název modelu -> další
                if e.code in (401, 403):
                    raise Unavailable("Klíč Gemini nebyl přijat.") from e
                time.sleep(5)
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as e:
                last = e
                time.sleep(5)
    raise Unavailable(f"Gemini neodpovědělo ({type(last).__name__}).")


def select_gemini(transcript, cfg, key, log=print):
    parts = build_prompts(transcript, cfg)
    candidates, ok = [], 0
    for i, p in enumerate(parts):
        if i:
            time.sleep(6)      # bezplatná kvóta je omezená na počet dotazů za minutu
        try:
            text = ask(key, p["prompt"], log)
        except Unavailable:
            if ok == 0 and i == 0:
                raise
            log(f"Část {p['label']}: Gemini neodpovědělo, přeskakuji.")
            continue
        try:
            candidates += parse_answer(text)
            ok += 1
        except ValueError as e:
            log(f"Část {p['label']}: {e}")
            ok += 1
    return finalize(candidates, transcript, cfg)
