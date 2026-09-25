"""Tests for app/glossary.py."""

from __future__ import annotations

from pathlib import Path

from app.glossary import Glossary


def test_empty_glossary_for_missing_path() -> None:
    g = Glossary.load(None)
    assert len(g) == 0
    assert g.format_for_prompt() == ""

    g2 = Glossary.load(Path("/nonexistent/glossary.yaml"))
    assert len(g2) == 0
    assert g2.format_for_prompt() == ""


def test_load_and_format_both_directions(tmp_path: Path) -> None:
    path = tmp_path / "glossary.yaml"
    path.write_text(
        'terms:\n  - en: "showroom"\n    ar: "صالة العرض"\n  - en: "invoice"\n    ar: "فاتورة"\n',
        encoding="utf-8",
    )
    g = Glossary.load(path)
    assert len(g) == 2

    block = g.format_for_prompt()
    assert '"showroom" = "صالة العرض"' in block
    assert '"invoice" = "فاتورة"' in block
    assert "either direction" in block


def test_repo_glossary_loads() -> None:
    from app.config import REPO_ROOT

    g = Glossary.load(REPO_ROOT / "glossary.yaml")
    assert '"walnut" = "خشب الجوز"' in g.format_for_prompt()


def test_glossary_terms_become_whisper_hint_words_per_language(tmp_path) -> None:
    from app.glossary import Glossary
    from app.providers.asr_faster_whisper import hotwords_by_language

    path = tmp_path / "glossary.yaml"
    path.write_text('terms:\n  - en: "walnut"\n    ar: "خشب الجوز"\n  - en: "sofa"\n    ar: "أريكة"\n', encoding="utf-8")
    assert hotwords_by_language(Glossary.load(path)) == {"en": "walnut, sofa", "ar": "خشب الجوز، أريكة"}
    assert hotwords_by_language(Glossary.load(None)) == {}
