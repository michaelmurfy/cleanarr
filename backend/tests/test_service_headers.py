from app.services.arr import Radarr, Sonarr
from app.services.jellystat import Jellystat
from app.services.seerr import Seerr
from app.services.tracearr import Tracearr

KEY = "test-key"


def test_jellystat_sends_one_auth_header():
    headers = Jellystat("https://jellystat.example", KEY).headers
    auth = {name: value for name, value in headers.items() if value == KEY}
    assert list(auth) == ["x-api-token"]


def test_jellystat_sends_no_authorization_header():
    # Jellystat answers 401 when an Authorization header is present, even
    # alongside a valid x-api-token.
    headers = Jellystat("https://jellystat.example", KEY).headers
    assert not any(name.lower() == "authorization" for name in headers)


def test_no_client_repeats_a_header_in_two_cases():
    # HTTP header names are case-insensitive, so "X-API-Token" and "x-api-token"
    # in one dict go out as a duplicate header. Jellystat answers 403 to that.
    clients = [
        Jellystat("https://jellystat.example", KEY),
        Seerr("https://seerr.example", KEY),
        Radarr("https://radarr.example", KEY),
        Sonarr("https://sonarr.example", KEY),
        Tracearr("https://tracearr.example", KEY),
    ]
    for client in clients:
        names = [name.lower() for name in client.headers]
        assert len(names) == len(set(names)), f"{type(client).__name__} repeats a header"


def test_arr_and_seerr_use_x_api_key():
    for cls, url in (
        (Radarr, "https://radarr.example"),
        (Sonarr, "https://sonarr.example"),
        (Seerr, "https://seerr.example"),
    ):
        assert cls(url, KEY).headers["X-Api-Key"] == KEY
