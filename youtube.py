"""Nahrávání hotových klipů na YouTube přes oficiální API (OAuth pro aplikaci na počítači, bez dalších knihoven)."""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def parse_client(text):
    """Z obsahu staženého souboru client_secret*.json vytáhne (client_id, client_secret)."""
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("Tohle není platný soubor JSON z Google Cloud.") from e
    d = d.get("installed") or d.get("web") or d
    cid, sec = d.get("client_id"), d.get("client_secret")
    if not cid or not sec:
        raise ValueError("V souboru chybí client_id nebo client_secret. Vytvořili jste přístup typu „Aplikace pro počítač“?")
    return cid, sec


def auth_url(client_id, redirect, state=""):
    return AUTH_URL + "?" + urllib.parse.urlencode({"state": state,
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent"})


def _post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error_description") or str(e)
        except Exception:  # noqa: BLE001
            msg = str(e)
        raise RuntimeError(f"Google odmítl přihlášení: {msg}") from e


def exchange_code(client, code, redirect):
    d = _post_form(TOKEN_URL, {"code": code, "client_id": client[0], "client_secret": client[1],
                               "redirect_uri": redirect, "grant_type": "authorization_code"})
    if not d.get("refresh_token"):
        raise RuntimeError("Google nevrátil trvalé povolení. Zkuste přihlášení znovu.")
    return d["refresh_token"]


def access_token(client, refresh):
    return _post_form(TOKEN_URL, {"client_id": client[0], "client_secret": client[1],
                                  "refresh_token": refresh, "grant_type": "refresh_token"})["access_token"]


def upload(path, title, description, privacy, client, refresh, tags=None, on_progress=None):
    """Nahraje video. Vrátí odkaz na něj. Používá resumable upload po kouscích."""
    from pathlib import Path
    p = Path(path)
    size = p.stat().st_size
    token = access_token(client, refresh)
    meta = {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags or [], "categoryId": "24"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    req = urllib.request.Request(
        UPLOAD_URL, data=json.dumps(meta).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ctx()) as r:
            session = r.headers["Location"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(_api_error(e)) from e
    chunk = 8 * 1024 * 256 * 4   # 8 MB (násobek 256 kB, jak API vyžaduje)
    sent = 0
    with p.open("rb") as f:
        while sent < size:
            data = f.read(chunk)
            end = sent + len(data) - 1
            req = urllib.request.Request(session, data=data, method="PUT", headers={
                "Content-Length": str(len(data)), "Content-Range": f"bytes {sent}-{end}/{size}"})
            try:
                with urllib.request.urlopen(req, timeout=300, context=_ctx()) as r:
                    body = json.loads(r.read().decode("utf-8") or "{}")
                    if on_progress:
                        on_progress(1.0)
                    return "https://youtu.be/" + body["id"]
            except urllib.error.HTTPError as e:
                if e.code != 308:     # 308 = kousek přijat, pokračuje se dalším
                    raise RuntimeError(_api_error(e)) from e
            sent += len(data)
            if on_progress:
                on_progress(sent / size)
    raise RuntimeError("Nahrávání skončilo bez odpovědi od YouTube.")


def _api_error(e):
    try:
        err = json.loads(e.read().decode("utf-8")).get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        msg = err.get("message", str(e))
    except Exception:  # noqa: BLE001
        return f"YouTube vrátil chybu {e.code}."
    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        return "Dnešní limit YouTube pro nahrávání je vyčerpaný. Zkuste to zítra."
    if reason == "uploadLimitExceeded":
        return "Váš kanál dosáhl denního limitu nahrávání."
    return f"YouTube: {msg}"
