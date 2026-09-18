from __future__ import annotations

import re
from collections import defaultdict
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


class CatalogIndex:
    def __init__(self) -> None:
        self.by_tmdb: dict[tuple[str, int], tuple[str, int, int]] = {}
        self.by_tvdb: dict[tuple[str, int], tuple[str, int, int]] = {}
        self.by_imdb: dict[str, tuple[str, int, int]] = {}
        self.by_title: dict[tuple[str, str, int | None], list[tuple[str, int, int]]] = defaultdict(list)

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
        titles = [item.get("title") or "", *extra_titles]
        for title in titles:
            for form in title_keys(title):
                self._index_title(form, media_type, year, key)
                self._index_title(form, media_type, None, key)

    def _index_title(self, form: str, media_type: str, year: int | None, key: tuple[str, int, int]) -> None:
        slot = self.by_title[(form, media_type, year)]
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
        if tmdb_id:
            match = self.by_tmdb.get((media_type, tmdb_id))
            if match:
                return match
        if tvdb_id:
            match = self.by_tvdb.get((media_type, tvdb_id))
            if match:
                return match
        imdb = str(imdb_id or "").lower()
        if imdb and imdb in self.by_imdb:
            return self.by_imdb[imdb]

        parsed_year = parse_year(year)
        title_list = [t for t in titles if t]
        for title in title_list:
            extracted = parse_year(title)
            if extracted and not parsed_year:
                parsed_year = extracted
        years: list[int | None] = []
        if parsed_year:
            years.extend([parsed_year, parsed_year - 1, parsed_year + 1])
        years.append(None)

        for title in title_list:
            for form in title_keys(title):
                for candidate_year in years:
                    match = self._unique(form, media_type, candidate_year)
                    if match:
                        return match
        return None

    def _unique(self, form: str, media_type: str, year: int | None) -> tuple[str, int, int] | None:
        keys = self.by_title.get((form, media_type, year)) or []
        if len(keys) == 1:
            return keys[0]
        return None
