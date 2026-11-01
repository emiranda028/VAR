"""Importa jugadas reales desde un archivo JSONL (formato del agente scout-datos).

Uso:
    python -m vargento.importar data/candidatas/lote.jsonl            # sólo simula
    python -m vargento.importar data/candidatas/lote.jsonl --aplicar  # carga en la base

Cada línea es un objeto JSON con: play_type, referee_name, called, features y,
opcionalmente, match, date, description, fuentes, inferidos, confianza y video
({"url": <YouTube>, "inicio": <segundos>}).
Por defecto sólo simula: valida todo e informa qué cargaría, sin tocar la base.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .db import Database
from .domain import get_play_type, validate_features


SEPARADOR_FUENTES = " | Fuentes: "
CONFIANZAS = ("alta", "media", "baja")
HOSTS_YOUTUBE = ("youtube.com", "www.youtube.com", "m.youtube.com")
ID_YOUTUBE = re.compile(r"[A-Za-z0-9_-]{11}")


@dataclass
class Jugada:
    linea: int
    play_type: str
    referee_name: str
    features: dict[str, float]
    called: bool
    texto: str  # descripción tal como vino, sin las fuentes
    fuentes: list[str]
    match: str
    date: str
    confianza: str
    video: str | None = None  # JSON compacto {"url", "inicio"} o None

    @property
    def description(self) -> str:
        """Descripción con las fuentes al final, para no perder la trazabilidad."""
        return f"{self.texto}{SEPARADOR_FUENTES}{' '.join(self.fuentes)}".strip(" |")


@dataclass
class Resultado:
    validas: list[Jugada] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    duplicadas: list[str] = field(default_factory=list)
    baja_confianza: list[str] = field(default_factory=list)
    arbitros_nuevos: dict[str, str] = field(default_factory=dict)  # clave casefold -> nombre
    videos_actualizados: list[str] = field(default_factory=list)
    videos_nuevos: dict[int, str] = field(default_factory=dict)  # id de jugada real -> video JSON
    cargadas: int = 0


def _texto_sin_fuentes(description: str) -> str:
    return description.split(SEPARADOR_FUENTES.strip(), 1)[0].strip(" |")


def _fuentes(valor) -> list[str]:
    """Normaliza `fuentes` y exige al menos una URL http(s)."""
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list):
        raise ValueError("fuentes tiene que ser una lista de URLs")
    urls = [str(f).strip() for f in valor if str(f).strip()]
    if not any(u.startswith(("http://", "https://")) for u in urls):
        raise ValueError("falta al menos una fuente con URL http(s) (sin fuente no hay jugada)")
    return urls


def _id_youtube(url: str) -> str:
    """Saca el ID de un enlace de YouTube (watch?v=ID o youtu.be/ID)."""
    partes = urlsplit(url.strip())
    host = (partes.hostname or "").lower()
    if partes.scheme not in ("http", "https"):
        raise ValueError("video.url tiene que empezar con http:// o https://")
    if host in HOSTS_YOUTUBE and partes.path == "/watch":
        ids = parse_qs(partes.query).get("v", [])
        video_id = ids[0] if len(ids) == 1 else ""
    elif host == "youtu.be":
        video_id = partes.path[1:]
    else:
        raise ValueError(f"video.url tiene que ser un enlace de YouTube (vino {url!r})")
    if not ID_YOUTUBE.fullmatch(video_id):
        raise ValueError(f"video.url no tiene un ID de YouTube válido de 11 caracteres (vino {url!r})")
    return video_id


def _video(valor) -> str | None:
    """Valida `video` y lo devuelve como JSON compacto con la URL canónica, o None si no hay."""
    if valor is None:
        return None
    if not isinstance(valor, dict):
        raise ValueError("video tiene que ser un objeto con url e inicio")
    if not isinstance(valor.get("url"), str) or not valor["url"].strip():
        raise ValueError("falta video.url")
    inicio = valor.get("inicio")
    if isinstance(inicio, bool) or not isinstance(inicio, int) or inicio < 0:
        raise ValueError(f"video.inicio tiene que ser un número entero de segundos >= 0 (vino {inicio!r})")
    url = f"https://www.youtube.com/watch?v={_id_youtube(valor['url'])}"
    return json.dumps({"url": url, "inicio": inicio}, separators=(",", ":"))


def _parsear(linea: int, obj: dict) -> Jugada:
    if not isinstance(obj, dict):
        raise ValueError("la línea no es un objeto JSON")
    pt = get_play_type(str(obj.get("play_type", "")))
    nombre = str(obj.get("referee_name") or "").strip()
    if not nombre:
        raise ValueError("falta referee_name")
    if not isinstance(obj.get("called"), bool):
        raise ValueError("called tiene que ser true o false")
    fuentes = _fuentes(obj.get("fuentes"))
    confianza = str(obj.get("confianza") or "media").strip().casefold()
    if confianza not in CONFIANZAS:
        raise ValueError(f"confianza tiene que ser alta, media o baja (vino {obj.get('confianza')!r})")
    features = validate_features(pt, obj.get("features") or {})
    video = _video(obj.get("video"))
    return Jugada(
        linea=linea,
        play_type=pt.key,
        referee_name=nombre,
        features=features,
        called=obj["called"],
        texto=str(obj.get("description") or "").strip(),
        fuentes=fuentes,
        match=str(obj.get("match") or "").strip(),
        date=str(obj.get("date") or "").strip(),
        confianza=confianza,
        video=video,
    )


def importar(
    db: Database, lineas: list[str], aplicar: bool = False, incluir_baja: bool = False
) -> Resultado:
    """Valida las líneas y, si `aplicar`, carga las válidas en una sola transacción.

    Una jugada es duplicada si coincide tipo, partido, fecha y descripción (sin
    contar las fuentes), ya sea con otra línea del archivo o con la base. Si una
    duplicada de la base trae un video nuevo o distinto, se anota en
    `videos_actualizados` y, si `aplicar`, se guarda en esa jugada (sólo reales).
    """
    res = Resultado()
    vistas: set[tuple] = set()
    for n, texto in enumerate(lineas, start=1):
        if not texto.strip():
            continue
        try:
            jugada = _parsear(n, json.loads(texto))
        except (ValueError, KeyError, TypeError) as exc:
            res.errores.append(f"línea {n}: {exc}")
            continue
        if jugada.confianza == "baja" and not incluir_baja:
            res.baja_confianza.append(f"línea {n}: {jugada.match or jugada.texto[:40]}")
            continue
        clave = (jugada.play_type, jugada.match, jugada.date, jugada.texto.casefold())
        en_base = [
            j for j in db.jugadas_para(jugada.play_type, jugada.match, jugada.date)
            if _texto_sin_fuentes(j["description"]).casefold() == clave[3]
        ]
        if clave in vistas or en_base:
            res.duplicadas.append(f"línea {n}: {jugada.match} {jugada.date} {jugada.texto[:40]}")
            _anotar_video(res, n, jugada, en_base)
            continue
        vistas.add(clave)
        ref_clave = jugada.referee_name.casefold()
        if ref_clave not in res.arbitros_nuevos and db.get_referee_by_name(jugada.referee_name) is None:
            res.arbitros_nuevos[ref_clave] = jugada.referee_name
        res.validas.append(jugada)

    if aplicar and res.validas:
        res.cargadas = db.import_plays(
            [
                (j.play_type, j.referee_name, j.features, j.called, j.description, j.match, j.date, j.video)
                for j in res.validas
            ]
        )
    if aplicar and res.videos_nuevos:
        db.actualizar_videos(res.videos_nuevos)
    return res


def _anotar_video(res: Resultado, n: int, jugada: Jugada, en_base: list[dict]) -> None:
    """Si la duplicada trae un video nuevo o distinto para una jugada real de la base, lo anota."""
    if jugada.video is None:
        return
    for fila in en_base:
        if fila["origen"] == "real" and res.videos_nuevos.get(fila["id"], fila["video"]) != jugada.video:
            res.videos_nuevos[fila["id"]] = jugada.video
            res.videos_actualizados.append(f"línea {n}: {jugada.match} {jugada.date} {jugada.texto[:40]}")


def _informe(res: Resultado, aplicar: bool) -> str:
    partes = [
        f"Válidas: {len(res.validas)}",
        f"Errores: {len(res.errores)}",
        f"Duplicadas: {len(res.duplicadas)}",
        f"Baja confianza (omitidas): {len(res.baja_confianza)}",
        f"Árbitros nuevos: {len(res.arbitros_nuevos)}"
        + (f" ({', '.join(sorted(res.arbitros_nuevos.values()))})" if res.arbitros_nuevos else ""),
        f"Videos actualizados: {len(res.videos_actualizados)}",
    ]
    for titulo, items in (("Errores", res.errores), ("Duplicadas", res.duplicadas),
                          ("Baja confianza", res.baja_confianza), ("Videos actualizados", res.videos_actualizados)):
        if items:
            partes.append(f"\n{titulo}:")
            partes.extend(f"  - {i}" for i in items)
    if aplicar:
        partes.append(f"\nCargadas en la base: {res.cargadas}")
    else:
        partes.append("\nSimulación: no se cargó nada. Usá --aplicar para cargar.")
    return "\n".join(partes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importar jugadas reales a VARgento")
    parser.add_argument("archivo", type=Path, help="archivo .jsonl con las jugadas")
    parser.add_argument("--db", default=os.environ.get("VARGENTO_DB", "vargento.db"))
    parser.add_argument("--aplicar", action="store_true", help="cargar en la base (si no, sólo simula)")
    parser.add_argument("--incluir-baja", action="store_true", help="incluir jugadas de confianza baja")
    args = parser.parse_args(argv)

    try:
        lineas = args.archivo.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        print(f"No se pudo leer {args.archivo}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    # En simulación no se crea una base nueva: si no existe, se valida contra una vacía en memoria.
    ruta = args.db if (args.aplicar or Path(args.db).exists()) else ":memory:"
    db = Database(ruta)
    try:
        res = importar(db, lineas, aplicar=args.aplicar, incluir_baja=args.incluir_baja)
    finally:
        db.close()
    print(_informe(res, args.aplicar))
    return 1 if res.errores else 0


if __name__ == "__main__":
    sys.exit(main())
