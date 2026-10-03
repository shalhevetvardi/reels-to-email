"""run_pipeline routing between the video and carousel flows. No network."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pipeline  # noqa: E402
from carousel_analyze import CarouselAnalysis  # noqa: E402
from instagram_post import Post, Slide, SlideImage  # noqa: E402

REEL = "https://www.instagram.com/reel/ABC/"
POST = "https://www.instagram.com/p/ABC/"


def _post(slides, url=POST):
    return Post("ABC", url, "user", "User", "caption", 5, 1, tuple(slides))


def _stub_video_flow(monkeypatch, calls):
    async def fake_video(url, cb, lang):
        calls.append("video")
        return {"success": True, "kind": "video"}

    monkeypatch.setattr(pipeline, "_run_video_pipeline", fake_video)


def _forbid_probe(monkeypatch):
    def boom(url):
        raise AssertionError("probe_post must not be called")

    monkeypatch.setattr(pipeline, "probe_post", boom)


def test_reel_skips_probe_and_runs_video_flow(monkeypatch):
    calls = []
    _stub_video_flow(monkeypatch, calls)
    _forbid_probe(monkeypatch)
    result = asyncio.run(pipeline.run_pipeline(REEL))
    assert calls == ["video"]
    assert result["kind"] == "video"


def test_single_video_post_runs_video_flow(monkeypatch):
    calls = []
    _stub_video_flow(monkeypatch, calls)
    monkeypatch.setattr(pipeline, "probe_post", lambda url: _post([Slide(1, "video", None)]))
    result = asyncio.run(pipeline.run_pipeline(POST))
    assert calls == ["video"]
    assert result["kind"] == "video"


def test_probe_failure_falls_back_to_video_flow(monkeypatch):
    calls = []
    _stub_video_flow(monkeypatch, calls)

    def failing(url):
        raise RuntimeError("instagram said no")

    monkeypatch.setattr(pipeline, "probe_post", failing)
    result = asyncio.run(pipeline.run_pipeline(POST))
    assert calls == ["video"]
    assert result["kind"] == "video"


def test_carousel_runs_carousel_flow(monkeypatch):
    slides = [Slide(i, "image", f"https://x.fbcdn.net/{i}.jpg") for i in (1, 2, 3)]
    monkeypatch.setattr(pipeline, "probe_post", lambda url: _post(slides))

    async def forbidden(*a, **k):
        raise AssertionError("video flow must not run")

    monkeypatch.setattr(pipeline, "_run_video_pipeline", forbidden)

    monkeypatch.setattr(
        pipeline,
        "download_slide_images",
        lambda post, max_slides: [SlideImage(s.index, "image", b"d", "image/jpeg") for s in post.slides],
    )
    seen = {}

    def fake_analyze(slide_inputs, **kw):
        seen["slides"] = list(slide_inputs)
        seen["kw"] = kw
        return CarouselAnalysis(content="c", structure="s", brief="b")

    monkeypatch.setattr(pipeline, "analyze_carousel", fake_analyze)
    fed = {}

    def fake_research(text, language):
        fed["research"] = text
        return "research"

    def fake_tag(text, language):
        fed["tag"] = text
        return {"relevant": True, "reason": "r"}

    monkeypatch.setattr(pipeline, "research_topic", fake_research)
    monkeypatch.setattr(pipeline, "tag_relevance", fake_tag)
    sent = {}

    def fake_send(**kw):
        sent.update(kw)
        return "email-1"

    monkeypatch.setattr(pipeline, "send_carousel_email", fake_send)

    messages = []

    async def cb(msg):
        messages.append(msg)

    result = asyncio.run(pipeline.run_pipeline(POST, status_callback=cb, language="en", user_note="my note"))
    assert result["success"] is True
    assert result["kind"] == "carousel"
    assert result["slides"] == 3
    assert result["email_id"] == "email-1"
    assert [s.index for s in seen["slides"]] == [1, 2, 3]
    assert seen["kw"]["user_note"] == "my note"
    assert sent["research"] == "research" and sent["slide_count"] == 3
    # Each answer lands in its own card, and nothing else is passed off as it.
    assert (sent["content"], sent["structure"], sent["brief"]) == ("c", "s", "b")
    # Research and tagging are told the source is a carousel, then given the content.
    for text in (fed["research"], fed["tag"]):
        assert text.startswith("(Note: the source is an Instagram carousel") and text.endswith("c")
    assert [name for name, _, _ in sent["images"]] == ["slide-01.jpg", "slide-02.jpg", "slide-03.jpg"]
    assert any("carousel with 3 slides" in m for m in messages)


def test_carousel_research_failure_still_sends_email(monkeypatch):
    slides = [Slide(1, "image", "https://x.fbcdn.net/1.jpg"), Slide(2, "image", "https://x.fbcdn.net/2.jpg")]
    monkeypatch.setattr(pipeline, "probe_post", lambda url: _post(slides))
    monkeypatch.setattr(
        pipeline,
        "download_slide_images",
        lambda post, max_slides: [SlideImage(1, "image", b"d", "image/jpeg")],
    )
    monkeypatch.setattr(
        pipeline, "analyze_carousel", lambda s, **kw: CarouselAnalysis("c", "s", "b")
    )

    def failing_research(text, language):
        raise RuntimeError("perplexity down")

    monkeypatch.setattr(pipeline, "research_topic", failing_research)
    monkeypatch.setattr(pipeline, "tag_relevance", lambda text, language: {"relevant": False, "reason": "r"})
    sent = {}
    monkeypatch.setattr(pipeline, "send_carousel_email", lambda **kw: sent.update(kw) or "e")
    result = asyncio.run(pipeline.run_pipeline(POST))
    assert result["success"] is True
    assert sent["research"] == ""


def test_carousel_with_no_downloadable_images_fails_generically(monkeypatch):
    slides = [Slide(1, "image", None), Slide(2, "image", None)]
    monkeypatch.setattr(pipeline, "probe_post", lambda url: _post(slides))
    monkeypatch.setattr(pipeline, "download_slide_images", lambda post, max_slides: [])
    result = asyncio.run(pipeline.run_pipeline(POST))
    assert result["success"] is False
    assert "no slide images" in result["error"]


def test_max_slides_is_clamped_and_survives_bad_values(monkeypatch):
    for raw, expected in [("5", 5), ("0", 1), ("-3", 1), ("999", 20), ("abc", 20), ("", 20)]:
        monkeypatch.setenv("CAROUSEL_MAX_SLIDES", raw)
        assert pipeline._max_slides() == expected, raw
    monkeypatch.delenv("CAROUSEL_MAX_SLIDES")
    assert pipeline._max_slides() == 20


def test_carousel_analysis_failure_sends_nothing(monkeypatch):
    slides = [Slide(1, "image", "https://x.fbcdn.net/1.jpg"), Slide(2, "image", "https://x.fbcdn.net/2.jpg")]
    monkeypatch.setattr(pipeline, "probe_post", lambda url: _post(slides))
    monkeypatch.setattr(
        pipeline,
        "download_slide_images",
        lambda post, max_slides: [SlideImage(1, "image", b"d", "image/jpeg")],
    )

    def failing_analysis(slide_inputs, **kw):
        raise RuntimeError("vision call failed")

    def forbidden(*a, **k):
        raise AssertionError("nothing may be researched, tagged or sent after a failed analysis")

    monkeypatch.setattr(pipeline, "analyze_carousel", failing_analysis)
    monkeypatch.setattr(pipeline, "research_topic", forbidden)
    monkeypatch.setattr(pipeline, "tag_relevance", forbidden)
    monkeypatch.setattr(pipeline, "send_carousel_email", forbidden)
    messages = []

    async def cb(msg):
        messages.append(msg)

    result = asyncio.run(pipeline.run_pipeline(POST, status_callback=cb, language="en"))
    assert result["success"] is False
    assert "vision call failed" in result["error"]
    # The sender only ever sees the generic message.
    assert not any("vision call failed" in m for m in messages)
