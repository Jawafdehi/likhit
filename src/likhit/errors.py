"""Project-specific exceptions."""


class LikhitError(Exception):
    """Base exception for extraction failures."""


class ValidationError(LikhitError):
    """Raised when user input or extracted content is invalid."""


class ExtractionError(LikhitError):
    """Raised when document extraction fails."""


class ScannedPdfError(ExtractionError):
    """Raised when a PDF has no recoverable text layer and needs OCR.

    Signals that the document is a scanned raster (optionally carrying a
    non-embedded core-font "decoy" text layer that decodes to garbage) rather
    than born-digital text. Callers should catch this and route the document to
    their OCR path instead of storing the extraction output.

    ``needs_ocr_pages`` lists the 1-based page numbers that require OCR.
    """

    def __init__(self, message: str, needs_ocr_pages: list[int] | None = None) -> None:
        super().__init__(message)
        self.needs_ocr_pages = needs_ocr_pages or []
