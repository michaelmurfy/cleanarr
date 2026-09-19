from app.services.seerr import (
    media_available,
    media_blocked,
    media_claimed,
    media_deleted,
    media_in_flight,
)

# Shape taken from a live Jellyseerr /api/v1/media row.
def row(**over):
    base = {
        "mediaType": "movie",
        "tmdbId": 100074,
        "status": 5,
        "status4k": 1,
        "mediaAddedAt": "2023-01-20T15:06:43.375Z",
        "externalServiceId": None,
        "externalServiceId4k": None,
    }
    base.update(over)
    return base


def test_deleted_rows_are_not_claimed():
    deleted = row(status=7)
    assert media_deleted(deleted)
    assert not media_claimed(deleted)


def test_media_added_at_alone_is_not_a_claim():
    # Jellyseerr defaults mediaAddedAt on insert, so every row carries one.
    assert not media_claimed(row(status=1))
    assert not media_claimed(row(status=7, mediaAddedAt="2020-01-01T00:00:00.000Z"))


def test_available_in_hd_wins_over_deleted_4k():
    both = row(status=5, status4k=7)
    assert not media_deleted(both)
    assert media_claimed(both)


def test_dangling_service_id_still_counts_as_claimed():
    orphan = row(status=1, externalServiceId=685)
    assert media_claimed(orphan)
    assert not media_available(orphan)


def test_processing_row_is_in_flight_not_claimed():
    processing = row(status=3)
    assert media_in_flight(processing)
    assert not media_claimed(processing)


def test_blocked_beats_everything():
    assert media_blocked(row(status=6))
    assert not media_claimed(row(status=6))
