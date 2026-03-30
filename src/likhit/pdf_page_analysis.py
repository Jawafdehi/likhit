"""Helpers for detecting suspicious text layers and image-dominant PDF pages."""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import re

import fitz

_TOKEN_PATTERN = re.compile(r"\S+")
_DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097F]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_SUSPICIOUS_LATIN_TOKEN_PATTERN = re.compile(
    r"""[\\\[\]\{\}\$^&*_+=<>]|[A-Za-z]\d|\d[A-Za-z]"""
)


@dataclass(frozen=True)
class PdfPageAnalysis:
    page_number: int
    image_count: int
    max_image_coverage: float
    text_length: int
    token_count: int
    devanagari_char_count: int
    suspicious_latin_ratio: float
    vowel_poor_latin_ratio: float

    @property
    def is_image_dominant(self) -> bool:
        return self.max_image_coverage >= 0.85

    @property
    def has_suspicious_text_layer(self) -> bool:
        return (
            self.devanagari_char_count < 20
            and self.token_count >= 12
            and (
                self.suspicious_latin_ratio >= 0.12
                or (
                    self.suspicious_latin_ratio >= 0.06
                    and self.vowel_poor_latin_ratio >= 0.45
                )
            )
        )

    @property
    def likely_needs_ocr(self) -> bool:
        return self.is_image_dominant and self.has_suspicious_text_layer


def analyze_pdf_pages(source: bytes | str | Path) -> list[PdfPageAnalysis]:
    if isinstance(source, bytes):
        doc = fitz.open(stream=source, filetype="pdf")
    else:
        doc = fitz.open(str(source))

    try:
        analyses: list[PdfPageAnalysis] = []
        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_text = page.get_text()
            page_area = max(page.rect.width * page.rect.height, 1.0)

            max_image_coverage = 0.0
            images = page.get_images(full=True)
            for image in images:
                xref = image[0]
                for rect in page.get_image_rects(xref):
                    coverage = (rect.width * rect.height) / page_area
                    if coverage > max_image_coverage:
                        max_image_coverage = coverage

            token_count, devanagari_char_count, suspicious_ratio, vowel_poor_ratio = (
                _analyze_text_quality(page_text)
            )

            analyses.append(
                PdfPageAnalysis(
                    page_number=page_index + 1,
                    image_count=len(images),
                    max_image_coverage=max_image_coverage,
                    text_length=len(page_text),
                    token_count=token_count,
                    devanagari_char_count=devanagari_char_count,
                    suspicious_latin_ratio=suspicious_ratio,
                    vowel_poor_latin_ratio=vowel_poor_ratio,
                )
            )

        return analyses
    finally:
        doc.close()


def pdf_likely_needs_ocr(source: bytes | str | Path) -> bool:
    analyses = analyze_pdf_pages(source)
    if not analyses:
        return False
    suspicious_pages = sum(1 for analysis in analyses if analysis.likely_needs_ocr)
    return suspicious_pages >= max(1, len(analyses) // 2)


def _analyze_text_quality(text: str) -> tuple[int, int, float, float]:
    tokens = _TOKEN_PATTERN.findall(text)
    if not tokens:
        return 0, 0, 0.0, 0.0

    devanagari_char_count = len(_DEVANAGARI_PATTERN.findall(text))
    latin_tokens = [token for token in tokens if _LATIN_PATTERN.search(token)]
    if not latin_tokens:
        return len(tokens), devanagari_char_count, 0.0, 0.0

    suspicious_tokens = [
        token for token in latin_tokens if _SUSPICIOUS_LATIN_TOKEN_PATTERN.search(token)
    ]
    vowel_poor_tokens = [
        token for token in latin_tokens if _is_vowel_poor_latin_token(token)
    ]
    return (
        len(tokens),
        devanagari_char_count,
        len(suspicious_tokens) / len(latin_tokens),
        len(vowel_poor_tokens) / len(latin_tokens),
    )


def _is_vowel_poor_latin_token(token: str) -> bool:
    letters = [char for char in token if char.isalpha()]
    if len(letters) < 4:
        return False
    vowels = sum(char in "aeiouAEIOU" for char in letters)
    return vowels / len(letters) < 0.2
