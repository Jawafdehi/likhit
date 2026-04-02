"""Structure-aware handlers and routing helpers."""

from likhit.handlers.routing import convert_with_detected_structure
from likhit.handlers.single_column_notice import SingleColumnNoticeHandler
from likhit.handlers.structure_detection import detect_structure
from likhit.handlers.two_column_layout import TwoColumnLayoutHandler

__all__ = [
    "SingleColumnNoticeHandler",
    "TwoColumnLayoutHandler",
    "convert_with_detected_structure",
    "detect_structure",
]
