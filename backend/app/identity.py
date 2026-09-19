from __future__ import annotations

import ast
import re
from typing import Any


def _unwrap_user(value: Any) -> Any:
    """Pull a username out of nested Plex/Seerr user objects (or their str() form)."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}") and ("username" in text or "displayName" in text):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
            if isinstance(parsed, dict):
                value = parsed
    if isinstance(value, dict):
        return (
            value.get("displayName")
            or value.get("friendly_name")
            or value.get("username")
            or value.get("user")
            or value.get("Name")
            or value.get("UserName")
            or value.get("name")
            or value.get("email")
            or ""
        )
    return value


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(_unwrap_user(value) or "")).strip()


def _key(value: Any) -> str:
    return _clean(value).lower()


class UserDirectory:
    """Merge Tautulli / Seerr identities, preferring Plex usernames."""

    def __init__(self) -> None:
        self.people: list[dict[str, Any]] = []

    def ingest_seerr(self, user: dict[str, Any]) -> dict[str, Any]:
        return self.add(
            plex=user.get("plexUsername") or user.get("plex_username") or "",
            display=user.get("displayName") or user.get("username") or "",
            email=user.get("email") or "",
            extras=[
                user.get("username") or "",
                user.get("jellyfinUsername") or "",
                f"seerr:{user.get('id')}" if user.get("id") is not None else "",
            ],
            seerr_id=user.get("id"),
        )

    def ingest_tautulli(self, user: dict[str, Any]) -> dict[str, Any]:
        return self.add(
            plex=user.get("username") or user.get("user") or "",
            display=user.get("friendly_name") or user.get("username") or "",
            email=user.get("email") or "",
            extras=[
                user.get("friendly_name") or "",
                f"tautulli:{user.get('user_id')}" if user.get("user_id") is not None else "",
            ],
            tautulli_id=user.get("user_id"),
        )

    def ingest_jellystat(self, user: dict[str, Any]) -> dict[str, Any]:
        uid = user.get("Id") or user.get("UserId") or user.get("userid") or user.get("user_id")
        name = (
            user.get("Name")
            or user.get("UserName")
            or user.get("userName")
            or user.get("username")
            or user.get("displayName")
            or ""
        )
        return self.add(
            display=name,
            extras=[name, f"jellystat:{uid}" if uid not in (None, "") else ""],
            jellystat_id=uid,
        )

    def add(
        self,
        *,
        plex: str = "",
        display: str = "",
        email: str = "",
        extras: list[Any] | None = None,
        seerr_id: Any = None,
        tautulli_id: Any = None,
        jellystat_id: Any = None,
    ) -> dict[str, Any]:
        plex = _clean(plex)
        display = _clean(display)
        email = _clean(email)
        aliases = {_key(item) for item in [plex, display, email, *(extras or [])] if _clean(item)}
        if not aliases:
            return {"canonical": "", "display": "", "plex": "", "email": "", "aliases": []}

        person = None
        plex_key = _key(plex)
        email_key = _key(email)
        for candidate in self.people:
            if plex_key and _key(candidate.get("plex")) == plex_key:
                person = candidate
                break
            if email_key and _key(candidate.get("email")) == email_key:
                person = candidate
                break
        if person is None:
            for candidate in self.people:
                if aliases & set(candidate["aliases"]):
                    person = candidate
                    break
        if person is None:
            person = {
                "plex": plex,
                "display": display or plex or email,
                "email": email,
                "aliases": set(),
                "seerr_id": seerr_id,
                "tautulli_id": tautulli_id,
                "jellystat_id": jellystat_id,
            }
            self.people.append(person)

        if plex and (not person.get("plex") or _key(person.get("plex")) == _key(person.get("display"))):
            person["plex"] = plex
        if display and (not person.get("display") or person["display"] == person.get("plex")):
            person["display"] = display
        if email and not person.get("email"):
            person["email"] = email
        if seerr_id and not person.get("seerr_id"):
            person["seerr_id"] = seerr_id
        if tautulli_id not in (None, "") and not person.get("tautulli_id"):
            person["tautulli_id"] = tautulli_id
        if jellystat_id not in (None, "") and not person.get("jellystat_id"):
            person["jellystat_id"] = jellystat_id
        person["aliases"] = set(person.get("aliases") or []) | aliases
        return person

    def resolve(self, raw: Any) -> dict[str, str]:
        if isinstance(raw, dict):
            person = self.add(
                plex=raw.get("plexUsername") or raw.get("plex_username") or raw.get("user") or raw.get("username") or "",
                display=raw.get("displayName")
                or raw.get("friendly_name")
                or raw.get("display_name")
                or raw.get("username")
                or "",
                email=raw.get("email") or "",
                extras=[
                    raw.get("username") or "",
                    raw.get("jellyfinUsername") or "",
                    raw.get("UserName") or "",
                    raw.get("Name") or "",
                    raw.get("name") or "",
                ],
            )
        else:
            text = _clean(raw)
            person = None
            needle = _key(text)
            if needle:
                for candidate in self.people:
                    if needle in candidate["aliases"] or needle == _key(candidate.get("plex")):
                        person = candidate
                        break
            if person is None and text:
                # Stringified Plex/Seerr user objects should land as plex usernames.
                as_plex = isinstance(raw, str) and raw.strip().startswith("{") and text != raw.strip()
                person = self.add(plex=text if as_plex else "", display=text, extras=[text])
            if person is None:
                return {"canonical": "", "display": "", "plex": "", "email": ""}
        display = person.get("display") or person.get("plex") or ""
        plex = person.get("plex") or ""
        canonical = plex or display
        return {
            "canonical": canonical,
            "display": display or canonical,
            "plex": plex,
            "email": person.get("email") or "",
        }

    def snapshot(self) -> list[dict[str, Any]]:
        rows = []
        for person in self.people:
            plex = person.get("plex") or ""
            display = person.get("display") or plex
            canonical = plex or display
            if not canonical:
                continue
            rows.append(
                {
                    "canonical": canonical,
                    "display_name": display,
                    "plex_username": plex,
                    "email": person.get("email") or "",
                    "aliases": sorted(person.get("aliases") or []),
                    "seerr_id": person.get("seerr_id"),
                    "tautulli_id": person.get("tautulli_id"),
                    "jellystat_id": person.get("jellystat_id"),
                }
            )
        rows.sort(key=lambda row: row["display_name"].lower())
        return rows
