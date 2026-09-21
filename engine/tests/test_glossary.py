"""Tests for app/glossary.py."""

from __future__ import annotations

from pathlib import Path

from app.glossary import Glossary


def test_empty_glossary_for_missing_path() -> None:
    g = Glossary.load(None)
    assert len(g) == 0
    assert g.format_for_prompt("en", "ar") == ""

    g2 = Glossary.load(Path("/nonexistent/glossary.yaml"))
    assert len(g2) == 0
    assert g2.format_for_prompt("en", "ar") == ""


def test_load_and_format_both_directions(tmp_path: Path) -> None:
    path = tmp_path / "glossary.yaml"
    path.write_text(
        'terms:\n  - en: "showroom"\n    ar: "صالة العرض"\n  - en: "invoice"\n    ar: "فاتورة"\n',
        encoding="utf-8",
    )
    g = Glossary.load(path)
    assert len(g) == 2

    en_to_ar = g.format_for_prompt("en", "ar")
    assert '"showroom" -> "صالة العرض"' in en_to_ar
    assert '"invoice" -> "فاتورة"' in en_to_ar

    ar_to_en = g.format_for_prompt("ar", "en")
    assert '"صالة العرض" -> "showroom"' in ar_to_en
    assert '"فاتورة" -> "invoice"' in ar_to_en


def test_format_for_prompt_ignores_non_en_ar_pairs(tmp_path: Path) -> None:
    path = tmp_path / "glossary.yaml"
    path.write_text('terms:\n  - en: "showroom"\n    ar: "صالة العرض"\n', encoding="utf-8")
    g = Glossary.load(path)
    assert g.format_for_prompt("en", "fr") == ""
