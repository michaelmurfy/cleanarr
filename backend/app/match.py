from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

ARTICLES = ("the", "a", "an")
YEAR_RE = re.compile(r"\((\d{4})\)")


def parse_year(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        year = int(value)
        return year if 1800 < year < 2100 else None
    text = str(value).strip()
    if text.isdigit():
        year = int(text)
        return year if 1800 < year < 2100 else None
    found = YEAR_RE.search(text)
    if found:
        return int(found.group(1))
    return None


def normalize(title: str) -> str:
    value = (title or "").lower().replace("&", " and ")
    value = YEAR_RE.sub(" ", value)
    value = re.sub(r",\s+(the|a|an)\s*$", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def bare(title: str) -> str:
    value = normalize(title)
    for article in ARTICLES:
        prefix = f"{article} "
        if value.startswith(prefix) and len(value) > len(prefix) + 1:
            return value[len(prefix) :]
    return value


def title_keys(title: str) -> list[str]:
    forms = []
    for form in (normalize(title), bare(title)):
        if form and form not in forms:
            forms.append(form)
    return forms


@dataclass(frozen=True)
class MatchHit:
    """A catalog key plus how it was found (for diagnostics and ignore rules)."""

    key: tuple[str, int, int]
    via: str  # tmdb | tvdb | imdb | title | title_alt


class CatalogIndex:
    def __init__(self) -> None:
        self.by_tmdb: dict[tuple[str, int], tuple[str, int, int]] = {}
        self.by_tvdb: dict[tuple[str, int], tuple[str, int, int]] = {}
        self.by_imdb: dict[str, tuple[str, int, int]] = {}
        # Primary titles may fall back to year=None when the query has no year.
        self.by_title: dict[tuple[str, str, int | None], list[tuple[str, int, int]]] = defaultdict(list)
        # Alternate / original / sort titles only match with an exact year.
        self.by_alt_title: dict[tuple[str, str, int], list[tuple[str, int, int]]] = defaultdict(list)

    def add(self, key: tuple[str, int, int], item: dict[str, Any], extra_titles: Iterable[str] = ()) -> None:
        media_type, tmdb_id, tvdb_id = key
        if tmdb_id:
            self.by_tmdb[(media_type, tmdb_id)] = key
        if tvdb_id:
            self.by_tvdb[(media_type, tvdb_id)] = key
        imdb = str(item.get("imdb_id") or "").lower()
        if imdb:
            self.by_imdb[imdb] = key
        year = parse_year(item.get("year"))
        primary = item.get("title") or ""
        for form in title_keys(primary):
            self._index_title(form, media_type, year, key)
            self._index_title(form, media_type, None, key)
        if year is not None:
            for title in extra_titles:
                if not title or title == primary:
                    continue
                for form in title_keys(title):
                    # Skip forms already covered by the primary title; those stay
                    # on the looser primary index.
                    if form in title_keys(primary):
                        continue
                    self._index_alt(form, media_type, year, key)

    def _index_title(self, form: str, media_type: str, year: int | None, key: tuple[str, int, int]) -> None:
        slot = self.by_title[(form, media_type, year)]
        if key not in slot:
            slot.append(key)

    def _index_alt(self, form: str, media_type: str, year: int, key: tuple[str, int, int]) -> None:
        slot = self.by_alt_title[(form, media_type, year)]
        if key not in slot:
            slot.append(key)

    def resolve(
        self,
        media_type: str,
        tmdb_id: int = 0,
        tvdb_id: int = 0,
        imdb_id: str = "",
        titles: Iterable[str] = (),
        year: Any = None,
    ) -> tuple[str, int, int] | None:
        hit = self.resolve_hit(media_type, tmdb_id, tvdb_id, imdb_id, titles, year)
        return hit.key if hit else None

    def resolve_hit(
        self,
        media_type: str,
        tmdb_id: int = 0,
        tvdb_id: int = 0,
        imdb_id: str = "",
        titles: Iterable[str] = (),
        year: Any = None,
    ) -> MatchHit | None:
        if tmdb_id:
            match = self.by_tmdb.get((media_type, tmdb_id))
            if match:
                return MatchHit(match, "tmdb")
        if tvdb_id:
            match = self.by_tvdb.get((media_type, tvdb_id))
            if match:
                return MatchHit(match, "tvdb")
        imdb = str(imdb_id or "").lower()
        if imdb and imdb in self.by_imdb:
            return MatchHit(self.by_imdb[imdb], "imdb")

        parsed_year = parse_year(year)
        title_list = [t for t in titles if t]
        for title in title_list:
            extracted = parse_year(title)
            if extracted and not parsed_year:
                parsed_year = extracted

        # Primary titles: exact year, then ±1. Only when the query has no year
        # at all do we allow a unique yearless hit — that is what used to attach
        # Pinocchio (1940) to Guillermo del Toro's Pinocchio (2022) via an
        # alternate-title slot that shared the bare form.
        years: list[int | None] = []
        if parsed_year:
            years.extend([parsed_year, parsed_year - 1, parsed_year + 1])
        else:
            years.append(None)

        for title in title_list:
            for form in title_keys(title):
                for candidate_year in years:
                    match = self._unique(self.by_title, form, media_type, candidate_year)
                    if match:
                        return MatchHit(match, "title")

        # Alternate titles only with an exact year — never ±1 or yearless.
        if parsed_year:
            for title in title_list:
                for form in title_keys(title):
                    match = self._unique(self.by_alt_title, form, media_type, parsed_year)
                    if match:
                        return MatchHit(match, "title_alt")
        return None

    def _unique(
        self,
        index: dict,
        form: str,
        media_type: str,
        year: int | None,
    ) -> tuple[str, int, int] | None:
        keys = index.get((form, media_type, year)) or []
        if len(keys) == 1:
            return keys[0]
        return None
