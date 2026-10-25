from app.db import connect


def add(title, *, size=1000, rating=None, requester="", last_watched=0, plays=0):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO media (
                media_type, tmdb_id, title, size_bytes, rating, requested_by,
                last_watched_at, play_count, availability
            ) VALUES ('movie', ?, ?, ?, ?, ?, ?, ?, 'downloaded')
            """,
            (abs(hash(title)) % 100000, title, size, rating, requester, last_watched, plays),
        )


def titles(client, query):
    return [item["title"] for item in client.get(f"/api/library?{query}").json()["items"]]


def setup_function():
    with connect() as conn:
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM whitelist")


def test_reverse_flips_size_to_smallest_first(auth_client):
    add("Medium", size=500)
    add("Huge", size=9000)
    add("Tiny", size=10)

    assert titles(auth_client, "sort=size") == ["Huge", "Medium", "Tiny"]
    assert titles(auth_client, "sort=size&reverse=true") == ["Tiny", "Medium", "Huge"]


def test_reverse_flips_title_and_watch_dates(auth_client):
    add("Alpha", last_watched=300, plays=1)
    add("Charlie", last_watched=100, plays=1)
    add("Bravo", last_watched=200, plays=1)

    assert titles(auth_client, "sort=title") == ["Alpha", "Bravo", "Charlie"]
    assert titles(auth_client, "sort=title&reverse=true") == ["Charlie", "Bravo", "Alpha"]
    assert titles(auth_client, "sort=oldest") == ["Charlie", "Bravo", "Alpha"]
    assert titles(auth_client, "sort=oldest&reverse=true") == ["Alpha", "Bravo", "Charlie"]


def test_missing_values_stay_last_in_both_directions(auth_client):
    add("Unrated")
    add("Good", rating=8.1)
    add("Bad", rating=3.2)
    add("Nobody", requester="")
    add("Zed", requester="zed", rating=5.0)
    add("Amy", requester="amy", rating=6.0)

    assert titles(auth_client, "sort=rating")[:4] == ["Bad", "Zed", "Amy", "Good"]
    assert titles(auth_client, "sort=rating&reverse=true")[:4] == ["Good", "Amy", "Zed", "Bad"]
    assert titles(auth_client, "sort=rating&reverse=true")[4:] == ["Unrated", "Nobody"]
    assert titles(auth_client, "sort=requested")[:2] == ["Amy", "Zed"]
    assert titles(auth_client, "sort=requested&reverse=true")[:2] == ["Zed", "Amy"]
