"""
markitdown-likhit plugin entry point.

Registers Nepal-specific converters with every MarkItDown instance when
enable_plugins=True.
"""

from typing import Any

from markitdown import MarkItDown

from likhit.converters.nepali_docx import LegacyWordConverter
from likhit.converters.nepali_pdf import NepaliPdfConverter

__plugin_interface_version__ = 1


def register_converters(markitdown: MarkItDown, **kwargs: Any) -> None:
    """Called once per MarkItDown instance when plugins are enabled."""
    del kwargs
    markitdown.register_converter(NepaliPdfConverter(), priority=-2.0)
    markitdown.register_converter(LegacyWordConverter(), priority=-2.0)
