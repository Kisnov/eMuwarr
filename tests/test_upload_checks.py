"""Tests for the pre-upload checks (services/upload_checks)."""
from __future__ import annotations

import pytest

from emuwarez_uploadrr.services import upload_checks as uc


@pytest.fixture(autouse=True)
def _carpeta_de_subidas_aislada(tmp_path, monkeypatch):
    """La comprobación "carpeta_subida" enlaza de verdad en /config/Uploadrr/
    uploads, que solo existe dentro del contenedor. Se apunta a un temporal
    para que los tests no dependan de eso — y sigan probando lo que importa,
    que es que el enlace se pueda hacer."""
    from emuwarez_uploadrr.services import carpeta_subida
    monkeypatch.setattr(carpeta_subida, "RAIZ_SUBIDAS", str(tmp_path / "uploads"))


@pytest.fixture
def db_conn(isolated_db):
    from emuwarez_uploadrr.database import connect, init_db
    conn = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(conn)
    return conn


# Defaults to Castilian audio: the checks now require Castilian one way or the
# other (upload_checks' "spanish" check), so a film without it isn't a normal
# uploadable film any more. The Castilian tests pass their own values.
# Y con el Source de Radarr, que da el tipo de la copia (la comprobación "tipo").
def _movie_with_path(conn, path: str, *, audio_languages: str | None = "spa",
                     subtitle_languages: str | None = None, source: str | None = "WEB-DL") -> int:
    conn.execute(
        """INSERT INTO library_items (item_type, title, local_status, request_status, nomenclature_status,
            path, size_bytes, audio_languages, subtitle_languages, source, updated_at)
           VALUES ('movie', 'X', 'Disponible', 'Sin petición', 'N/A', ?, 100, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (path, audio_languages, subtitle_languages, source),
    )
    conn.commit()
    return conn.execute("SELECT id FROM library_items ORDER BY id DESC LIMIT 1").fetchone()["id"]


def test_stops_at_unrenameable_when_path_missing(db_conn):
    # Accessibility/integrity are no longer checked here — they're checked for
    # real once the upload is queued (upload_queue.py's "integrity" stage).
    # Una ruta que no existe falla ya en "accesible", que va la primera: el
    # asistente calcula la nomenclatura leyendo el fichero, así que sin poder
    # abrirlo el nombre propuesto sale de las columnas de Radarr/Sonarr.
    # Antes esto llegaba a "renameable", que solo mira la carpeta padre.
    # directory doesn't exist either, so it's not writable.
    iid = _movie_with_path(db_conn, "/nope/does/not/exist.mkv")
    r = uc.run_upload_checks(db_conn, iid)
    assert r["ready"] is False
    assert len(r["checks"]) == 1
    assert r["checks"][0]["id"] == "accesible" and r["checks"][0]["status"] == "fail"
    # El nombre del torrent solo lo dan los packs (ver test_season_pack.py).
    assert r["nombre"] is None


def test_output_check_falls_back_to_bundled_uploadrr_path(db_conn, tmp_path, monkeypatch):
    """Regression: the bundled runtime (RUNTIME_EMU, the default) needs no
    configuration at all — DEFAULT_UPLOADRR_PATH (/opt/Uploadrr, or
    UPLOADRR_PATH) must be used when `uploadrr.path` was never set by hand,
    the same way uploadrr_contract.py::get_runtime_settings() already does
    for the actual upload command. Previously this check read the setting
    directly with no fallback, so it failed with "no configurado" even
    though the runtime it validates for doesn't require configuring it."""
    from emuwarez_uploadrr.services import uploadrr_contract as ctr
    monkeypatch.setattr(ctr, "DEFAULT_UPLOADRR_PATH", str(tmp_path))
    import emuwarez_uploadrr.services.image_hosts as ih
    monkeypatch.setattr(ih, "test_image_host", lambda hid, key=None, **k: {"ok": True})
    (tmp_path / "tmp").mkdir()  # Uploadrr-EMU stages output under <path>/tmp, not the root

    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 2048)
    iid = _movie_with_path(db_conn, str(f))
    r = uc.run_upload_checks(db_conn, iid)
    output_check = next(c for c in r["checks"] if c["id"] == "output")
    assert output_check["status"] == "ok"


def test_all_green_when_everything_passes(db_conn, tmp_path, monkeypatch):
    from emuwarez_uploadrr.services.settings import set_setting
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 2048)
    (tmp_path / "tmp").mkdir()  # Uploadrr-EMU stages output under <path>/tmp, not the root
    set_setting(db_conn, "qbit.url", "http://localhost:8080")     # so qBittorrent runs
    set_setting(db_conn, "uploadrr.path", str(tmp_path))           # writable output dir

    # Network checks all OK.
    import emuwarez_uploadrr.services.integration_tests as it
    monkeypatch.setattr(it, "test_qbit_connection", lambda **k: {"ok": True, "message": "ok"})
    monkeypatch.setattr(it, "test_emuwarez_connection", lambda **k: {"ok": True, "message": "ok"})
    import emuwarez_uploadrr.services.image_hosts as ih
    monkeypatch.setattr(ih, "test_image_host", lambda hid, key=None, **k: {"ok": True})

    iid = _movie_with_path(db_conn, str(f))
    r = uc.run_upload_checks(db_conn, iid)
    ids = [c["id"] for c in r["checks"]]
    assert ids == ["accesible", "carpeta_subida", "renameable", "idioma_audio", "spanish", "tipo",
                   "output", "qbittorrent", "imghosts", "emuwarez"]
    # Un archivo de la biblioteca no se renombra: esa no bloquea.
    assert {c["id"]: c["status"] for c in r["checks"]}["renameable"] == "skip"
    assert all(c["status"] == "ok" for c in r["checks"] if c["id"] != "renameable")
    assert r["ready"] is True


def test_una_subida_a_otro_tracker_no_comprueba_emuwarez(db_conn, tmp_path, monkeypatch):
    """Solo N.O.B.S elegido: eMuwarez caído no la para (la comprobación sale omitida y no se llama)."""
    from emuwarez_uploadrr.services.settings import set_setting
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 2048)
    (tmp_path / "tmp").mkdir()
    set_setting(db_conn, "qbit.url", "http://localhost:8080")
    set_setting(db_conn, "uploadrr.path", str(tmp_path))
    import emuwarez_uploadrr.services.integration_tests as it
    monkeypatch.setattr(it, "test_qbit_connection", lambda **k: {"ok": True, "message": "ok"})
    monkeypatch.setattr(it, "test_emuwarez_connection",
                        lambda **k: (_ for _ in ()).throw(AssertionError("no debe llamarse")))
    import emuwarez_uploadrr.services.image_hosts as ih
    monkeypatch.setattr(ih, "test_image_host", lambda hid, key=None, **k: {"ok": True})

    r = uc.run_upload_checks(db_conn, _movie_with_path(db_conn, str(f)), tracker="nobs")
    emu = {c["id"]: c for c in r["checks"]}["emuwarez"]
    assert emu["status"] == "skip" and "N.O.B.S" in emu["message"]
    assert r["ready"] is True


def test_emuwarez_check_does_not_enforce_rate_limit(db_conn, tmp_path, monkeypatch):
    """Regression: two uploads (manual or automático) queued within the same
    minute must not make the second one's "eMuwarez disponible" check fail
    with the 1/minute "Probar conexión" throttle — that throttle exists for
    the user-facing Settings button, not for this automated precondition."""
    from emuwarez_uploadrr.services.settings import set_setting
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 2048)
    (tmp_path / "tmp").mkdir()
    set_setting(db_conn, "qbit.url", "http://localhost:8080")
    set_setting(db_conn, "uploadrr.path", str(tmp_path))

    import emuwarez_uploadrr.services.integration_tests as it
    monkeypatch.setattr(it, "test_qbit_connection", lambda **k: {"ok": True, "message": "ok"})
    calls = []

    def _spy(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "message": "ok"}
    monkeypatch.setattr(it, "test_emuwarez_connection", _spy)
    import emuwarez_uploadrr.services.image_hosts as ih
    monkeypatch.setattr(ih, "test_image_host", lambda hid, key=None, **k: {"ok": True})

    iid = _movie_with_path(db_conn, str(f))
    uc.run_upload_checks(db_conn, iid)
    assert len(calls) == 1
    assert calls[0].get("enforce_rate_limit") is False


def test_skips_unconfigured_qbit(db_conn, tmp_path, monkeypatch):
    """qBittorrent not configured → yellow 'skip', non-blocking: the upload
    is still ready."""
    from emuwarez_uploadrr.services.settings import set_setting
    f = tmp_path / "movie.mkv"; f.write_bytes(b"x" * 2048)
    (tmp_path / "tmp").mkdir()  # Uploadrr-EMU stages output under <path>/tmp, not the root
    set_setting(db_conn, "uploadrr.path", str(tmp_path))
    # qbit.url intentionally not set → qbittorrent skip
    import emuwarez_uploadrr.services.integration_tests as it
    monkeypatch.setattr(it, "test_emuwarez_connection", lambda **k: {"ok": True, "message": "ok"})
    import emuwarez_uploadrr.services.image_hosts as ih
    monkeypatch.setattr(ih, "test_image_host", lambda hid, key=None, **k: {"ok": True})

    iid = _movie_with_path(db_conn, str(f))
    r = uc.run_upload_checks(db_conn, iid)
    by_id = {c["id"]: c for c in r["checks"]}
    assert by_id["qbittorrent"]["status"] == "skip"
    assert r["ready"] is True  # skips don't block


def test_stops_at_qbit_before_emuwarez(db_conn, tmp_path, monkeypatch):
    from emuwarez_uploadrr.services.settings import set_setting
    f = tmp_path / "m.mkv"; f.write_bytes(b"x" * 2048)
    (tmp_path / "tmp").mkdir()  # Uploadrr-EMU stages output under <path>/tmp, not the root
    set_setting(db_conn, "qbit.url", "http://localhost:8080")   # configured → qbit runs
    set_setting(db_conn, "uploadrr.path", str(tmp_path))
    import emuwarez_uploadrr.services.integration_tests as it
    monkeypatch.setattr(it, "test_qbit_connection", lambda **k: {"ok": False, "message": "caído"})
    # eMuwarez must NOT be reached — make it explode if called.
    monkeypatch.setattr(it, "test_emuwarez_connection", lambda **k: (_ for _ in ()).throw(AssertionError("no debe llamarse")))

    iid = _movie_with_path(db_conn, str(f))
    r = uc.run_upload_checks(db_conn, iid)
    ids = [c["id"] for c in r["checks"]]
    assert ids[-1] == "qbittorrent" and r["checks"][-1]["status"] == "fail"
    assert "emuwarez" not in ids
    assert r["ready"] is False


# ---------------------------------------------------------------------------
# Castilian: present in the audio OR in the subtitles
# ---------------------------------------------------------------------------

def _spanish_check(conn, item_id):
    return next(c for c in uc.run_upload_checks(conn, item_id)["checks"] if c["id"] == "spanish")


def test_castilian_audio_needs_no_subtitles(db_conn, tmp_path):
    f = tmp_path / "peli.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), audio_languages="spa", subtitle_languages=None)
    assert _spanish_check(db_conn, iid)["status"] == "ok"


def test_castilian_subtitles_are_enough_without_castilian_audio(db_conn, tmp_path):
    f = tmp_path / "peli.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), audio_languages="eng", subtitle_languages="spa / eng")
    assert _spanish_check(db_conn, iid)["status"] == "ok"


def test_blocked_when_neither_audio_nor_subtitles_are_castilian(db_conn, tmp_path):
    f = tmp_path / "peli.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), audio_languages="eng", subtitle_languages="eng")
    r = uc.run_upload_checks(db_conn, iid)
    assert r["ready"] is False
    check = next(c for c in r["checks"] if c["id"] == "spanish")
    assert check["status"] == "fail"
    # Says what it actually found, so it's obvious what to fix.
    assert "eng" in check["message"]


def test_latin_only_audio_without_castilian_subtitles_is_blocked(db_conn, tmp_path):
    f = tmp_path / "peli.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), audio_languages="es-419", subtitle_languages="es-419")
    assert _spanish_check(db_conn, iid)["status"] == "fail"


def test_unknown_languages_block_instead_of_passing(db_conn, tmp_path):
    """"We never read this file" is not evidence that it meets the rule."""
    f = tmp_path / "peli.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), audio_languages=None, subtitle_languages=None)
    assert _spanish_check(db_conn, iid)["status"] == "fail"


def test_the_rule_is_the_same_for_a_single_episode(db_conn, tmp_path):
    f = tmp_path / "cap.mkv"; f.write_bytes(b"x")
    db_conn.execute(
        """INSERT INTO library_items (item_type, title, local_status, request_status,
            nomenclature_status, path, size_bytes, audio_languages, subtitle_languages, updated_at)
           VALUES ('episode', 'Serie S01E01', 'Disponible', 'Sin petición', 'N/A', ?, 100,
                   'spa', NULL, CURRENT_TIMESTAMP)""",
        (str(f),),
    )
    db_conn.commit()
    iid = db_conn.execute("SELECT id FROM library_items ORDER BY id DESC LIMIT 1").fetchone()["id"]
    assert _spanish_check(db_conn, iid)["status"] == "ok"


# ---------------------------------------------------------------------------
# Reparto en dos grupos: lo del elemento va una vez por elemento, lo del
# entorno una sola vez para toda la tanda
# ---------------------------------------------------------------------------

def test_content_scope_only_runs_the_per_item_checks(db_conn, tmp_path):
    """Con 30 capítulos elegidos, estas sí van 30 veces: dependen del fichero."""
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 2048)
    iid = _movie_with_path(db_conn, str(f))
    r = uc.run_upload_checks(db_conn, iid, scope="content")
    ids = {c["id"] for c in r["checks"]}
    assert ids <= set(uc.CONTENT_CHECK_IDS), ids
    assert not (ids & set(uc.ENVIRONMENT_CHECK_IDS))


def test_environment_scope_never_touches_the_item(db_conn, tmp_path):
    """Y estas van una sola vez: preguntarle a eMuwarez 30 veces si está vivo
    no aporta nada y multiplica por 30 la espera. Con un item_id que no existe
    tienen que salir igual, porque no dependen de él."""
    r = uc.run_upload_checks(db_conn, 999999, scope="environment")
    ids = {c["id"] for c in r["checks"]}
    assert ids <= set(uc.ENVIRONMENT_CHECK_IDS), ids
    assert not (ids & set(uc.CONTENT_CHECK_IDS))
    # Y no se cuela el "Elemento no encontrado" del camino normal.
    assert not any(c.get("message") == "Elemento no encontrado" for c in r["checks"])


def test_the_two_scopes_together_cover_exactly_the_full_run(db_conn, tmp_path, monkeypatch):
    """Partir no puede perder ni duplicar comprobaciones: contenido + entorno
    tiene que dar lo mismo que la pasada completa de siempre."""
    from emuwarez_uploadrr.services import uploadrr_contract as ctr
    monkeypatch.setattr(ctr, "DEFAULT_UPLOADRR_PATH", str(tmp_path))
    import emuwarez_uploadrr.services.image_hosts as ih
    monkeypatch.setattr(ih, "test_image_host", lambda hid, key=None, **k: {"ok": True})
    (tmp_path / "tmp").mkdir()
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 2048)
    iid = _movie_with_path(db_conn, str(f))

    full = [c["id"] for c in uc.run_upload_checks(db_conn, iid)["checks"]]
    content = [c["id"] for c in uc.run_upload_checks(db_conn, iid, scope="content")["checks"]]
    env = [c["id"] for c in uc.run_upload_checks(db_conn, iid, scope="environment")["checks"]]
    assert content + env == full


def test_a_failed_content_check_is_not_ready(db_conn, tmp_path):
    """Lo que hace que al pulsar Subir se avise de qué elemento no puede ir."""
    iid = _movie_with_path(db_conn, str(tmp_path / "no-existe" / "movie.mkv"))
    r = uc.run_upload_checks(db_conn, iid, scope="content")
    assert r["ready"] is False
    assert any(c["status"] == "fail" for c in r["checks"])


def test_qui_alone_counts_as_configured_qbit(db_conn, tmp_path, monkeypatch):
    """Con solo el proxy de qui, qBittorrent se comprueba en vez de omitirse."""
    from emuwarez_uploadrr.services.settings import set_setting
    f = tmp_path / "movie.mkv"; f.write_bytes(b"x" * 2048)
    (tmp_path / "tmp").mkdir()
    set_setting(db_conn, "uploadrr.path", str(tmp_path))
    set_setting(db_conn, "qbit.qui_proxy_url", "http://qui:7476/proxy/clave")
    import emuwarez_uploadrr.services.integration_tests as it
    monkeypatch.setattr(it, "test_qbit_connection", lambda **k: {"ok": True, "message": "ok"})
    monkeypatch.setattr(it, "test_emuwarez_connection", lambda **k: {"ok": True, "message": "ok"})
    import emuwarez_uploadrr.services.image_hosts as ih
    monkeypatch.setattr(ih, "test_image_host", lambda hid, key=None, **k: {"ok": True})

    r = uc.run_upload_checks(db_conn, _movie_with_path(db_conn, str(f)))
    assert {c["id"]: c for c in r["checks"]}["qbittorrent"]["status"] != "skip"


# ---------------------------------------------------------------------------
# El tipo de la copia: sin un `-t` que eMuwarez conozca no se sube (iría con el tipo 0)
# ---------------------------------------------------------------------------

def _tipo(r):
    return next(c for c in r["checks"] if c["id"] == "tipo")


def _propuesta(conn, item_id, source):
    conn.execute("INSERT INTO nomenclature_proposals (library_item_id, status, source) VALUES (?, 'draft', ?)",
                 (item_id, source))
    conn.commit()


@pytest.mark.parametrize("source, cual", [(None, "sin Source"), ("DVD", "Source «DVD»")])
def test_sin_tipo_conocido_bloquea_y_dice_donde_elegirlo(db_conn, tmp_path, source, cual):
    f = tmp_path / "movie.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), source=source)
    r = uc.run_upload_checks(db_conn, iid)
    assert r["ready"] is False and r["checks"][-1]["id"] == "tipo"  # se para aquí: ni qBittorrent ni eMuwarez
    assert _tipo(r) == {"id": "tipo", "label": "No se sabe el tipo de la copia", "status": "fail",
                        "message": f"No se sabe el tipo de esta copia ({cual}): elígelo en el resumen"}
    contenido = uc.run_upload_checks(db_conn, iid, scope="content")
    assert _tipo(contenido)["message"].endswith(": elige su Source en el asistente de subida")


def test_el_tipo_sale_del_source_de_la_propuesta_guardada(db_conn, tmp_path):
    f = tmp_path / "movie.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), source=None)
    _propuesta(db_conn, iid, "BDRemux")
    assert _tipo(uc.run_upload_checks(db_conn, iid, scope="content"))["status"] == "ok"


def test_sin_propuesta_vale_el_source_que_se_deducira_del_nombre(db_conn, tmp_path):
    f = tmp_path / "Peli.2020.1080p.WEB-DL.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), source=None)
    assert _tipo(uc.run_upload_checks(db_conn, iid, scope="content"))["status"] == "ok"


def test_el_source_elegido_en_el_asistente_desbloquea(db_conn, tmp_path):
    f = tmp_path / "movie.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), source=None)
    r = uc.run_upload_checks(db_conn, iid, scope="content", ajustes={"source": "BluRay"})
    assert (_tipo(r)["status"], _tipo(r)["message"], r["ready"]) == ("ok", "BluRay", True)


@pytest.mark.parametrize("tracker, nombre", [("nobs", "N.O.B.S"), ("mnv", "Milnueve")])
def test_un_tipo_que_el_tracker_no_tiene_bloquea(db_conn, tmp_path, tracker, nombre):
    f = tmp_path / "movie.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), source="SD")
    assert _tipo(uc.run_upload_checks(db_conn, iid, scope="content"))["status"] == "ok"  # eMuwarez sí lo tiene
    r = uc.run_upload_checks(db_conn, iid, scope="content", tracker=tracker)
    assert r["ready"] is False
    assert _tipo(r) == {"id": "tipo", "label": f"{nombre} no tiene este tipo", "status": "fail",
                        "message": f"{nombre} no tiene el tipo «SD»: esta copia no se puede subir ahí"}
    assert _tipo(uc.run_upload_checks(db_conn, iid, scope="content", tracker=tracker,
                                      ajustes={"source": "WEB-DL"}))["status"] == "ok"


@pytest.mark.parametrize("source", ["", None, "WEB-DL", "BluRay", "DVD", "Full BluRay"])
def test_la_comprobacion_y_el_comando_dicen_lo_mismo(db_conn, tmp_path, source):
    """Pasa si y solo si el comando lleva `-t`: los dos leen el formato de la misma propuesta."""
    from emuwarez_uploadrr.services.nomenclature import get_latest_nomenclature_proposal
    from emuwarez_uploadrr.services.upload_preflight import build_uploadrr_command_preview
    f = tmp_path / "movie.mkv"; f.write_bytes(b"x")
    iid = _movie_with_path(db_conn, str(f), source=None)
    _propuesta(db_conn, iid, source)
    item = dict(db_conn.execute("SELECT * FROM library_items WHERE id = ?", (iid,)).fetchone())
    comando = build_uploadrr_command_preview(item, get_latest_nomenclature_proposal(iid, conn=db_conn), conn=db_conn)
    pasa = _tipo(uc.run_upload_checks(db_conn, iid, scope="content"))["status"] == "ok"
    assert pasa == ("-t" in comando["command_args"])


@pytest.mark.parametrize("plan_source, ajustes, estado", [
    (None, None, "fail"), ("WEB-DL", None, "ok"), ("BluRay", {"source": "BluRay"}, "ok")])
def test_un_pack_toma_el_tipo_de_su_plan(db_conn, tmp_path, monkeypatch, plan_source, ajustes, estado):
    from emuwarez_uploadrr.services import season_pack
    cap = tmp_path / "S01E01.mkv"; cap.write_bytes(b"x")
    vistos = []

    def plan(item_id, **kw):
        vistos.append(kw.get("source_override"))
        return {"status": "valid", "errors": [], "episodes": [{"source_path": str(cap), "episode_number": 1}],
                "pack_folder": str(tmp_path), "source": plan_source}

    monkeypatch.setattr(season_pack, "build_season_pack_plan", plan)
    db_conn.execute("""INSERT INTO library_items (item_type, title, local_status, request_status, nomenclature_status,
                       season_number, updated_at)
                       VALUES ('season', 'Serie - Temporada 1', 'Disponible', '', 'N/A', 1, CURRENT_TIMESTAMP)""")
    db_conn.commit()
    iid = db_conn.execute("SELECT id FROM library_items ORDER BY id DESC LIMIT 1").fetchone()["id"]
    r = uc.run_upload_checks(db_conn, iid, scope="content", **({"ajustes": ajustes} if ajustes else {}))
    assert _tipo(r)["status"] == estado
    assert vistos == [(ajustes or {}).get("source")]  # el Source elegido llega al plan, del que sale el `-t`
