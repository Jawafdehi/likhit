"""Tests for the generated GitHub Pages benchmark artifact."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
from typing import Any

import fitz
import pytest

SITE_DIR = pathlib.Path(__file__).resolve().parents[1] / "site"
SPEC = importlib.util.spec_from_file_location(
    "likhit_site_generator", SITE_DIR / "generate.py"
)
assert SPEC and SPEC.loader
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)

RUNNER_SPEC = importlib.util.spec_from_file_location(
    "likhit_site_runner", SITE_DIR / "run_case.py"
)
assert RUNNER_SPEC and RUNNER_SPEC.loader
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


def _fake_run(
    source: pathlib.Path,
    run: dict[str, Any],
    _config: dict[str, Any],
    _timeout_s: int,
) -> tuple[dict[str, Any], str]:
    if source.stem == "legacy-then-english" and run.get("pages") == "2":
        text = "Ordinary English catalogue reference line one."
    else:
        text = (
            ("नेपाल सरकार डिजिटल अभिलेख परीक्षण प्रतिवेदन " * 30)
            + "\nThis is an ordinary born-digital paragraph."
            + "\nOrdinary English catalogue reference line one."
            + "\nProcurement Plan\nStatus\nApproved"
            + "\nQuarterly programme review."
        )
    return {
        "status": "ok",
        "text": text,
        "wall_s": 0.125,
        "max_rss_mb": 64.0,
    }, "OCR appears necessary for image-dominant pages."


def test_generator_writes_complete_synthetic_artifact(tmp_path: pathlib.Path) -> None:
    junit = tmp_path / "integration.xml"
    junit.write_text(
        """<?xml version="1.0"?>
<testsuites>
  <testsuite name="integration">
    <testcase classname="tests.integration" name="test_conversion" time="0.25"/>
    <testcase classname="tests.integration" name="test_optional_ocr" time="0.01">
      <skipped message="credentials not configured"/>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    output = tmp_path / "site"

    artifact = generator.generate(
        output,
        junit=junit,
        include_public=False,
        commit="a" * 40,
        ref="test",
        run_case=_fake_run,
    )

    assert artifact["summary"] == {
        "documents": 7,
        "runs": 14,
        "pass": 5,
        "fail": 0,
        "known_issue": 0,
        "blocked": 3,
        "reference": 6,
    }
    assert artifact["integration"]["status"] == "passed"
    assert artifact["integration"]["tests"] == 2
    assert artifact["integration"]["skipped"] == 1
    assert (output / "index.html").is_file()
    assert (output / ".nojekyll").is_file()
    assert (output / "data" / "schema.json").is_file()

    stored = json.loads((output / "data" / "results.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == 1
    assert len(stored["documents"]) == 7
    for document in stored["documents"]:
        assert document["privacy"] == "synthetic-pii-free"
        source = output / document["source"]["download"]
        assert source.is_file()
        assert (
            hashlib.sha256(source.read_bytes()).hexdigest()
            == document["source"]["sha256"]
        )
        for run in document["runs"]:
            transcript = output / run["transcript"]
            diagnostics = output / run["diagnostics"]
            assert transcript.is_file()
            assert diagnostics.is_file()
            assert (
                hashlib.sha256(transcript.read_bytes()).hexdigest()
                == run["transcript_sha256"]
            )
            assert (
                hashlib.sha256(diagnostics.read_bytes()).hexdigest()
                == run["diagnostics_sha256"]
            )


def test_generator_refuses_to_replace_repository_content(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    source_directory = repository / "site"
    source_directory.mkdir(parents=True)
    sentinel = source_directory / "generate.py"
    sentinel.write_text("must survive", encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", repository)

    with pytest.raises(ValueError, match="repository content"):
        generator._prepare_output(source_directory)

    assert sentinel.read_text(encoding="utf-8") == "must survive"


def test_public_catalog_is_hash_pinned_and_screened() -> None:
    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))
    public = [
        document
        for document in catalog["documents"]
        if document["origin"] == "public-institutional"
    ]

    assert {document["id"] for document in public} == {
        "economic-act-2083",
        "gcf-country-programme",
    }
    for document in public:
        assert document["privacy"] == "public-institutional"
        assert document["screening"]
        assert document["publish_pages"] == "1-3"
        assert document["sanitization"]
        assert document["url"].startswith("https://")
        assert len(document["sha256"]) == 64
        assert all(run.get("pages") for run in document["runs"])


def test_public_excerpt_removes_metadata_and_unselected_pages() -> None:
    source = fitz.open()
    try:
        for page_number in range(3):
            page = source.new_page()
            page.insert_text((72, 72), f"Public policy page {page_number + 1}")
        source.set_metadata({"author": "Private test author", "title": "Source"})
        data = source.tobytes()
    finally:
        source.close()

    excerpt_bytes = generator._sanitize_pdf_excerpt(data, "2")
    assert excerpt_bytes == generator._sanitize_pdf_excerpt(data, "2")
    with fitz.open(stream=excerpt_bytes, filetype="pdf") as excerpt:
        assert excerpt.page_count == 1
        assert "Public policy page 2" in excerpt[0].get_text()
        assert "Public policy page 1" not in excerpt[0].get_text()
        identifying_fields = {
            "title",
            "author",
            "subject",
            "keywords",
            "creator",
            "producer",
            "creationDate",
            "modDate",
        }
        assert not any(excerpt.metadata[field] for field in identifying_fields)


def test_public_download_retries_and_names_failed_url() -> None:
    spec = {
        "id": "public-sample",
        "kind": "pdf",
        "url": "https://example.com/public-sample.pdf",
        "sha256": "0" * 64,
    }
    calls: list[str] = []
    delays: list[float] = []

    def failing_open(_request: object, *, timeout: int) -> object:
        calls.append(f"attempt-{timeout}")
        raise urllib.error.URLError("temporarily unavailable")

    with pytest.raises(RuntimeError) as raised:
        generator._download_public_source(
            spec,
            None,
            attempts=3,
            open_url=failing_open,
            sleep=delays.append,
        )

    assert spec["url"] in str(raised.value)
    assert isinstance(raised.value.__cause__, urllib.error.URLError)
    assert calls == ["attempt-120"] * 3
    assert delays == [1.0, 2.0]


def test_isolated_runner_converts_generated_docx(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "unicode.docx"
    source.write_bytes(generator._build_unicode_docx())
    environment = os.environ.copy()
    environment["LIKHIT_MEM_CAP_GB"] = "invalid"

    completed = subprocess.run(
        [
            sys.executable,
            str(SITE_DIR / "run_case.py"),
            str(source),
            "--plugins",
            "enabled",
        ],
        capture_output=True,
        check=True,
        env=environment,
        timeout=30,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert "नेपाल सरकार" in payload["text"]
    assert "Quarterly programme" in payload["text"]


def test_isolated_runner_retries_short_writes() -> None:
    written = bytearray()

    def short_write(_file_descriptor: int, data: bytes | memoryview) -> int:
        chunk = bytes(data[:3])
        written.extend(chunk)
        return len(chunk)

    runner._write_all(1, b"complete result", write=short_write)

    assert written == b"complete result"


@pytest.mark.parametrize("progress", [0, -1])
def test_isolated_runner_rejects_zero_progress(progress: int) -> None:
    def stalled_write(_file_descriptor: int, _data: memoryview) -> int:
        return progress

    with pytest.raises(OSError, match="made no progress"):
        runner._write_all(1, b"complete result", write=stalled_write)


@pytest.mark.parametrize("value", [None, "", "invalid", "0", "-2"])
def test_isolated_runner_memory_cap_falls_back(value: str | None) -> None:
    assert runner._parse_memory_cap(value) == runner.DEFAULT_MEM_CAP_BYTES


def test_isolated_runner_clamps_memory_cap_to_hard_limit() -> None:
    class FakeResource:
        RLIMIT_DATA = 2
        RLIM_INFINITY = -1

        def __init__(self) -> None:
            self.applied: tuple[int, int] | None = None

        @staticmethod
        def getrlimit(_limit: int) -> tuple[int, int]:
            return (256, 1024)

        def setrlimit(self, _limit: int, limits: tuple[int, int]) -> None:
            self.applied = limits

    fake_resource = FakeResource()

    runner._apply_memory_cap(fake_resource, cap_bytes=2048)

    assert fake_resource.applied == (1024, 1024)


def test_isolated_runner_continues_when_memory_cap_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class RestrictedResource:
        RLIMIT_DATA = 2
        RLIM_INFINITY = -1

        @staticmethod
        def getrlimit(_limit: int) -> tuple[int, int]:
            return (256, 1024)

        @staticmethod
        def setrlimit(_limit: int, _limits: tuple[int, int]) -> None:
            raise OSError("restricted")

    runner._apply_memory_cap(RestrictedResource(), cap_bytes=512)

    assert "memory cap not applied: restricted" in capsys.readouterr().err
