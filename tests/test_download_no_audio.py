"""A reel that comes down without a sound track: named, logged and cleaned up.

Production, 16-18 Oct 2026: two reels failed on every attempt with yt-dlp's "unable to
obtain file audio codec". The download itself worked (8-11 MB of video), the file had
no audio, and the sender got "try again later" every time. Retrying never helped, and
each attempt left the video in data/. No network: yt-dlp is replaced by a stand-in.

Tests prefixed test_fix_ fail on the code before this change; test_regression_ ones
guard behaviour that already worked.
"""
import logging
import sys
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError
from yt_dlp.version import __version__ as YT_DLP_VERSION

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import download  # noqa: E402

URL = "https://www.instagram.com/reel/DeXi-IboGbE/?cplk=abc"
NO_AUDIO = DownloadError("ERROR: Postprocessing: WARNING: unable to obtain file audio codec with ffprobe")
SIGNED = "https://cdn.example/SECRET-SIGNED-MEDIA-URL"

INFO = {
    "id": "DeXi-IboGbE",
    "format_id": "7",
    "acodec": None,
    "vcodec": "vp09.00.40.08",
    "formats": [
        {"format_id": "dash-a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.5", "tbr": 64, "url": SIGNED},
        {"format_id": "dash-v", "ext": "mp4", "vcodec": "vp09.00.40.08", "acodec": "none", "tbr": 1903, "url": SIGNED},
    ],
}


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: the download raises, the metadata call answers."""

    failure = NO_AUDIO
    metadata = INFO
    calls = []

    instances = []

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        FakeYDL.calls.append(download)
        if download:
            raise FakeYDL.failure
        if isinstance(FakeYDL.metadata, Exception):
            raise FakeYDL.metadata
        return FakeYDL.metadata


@pytest.fixture
def fake_ydl(monkeypatch, tmp_path):
    FakeYDL.failure, FakeYDL.metadata, FakeYDL.calls, FakeYDL.instances = NO_AUDIO, INFO, [], []
    monkeypatch.setattr(download.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(download, "DATA_DIR", tmp_path)
    monkeypatch.setattr(download, "_resolve_cookies_path", lambda: None)
    return tmp_path


def test_fix_a_download_without_audio_is_named_and_chained(fake_ydl):
    with pytest.raises(download.NoAudioTrackError) as caught:
        download.download_video(URL)
    assert caught.value.__cause__ is NO_AUDIO


def test_regression_other_download_errors_pass_through_untouched(fake_ydl):
    FakeYDL.failure = DownloadError("ERROR: Video unavailable")
    with pytest.raises(DownloadError) as caught:
        download.download_video(URL)
    assert type(caught.value) is DownloadError  # not wrapped, not renamed
    assert FakeYDL.calls == [True]  # no diagnostics request for a failure that is not this one


def test_fix_failure_logs_what_was_selected_and_what_was_offered(fake_ydl, caplog):
    with caplog.at_level(logging.WARNING, logger="download"):
        with pytest.raises(download.NoAudioTrackError):
            download.download_video(URL)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "format_id=7" in text                       # what the selector picked
    assert "dash-a:m4a" in text and "dash-v:mp4" in text  # what Instagram offered
    assert YT_DLP_VERSION in text                      # the version that decided
    assert "SECRET-SIGNED" not in text                 # signed media links stay out of the log


def test_fix_the_diagnostics_request_downloads_nothing(fake_ydl):
    with pytest.raises(download.NoAudioTrackError):
        download.download_video(URL)
    assert FakeYDL.calls == [True, False]
    assert FakeYDL.instances[1].opts["simulate"] is True
    assert FakeYDL.instances[1].opts["format"] == "bestaudio/best"  # the selector under suspicion


def test_fix_the_diagnostics_request_uses_the_same_cookies(fake_ydl, monkeypatch):
    monkeypatch.setattr(download, "_resolve_cookies_path", lambda: Path("cookies.txt"))
    with pytest.raises(download.NoAudioTrackError):
        download.download_video(URL)
    assert FakeYDL.instances[1].opts["cookiefile"] == "cookies.txt"


@pytest.mark.parametrize("link, expected", [
    ("https://www.instagram.com/reel/AbC-1_x/?utm=1", "AbC-1_x"),
    ("https://www.instagram.com/reels/AbC/", "AbC"),
    ("https://www.instagram.com/p/AbC/", "AbC"),
    ("https://www.instagram.com/tv/AbC/", "AbC"),
    ("https://www.instagram.com/stories/user/", None),
])
def test_fix_the_video_id_can_be_read_from_the_link(link, expected):
    assert download._id_from_link(link) == expected


def test_fix_failure_removes_the_downloaded_video(fake_ydl):
    (fake_ydl / "DeXi-IboGbE.mp4").write_bytes(b"x" * 10)
    (fake_ydl / "DeXi-IboGbE.mp4.part").write_bytes(b"x")
    (fake_ydl / "other.mp3").write_bytes(b"keep")
    with pytest.raises(download.NoAudioTrackError):
        download.download_video(URL)
    assert sorted(p.name for p in fake_ydl.iterdir()) == ["other.mp3"]


def test_fix_cleanup_and_error_survive_a_failing_diagnostics_request(fake_ydl, caplog):
    FakeYDL.metadata = RuntimeError("instagram said no")
    (fake_ydl / "DeXi-IboGbE.mp4").write_bytes(b"x")
    with caplog.at_level(logging.WARNING, logger="download"):
        with pytest.raises(download.NoAudioTrackError):
            download.download_video(URL)
    assert list(fake_ydl.iterdir()) == []  # the id came from the link instead
    assert "unavailable" in " ".join(r.getMessage() for r in caplog.records)


def test_fix_an_id_that_is_not_plain_is_never_used_to_delete_files(fake_ydl):
    FakeYDL.metadata = dict(INFO, id="*")
    (fake_ydl / "other.mp3").write_bytes(b"keep")
    (fake_ydl / "another.mp4").write_bytes(b"keep")
    with pytest.raises(download.NoAudioTrackError):
        download.download_video("https://www.instagram.com/p/*/")
    assert sorted(p.name for p in fake_ydl.iterdir()) == ["another.mp4", "other.mp3"]


def test_regression_a_normal_download_still_returns_the_mp3(fake_ydl, monkeypatch):
    class Working(FakeYDL):
        def extract_info(self, url, download=True):
            (fake_ydl / "AbC.mp3").write_bytes(b"audio")
            return {"id": "AbC"}

    monkeypatch.setattr(download.yt_dlp, "YoutubeDL", Working)
    assert download.download_video("https://www.instagram.com/reel/AbC/") == fake_ydl / "AbC.mp3"


# --- Instagram's login wall instead of the reel (production, 18 Oct 2026) ---------------

LOGIN_WALL = DownloadError(
    "ERROR: [Instagram] DeXi-IboGbE: The webpage request was redirected to the login page. "
    "You have exceeded the rate-limit for accessing posts anonymously. Use --cookies-from-browser "
    "or --cookies for the authentication."
)


@pytest.mark.parametrize("failure", [
    LOGIN_WALL,
    DownloadError("ERROR: [Instagram] AbC: Requested content is not available, rate-limit reached or login required"),
    DownloadError("ERROR: [Instagram] AbC: Main webpage is locked behind the login page. LOGIN REQUIRED"),
    DownloadError("ERROR: unable to download webpage: HTTP Error 429: Too Many Requests"),
    # one marker each, so every marker is shown to matter on its own
    DownloadError("ERROR: [Instagram] AbC: The webpage request was redirected to the login page."),
    DownloadError("ERROR: [Instagram] AbC: You have exceeded the rate-limit for accessing posts anonymously."),
])
def test_fix_a_login_wall_is_named_and_chained(fake_ydl, failure):
    FakeYDL.failure = failure
    with pytest.raises(download.InstagramBlockedError) as caught:
        download.download_video(URL)
    assert caught.value.__cause__ is failure
    assert "DeXi-IboGbE" in str(caught.value)
    assert FakeYDL.calls == [True]  # no diagnostics request: there is nothing to diagnose


def test_regression_a_missing_audio_track_is_still_not_a_login_wall(fake_ydl):
    with pytest.raises(download.NoAudioTrackError):
        download.download_video(URL)
