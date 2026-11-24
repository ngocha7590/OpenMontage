from pathlib import Path
from unittest import mock

from tools.video.stock_sources import SearchFilters
from tools.video.stock_sources import nasa as nasa_mod
from tools.video.stock_sources.base import Candidate
from tools.video.stock_sources.nasa import NasaSource, _pick_video_url

BASE = "https://images-assets.nasa.gov/video/X/X"
MANIFEST = [f"{BASE}~{t}.mp4" for t in ("orig", "large", "medium", "mobile", "preview")] + [
    f"{BASE}~thumb.jpg"
]


def test_pick_prefers_large_over_orig():
    assert _pick_video_url(MANIFEST) == f"{BASE}~large.mp4"
    assert _pick_video_url([f"{BASE}~orig.mp4", f"{BASE}~mobile.mp4"]) == f"{BASE}~mobile.mp4"
    assert _pick_video_url([f"{BASE}~orig.mp4"]) == f"{BASE}~orig.mp4"


def _resp(json_data=None, headers=None):
    m = mock.Mock()
    m.json.return_value = json_data
    m.headers = headers or {}
    m.raise_for_status.return_value = None
    return m


def _item():
    return {
        "href": "https://images-assets.nasa.gov/video/X/collection.json",
        "data": [{"nasa_id": "X", "media_type": "video", "title": "t"}],
    }


def test_hydrate_falls_back_to_smaller_when_over_limit():
    sizes = {f"{BASE}~large.mp4": 900 * 1024 * 1024, f"{BASE}~medium.mp4": 50 * 1024 * 1024}

    def fake_get(url, **kw):
        return _resp(MANIFEST)

    def fake_head(url, **kw):
        return _resp(headers={"Content-Length": str(sizes.get(url, 10))})

    with mock.patch("requests.get", fake_get), mock.patch("requests.head", fake_head):
        cand = NasaSource()._hydrate_candidate(_item(), SearchFilters())
    assert cand.download_url.endswith("~medium.mp4")


def test_hydrate_skips_when_only_orig_and_too_big():
    manifest = [f"{BASE}~orig.mp4"]
    with mock.patch("requests.get", lambda u, **k: _resp(manifest)), mock.patch(
        "requests.head", lambda u, **k: _resp(headers={"Content-Length": str(3_800_000_000)})
    ):
        assert NasaSource()._hydrate_candidate(_item(), SearchFilters()) is None
        # guard disabled -> accepted
        cand = NasaSource()._hydrate_candidate(_item(), SearchFilters(max_file_mb=0))
        assert cand is not None


class _FakeSrc:
    name = "fake"

    def __init__(self):
        self.downloaded = []

    def is_available(self):
        return True

    def search(self, q, f):
        assert f.max_file_mb == 100
        return [
            Candidate("fake", "big", "u", "https://x/big.mp4", "video"),
            Candidate("fake", "ok", "u", "https://x/ok.mp4", "video"),
        ]

    def download(self, cand, out):
        self.downloaded.append(cand.source_id)
        Path(out).write_bytes(b"x" * 2048)
        return out


def test_direct_clip_search_skips_oversize(tmp_path):
    from tools.video.direct_clip_search import DirectClipSearch

    src = _FakeSrc()
    heads = {"https://x/big.mp4": 500 * 1024 * 1024, "https://x/ok.mp4": 5 * 1024 * 1024}
    with mock.patch("tools.video.stock_sources.available_sources", lambda: [src]), mock.patch(
        "tools.video.direct_clip_search._head_size_bytes", lambda u: heads[u]
    ):
        res = DirectClipSearch().execute(
            {
                "output_dir": str(tmp_path),
                "queries": [{"query": "q"}],
                "clips_per_query": 2,
                "max_file_mb": 100,
                "extract_thumbnails": False,
            }
        )
    assert res.success, res.error
    assert src.downloaded == ["ok"]
    assert any("exceeds" in e["error"] for e in res.data["errors"])
