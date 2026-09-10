"""The email body embeds LLM/transcript-derived markdown. It must be sanitized."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from email_sender import _markdown_to_html  # noqa: E402


def test_script_tag_stripped():
    out = _markdown_to_html("hello <script>alert(1)</script> world")
    assert "<script" not in out.lower()
    assert "alert(1)" not in out or "<script" not in out.lower()


def test_onerror_attribute_stripped():
    out = _markdown_to_html('<img src=x onerror="alert(1)">')
    assert "onerror" not in out.lower()


def test_javascript_href_stripped():
    out = _markdown_to_html("[click](javascript:alert(1))")
    assert "javascript:" not in out.lower()


def test_safe_markdown_preserved():
    out = _markdown_to_html("**bold** and [link](https://example.com)")
    assert "<strong>bold</strong>" in out
    assert 'href="https://example.com"' in out


def test_empty_is_empty():
    assert _markdown_to_html("") == ""
