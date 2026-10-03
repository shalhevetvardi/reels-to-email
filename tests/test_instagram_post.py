"""Post parsing and the media-URL guard. No network."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import instagram_post  # noqa: E402
from instagram_post import (  # noqa: E402
    Post,
    Slide,
    download_image,
    download_slide_images,
    extract_user_note,
    is_allowed_media_url,
    needs_probe,
    parse_post,
    pick_image_url,
)

CDN = "https://instagram.fsdv1-2.fna.fbcdn.net/v/t51/"


# --- needs_probe ---

@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.instagram.com/reel/ABC123/", False),
        ("https://www.instagram.com/reels/ABC123/", False),
        ("https://www.instagram.com/tv/ABC123/", False),
        ("https://www.instagram.com/p/ABC123/", True),
        ("https://www.instagram.com/share/p/ABC123/", True),
        ("https://www.instagram.com/share/BAbc123/", True),
        ("https://www.instagram.com/someuser/p/ABC123/", True),
        ("https://www.instagram.com/P/ABC123/", True),
        ("https://www.instagram.com/Share/ABC123/", True),
        ("https://www.instagram.com/p/ABC123/?igsh=xyz", True),
        ("https://www.instagram.com/reel/ABC123/?next=/p/other/", False),
        ("https://www.instagram.com/", False),
        ("", False),
    ],
)
def test_needs_probe(url, expected):
    assert needs_probe(url) is expected


# --- is_allowed_media_url ---

@pytest.mark.parametrize(
    "url",
    [
        "https://instagram.fsdv1-2.fna.fbcdn.net/v/t51/a.jpg?stp=x",
        "https://scontent-lhr8-1.cdninstagram.com/v/a.jpg",
        "https://scontent.cdninstagram.com:443/v/a.jpg",
        "https://SCONTENT.CDNINSTAGRAM.COM/v/a.jpg",
    ],
)
def test_allowed_media_urls_accepted(url):
    assert is_allowed_media_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://x.fbcdn.net/a.jpg",
        "file:///etc/passwd",
        "https://evilfbcdn.net/x",
        "https://fbcdn.net.evil.com/x",
        "https://user@x.fbcdn.net/x",
        "https://user:pw@x.fbcdn.net/x",
        "https://x.fbcdn.net:8080/x",
        "https://fbcdn.net/x",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/x",
        "",
        "not a url",
        "https://",
    ],
)
def test_disallowed_media_urls_rejected(url):
    assert is_allowed_media_url(url) is False


# --- pick_image_url ---

def _t(host_path, stp=None, **extra):
    url = CDN + host_path
    if stp:
        url += "?stp=" + stp
    return {"url": url, **extra}


CROPPED = "c0.177.1417.1417a_dst-jpg_e35_s150x150_tt6"


def test_pick_prefers_uncropped_and_last_without_dims():
    thumbs = [
        _t("a.jpg", CROPPED),
        _t("b.jpg", "dst-jpg_e35_p1080x1080_tt6"),
        _t("c.jpg", "dst-jpg_e35_tt6"),
    ]
    assert pick_image_url(thumbs) == thumbs[2]["url"]


def test_pick_falls_back_to_cropped_when_all_cropped():
    thumbs = [_t("a.jpg", CROPPED), _t("b.jpg", CROPPED)]
    assert pick_image_url(thumbs) == thumbs[1]["url"]


def test_pick_largest_area_when_dimensions_exist():
    thumbs = [
        _t("a.jpg", "dst-jpg_e35_tt6", width=1080, height=1080),
        _t("b.jpg", "dst-jpg_e35_tt6", width=320, height=320),
        _t("c.jpg", "dst-jpg_e35_tt6"),
    ]
    assert pick_image_url(thumbs) == thumbs[0]["url"]


def test_pick_area_tie_goes_to_later():
    thumbs = [
        _t("a.jpg", None, width=100, height=100),
        _t("b.jpg", None, width=100, height=100),
    ]
    assert pick_image_url(thumbs) == thumbs[1]["url"]


def test_pick_ignores_disallowed_hosts():
    good = _t("a.jpg", "dst-jpg_e35_tt6")
    thumbs = [good, {"url": "https://evil.example.com/x.jpg"}, {"url": "http://x.fbcdn.net/y.jpg"}]
    assert pick_image_url(thumbs) == good["url"]
    assert pick_image_url([{"url": "https://evil.example.com/x.jpg"}]) is None


def test_pick_empty_is_none():
    assert pick_image_url([]) is None


# --- parse_post ---

def _image_entry(n):
    return {
        "id": f"s{n}",
        "formats": [],
        "duration": None,
        "thumbnails": [_t(f"{n}a.jpg", CROPPED), _t(f"{n}b.jpg", "dst-jpg_e35_tt6")],
    }


def _video_entry(n):
    return {
        "id": f"v{n}",
        "formats": [{"url": "https://x.fbcdn.net/v.mp4"}],
        "duration": 12,
        "thumbnails": [_t(f"{n}v.jpg", "dst-jpg_e35_tt6")],
    }


def _top(**kw):
    base = {
        "id": "CODE1",
        "channel": "someuser",
        "uploader": "Some User",
        "description": "a caption",
        "like_count": 10,
        "comment_count": 2,
    }
    base.update(kw)
    return base


def test_parse_three_image_carousel():
    info = _top(_type="playlist", entries=[_image_entry(1), _image_entry(2), _image_entry(3)])
    post = parse_post(info, "https://www.instagram.com/p/CODE1/")
    assert [s.index for s in post.slides] == [1, 2, 3]
    assert all(s.kind == "image" and s.entry is None for s in post.slides)
    assert post.slides[0].image_url == CDN + "1b.jpg?stp=dst-jpg_e35_tt6"
    assert (post.shortcode, post.author, post.author_name, post.caption) == (
        "CODE1", "someuser", "Some User", "a caption",
    )
    assert (post.like_count, post.comment_count) == (10, 2)
    assert post.is_single_video is False


def test_parse_mixed_carousel_keeps_entry_only_for_video():
    info = _top(entries=[_image_entry(1), _video_entry(2)])
    post = parse_post(info, "u")
    assert [s.kind for s in post.slides] == ["image", "video"]
    assert post.slides[0].entry is None
    assert post.slides[1].entry == info["entries"][1]
    assert post.slides[1].duration == 12.0


def test_parse_single_image():
    post = parse_post(_top(**_image_entry(1)), "u")
    assert len(post.slides) == 1
    assert post.slides[0].kind == "image"
    assert post.is_single_video is False


def test_parse_single_video():
    entry = _video_entry(1)
    info = _top(formats=entry["formats"], thumbnails=entry["thumbnails"], duration=12)
    post = parse_post(info, "u")
    assert post.is_single_video is True


def test_parse_no_entries_raises():
    with pytest.raises(ValueError):
        parse_post(_top(entries=[]), "u")


def test_parse_missing_metadata_defaults():
    post = parse_post({"entries": [_image_entry(1)]}, "u")
    assert (post.shortcode, post.author, post.caption) == ("", "", "")
    assert post.like_count is None and post.comment_count is None


# --- downloads ---

def test_download_image_rejects_disallowed_url_before_any_request(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("httpx.Client must not be constructed for a disallowed URL")

    monkeypatch.setattr(instagram_post.httpx, "Client", boom)
    with pytest.raises(ValueError):
        download_image("https://evil.example.com/x.jpg")
    with pytest.raises(ValueError):
        download_image("http://x.fbcdn.net/x.jpg")


class _FakeResponse:
    def __init__(self, status=200, content_type="image/jpeg", body=b"abc"):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self):
        yield self._body


class _FakeClient:
    response = _FakeResponse()

    def __init__(self, *a, **k):
        assert k.get("follow_redirects") is False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, headers=None):
        assert headers["Referer"] == "https://www.instagram.com/"
        return self.response


def test_download_image_happy_path_strips_content_type_params(monkeypatch):
    _FakeClient.response = _FakeResponse(content_type="Image/JPEG; charset=binary", body=b"jpegdata")
    monkeypatch.setattr(instagram_post.httpx, "Client", _FakeClient)
    assert download_image(CDN + "a.jpg") == (b"jpegdata", "image/jpeg")


@pytest.mark.parametrize(
    "response, kwargs",
    [
        (_FakeResponse(status=404), {}),
        (_FakeResponse(content_type="text/html"), {}),
        (_FakeResponse(body=b"x" * 50), {"max_bytes": 10}),
    ],
)
def test_download_image_rejects_bad_responses(monkeypatch, response, kwargs):
    _FakeClient.response = response
    monkeypatch.setattr(instagram_post.httpx, "Client", _FakeClient)
    with pytest.raises(ValueError):
        download_image(CDN + "a.jpg", **kwargs)


def test_download_slide_images_skips_failures_and_keeps_order(monkeypatch):
    post = Post(
        shortcode="c", url="u", author="a", author_name="A", caption="",
        like_count=None, comment_count=None,
        slides=(
            Slide(1, "image", CDN + "1.jpg"),
            Slide(2, "image", CDN + "2.jpg"),
            Slide(3, "image", None),
            Slide(4, "video", CDN + "4.jpg"),
        ),
    )

    def fake_download(url, **kw):
        if url.endswith("2.jpg"):
            raise ValueError("boom")
        return url.encode(), "image/jpeg"

    monkeypatch.setattr(instagram_post, "download_image", fake_download)
    images = download_slide_images(post)
    assert [i.index for i in images] == [1, 4]
    assert [i.kind for i in images] == ["image", "video"]


def test_download_slide_images_respects_max_slides(monkeypatch):
    slides = tuple(Slide(i, "image", CDN + f"{i}.jpg") for i in range(1, 6))
    post = Post("c", "u", "a", "A", "", None, None, slides)
    monkeypatch.setattr(instagram_post, "download_image", lambda url, **kw: (b"x", "image/png"))
    assert [i.index for i in download_slide_images(post, max_slides=2)] == [1, 2]


# --- extract_user_note ---

@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://www.instagram.com/p/ABC/", None),
        ("https://www.instagram.com/p/ABC/?igsh=MTc4%3D%3D&img_index=1", None),
        ("אהבתי את הסגנון https://www.instagram.com/p/ABC/?igsh=x%3D", "אהבתי את הסגנון"),
        ("https://instagr.am/p/ABC/ focus on the hook", "focus on the hook"),
        ("look\nhttps://www.INSTAGRAM.com/p/ABC/\nat the proof slides", "look at the proof slides"),
        ("   ", None),
    ],
)
def test_extract_user_note(text, expected):
    assert extract_user_note(text) == expected


def test_extract_user_note_is_truncated():
    assert len(extract_user_note("x " * 600)) <= 500


# --- parser-differential URLs (urlsplit and the HTTP client read these differently) ---

@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com\\.cdninstagram.com/a.jpg",
        "https://evil.com\t.cdninstagram.com/a.jpg",
        "https://evil.com\n.cdninstagram.com/a.jpg",
        " https://x.cdninstagram.com/a.jpg",
        "https://x.cdninstagram.com/a b.jpg",
        "https://ａ.cdninstagram.com/a.jpg",
        "https://evil.com∕.cdninstagram.com/a.jpg",
        "https://x_y.fbcdn.net/a.jpg",
        None,
        123,
    ],
)
def test_parser_differential_urls_rejected(url):
    assert is_allowed_media_url(url) is False


def test_download_slide_images_stops_at_total_budget(monkeypatch):
    slides = tuple(Slide(index=i, kind="image", image_url=f"{CDN}{i}.jpg") for i in range(1, 6))
    post = Post("c", "u", "a", "n", "", None, None, slides)
    monkeypatch.setattr(instagram_post, "download_image", lambda url, **kw: (b"x" * 40, "image/jpeg"))
    images = download_slide_images(post, max_total_bytes=100)
    assert [img.index for img in images] == [1, 2]
