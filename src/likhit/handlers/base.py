"""Document type handler abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod

from likhit.extractors.base import ExtractionStrategy, RawDocument
from likhit.models import ExtractionResult


class DocumentTypeHandler(ABC):
    """Coordinates extraction and document-specific normalization."""

    @abstractmethod
    def get_extraction_strategy(self) -> ExtractionStrategy:
        """Return the strategy used for the document type."""

    @abstractmethod
    def build_result(
        self, raw_document: RawDocument, metadata: dict[str, str | None]
    ) -> ExtractionResult:
        """Build a structured result from extracted text."""
