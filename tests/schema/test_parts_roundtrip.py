from __future__ import annotations

import pytest
from a2a.types import Part

from a2a_utility.schema import ExtendedPart, SourceReferenceResponse, VercelThinkingResponse


def test_text_part_roundtrip():
    part = ExtendedPart.from_text("hello", foo="bar")
    proto = part.to_protobuf()
    back = ExtendedPart.from_protobuf(proto)
    assert back.text == "hello"
    assert back.metadata == {"foo": "bar"}


def test_raw_part_roundtrip():
    part = ExtendedPart.file(raw=b"binary-data", filename="a.bin", media_type="application/octet-stream")
    proto = part.to_protobuf()
    back = ExtendedPart.from_protobuf(proto)
    assert back.raw == b"binary-data"
    assert back.filename == "a.bin"
    assert back.media_type == "application/octet-stream"


def test_url_part_roundtrip():
    part = ExtendedPart.file(url="https://example.com/x.png", filename="x.png")
    proto = part.to_protobuf()
    back = ExtendedPart.from_protobuf(proto)
    assert back.url == "https://example.com/x.png"
    assert back.filename == "x.png"


def test_thinking_data_part_roundtrip():
    part = ExtendedPart.thinking("reasoning...")
    proto = part.to_protobuf()
    back = ExtendedPart.from_protobuf(proto)
    assert isinstance(back.data.data_content, VercelThinkingResponse)
    assert back.data.data_content.text == "reasoning..."


def test_source_reference_data_part_roundtrip():
    refs = [{"source": "demo", "note": "test"}]
    part = ExtendedPart.source_reference(refs)
    proto = part.to_protobuf()
    back = ExtendedPart.from_protobuf(proto)
    assert isinstance(back.data.data_content, SourceReferenceResponse)
    assert back.data.data_content.merged_reference == refs


def test_construction_rejects_more_than_one_oneof_field():
    with pytest.raises(ValueError):
        ExtendedPart(text="a", raw=b"b")


def test_native_part_oneof_silently_keeps_last_assignment_not_a_raise():
    """This is the exact footgun ExtendedPart's validator exists to catch —
    demonstrated here against the *native* proto directly (not ExtendedPart)
    to show it's a real native SDK behavior, not a hypothetical: constructing
    a native Part with two oneof fields doesn't raise, it just silently keeps
    whichever was assigned last. ExtendedPart(...) construction (the test
    above) is where a2a_utility actually guards against this — there's
    nothing for from_protobuf() to reject after the fact, since by the time a
    Part object exists, protobuf has already resolved the oneof to a single
    field."""
    part = Part(text="a")
    part.raw = b"b"  # overwrites the oneof; `text` is no longer set
    assert not part.HasField("text")
    assert part.HasField("raw")
