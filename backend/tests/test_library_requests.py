from app.db import connect


def add_request(title, *, requester, requested_at, plays=0, availability="downloaded"):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO media (
                media_type, tmdb_id, title, play_count, availability,
                requested_by, requested_at, size_bytes
            ) VALUES ('movie', ?, ?, ?, ?, ?, ?, 1000)
            """,
            (abs(hash(title)) % 100000, title, plays, availability, requester, requested_at),
        )


def titles(response):
    return [item["title"] for item in response.json()["items"]]


def clear_media():
    with connect() as conn:
        conn.execute("DELETE FROM media")


def test_requester_filter_is_exact_and_case_insensitive(auth_client):
    clear_media()
    add_request("Corey Film", requester="Corey___", requested_at="2024-01-01")
    add_request("Other Film", requester="Dan", requested_at="2024-01-02")

    body = auth_client.get("/api/library?requester=corey___&hide_unprocessed=false")
    assert titles(body) == ["Corey Film"]


def test_hide_unprocessed_defaults_on_but_requested_view_still_works(auth_client):
    clear_media()
    add_request("On Disk", requester="Corey___", requested_at="2024-01-01")
    add_request("Still Queued", requester="Corey___", requested_at="2024-01-02", availability="requested")

    hidden = auth_client.get("/api/library?requester=Corey___")
    assert titles(hidden) == ["On Disk"]
    assert hidden.json()["stats"]["requested"] == 1

    shown = auth_client.get("/api/library?requester=Corey___&hide_unprocessed=false")
    assert titles(shown) == ["On Disk", "Still Queued"]

    pending = auth_client.get("/api/library?watched=requested&requester=Corey___")
    assert titles(pending) == ["Still Queued"]


def test_requests_sort_puts_never_watched_first_then_oldest_request(auth_client):
    clear_media()
    add_request("Watched New", requester="Ada", requested_at="2024-03-01", plays=2)
    add_request("Never Old", requester="Ada", requested_at="2024-01-01")
    add_request("Never New", requester="Ada", requested_at="2024-02-01")
    add_request("Watched Old", requester="Ada", requested_at="2024-01-15", plays=1)

    body = auth_client.get("/api/library?requester=Ada&sort=requests&hide_unprocessed=false")
    assert titles(body) == ["Never Old", "Never New", "Watched Old", "Watched New"]
