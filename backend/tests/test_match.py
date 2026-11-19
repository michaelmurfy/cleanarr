from app.match import CatalogIndex, bare, normalize, parse_year, title_keys


def test_parse_year_handles_numbers_strings_and_titles():
    from app.match import year_from_title

    assert parse_year(1999) == 1999
    assert parse_year("2014") == 2014
    assert parse_year("Interstellar (2014)") == 2014
    assert parse_year("") is None
    assert parse_year(0) is None
    assert parse_year(1700) is None
    assert parse_year("not a year") is None
    # Digit-only titles are movies, not years.
    assert year_from_title("1917") is None
    assert year_from_title("1917 (2019)") == 2019


def test_normalize_strips_year_punctuation_and_trailing_article():
    assert normalize("The Matrix (1999)") == "the matrix"
    assert normalize("Fast & Furious") == "fast and furious"
    assert normalize("Thing, The") == "thing"
    # Non-Latin titles must not collapse to an empty key.
    assert normalize("七人の侍") == "七人の侍"
    assert normalize("Amélie") == "amélie"


def test_bare_drops_leading_article():
    assert bare("The Matrix") == "matrix"
    assert bare("A Quiet Place") == "quiet place"
    assert bare("Up") == "up"


def test_title_keys_dedupes():
    assert title_keys("The Matrix") == ["the matrix", "matrix"]
    assert title_keys("Dune") == ["dune"]


def _index():
    index = CatalogIndex()
    index.add(("movie", 603, 0), {"title": "The Matrix", "year": 1999, "imdb_id": "tt0133093"})
    index.add(("tv", 0, 81189), {"title": "Breaking Bad", "year": 2008})
    return index


def test_resolve_prefers_ids():
    index = _index()
    assert index.resolve("movie", tmdb_id=603) == ("movie", 603, 0)
    assert index.resolve("tv", tvdb_id=81189) == ("tv", 0, 81189)
    assert index.resolve("movie", imdb_id="TT0133093") == ("movie", 603, 0)
    assert index.resolve_hit("movie", tmdb_id=603).via == "tmdb"


def test_resolve_falls_back_to_title_and_year():
    index = _index()
    # ±1 year still counts as the same release window.
    assert index.resolve("movie", titles=["Matrix"], year=2000) == ("movie", 603, 0)
    assert index.resolve("movie", titles=["The Matrix (1999)"]) == ("movie", 603, 0)
    # A far-off year must not fall back to a yearless unique hit.
    assert index.resolve("movie", titles=["The Matrix"], year=1980) is None
    # No year at all may still hit the unique primary title.
    assert index.resolve("movie", titles=["The Matrix"]) == ("movie", 603, 0)


def test_resolve_wrong_media_type_or_unknown_title_misses():
    index = _index()
    assert index.resolve("tv", titles=["The Matrix"]) is None
    assert index.resolve("movie", titles=["Nothing Here"]) is None


def test_resolve_skips_ambiguous_titles():
    index = CatalogIndex()
    index.add(("movie", 1, 0), {"title": "Dune", "year": 1984})
    index.add(("movie", 2, 0), {"title": "Dune", "year": 2021})
    assert index.resolve("movie", titles=["Dune"]) is None
    assert index.resolve("movie", titles=["Dune"], year=2021) == ("movie", 2, 0)


def test_extra_titles_are_indexed():
    index = CatalogIndex()
    index.add(("movie", 7, 0), {"title": "Amélie", "year": 2001}, extra_titles=["Le Fabuleux Destin d'Amélie Poulain"])
    assert index.resolve("movie", titles=["Le Fabuleux Destin d Amelie Poulain"]) is None
    # Alternate titles need an exact year — no yearless fallback.
    assert index.resolve("movie", titles=["Le Fabuleux Destin d'Amélie Poulain"]) is None
    assert index.resolve("movie", titles=["Le Fabuleux Destin d'Amélie Poulain"], year=2001) == ("movie", 7, 0)
    assert index.resolve_hit("movie", titles=["Le Fabuleux Destin d'Amélie Poulain"], year=2001).via == "title_alt"


def test_pinocchio_does_not_steal_guillermo_via_short_alt():
    index = CatalogIndex()
    index.add(
        ("movie", 555604, 0),
        {"title": "Guillermo del Toro's Pinocchio", "year": 2022},
        extra_titles=["Pinocchio"],
    )
    # Different Pinocchio with a different year must not attach.
    assert index.resolve("movie", titles=["Pinocchio"], year=1940) is None
    # Yearless query must not use the short alternate form either.
    assert index.resolve("movie", titles=["Pinocchio"]) is None
    # Same year + alternate title is still a valid hit.
    assert index.resolve("movie", titles=["Pinocchio"], year=2022) == ("movie", 555604, 0)


def test_battlestar_years_do_not_cross_match():
    index = CatalogIndex()
    index.add(("tv", 1978, 0), {"title": "Battlestar Galactica", "year": 1978})
    index.add(("tv", 1972, 0), {"title": "Battlestar Galactica", "year": 2004})
    assert index.resolve("tv", titles=["Battlestar Galactica"]) is None
    assert index.resolve("tv", titles=["Battlestar Galactica"], year=1978) == ("tv", 1978, 0)
    assert index.resolve("tv", titles=["Battlestar Galactica"], year=2004) == ("tv", 1972, 0)
    # Far year does not collapse onto either show.
    assert index.resolve("tv", titles=["Battlestar Galactica"], year=2010) is None


def test_numeric_title_matches_without_being_treated_as_a_year():
    index = CatalogIndex()
    index.add(("movie", 522938, 0), {"title": "1917", "year": 2019})
    assert index.resolve("movie", titles=["1917"]) == ("movie", 522938, 0)
    assert index.resolve("movie", titles=["1917"], year=2019) == ("movie", 522938, 0)


def test_cjk_title_normalizes_and_matches():
    index = CatalogIndex()
    index.add(("movie", 346, 0), {"title": "七人の侍", "year": 1954})
    assert index.resolve("movie", titles=["七人の侍"], year=1954) == ("movie", 346, 0)
