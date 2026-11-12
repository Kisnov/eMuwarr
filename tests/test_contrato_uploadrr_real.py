"""El argv que arma la app, contra el parser de verdad de Uploadrr-EMU (`src/args.py` de su `main`).

Los dos repos se prueban por separado: si la app pasa una opción que Uploadrr-EMU no conoce (o un valor fuera de
sus `choices`), upload.py aborta antes de empezar y cada subida falla, sin que ningún test de los dos repos lo vea.
Aquí se carga su parser y se le pasan los comandos que de verdad sale de build_uploadrr_emu_command.

De dónde sale Uploadrr-EMU: `UPLOADRR_EMU_DIR`, o `/opt/Uploadrr` (la imagen), o un `git clone --depth 1` de su
`main` (lo que clona el Dockerfile). Sin ninguno, en CI suspende y en local se salta.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from emuwarez_uploadrr.services.uploadrr_contract import (
    _UPLOADRR_RES_CHOICES,
    _UPLOADRR_TYPES,
    build_uploadrr_emu_command,
)

REPO_UPLOADRR = "https://github.com/Kisnov/Uploadrr-EMU.git"


def _carpeta_uploadrr(tmp_path_factory) -> Path | None:
    for candidata in (os.environ.get("UPLOADRR_EMU_DIR"), "/opt/Uploadrr"):
        if candidata and (Path(candidata) / "src" / "args.py").is_file():
            return Path(candidata)
    destino = tmp_path_factory.mktemp("uploadrr") / "Uploadrr-EMU"
    try:
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", REPO_UPLOADRR, str(destino)],
            check=True, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return destino


@pytest.fixture(scope="module")
def args_uploadrr(tmp_path_factory):
    """El módulo `src/args.py` real de Uploadrr-EMU."""
    carpeta = _carpeta_uploadrr(tmp_path_factory)
    if carpeta is None:
        motivo = "No se pudo conseguir Uploadrr-EMU (ni UPLOADRR_EMU_DIR, ni /opt/Uploadrr, ni git clone)."
        if os.environ.get("CI"):
            pytest.fail(motivo)
        pytest.skip(motivo)
    # src/args.py solo trae de su paquete la consola de Rich, que aquí no está instalada: el parser no la usa.
    sustitutos = {
        "src": types.ModuleType("src"),
        "src.utils": types.ModuleType("src.utils"),
        "src.utils.console": types.ModuleType("src.utils.console"),
    }
    sustitutos["src.utils.console"].console = mock.MagicMock()
    with mock.patch.dict(sys.modules, sustitutos):
        spec = importlib.util.spec_from_file_location("_uploadrr_args_real", carpeta / "src" / "args.py")
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def parser_uploadrr(args_uploadrr) -> argparse.ArgumentParser:
    return args_uploadrr.build_parser({})


def _parsear(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    try:
        return parser.parse_args(argv)
    except SystemExit:
        pytest.fail(f"Uploadrr-EMU rechaza este argv (mira el stderr capturado): {argv}")


def _cmd(**cambios) -> list[str]:
    datos = dict(
        python_path="/usr/bin/python3", uploadrr_script_path="/opt/Uploadrr/upload.py",
        prepared_path="/data/subidas/Peli.2024.1080p.WEB-DL.mkv", torrent_name="Peli.2024.1080p.WEB-DL",
        tmdb_id="603", category="movie", resolution="1080p", release_format="WEB-DL",
    )
    datos.update(cambios)
    return build_uploadrr_emu_command(**datos)[2:]  # sin el intérprete ni upload.py


@pytest.mark.parametrize(
    "cambios",
    [
        {},
        # Todas las banderas a la vez: un pack de temporada de baja calidad, anónimo, release propio y reanudado.
        dict(category="tv", resolution="480p", release_format="SD", season=1, anonimo=True, personal_release=True,
             reanudar=True),
        dict(category="tv", resolution="720p", season=0, episode=5, release_format="WEBRip"),
        dict(resolution="", tmdb_id=None, release_format=None),
    ],
    ids=["pelicula", "pack-todas-las-banderas", "capitulo", "minimo"],
)
def test_uploadrr_acepta_el_comando_de_la_app(parser_uploadrr, cambios):
    argv = _cmd(**cambios)
    ns = _parsear(parser_uploadrr, argv)
    assert ns.dummy_path == [argv[0]]


def test_las_banderas_llegan_con_el_nombre_que_lee_uploadrr(parser_uploadrr):
    ns = _parsear(parser_uploadrr, _cmd(category="tv", resolution="480p", season=1, anonimo=True,
                                         personal_release=True, reanudar=True))
    assert (ns.anon, ns.personalrelease, ns.resume, ns.aviso_baja_calidad) == (True, True, True, True)
    ns = _parsear(parser_uploadrr, _cmd())
    assert (ns.anon, ns.personalrelease, ns.resume, ns.aviso_baja_calidad) == (False, False, False, False)


def test_reenviar_a_qbittorrent_llega_como_solo_cliente_con_resume(parser_uploadrr):
    ns = _parsear(parser_uploadrr, _cmd(solo_cliente="4242"))
    assert (ns.solo_cliente, ns.resume) == (4242, True)
    assert _parsear(parser_uploadrr, _cmd()).solo_cliente is None


def _choices(parser: argparse.ArgumentParser, opcion: str) -> set[str]:
    (accion,) = [a for a in parser._actions if opcion in a.option_strings]
    return set(accion.choices)


def test_cada_resolucion_y_tipo_que_puede_mandar_la_app_existe_en_uploadrr(parser_uploadrr):
    assert _UPLOADRR_RES_CHOICES <= _choices(parser_uploadrr, "-res")
    assert set(_UPLOADRR_TYPES.values()) <= _choices(parser_uploadrr, "-t")
    assert {"movie", "tv"} <= _choices(parser_uploadrr, "-c")


def _meta_como_upload_py(args, parser, argv: list[str]) -> dict:
    """Lo que upload.py hace con el argv: agrupa por ruta, lo parsea y lo pasa por normalize_args y apply_special_logic."""
    items, global_argv = args.group_args_by_path(argv)
    assert items == [([], str(Path(argv[0]).resolve()))], "la ruta tiene que ser el primer elemento y la única"
    ns = parser.parse_args(global_argv)
    args.enforce_global_only(ns, is_global=True, parser=parser)
    return args.apply_special_logic(args.normalize_args(ns))


# Cada campo con su valor Y su tipo: `manual_name` como lista rompería la comparación con el nombre del tracker.
_BASE = {"manual_name": "Peli.2024.1080p.WEB-DL", "trackers": ["EMU"], "unattended": True, "tmdb_manual": "603",
         "manual_type": "webdl", "torrent_creation": "mkbrr"}


@pytest.mark.parametrize(
    "cambios, esperado, ausentes",
    [
        ({}, {**_BASE, "category": "MOVIE", "resolution": "1080p"}, ("anon", "resume", "aviso_baja_calidad", "solo_cliente")),
        (dict(category="tv", season=1, episode=5),
         {**_BASE, "category": "TV", "manual_season": "S01", "manual_episode": "E05"}, ("anon", "resume")),
        (dict(category="tv", season=2, resolution="480p", anonimo=True, reanudar=True),
         {**_BASE, "category": "TV", "resolution": "480p", "manual_season": "S02", "anon": True, "resume": True,
          "aviso_baja_calidad": True, "full_dir": True}, ("manual_episode", "solo_cliente")),
        (dict(solo_cliente="4242"), {**_BASE, "category": "MOVIE", "resume": True, "solo_cliente": 4242}, ("anon",)),
    ],
    ids=["pelicula", "capitulo", "pack-480p-anonimo-resume", "solo-cliente"],
)
def test_el_comando_de_la_app_llega_a_la_meta_con_el_tipo_y_valor_que_espera_uploadrr(
        args_uploadrr, parser_uploadrr, tmp_path, cambios, esperado, ausentes):
    archivo = tmp_path / "Peli.2024.1080p.WEB-DL.mkv"
    archivo.write_bytes(b"x")

    meta = _meta_como_upload_py(args_uploadrr, parser_uploadrr, _cmd(prepared_path=str(archivo), **cambios))

    for clave, valor in esperado.items():
        assert meta[clave] == valor and type(meta[clave]) is type(valor), (clave, meta.get(clave))
    for clave in ausentes:
        assert not meta.get(clave), (clave, meta.get(clave))
