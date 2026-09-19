from app.actions import is_protected, protect_reason


def rule(**overrides):
    row = {"match_type": "title", "media_type": "any", "tmdb_id": 0, "pattern": "", "note": ""}
    row.update(overrides)
    return row


def test_title_rule_matches_substring_case_insensitively():
    rows = [rule(pattern="matrix")]
    assert is_protected("The Matrix Reloaded", "movie", 0, rows) is not None
    assert is_protected("Dune", "movie", 0, rows) is None


def test_media_type_scopes_the_rule():
    rows = [rule(pattern="dune", media_type="movie")]
    assert is_protected("Dune", "movie", 0, rows) is not None
    assert is_protected("Dune", "tv", 0, rows) is None


def test_id_rule_matches_tmdb_id_only():
    rows = [rule(match_type="id", tmdb_id=603, pattern="603")]
    assert is_protected("Anything", "movie", 603, rows) is not None
    assert is_protected("The Matrix", "movie", 604, rows) is None
    assert is_protected("The Matrix", "movie", 0, rows) is None


def test_empty_pattern_never_matches():
    assert is_protected("The Matrix", "movie", 603, [rule(pattern="")]) is None


def test_protect_reason_labels():
    assert protect_reason(rule(pattern="matrix")) == "contains “matrix”"
    assert protect_reason(rule(pattern="matrix", note="favourite")) == "contains “matrix” · favourite"
    assert protect_reason(rule(match_type="id", tmdb_id=603, pattern="603")) == "TMDB 603"
    assert protect_reason(rule(match_type="id", tmdb_id=603, pattern="The Matrix")) == "TMDB 603 · The Matrix"


def test_protect_reason_skips_note_that_repeats_the_pattern():
    assert protect_reason(rule(pattern="matrix", note="Matrix")) == "contains “matrix”"
