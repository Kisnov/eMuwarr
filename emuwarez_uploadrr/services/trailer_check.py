"""Post-upload trailer check.

Uploadrr-EMU writes its own `[EMU]DESCRIPTION.txt` under its `tmp/` dir right
before uploading, and — confirmed by reading Uploadrr-EMU's own source
(src/trackers/UNIT3D.py) — that exact file is read back verbatim and sent as
the torrent's real description to eMuwarez. So finding a `[youtube]KEY
[/youtube]` tag in it means the trailer really is on the eMuwarez page, not
just a guess.

This is a best-effort, read-only convenience check — never blocks anything.
If the description file can't even be located, that's inconclusive (not a
"no trailer" result): Uploadrr's tmp folder naming isn't 100% predictable
from the outside, so this tries a couple of likely candidate names before
falling back to the most recently modified `[EMU]DESCRIPTION.txt` under
tmp/, same strategy the legacy CLI already used (legacy.py::
find_emu_description_file) when this only existed for the interactive path.
"""
from __future__ import annotations

import glob
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_YOUTUBE_RE = re.compile(r"\[youtube\]\s*([A-Za-z0-9_-]+)\s*\[/youtube\]", re.I)

# upload_jobs.trailer_estado (esquema 54). NULL: no se comprobó, o no se encontró el archivo de descripción.
TRAILER_INCLUIDO = "incluido"
# eMuwarez exige el tráiler y la descripción salió sin él: hay que ponerlo a mano editando el torrent.
TRAILER_FALTA = "falta"
# El usuario dijo en Subidas que ya lo puso a mano: deja de avisar.
TRAILER_A_MANO = "a_mano"


def _instante(desde) -> Optional[float]:
    """`desde` (el started_at ISO del trabajo) como marca de tiempo; None si no hay."""
    if not desde:
        return None
    try:
        dt = datetime.fromisoformat(str(desde))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def find_emu_description_file(uploadrr_path: str, candidate_names: list[str],
                              desde=None) -> Optional[Path]:
    """Locate the [EMU]DESCRIPTION.txt Uploadrr-EMU generated for this
    upload. `candidate_names` are the tmp/ subfolder names most likely to
    match (the applied filename for a single file, the season-pack's own
    eMuwarez title for a pack) — checked first since they're exact and free;
    the mtime-sorted scan of the whole tmp/ tree is the fallback, and it only
    accepts a file written after `desde` (when the job started): otherwise it
    picked up the previous job's description and reported its trailer."""
    if not uploadrr_path:
        return None
    tmp_dir = Path(uploadrr_path) / "tmp"
    if not tmp_dir.exists():
        return None

    for name in candidate_names:
        if not name:
            continue
        exact = tmp_dir / name / "[EMU]DESCRIPTION.txt"
        if exact.exists():
            return exact

    try:
        # rglob() treats [...] as a glob character class, not literal
        # brackets — glob.escape() is needed or "[EMU]DESCRIPTION.txt"
        # would actually match e.g. "EDESCRIPTION.txt" (one char from
        # {E,U,M}) and never the real filename.
        candidates = sorted(
            tmp_dir.rglob(glob.escape("[EMU]DESCRIPTION.txt")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    limite = _instante(desde)
    if limite is None:
        return None
    try:
        candidates = [c for c in candidates if c.stat().st_mtime >= limite]
    except OSError:
        return None
    if not candidates:
        return None

    preferred = {n for n in candidate_names if n}
    for c in candidates:
        if c.parent.name in preferred:
            return c
    return candidates[0]


def extract_youtube_trailer_url(description_file: Path) -> Optional[str]:
    try:
        text = description_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = _YOUTUBE_RE.search(text)
    if not m:
        return None
    return f"https://www.youtube.com/watch?v={m.group(1)}"


def check_trailer_for_job(uploadrr_path: str, candidate_names: list[str], desde=None) -> dict:
    """{checked, found, url}. `checked=False` means the description file
    itself couldn't be located at all (inconclusive — say nothing rather
    than falsely claim "no trailer"). `checked=True, found=False` means the
    file WAS read but had no [youtube] tag — a real "not found"."""
    desc_file = find_emu_description_file(uploadrr_path, candidate_names, desde=desde)
    if not desc_file:
        return {"checked": False, "found": False, "url": None}
    url = extract_youtube_trailer_url(desc_file)
    return {"checked": True, "found": bool(url), "url": url}


def marcar_trailer_a_mano(job_id: int, puesto: bool = True, conn=None) -> dict:
    """«Ya lo he añadido» en Subidas: una subida a la que le faltaba el tráiler pasa a 'a_mano' y deja de avisar.
    Con `puesto=False` se deshace (vuelve a 'falta'). Solo entre esos dos estados: ni inventa que faltaba en una
    que no se comprobó, ni toca una que ya lo llevaba. {ok, error, trailer_estado}."""
    from .upload_states import escribir_estado
    from .upload_execution.comun import _get_db_conn

    propia = conn is None
    conn = conn or _get_db_conn()
    try:
        fila = conn.execute("SELECT trailer_estado FROM upload_jobs WHERE id = ?", (job_id,)).fetchone()
        if fila is None:
            return {"ok": False, "error": "not_found", "trailer_estado": None}
        desde, hacia = (TRAILER_FALTA, TRAILER_A_MANO) if puesto else (TRAILER_A_MANO, TRAILER_FALTA)
        actual = fila[0]
        if actual == hacia:
            return {"ok": True, "error": None, "trailer_estado": actual}
        if actual != desde:
            return {"ok": False, "error": "sin_aviso", "trailer_estado": actual}
        # Por escribir_estado: es el que avisa a la interfaz (subida-estado) para que la lista se ponga al día.
        escribir_estado(job_id, salvo=(), trailer_estado=hacia, conn=conn)
        return {"ok": True, "error": None, "trailer_estado": hacia}
    finally:
        if propia:
            conn.close()
