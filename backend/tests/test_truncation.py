from kubelens.tools import MAX_OUTPUT_CHARS, finalize, truncate


def test_short_text_unchanged() -> None:
    assert truncate("hello") == ("hello", False)
    text = "x" * MAX_OUTPUT_CHARS
    assert truncate(text) == (text, False)


def test_long_text_truncated_to_limit_keeping_head_and_tail() -> None:
    text = "HEAD" + "x" * 10_000 + "TAIL"
    out, truncated = truncate(text)
    assert truncated
    assert len(out) <= MAX_OUTPUT_CHARS
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "[truncated:" in out


def test_finalize_masks_before_truncating() -> None:
    text = "x" * 10_000 + "\npassword=hunter2hunter2\n"
    out, truncated = finalize(text)
    assert truncated
    assert len(out) <= MAX_OUTPUT_CHARS
    assert "password=***" in out
    assert "hunter2" not in out
