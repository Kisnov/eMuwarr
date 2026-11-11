"""Authoritative nomenclature check against the eMuwarez guide, using MediaInfo.

Radarr/Sonarr only expose a *summary* mediaInfo (one audio codec/channels, not
per-track), so the exact eMuwarez name — which needs the per-track audio —
can only be built by reading MediaInfo from the actual file. This module:

  1. needs a MediaInfo read of the file (if not, `evaluate_file` raises),
  2. builds the expected file name with the SAME generator the upload uses
     (nomenclature.build_nomenclature_proposal), so the two can't disagree,
  3. compares it to the real file name, ignoring the group after the last '-',
  4. yields 'Correcta' / 'No adaptada' + a per-token diff.

Hoy solo lo ejercitan los tests (`evaluate_file`, `compare`): la app ya no tiene un
recorrido por temporadas o series que lo llame.
"""
from __future__ import annotations

import difflib
import os

from .mkv_title import to_filesystem_name

STATUS_CORRECT = "Correcta"
STATUS_NOT_ADAPTED = "No adaptada"


def _strip_group(core_dotted: str) -> str:
    """Remove the trailing '-GROUP' (uploader), which is optional.

    The group is always the tail of the LAST dot-segment (e.g. '5.1-GRUPO' →
    last segment '1-GRUPO'). Splitting only inside that last segment keeps
    mid-name compounds like 'WEB-DL' / 'DTS-HD' intact even when there is no
    group at all."""
    parts = core_dotted.split(".")
    if parts and "-" in parts[-1]:
        parts[-1] = parts[-1].rsplit("-", 1)[0]
        if not parts[-1]:
            parts = parts[:-1]
    return ".".join(parts)


def _provider_codes() -> set[str]:
    from .source_assistant import EMU_PROVIDERS
    return {p["code"].lower() for p in EMU_PROVIDERS}


def _drop_optional_provider(tokens: list[str]) -> list[str]:
    """Provider is optional and sits right before the WEB-DL source (e.g.
    'AMZN WEB-DL'). Drop it so a name with or without provider both match."""
    codes = _provider_codes()
    out = list(tokens)
    for i in range(len(out) - 1):
        if out[i].lower() in codes and out[i + 1].lower() == "web-dl":
            del out[i]
            break
    return out


def compare(actual_filename: str, exp_core: str) -> tuple[bool, list[str]]:
    """(matches, diff). The real file complies if its cleaned core (group and
    optional provider aside) equals the expected core, token by token,
    case-insensitively."""
    base = os.path.splitext(actual_filename)[0]
    actual_core = _strip_group(to_filesystem_name(base))
    # Provider is optional (before the source) — ignore it on BOTH sides so it
    # never drives the verdict, whether or not each name carries it.
    at = _drop_optional_provider([t for t in actual_core.split(".") if t])
    et = _drop_optional_provider([t for t in exp_core.split(".") if t])
    if [t.lower() for t in at] == [t.lower() for t in et]:
        return True, []
    # Alineado por tokens: un token de más o de menos no desplaza todo lo demás.
    diff: list[str] = []
    matcher = difflib.SequenceMatcher(a=[t.lower() for t in at], b=[t.lower() for t in et], autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "equal":
            continue
        sobran, faltan = at[a1:a2], et[b1:b2]
        for i in range(max(len(sobran), len(faltan))):
            av = sobran[i] if i < len(sobran) else None
            ev = faltan[i] if i < len(faltan) else None
            if av and ev:
                diff.append(f"'{av}' ≠ esperado '{ev}'")
            elif av:
                diff.append(f"'{av}' sobra")
            else:
                diff.append(f"falta '{ev}'")
    return False, diff or ["El nombre no coincide con el esperado"]


# --- Veredicto de un fichero --------------------------------------------------

def evaluate_file(conn, item: dict) -> dict:
    """El veredicto para un elemento: {status, expected, actual, diff}.
    Necesita el fichero y el binario `mediainfo`; si no, lanza una excepción."""
    from .media_info_service import build_mediainfo_report
    from .nomenclature import build_nomenclature_proposal
    from .path_mapping import resolve_local_path, ruta_de_lectura
    leida = ruta_de_lectura(item, conn=conn)
    local = resolve_local_path(leida, conn=conn) or leida
    if not local or not os.path.exists(local):
        raise FileNotFoundError(f"El archivo no existe o no es accesible: {local or '(sin ruta)'}")
    # Sin radarr_source, como la propuesta: así las dos lecturas comparten caché.
    report = build_mediainfo_report(local)
    if not report.get("available"):
        raise RuntimeError(report.get("message") or "MediaInfo no devolvió datos")
    proposal = build_nomenclature_proposal(item["id"], conn=conn, guardar=False)
    exp = _strip_group(proposal.get("proposed_filename") or "")
    actual_name = os.path.basename(item.get("path") or local)
    matches, diff = compare(actual_name, exp)
    return {
        "status": STATUS_CORRECT if matches else STATUS_NOT_ADAPTED,
        "expected": exp,
        "actual": to_filesystem_name(os.path.splitext(actual_name)[0]),
        "diff": diff,
    }
