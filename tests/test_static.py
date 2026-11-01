"""Chequeos de la UI estática: tarjetas de jugadas reales con video embebido sin cookies."""

import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vargento" / "static"


def _leer(nombre: str) -> str:
    return (STATIC / nombre).read_text(encoding="utf-8")


def test_app_js_usa_youtube_sin_cookies_y_miniaturas():
    js = _leer("app.js")
    assert "youtube-nocookie.com/embed/" in js
    assert "i.ytimg.com/vi/" in js
    assert "youtube.com/embed" not in js.replace("youtube-nocookie.com/embed", "")


def test_app_js_lee_jugadas_reales_y_no_las_demo():
    js = _leer("app.js")
    assert "jugadas_reales_similares" in js and "jugadas_reales_arbitro" in js
    assert "r.similar_plays" not in js and "r.referee_similar_plays" not in js


def test_app_js_revalida_id_y_abre_fuentes_seguras():
    js = _leer("app.js")
    assert "/^[A-Za-z0-9_-]{11}$/" in js
    assert '"noopener noreferrer"' in js and '"_blank"' in js


def test_index_conserva_contenedores_y_no_tiene_iframe():
    html = _leer("index.html")
    assert 'id="ref-plays"' in html and 'id="similar-plays"' in html
    assert not re.search(r"<iframe", html, re.IGNORECASE)
