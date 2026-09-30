"""Videos y fotos de las jugadas: archivos subidos o enlaces externos.

Los enlaces (YouTube, Vimeo, un .mp4 público, etc.) evitan alojar material con
derechos de TV: sólo se guarda la dirección y el segundo donde empieza la jugada.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MAX_UPLOAD_BYTES = 300 * 1024 * 1024  # 300 MB

ALLOWED_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > 2000:
        raise ValueError("El enlace debe empezar con http:// o https://")
    return url


def youtube_id(url: str) -> str | None:
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    vid = None
    if host == "youtu.be":
        vid = p.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        if p.path == "/watch":
            vid = parse_qs(p.query).get("v", [None])[0]
        else:
            parts = p.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
                vid = parts[1]
    return vid if vid and _YT_ID.match(vid) else None


def vimeo_id(url: str) -> str | None:
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    if host in ("vimeo.com", "player.vimeo.com"):
        for part in p.path.strip("/").split("/"):
            if part.isdigit():
                return part
    return None


def start_from_url(url: str) -> float:
    """Toma el inicio de enlaces tipo ...?t=1m30s o ...&t=90."""
    t = parse_qs(urlparse(url).query).get("t", [""])[0] or urlparse(url).fragment.removeprefix("t=")
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", t or "")
    if not t or not m:
        return 0.0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return float(h * 3600 + mi * 60 + s)


def embed(item: dict) -> dict:
    """Cómo mostrar un medio en el navegador."""
    start = int(item.get("start_seconds") or 0)
    if item["kind"] == "file":
        src = f"/api/media/{item['id']}/file"
        kind = "image" if item["mime"].startswith("image/") else "video"
        return {"type": kind, "src": src + (f"#t={start}" if kind == "video" and start else "")}
    url = item["url"]
    if yt := youtube_id(url):
        return {"type": "iframe", "src": f"https://www.youtube-nocookie.com/embed/{yt}?start={start}&rel=0"}
    if vm := vimeo_id(url):
        return {"type": "iframe", "src": f"https://player.vimeo.com/video/{vm}#t={start}s"}
    path = urlparse(url).path.lower()
    if path.endswith(_VIDEO_EXT):
        return {"type": "video", "src": url + (f"#t={start}" if start else "")}
    if path.endswith(_IMAGE_EXT):
        return {"type": "image", "src": url}
    return {"type": "link", "src": url}


def public(item: dict) -> dict:
    return {
        "id": item["id"],
        "kind": item["kind"],
        "mime": item["mime"],
        "url": item["url"],
        "start_seconds": item["start_seconds"],
        "caption": item["caption"],
        "play_id": item["play_id"],
        "review_id": item["review_id"],
        "created_by": item["created_by"],
        "embed": embed(item),
    }


def new_filename(mime: str) -> str:
    return secrets.token_hex(16) + ALLOWED_MIME[mime]


def safe_path(media_dir: Path, name: str) -> Path:
    path = (media_dir / name).resolve()
    if path.parent != media_dir.resolve():
        raise ValueError("ruta inválida")
    return path
