"""El Tag de la subida sale por defecto del grupo de release del archivo (la
columna Grupo de Biblioteca); sin un grupo claro, del tag de Ajustes."""
from __future__ import annotations

import pytest

from emuwarez_uploadrr.database import connect, init_db
from emuwarez_uploadrr.services.nomenclature import tag_por_defecto
from emuwarez_uploadrr.services.season_pack import build_season_pack_plan

from tests.test_season_pack import _seed_series_season


def _open(isolated_db):
    conn = connect(isolated_db / "emuwarez_uploadrr.db")
    init_db(conn)
    return conn


def test_un_solo_grupo_es_el_tag(isolated_db):
    conn = _open(isolated_db)
    assert tag_por_defecto(["Loco87"], conn=conn) == "Loco87"
    assert tag_por_defecto([" Loco87 ", "Loco87"], conn=conn) == "Loco87"


def test_sin_grupo_o_mezclados_va_el_de_ajustes(isolated_db):
    conn = _open(isolated_db)
    assert tag_por_defecto([None], conn=conn) == "EMUWAREZ"
    assert tag_por_defecto(["Loco87", None], conn=conn) == "EMUWAREZ"
    assert tag_por_defecto(["Loco87", "Okeda"], conn=conn) == "EMUWAREZ"


def _pack(conn, tmp_path, grupos):
    _series_id, season_id = _seed_series_season(conn)
    season_dir = tmp_path / "Season 1"
    season_dir.mkdir()
    for i, grupo in enumerate(grupos, 1):
        src = season_dir / f"S01E0{i}.mkv"
        src.write_bytes(b"video")
        conn.execute(
            """INSERT INTO library_items
               (item_type, title, year, local_status, request_status, nomenclature_status,
                parent_id, season_number, episode_number, path, quality, subtitle_languages,
                release_group, updated_at)
               VALUES ('episode', ?, 1999, 'Disponible', 'Sin petición', 'No adaptada', ?, 1, ?, ?,
                       'WEBDL-1080p', 'spa', ?, CURRENT_TIMESTAMP)""",
            (f"One Piece S01E0{i}", season_id, i, str(src), grupo),
        )
    conn.commit()
    return season_id


def test_un_pack_de_un_solo_grupo_lleva_ese_tag(isolated_db, tmp_path):
    conn = _open(isolated_db)
    plan = build_season_pack_plan(_pack(conn, tmp_path, ["Loco87", "Loco87"]), conn=conn)
    assert plan["status"] == "valid", plan["errors"]
    assert plan["release_tag"] == "Loco87"
    assert plan["season_pack_title"].endswith("-Loco87")
    assert all(e["proposed_filename"].endswith("-Loco87") for e in plan["episodes"])


def test_un_pack_con_grupos_mezclados_lleva_el_de_mas_capitulos_y_cada_capitulo_el_suyo(isolated_db, tmp_path):
    conn = _open(isolated_db)
    plan = build_season_pack_plan(_pack(conn, tmp_path, ["Loco87", "Okeda", "Okeda"]), conn=conn)
    assert plan["status"] == "valid", plan["errors"]
    assert plan["release_tag"] == "Okeda" and plan["season_pack_title"].endswith("-Okeda")
    assert [e["proposed_filename"].rsplit("-", 1)[1] for e in plan["episodes"]] == ["Loco87", "Okeda", "Okeda"]


def test_si_los_grupos_empatan_el_pack_lleva_el_del_primer_capitulo(isolated_db, tmp_path):
    conn = _open(isolated_db)
    plan = build_season_pack_plan(_pack(conn, tmp_path, ["Loco87", "Okeda"]), conn=conn)
    assert plan["release_tag"] == "Loco87"


def test_un_capitulo_sin_grupo_va_sin_tag_y_el_pack_tambien_si_son_mayoria(isolated_db, tmp_path):
    """Sin grupo no se pone el Tag de Ajustes: el release no es de quien lo sube."""
    conn = _open(isolated_db)
    plan = build_season_pack_plan(_pack(conn, tmp_path, ["Loco87", None, None]), conn=conn)
    assert plan["status"] == "valid", plan["errors"]
    assert plan["release_tag"] == "" and "EMUWAREZ" not in plan["season_pack_title"]
    con_grupo, *sin_grupo = (e["proposed_filename"] for e in plan["episodes"])
    assert con_grupo.endswith("-Loco87")
    assert all("Loco87" not in n and "EMUWAREZ" not in n for n in sin_grupo)


def test_sin_tag_elegido_a_mano_quita_el_del_pack_y_no_el_de_los_capitulos(isolated_db, tmp_path):
    conn = _open(isolated_db)
    plan = build_season_pack_plan(_pack(conn, tmp_path, ["Loco87", "Loco87"]), conn=conn, tag_override="")
    assert plan["release_tag"] == "" and "Loco87" not in plan["season_pack_title"]
    assert all(e["proposed_filename"].endswith("-Loco87") for e in plan["episodes"])


def test_ver_un_pack_mezclado_no_reescribe_las_propuestas_de_los_capitulos(isolated_db, tmp_path):
    """Rehacer la propuesta de un capítulo caduca la guardada, con lo editado a mano."""
    conn = _open(isolated_db)
    season_id = _pack(conn, tmp_path, ["Loco87", "Okeda"])
    build_season_pack_plan(season_id, conn=conn)
    antes = conn.execute("SELECT COUNT(*) FROM nomenclature_proposals").fetchone()[0]
    build_season_pack_plan(season_id, conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM nomenclature_proposals").fetchone()[0] == antes


def test_un_capitulo_sin_grupo_pierde_el_tag_aunque_el_de_ajustes_lleve_espacios_acentos_o_puntos(
        isolated_db, tmp_path):
    """El nombre de archivo lleva el Tag sin acentos y con puntos."""
    from emuwarez_uploadrr.services.settings import set_setting_canonical
    conn = _open(isolated_db)
    set_setting_canonical(conn, "default_release_tag", "adrià & hakyra")
    conn.commit()
    plan = build_season_pack_plan(_pack(conn, tmp_path, [None, None]), conn=conn)
    assert all(e["proposed_filename"].endswith("SUBS") for e in plan["episodes"]), plan["episodes"]


def test_el_tag_elegido_gana_al_grupo(isolated_db, tmp_path):
    conn = _open(isolated_db)
    season_id = _pack(conn, tmp_path, ["Loco87", "Loco87"])
    plan = build_season_pack_plan(season_id, conn=conn, tag_override="MYTAG")
    assert plan["release_tag"] == "MYTAG" and plan["season_pack_title"].endswith("-MYTAG")
    assert all(e["proposed_filename"].endswith("-Loco87") for e in plan["episodes"])


@pytest.mark.usefixtures("ficheros_de_mentira")
def test_el_asistente_propone_el_grupo_de_la_pelicula(client, auth_headers, tmp_path):
    from unittest.mock import patch
    from tests.test_nomenclature_fields_api import _MI_OK, _seed_item
    f = tmp_path / "dune.mkv"; f.write_bytes(b"x" * 2048)
    item_id = _seed_item(client, auth_headers, title="Dune", path=str(f))
    from emuwarez_uploadrr.web.runtime import _db_path
    with connect(_db_path()) as c:
        c.execute("UPDATE library_items SET release_group = 'FLUX' WHERE id = ?", (item_id,))
        c.commit()
    with patch("emuwarez_uploadrr.services.source_assistant._get_tmdb_available_providers_raw", return_value=[]), \
         patch("emuwarez_uploadrr.services.media_info_service.build_mediainfo_report", return_value=_MI_OK):
        resp = client.get(f"/api/library/{item_id}/nomenclature/fields", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["fields"]["tag"] == "FLUX"
