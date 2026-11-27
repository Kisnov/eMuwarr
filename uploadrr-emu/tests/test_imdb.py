"""IMDb: la ficha llega, pero ya no se piden los títulos alternativos (su GraphQL respondía 403 en cada subida)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import imdbinfo
import pytest

from src.databases import imdb
from src.databases.imdb import IMDbHandler


def _ficha(imdb_id):
    return SimpleNamespace(
        title="Amanece, que no es poco",
        title_localized="Amanece, que no es poco",
        year=1989,
        imdbId="tt0088109",
        duration=107,
        cover_url="https://m.media-amazon.com/images/poster.jpg",
        plot="Un pueblo muy raro.",
        genres=["Comedy"],
        storyline_keywords=["pueblo"],
        sound_mixes=["Mono"],
        countries=["Spain"],
        languages=["Spanish"],
        directors=[SimpleNamespace(imdb_id="0193451")],
        kind="movie",
    )


@pytest.fixture
def sin_akas(monkeypatch):
    pedidas = []

    def get_akas(imdb_id):
        pedidas.append(imdb_id)
        raise AssertionError("no se piden AKAs a IMDb")

    monkeypatch.setattr(imdbinfo, "get_akas", get_akas)
    monkeypatch.setattr(imdb, "get_movie", _ficha)
    return pedidas


def test_la_ficha_llega_sin_pedir_akas(sin_akas):
    info = asyncio.run(IMDbHandler().get_imdb_info("0088109", {"title": "x", "year": 1989}))

    assert sin_akas == []
    assert not hasattr(imdb, "get_akas")
    assert (info["title"], info["year"], info["plot"], info["genres"], info["directors"]) == (
        "Amanece, que no es poco", 1989, "Un pueblo muy raro.", "Comedy", ["nm0193451"])
    assert (info["aka"], info["akas"], info["original_language"]) == ("", [], "Spanish")


def test_find_aka_da_el_idioma_sin_pedir_akas(sin_akas):
    assert asyncio.run(IMDbHandler().find_aka("0088109")) == ("", "Spanish")
    assert sin_akas == []
