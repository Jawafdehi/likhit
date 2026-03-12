"""Markdown renderer for extracted documents."""

from __future__ import annotations

from collections import OrderedDict

import yaml

from likhit.models import ExtractionResult, Section
from likhit.renderers.base import OutputRenderer


def _render_section(section: Section) -> list[str]:
    parts: list[str] = []
    if section.heading:
        parts.append(f"{'#' * section.level} {section.heading}")
        parts.append("")
    parts.append(section.body)
    for subsection in section.subsections:
        parts.append("")
        parts.extend(_render_section(subsection))
    return parts


class MarkdownRenderer(OutputRenderer):
    """Render extraction results as Markdown with YAML frontmatter."""

    def render(self, result: ExtractionResult) -> str:
        frontmatter = OrderedDict(
            [
                ("title", result.title),
                ("doc_type", result.doc_type.value),
                ("likhit_version", result.likhit_version),
            ]
        )
        if result.publication_date:
            frontmatter["publication_date"] = result.publication_date
        if result.source_url:
            frontmatter["source_url"] = result.source_url

        body_lines: list[str] = []
        for index, section in enumerate(result.sections):
            if index:
                body_lines.append("")
            body_lines.extend(_render_section(section))

        frontmatter_text = yaml.safe_dump(
            dict(frontmatter),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        body = "\n".join(body_lines).strip()
        return f"---\n{frontmatter_text}\n---\n\n{body}\n"
