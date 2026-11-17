"""The environment gateseal's uv runs in."""

from gateseal.environments import uv_environment


def test_uv_may_download_but_keeps_only_the_callers_caches(monkeypatch):
    for name, value in (
        ("UV_OFFLINE", "1"),
        ("UV_PYTHON_DOWNLOADS", "never"),
        ("UV_INDEX_URL", "https://mirror.invalid/simple"),
        ("UV_CACHE_DIR", "/caches/uv"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("UV_PYTHON_CACHE_DIR", raising=False)
    env = uv_environment()
    assert {key: value for key, value in env.items() if key.startswith("UV_")} == {
        "UV_CACHE_DIR": "/caches/uv",
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_HTTP_CONNECT_TIMEOUT": "10",
        "UV_HTTP_TIMEOUT": "30",
        "UV_HTTP_RETRIES": "1",
        "UV_NO_PROGRESS": "1",
    }
