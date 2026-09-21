"""Tests for the sentence segmenter (app/segmenter.py)."""

from __future__ import annotations

import pytest

from app.segmenter import segment


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("   ", []),
        ("Good afternoon.", ["Good afternoon."]),
        (
            "Good afternoon. Thank you for visiting our showroom today.",
            ["Good afternoon.", "Thank you for visiting our showroom today."],
        ),
        (
            "Please ask for Mr. Smith when you arrive. He manages the leather collection.",
            ["Please ask for Mr. Smith when you arrive.", "He manages the leather collection."],
        ),
        (
            "The sofa costs $1,299.50 and ships in 3.5 weeks.",
            ["The sofa costs $1,299.50 and ships in 3.5 weeks."],
        ),
        (
            "Is this in stock? We need twelve units by Friday!",
            ["Is this in stock?", "We need twelve units by Friday!"],
        ),
    ],
)
def test_segment(text: str, expected: list[str]) -> None:
    assert segment(text) == expected


def test_segment_preserves_arabic_boundary() -> None:
    result = segment("مرحبا بك. كيف حالك؟")
    assert result == ["مرحبا بك.", "كيف حالك؟"]
