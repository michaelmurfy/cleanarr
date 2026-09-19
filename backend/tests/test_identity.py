from app.identity import UserDirectory


def test_seerr_and_tautulli_merge_on_account_username():
    directory = UserDirectory()
    directory.ingest_seerr({"id": 3, "plexUsername": "dan", "displayName": "Dan M", "email": "dan@example.com"})
    directory.ingest_tautulli({"user_id": 11, "username": "dan", "friendly_name": "Danny"})

    assert len(directory.people) == 1
    person = directory.people[0]
    assert person["account"] == "dan"
    assert person["seerr_id"] == 3
    assert person["tautulli_id"] == 11


def test_merge_on_email_when_account_username_differs():
    directory = UserDirectory()
    directory.ingest_seerr({"id": 1, "username": "d.mason", "email": "dan@example.com"})
    directory.ingest_tautulli({"user_id": 2, "username": "dmason", "email": "dan@example.com"})
    assert len(directory.people) == 1


def test_distinct_users_stay_separate():
    directory = UserDirectory()
    directory.ingest_tautulli({"user_id": 1, "username": "alice"})
    directory.ingest_tautulli({"user_id": 2, "username": "bob"})
    assert len(directory.people) == 2


def test_jellystat_user_resolves_by_name():
    directory = UserDirectory()
    directory.ingest_jellystat({"Id": "abc123", "Name": "Carol"})
    resolved = directory.resolve("Carol")
    assert resolved["display"] == "Carol"
    assert resolved["canonical"] == "Carol"
    assert resolved["account"] == "Carol"


def test_seerr_jellyfin_login_merges_with_jellystat():
    directory = UserDirectory()
    directory.ingest_seerr({"id": 4, "jellyfinUsername": "carol", "displayName": "Carol", "email": "carol@example.com"})
    directory.ingest_jellystat({"Id": "abc123", "Name": "carol"})

    assert len(directory.people) == 1
    person = directory.people[0]
    assert person["account"] == "carol"
    assert person["seerr_id"] == 4
    assert person["jellystat_id"] == "abc123"
    assert directory.snapshot()[0]["account_username"] == "carol"


def test_resolve_is_case_and_whitespace_insensitive():
    directory = UserDirectory()
    directory.ingest_tautulli({"user_id": 5, "username": "dan", "friendly_name": "Dan M"})
    assert directory.resolve("  DAN  ")["account"] == "dan"
    assert directory.resolve("dan m")["account"] == "dan"


def test_resolve_unknown_name_creates_display_only_person():
    directory = UserDirectory()
    resolved = directory.resolve("Ghost")
    assert resolved == {"canonical": "Ghost", "display": "Ghost", "account": "", "email": ""}
    assert len(directory.people) == 1


def test_resolve_empty_input_yields_blank_identity():
    directory = UserDirectory()
    assert directory.resolve("")["canonical"] == ""
    assert directory.people == []


def test_snapshot_sorted_and_skips_anonymous():
    directory = UserDirectory()
    directory.ingest_tautulli({"user_id": 1, "username": "zoe", "email": "zoe@example.com"})
    directory.ingest_seerr({"id": 9, "plexUsername": "adam", "displayName": "Adam"})
    rows = directory.snapshot()
    assert [row["canonical"] for row in rows] == ["adam", "zoe"]
    assert rows[1]["email"] == "zoe@example.com"
    assert rows[0]["seerr_id"] == 9
    assert sorted(rows[0]["aliases"]) == rows[0]["aliases"]


def test_seerr_nested_user_object_uses_username():
    directory = UserDirectory()
    directory.ingest_seerr(
        {
            "id": 4,
            "plexUsername": {
                "id": "66fc6ce2-f607-4cef-8120-73fd31a66a4a",
                "username": "Corey___",
                "thumbUrl": "https://plex.tv/users/8409c8117aa5c76c/avatar?c=1789750883",
                "avatarUrl": "https://plex.tv/users/8409c8117aa5c76c/avatar?c=1789750883",
            },
        }
    )
    person = directory.people[0]
    assert person["account"] == "Corey___"
    assert person["display"] == "Corey___"
    assert "{" not in person["account"]


def test_resolve_recovers_stringified_user_object():
    directory = UserDirectory()
    raw = (
        "{'id': '66fc6ce2-f607-4cef-8120-73fd31a66a4a', 'username': 'Corey___', "
        "'thumbUrl': 'https://plex.tv/users/8409c8117aa5c76c/avatar?c=1789750883', "
        "'avatarUrl': 'https://plex.tv/users/8409c8117aa5c76c/avatar?c=1789750883'}"
    )
    resolved = directory.resolve(raw)
    assert resolved["display"] == "Corey___"
    assert resolved["canonical"] == "Corey___"
    assert resolved["account"] == "Corey___"
