"""Pre-upload checks for the "Subida a eMuwarez" step.

Runs a sequence of checks and STOPS at the first failure (fail-fast) so the
network services — especially eMuwarez, which goes last — are never hit until
everything local passes. Returns each check's result for a styled checklist,
plus a `ready` flag that's only true when every check passed.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional

# Live progress of a running checks call, so the UI can reveal each check as
# it completes (some — qBittorrent, image hosts, eMuwarez — are real network
# calls; the whole sequence has been observed taking ~30s) instead of showing
# nothing until the whole POST resolves. Keyed by item_id: only one checks run
# per item makes sense at a time. Single-process app → an in-memory dict is
# enough, same pattern as services/library.py's sync progress.
_CHECKS_LOCK = threading.Lock()
_CHECKS_PROGRESS: dict[int, dict] = {}

from .eventos import EVENTO_COMPROBACIONES, publicar
from .trackers import POR_DEFECTO


def get_checks_progress(item_id: int) -> dict:
    with _CHECKS_LOCK:
        return dict(_CHECKS_PROGRESS.get(item_id, {"checks": [], "done": False, "ready": False}))


def _set_checks_progress(item_id: int, *, checks: list[dict], done: bool = False, ready: bool = False) -> None:
    with _CHECKS_LOCK:
        _CHECKS_PROGRESS[item_id] = {"checks": list(checks), "done": done, "ready": ready}
    # Único escritor del progreso, así que basta con avisar aquí. Sin agrupar:
    # cada comprobación tarda lo suyo (hay llamadas a eMuwarez, a qBittorrent,
    # al disco), así que esto no sale a chorro — son unas pocas por elemento.
    publicar(EVENTO_COMPROBACIONES, {"item_id": item_id, "done": done})


# Las comprobaciones se parten en dos grupos porque no todas dependen de lo
# mismo. Las de CONTENIDO miran el elemento concreto (¿se puede armar el pack?
# ¿se puede renombrar? ¿tiene castellano?) y hay que hacerlas una por
# elemento. Las de ENTORNO miran la máquina y los servicios (directorio de
# salida, qBittorrent, hosts de imágenes, eMuwarez) y dan lo mismo para todos:
# con 30 capítulos elegidos, preguntarle 30 veces a eMuwarez si está vivo no
# aporta nada y multiplica por 30 la espera.
CONTENT_CHECK_IDS = ("accesible", "season_pack", "idioma_audio", "capitulos_accesibles",
                     "carpeta_subida", "renameable", "formato", "spanish", "tipo")
ENVIRONMENT_CHECK_IDS = ("output", "qbittorrent", "imghosts", "emuwarez")


def _se_puede_leer(ruta: str | None) -> bool:
    """Si el fichero existe y se puede abrir para lectura.

    `os.access(R_OK)` no basta: con un montaje remoto caído puede decir que sí
    y fallar al abrirlo de verdad, que es justo el caso que se quiere pillar.
    """
    if not ruta:
        return False
    try:
        if not os.path.isfile(ruta):
            return False
        with open(ruta, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


# Con las pistas en «Ninguno», «Ver pistas» no deja elegir el idioma: lo que hay que cambiar para poder.
_EN_NINGUNO = " Con «Renombrar/Ordenar pistas» en «Ninguno» (Configuración) no se puede poner: cámbialo para elegirlo."


def run_upload_checks(conn: sqlite3.Connection, item_id: int, *, scope: str = "all",
                      provider_override: str | None = None, ajustes: dict | None = None,
                      tracker: str = POR_DEFECTO) -> dict:
    """`ajustes`: lo que el asistente editó a mano para el pack, con las claves de la cola (`source`, `tag`, `title`,
    `idiomas`), para que el plan y su `nombre` sean los que saldrán de verdad. El Proveedor va aparte, en
    `provider_override`. De un fichero suelto solo cuenta `idiomas`: los elegidos en «Ver pistas», por posición.

    `tracker`: el de la subida (services/trackers.py). Sus normas de idioma (services/reglas_tracker.py) deciden la
    comprobación «spanish» de un fichero y la de cada capítulo de un pack; eMuwarez, el de por defecto, las de siempre.

    `scope`:

    - "all" (por defecto): contenido + entorno, el comportamiento de siempre,
      que es lo que usa una subida de un solo elemento.
    - "content": solo las del elemento. Para una selección de varias
      temporadas o capítulos, donde estas sí van una por una.
    - "environment": solo las compartidas, una vez para toda la tanda.
    """
    from .library import get_library_item
    from .integration_tests import test_qbit_connection, test_emuwarez_connection
    from .image_hosts import fallback_order, get_host_key, test_image_host, host_meta
    from .uploadrr_contract import get_runtime_settings
    from ..database import get_setting_canonical
    from ..integrations.qbittorrent import qbit_configurado

    checks: list[dict] = []
    # El nombre del torrent de un pack listo (el `season_pack_title` del plan),
    # para quien comprueba varias temporadas sin abrir el asistente («Subir por
    # lotes»). None en lo demás: una película o un capítulo lo calculan aparte.
    nombre: Optional[str] = None
    # Y sus avisos (los del plan: proveedor, fuente, pista de audio sin idioma), que el asistente deja corregir y el
    # lote no; `sin_idioma` si uno de ellos es el del audio (el lote la deja desmarcada).
    avisos: list[str] = []
    sin_idioma = False
    _set_checks_progress(item_id, checks=[])  # reset — a previous run's leftovers shouldn't leak in

    # The label states the outcome: the positive phrase when the check passes,
    # the negative one when it fails (e.g. "Contenido accesible" / "Contenido no
    # accesible"). Published to _CHECKS_PROGRESS immediately after each one, so
    # a concurrent poller sees checks land one at a time as they actually
    # complete (some — qBittorrent/image hosts/eMuwarez — are real network
    # calls, not instant).
    def add(cid: str, ok_label: str, fail_label: str, ok: bool, message: str = "") -> bool:
        checks.append({"id": cid, "label": ok_label if ok else fail_label,
                       "status": "ok" if ok else "fail", "message": message})
        _set_checks_progress(item_id, checks=checks)
        return ok

    # A skipped check (yellow): an optional integration that isn't configured, so
    # it's not blocking — the upload can still go ahead.
    def add_skip(cid: str, label: str, message: str = "") -> None:
        checks.append({"id": cid, "label": label, "status": "skip", "message": message})
        _set_checks_progress(item_id, checks=checks)

    def done(complete: bool = False) -> dict:
        # Skips don't block: only a real failure (red) does. `complete` dice si
        # se llegó al final del grupo pedido — antes se deducía mirando que la
        # última comprobación fuera "emuwarez", que deja de valer en cuanto un
        # grupo puede acabar en otra.
        ready = complete and bool(checks) and all(c["status"] in ("ok", "skip") for c in checks)
        _set_checks_progress(item_id, checks=checks, done=True, ready=ready)
        return {"checks": checks, "ready": ready, "nombre": nombre, "avisos": avisos, "sin_idioma": sin_idioma}

    item = get_library_item(conn, item_id) if scope != "environment" else None
    if scope != "environment" and not item:
        add("renameable", "Se puede renombrar", "No se puede renombrar", False, "Elemento no encontrado")
        return done()

    # La INTEGRIDAD (que MediaInfo y mkvmerge puedan leer el fichero entero) se
    # comprueba al encolar, no aquí: es cara y ya se hace allí de todos modos.
    #
    # Pero que el fichero EXISTA y se pueda abrir sí se comprueba aquí, y esto
    # es una corrección: antes se dejaba también para el encolado, y para
    # entonces ya es tarde. La nomenclatura se calcula en el asistente leyendo
    # el fichero; si no puede, cae a las columnas de Radarr/Sonarr y propone un
    # nombre con los códigos crudos ("…AAC.cat.spa.jpn.cat.cat.spa.spa" en un
    # caso real). El asistente solo lo enseñaba como un aviso que se podía
    # ignorar, así que se llegaba a renombrar los ficheros con ese nombre.
    #
    # Es barata: existe y se puede abrir, sin leer el contenido.

    from .mkv_remux import modo_pistas, toca_el_fichero
    # Con las pistas en «Ninguno» el idioma elegido en «Ver pistas» no se escribe en el archivo: no cuenta.
    escribe = toca_el_fichero(modo_pistas(conn))

    if scope == "environment":
        pass                                     # solo entorno: nada del elemento
    elif item.get("item_type") == "season":
        # A season has no `path` of its own — only its episodes do — so the
        # generic single-file "renameable" check below doesn't
        # apply. build_season_pack_plan() already covers everything relevant
        # here in one pass — completeness, per-episode consistency, and
        # whether a shared pack folder is achievable (every available episode
        # must already live in the same folder) — so it's reused as the
        # single season check instead of re-deriving pieces of it.
        from .season_pack import build_season_pack_plan
        # El Proveedor elegido en el asistente, el mismo que el plan y la cola:
        # sin él, un pack con proveedores distintos seguía bloqueado aquí.
        aj = ajustes or {}
        plan = build_season_pack_plan(
            item_id, conn=conn, provider_override=provider_override,
            source_override=aj.get("source") or None, tag_override=aj.get("tag") or None,
            title_override=aj.get("title") or None, idiomas_audio=(aj.get("idiomas") if escribe else None) or None,
            **({} if tracker == POR_DEFECTO else {"tracker": tracker}))
        ok = plan["status"] == "valid"
        if ok:
            nombre = plan.get("season_pack_title") or None
            audio = plan.get("audio_warning")
            avisos = [*(plan.get("warnings") or []),
                      *(a for a in (plan.get("source_mismatch_warning"), audio) if a)]
            sin_idioma = bool(audio)
        msg = "; ".join(plan["errors"]) if not ok else (
            f"Pack listo: {len(plan['episodes'])} episodio(s) → {plan.get('pack_folder')}"
        )
        formato = plan.get("source")  # el `-t` del pack (upload_queue._populate_ready)
        if not add("season_pack", "Se puede preparar el pack de temporada",
                   "No se puede preparar el pack de temporada", ok, msg):
            return done()
        if not add("idioma_audio", "Todas las pistas de audio tienen idioma", "Hay pistas de audio sin idioma",
                   not sin_idioma, (audio + ("" if escribe else _EN_NINGUNO)) if audio else ""):
            return done()

        # Y TODOS sus capítulos, uno a uno. Un pack se nombra leyendo cada
        # fichero: basta que uno no se pueda abrir para que su nombre se
        # calcule con las columnas de Radarr/Sonarr en vez de con el fichero,
        # y ese capítulo acabe renombrado con códigos crudos. Comprobar solo
        # "el pack se puede preparar" no lo pilla: eso mira que estén todos y
        # que compartan carpeta, no que se puedan leer.
        from .path_mapping import resolve_local_path
        ilegibles = []
        for ep in plan.get("episodes", []):
            ruta = ep.get("source_path") or resolve_local_path(ep.get("raw_source_path"), conn=conn)
            if not _se_puede_leer(ruta):
                ilegibles.append(f"capítulo {ep.get('episode_number') or ep.get('episode_id')}")
        cuantos = len(plan.get("episodes", []))
        if not add("capitulos_accesibles",
                   f"Los {cuantos} capítulos son accesibles",
                   "Hay capítulos que no se pueden leer",
                   not ilegibles,
                   ", ".join(ilegibles) if ilegibles else f"{cuantos} capítulo(s) legibles"):
            return done()

        from .uploadrr_contract import motivo_formato_no_subible
        no_subibles = [f"Capítulo {ep.get('episode_number') or ep.get('episode_id')}: {motivo}"
                       for ep in plan.get("episodes", [])
                       if (motivo := motivo_formato_no_subible(ep.get("source_path") or ep.get("raw_source_path")))]
        if not add("formato", "El formato de los capítulos se puede subir",
                   "Hay capítulos en un formato que no se sube", not no_subibles, " ".join(no_subibles)):
            return done()

        # Y que se pueda enlazar en la carpeta de subidas. Es lo que se le
        # entrega a Uploadrr para que el torrent se llame como el release sin
        # tocar la carpeta de Sonarr (ver services/carpeta_subida.py). Se
        # prueba haciendo un enlace de verdad y deshaciéndolo, no mirando
        # números de dispositivo: hay montajes donde coinciden y el enlace
        # falla igual.
        from .carpeta_subida import RAIZ_SUBIDAS, comprobar_enlace
        muestra = next((ep.get("source_path") for ep in plan.get("episodes", [])
                        if ep.get("source_path")), None)
        prueba = comprobar_enlace(muestra) if muestra else {
            "ok": False, "tipo": None, "error": "El pack no tiene ningún capítulo con ruta."}
        if not add("carpeta_subida",
                   "Se puede preparar la carpeta de subida",
                   "No se puede preparar la carpeta de subida",
                   prueba["ok"],
                   (f"{RAIZ_SUBIDAS} (enlace {prueba['tipo']})" if prueba["ok"]
                    else prueba["error"] or "")):
            return done()
        # Falls through to the shared checks below (output dir/qBittorrent/
        # image hosts/eMuwarez) — those don't depend on item_type.
    else:
        path = item.get("path")
        # Translate the Radarr/Sonarr path to where the file is actually
        # reachable (remote path mapping) — same as MediaInfo. Otherwise a
        # remote-mounted library reads fine but the filesystem checks fail on
        # the raw /data path.
        from .path_mapping import resolve_local_path
        local_path = resolve_local_path(path) or path

        # 0. Accesible — el fichero existe y se puede abrir. Va ANTES de
        # "se puede renombrar", que solo mira la carpeta padre: con la carpeta
        # accesible y el fichero no, aquella pasaba y esto se colaba.
        if not add("accesible", "El archivo es accesible", "El archivo no es accesible",
                   _se_puede_leer(local_path),
                   local_path or (path or "El elemento no tiene ruta de archivo")):
            return done()

        # 0b. Y que se pueda enlazar en la carpeta de subidas, igual que un
        # pack: desde 2026-08-04 TODO lo que se sube se enlaza ahí, no solo los
        # packs, para que la biblioteca no aparezca en ninguna ruta de subida.
        from .carpeta_subida import RAIZ_SUBIDAS, comprobar_enlace
        prueba = comprobar_enlace(local_path)
        if not add("carpeta_subida",
                   "Se puede preparar la carpeta de subida",
                   "No se puede preparar la carpeta de subida",
                   prueba["ok"],
                   (f"{RAIZ_SUBIDAS} (enlace {prueba['tipo']})" if prueba["ok"]
                    else prueba["error"] or "")):
            return done()

        # 1. Renameable — write permission on the file's directory. Solo cuenta
        # en la carpeta de subidas: un archivo de la biblioteca no se renombra.
        from .carpeta_subida import en_carpeta_de_subidas
        parent = os.path.dirname(local_path) if local_path else ""
        if not en_carpeta_de_subidas(local_path):
            add_skip("renameable", "No se renombra",
                     "El archivo de la biblioteca conserva su nombre: el de eMuwarez lo lleva su enlace.")
        elif not add("renameable", "Se puede renombrar", "No se puede renombrar",
                     bool(parent) and os.access(parent, os.W_OK), parent):
            return done()

        # 1b. Cada pista de audio con su idioma, contando los elegidos en «Ver pistas». Sin leer las pistas de un MKV,
        # lo que diga la biblioteca. Fuera de un MKV la app no puede etiquetarlas: no bloquea (reglas_tracker.aviso_no_mkv).
        from .emu_format import audio_sin_idioma
        from .mkv_remux import idiomas_de_las_pistas
        from .reglas_tracker import aviso_no_mkv, motivo_audio_sin_idioma
        elegidos = (ajustes or {}).get("idiomas") if escribe else None
        pistas = idiomas_de_las_pistas(local_path, idiomas=elegidos)
        sin_lengua = [i for i, c in enumerate(pistas or [], 1) if c == "UND"]
        falta = bool(sin_lengua) or (pistas is None and audio_sin_idioma(item.get("audio_languages")))
        if falta and not (local_path or "").lower().endswith(".mkv"):
            aviso = aviso_no_mkv(local_path)
            if aviso:
                add_skip("idioma_audio", "Hay pistas de audio sin idioma (no es MKV)", aviso)
        elif not add("idioma_audio", "Todas las pistas de audio tienen idioma", "Hay pistas de audio sin idioma",
                     not falta, (motivo_audio_sin_idioma(sin_lengua) + ("" if escribe else _EN_NINGUNO))
                     if falta else ""):
            return done()
        if elegidos and pistas:
            item = {**item, "audio_languages": " / ".join(dict.fromkeys(pistas))}

        from .uploadrr_contract import motivo_formato_no_subible
        motivo = motivo_formato_no_subible(local_path)
        if not add("formato", "El formato del archivo se puede subir", "El formato del archivo no se puede subir",
                   not motivo, motivo or ""):
            return done()

        # 1c. Idiomas — las normas del tracker (services/reglas_tracker.py). eMuwarez: castellano en el audio o, si
        # no, en los subtítulos; sin MediaInfo leído no se puede confirmar y bloquea.
        from .reglas_tracker import es_anime, reglas_de
        from .trackers import EMU, obtener
        reglas = reglas_de(tracker, item, anime=tracker != EMU.id and es_anime(conn, item))
        if tracker == EMU.id:
            etiquetas = ("Tiene castellano", "No tiene castellano")
        else:
            nombre_tracker = obtener(tracker).nombre
            etiquetas = (f"Idiomas admitidos en {nombre_tracker}", f"Idiomas no admitidos en {nombre_tracker}")
        if not add("spanish", *etiquetas, reglas.permitido, reglas.motivo):
            return done()

        # El formato del `-t` de la subida: el Source elegido en el asistente, el que leerá el comando de la propuesta
        # guardada o, sin ella, el que se le deducirá al encolar.
        from .nomenclature import _infer_source_from_item, get_latest_nomenclature_proposal
        from .upload_preflight import formato_de_subida
        propuesta = get_latest_nomenclature_proposal(item_id, conn=conn)
        formato = (ajustes or {}).get("source") or (
            formato_de_subida(item, propuesta) if propuesta else _infer_source_from_item(item))

    # Sin tipo, Uploadrr-EMU lo adivina del nombre y eMuwarez puede recibir el tipo 0.
    if scope != "environment":
        from .trackers import obtener
        from .uploadrr_contract import motivo_sin_tipo, tipo_de_subida
        donde = "elígelo en el resumen" if scope == "all" else "elige su Source en el asistente de subida"
        cual = f"Source «{formato}»" if formato else "sin Source"
        sin_tipo = motivo_sin_tipo(tracker, formato)  # N.O.B.S y Milnueve no tienen «SD»
        if not tipo_de_subida(formato):
            fallo, mensaje = "No se sabe el tipo de la copia", f"No se sabe el tipo de esta copia ({cual}): {donde}"
        else:
            fallo, mensaje = f"{obtener(tracker).nombre} no tiene este tipo", sin_tipo or formato
        if not add("tipo", "Se sabe el tipo de la copia", fallo, bool(tipo_de_subida(formato)) and not sin_tipo, mensaje):
            return done()

    if scope == "content":
        return done(complete=True)

    # 2. Output directory writable — where Uploadrr-EMU stages the .torrent +
    # images (torrents/screenshots/descriptions) is <uploadrr_path>/tmp, NOT
    # the top-level uploadrr_path itself: in the documented Docker deployment
    # (docker-compose.yml) the code root is mounted :ro on purpose and only
    # its data/ and tmp/ subdirs are mounted rw. Checking the top-level path
    # here always failed under that exact, recommended setup. Falls back to
    # the bundled runtime's own default (/opt/Uploadrr, or UPLOADRR_PATH) the
    # same way uploadrr_contract.py::get_runtime_settings already does — the
    # bundled runtime needs no configuration at all, so this check must not
    # require `uploadrr.path` to have been set by hand.
    base_dir = get_runtime_settings(conn)["uploadrr_path"]
    out_dir = os.path.join(base_dir, "tmp") if base_dir else None
    if not add("output", "Directorio de salida escribible", "Directorio de salida no escribible",
               bool(out_dir) and os.path.isdir(out_dir) and os.access(out_dir, os.W_OK),
               out_dir or "uploadrr.path no configurado"):
        return done()

    # 3. qBittorrent — WebUI login; skipped (yellow) when it isn't configured.
    if not qbit_configurado(get_setting_canonical(conn, "qbit.url"), get_setting_canonical(conn, "qbit.qui_proxy_url")):
        add_skip("qbittorrent", "qBittorrent sin configurar (se omite)",
                 "Configúralo en Ajustes para sembrar automáticamente tras subir")
    else:
        # Con qui guardado se prueba qui, que es por donde irá la subida (Uploadrr-EMU lo antepone a la URL directa).
        r = test_qbit_connection(conn=conn)
        if not add("qbittorrent", "qBittorrent conectado", "qBittorrent no conectado",
                   bool(r.get("ok")), r.get("message", "")):
            return done()

    # 4. Image hosts — at least one reachable, in the user's order.
    reachable = None
    hosts = fallback_order(conn)
    for host in hosts:
        if test_image_host(host, get_host_key(conn, host)).get("ok"):
            reachable = host
            break
    label = host_meta(reachable)["label"] if reachable else None
    if not add("imghosts", "Repo de imágenes disponible", "Repo de imágenes no disponible",
               reachable is not None,
               f"Responde: {label}" if reachable
               else "Ningún host de imágenes responde" if hosts
               else "No hay ningún proveedor de imágenes: añade uno en Configuración"):
        return done()

    # 5. eMuwarez — last, so we don't hit it unless everything else passed.
    # enforce_rate_limit=False: the 1/minute throttle exists to stop someone
    # mashing Configuración's "Probar conexión" button, not to block this
    # automated per-upload precondition — without it, two uploads (manual or
    # automático) queued within the same minute would always make the second
    # one's "eMuwarez disponible" check fail with "Espera Ns…" even though
    # eMuwarez itself was never actually asked twice in a way it would mind.
    # Solo si la subida va a eMuwarez: con eMuwarez caído o sin clave, una subida solo a N.O.B.S o Milnueve no se para.
    from .trackers import EMU, obtener
    if tracker != EMU.id:
        add_skip("emuwarez", "eMuwarez no se comprueba", f"La subida va a {obtener(tracker).nombre}.")
        return done(complete=True)
    r = test_emuwarez_connection(conn=conn, enforce_rate_limit=False)
    add("emuwarez", "eMuwarez disponible", "eMuwarez no disponible", bool(r.get("ok")), r.get("message", ""))
    return done(complete=True)
