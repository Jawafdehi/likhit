"""Tests for the generated GitHub Pages benchmark artifact."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
from typing import Any

import fitz
import pytest
import yaml

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


def _without_ocr_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the environment to "no OCR backend anywhere".

    Otherwise these assertions depend on whether the developer running them
    happens to have vision credentials or a local model server exported, and pass
    locally while failing in CI (or the reverse).
    """

    for name in (
        "MARKITDOWN_OCR_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GEMINI_API_KEY",
        "LIKHIT_LOCAL_OCR_BASE_URL",
        "LIKHIT_LOCAL_OCR_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_generator_writes_complete_synthetic_artifact(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_ocr_backends(monkeypatch)
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

    # Every document declares all three configurations, but with no OCR backend
    # available only the plain Likhit run of each executes -- the 14 OCR runs are
    # skipped, not failed.
    assert artifact["summary"] == {
        "documents": 7,
        "runs": 7,
        "pass": 3,
        "fail": 0,
        "known_issue": 1,
        "blocked": 3,
        "reference": 0,
        "skipped": 14,
    }
    assert artifact["integration"]["status"] == "passed"
    assert artifact["integration"]["tests"] == 2
    assert artifact["integration"]["skipped"] == 1
    assert (output / "index.html").is_file()
    assert (output / ".nojekyll").is_file()
    assert (output / generator.OUTPUT_MARKER).read_text(encoding="utf-8") == (
        generator.OUTPUT_MARKER_CONTENT
    )
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


def test_failed_regeneration_preserves_previous_site(tmp_path: pathlib.Path) -> None:
    output = tmp_path / "site"
    generator.generate(output, include_public=False, run_case=_fake_run)
    previous_results = (output / "data" / "results.json").read_bytes()

    def fail_run(
        _source: pathlib.Path,
        _run: dict[str, Any],
        _config: dict[str, Any],
        _timeout_s: int,
    ) -> tuple[dict[str, Any], str]:
        raise RuntimeError("injected conversion failure")

    with pytest.raises(RuntimeError, match="injected conversion failure"):
        generator.generate(output, include_public=False, run_case=fail_run)

    assert (output / "data" / "results.json").read_bytes() == previous_results


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


def test_generator_refuses_to_replace_repository_parent(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "parent" / "repository"
    repository.mkdir(parents=True)
    sentinel = repository / "must-survive.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", repository)

    with pytest.raises(ValueError, match="repository content"):
        generator._prepare_output(repository.parent)

    assert sentinel.read_text(encoding="utf-8") == "must survive"


def test_generator_refuses_to_replace_unowned_directory(
    tmp_path: pathlib.Path,
) -> None:
    output = tmp_path / "unrelated"
    output.mkdir()
    sentinel = output / "must-survive.txt"
    sentinel.write_text("must survive", encoding="utf-8")

    with pytest.raises(ValueError, match="unowned output"):
        generator._prepare_output(output)

    assert sentinel.read_text(encoding="utf-8") == "must survive"


def test_public_catalog_is_hash_pinned_and_described() -> None:
    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))
    public = [
        document
        for document in catalog["documents"]
        if document["origin"] == "public-institutional"
    ]

    assert {document["id"] for document in public} == {
        "ciaa-asset-declaration-guidance",
        "ciaa-corruption-study",
        "ciaa-detention-legacy-doc",
        "ciaa-earthquake-relief-docx",
        "ciaa-ebulletin-himali",
        "ciaa-news-article-notice",
        "ciaa-notice-image-only",
        "ciaa-notice-scanned",
        "ciaa-press-digest",
        "ciaa-press-release-tables",
        "ciaa-research-grant-notice",
        "economic-act-2083",
        "gcf-country-programme",
        "industry-annual-return-notice",
        "npc-press-note",
        "seed-rules-2081",
    }
    assert {document["kind"] for document in public} == {"pdf", "docx", "doc"}
    assert (
        sum(document["url"].startswith("https://ciaa.gov.np/") for document in public)
        == 11
    )
    excerpt_ids = {
        document["id"] for document in public if document.get("publish_pages")
    }
    assert excerpt_ids == {
        "ciaa-corruption-study",
        "ciaa-ebulletin-himali",
        "economic-act-2083",
        "gcf-country-programme",
        "seed-rules-2081",
    }
    for document in public:
        assert document["privacy"] == "public-institutional"
        assert document["content_note"]
        assert document["url"].startswith("https://")
        assert len(document["sha256"]) == 64
        if document.get("publish_pages"):
            # Every published sample stays under ten pages. Whole-file entries
            # cannot be checked offline (the source is not in the tree), so this
            # pins the excerpts, which are the ones cut from long sources.
            start, _, end = document["publish_pages"].partition("-")
            span = int(end or start) - int(start) + 1
            assert 1 <= span < 10, f"{document['id']} publishes {span} pages"
            assert document["sanitization"]
        else:
            assert not document.get("sanitization")
        # Page scoping belongs to the document, so the published file *is* the
        # excerpt and every run converts it whole. That is what keeps one run per
        # configuration instead of per-page run variants.
        assert all(not run.get("pages") for run in document["runs"])
        assert {run["config"] for run in document["runs"]} <= {
            "likhit",
            "likhit-ocr",
            "likhit-ocr-local",
        }


@pytest.mark.parametrize(
    "document_id",
    [
        "ciaa-news-article-notice",
        "ciaa-earthquake-relief-docx",
        "ciaa-detention-legacy-doc",
    ],
)
def test_public_source_cache_supports_catalog_formats(
    tmp_path: pathlib.Path, document_id: str
) -> None:
    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))
    document = next(item for item in catalog["documents"] if item["id"] == document_id)
    cached = tmp_path / f"{document['id']}.{document['kind']}"
    cached.write_bytes(b"cached public source")

    assert generator._cached_public_source(document, tmp_path) == cached


def test_replacement_character_check_reports_damage() -> None:
    result = generator._evaluate_check(
        {
            "kind": "max_replacement",
            "value": 0,
            "label": "No replacement-character corruption",
        },
        "",
        "",
        {"replacement": 970},
    )

    assert result == {
        "label": "No replacement-character corruption",
        "kind": "max_replacement",
        "passed": False,
        "detail": "970 replacement characters; maximum 0",
    }


@pytest.mark.parametrize(
    "kind",
    ["min_chars", "min_devanagari", "max_devanagari", "max_replacement"],
)
def test_metric_checks_fail_when_conversion_metrics_are_unavailable(kind: str) -> None:
    result = generator._evaluate_check(
        {"kind": kind, "value": 0, "label": "Metric check"},
        "",
        "",
        {},
    )

    assert result["passed"] is False
    assert "unavailable" in result["detail"]


@pytest.mark.parametrize(
    ("expectation", "checks", "expected_outcome"),
    [
        (
            "blocked",
            [
                {
                    "kind": "diagnostic_contains",
                    "value": "OCR",
                    "label": "OCR requirement reported",
                }
            ],
            "blocked",
        ),
        (
            "known_issue",
            [
                {
                    "kind": "max_replacement",
                    "value": 0,
                    "label": "No replacement characters",
                }
            ],
            "known-issue",
        ),
        ("reference", [], "reference"),
        ("pass", [], "fail"),
    ],
)
def test_non_ok_run_respects_declared_expectation(
    tmp_path: pathlib.Path,
    expectation: str,
    checks: list[dict[str, Any]],
    expected_outcome: str,
) -> None:
    output = tmp_path / expectation
    generator._prepare_output(output)

    def failed_run(
        _source: pathlib.Path,
        _run: dict[str, Any],
        _config: dict[str, Any],
        _timeout_s: int,
    ) -> tuple[dict[str, Any], str]:
        return {
            "status": "error",
            "text": "",
            "wall_s": 0.1,
            "max_rss_mb": 1.0,
            "exc_type": "ExtractionError",
            "exc_msg": "OCR is required",
        }, "OCR is required"

    result = generator._generate_run(
        {"id": "sample"},
        tmp_path / "sample.pdf",
        {
            "id": "failed-run",
            "config": "test",
            "expectation": expectation,
            "checks": checks,
        },
        {"test": {"label": "Test configuration"}},
        output,
        1,
        failed_run,
    )

    assert result["outcome"] == expected_outcome


def test_dashboard_exposes_inline_pdf_view_separately_from_download() -> None:
    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")

    assert "data-view-pdf" in app
    # The source document is rendered once per document, above the run selector,
    # rather than as a per-run tab: every configuration converts the same bytes.
    # Pin the write target and the absence of the tab, not merely the id string --
    # the "View PDF" handler also mentions the id, so a looser check does not bite.
    assert 'byId("document-source").innerHTML' in app
    assert 'byId("detail-body").innerHTML' in app  # the run views still use it
    assert 'state.tab === "source"' not in app
    assert 'data-tab="source"' not in index
    assert 'class="document-source"' in index
    assert "Source PDF preview" in app
    assert "Open in new tab" in app
    assert "First-page preview" in app
    assert "download>" in app
    assert '<option value="doc">Legacy Word (.doc)</option>' in index
    assert (
        'integrity="sha384-uTYyvsSSUZeaPhb5RbKlQa0zY/WpX/QHfvg2mczXyBQOpkWPEDy9lczyp+w7SKXu"'
        in index
    )
    assert 'crossorigin="anonymous"' in index


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


def test_public_download_rejects_oversized_response() -> None:
    data = b"oversized"
    spec = {
        "id": "public-sample",
        "kind": "pdf",
        "url": "https://example.com/public-sample.pdf",
        "sha256": hashlib.sha256(data).hexdigest(),
        "max_bytes": len(data) - 1,
    }

    def oversized_open(_request: object, *, timeout: int) -> io.BytesIO:
        assert timeout == 120
        return io.BytesIO(data)

    with pytest.raises(ValueError, match="download exceeds"):
        generator._download_public_source(spec, None, open_url=oversized_open)


def test_public_download_rejects_oversized_cached_source(
    tmp_path: pathlib.Path,
) -> None:
    data = b"oversized"
    spec = {
        "id": "public-sample",
        "kind": "pdf",
        "url": "https://example.com/public-sample.pdf",
        "sha256": hashlib.sha256(data).hexdigest(),
        "max_bytes": len(data) - 1,
    }
    (tmp_path / "public-sample.pdf").write_bytes(data)

    with pytest.raises(ValueError, match="cached source exceeds"):
        generator._download_public_source(spec, tmp_path)


def test_pages_deployment_requires_successful_main_branch_gate() -> None:
    workflow_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "benchmark-pages.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert workflow["jobs"]["gate"]["if"] == "always()"
    assert workflow["jobs"]["deploy"]["needs"] == ["build", "gate"]
    deploy_condition = workflow["jobs"]["deploy"]["if"]
    assert "github.ref == 'refs/heads/main'" in deploy_condition
    assert "needs.gate.result == 'success'" in deploy_condition


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


def test_catalog_exposes_likhit_plus_two_ocr_backends() -> None:
    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))
    configurations = catalog["configurations"]

    assert list(configurations) == ["likhit", "likhit-ocr", "likhit-ocr-local"]
    # Only the OCR configurations declare a backend requirement, and only those
    # can be skipped when the backend is absent.
    assert "requires" not in configurations["likhit"]
    assert configurations["likhit"]["environment"] == "no-ocr"
    assert configurations["likhit-ocr"]["requires"] == "ocr-api"
    assert configurations["likhit-ocr"]["environment"] == "default"
    assert configurations["likhit-ocr-local"]["requires"] == "ocr-local"
    assert configurations["likhit-ocr-local"]["environment"] == "ocr-local"

    for document in catalog["documents"]:
        ids = [run["id"] for run in document["runs"]]
        assert ids in (
            ["likhit"],
            ["likhit", "likhit-ocr", "likhit-ocr-local"],
        ), document["id"]


def test_ocr_backends_are_available_only_when_actually_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MARKITDOWN_OCR_MODEL",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "LIKHIT_LOCAL_OCR_BASE_URL",
        "LIKHIT_LOCAL_OCR_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    plain = {"label": "Likhit", "environment": "no-ocr"}
    hosted = {"label": "api", "environment": "default", "requires": "ocr-api"}
    local = {"label": "local", "environment": "ocr-local", "requires": "ocr-local"}

    # A configuration with no backend requirement always runs.
    assert generator._ocr_backend_available(plain) is True
    # Nothing configured: both OCR backends are unavailable, not failing.
    assert generator._ocr_backend_available(hosted) is False
    assert generator._ocr_backend_available(local) is False

    # A model alone is not enough for the hosted backend; a credential is needed.
    monkeypatch.setenv("MARKITDOWN_OCR_MODEL", "some-model")
    assert generator._ocr_backend_available(hosted) is False
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    assert generator._ocr_backend_available(hosted) is True

    # The local backend also requires that the server actually serve the model, so
    # a running-but-empty server does not produce a column of failures.
    monkeypatch.setenv("LIKHIT_LOCAL_OCR_BASE_URL", "http://localhost:1/v1")
    monkeypatch.setenv("LIKHIT_LOCAL_OCR_MODEL", "vision-model")
    monkeypatch.setattr(generator, "_local_ocr_models", lambda _base: frozenset())
    assert generator._ocr_backend_available(local) is False
    monkeypatch.setattr(
        generator, "_local_ocr_models", lambda _base: frozenset({"vision-model"})
    )
    assert generator._ocr_backend_available(local) is True


def test_unavailable_configurations_are_skipped_not_failed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_ocr_backends(monkeypatch)

    # The catalog declares all three configurations for every document, so the
    # PII-free synthetic fixtures already carry OCR runs to skip -- no fabrication
    # and no network needed.
    declared = generator._load_catalog()["documents"]
    synthetic_ocr_runs = sum(
        1
        for document in declared
        if document["origin"] == "synthetic"
        for run in document["runs"]
        if run["config"] != "likhit"
    )
    assert synthetic_ocr_runs, (
        "fixtures should declare OCR runs for this to mean anything"
    )

    artifact = generator.generate(
        tmp_path / "site", include_public=False, run_case=_fake_run
    )

    # Both OCR configurations are reported unavailable, with a reason naming what
    # is missing, and neither contributes a run -- least of all a failing one.
    configurations = artifact["configurations"]
    assert configurations["likhit"]["available"] is True
    assert configurations["likhit"]["unavailable_reason"] is None
    assert configurations["likhit-ocr"]["available"] is False
    assert "MARKITDOWN_OCR_MODEL" in configurations["likhit-ocr"]["unavailable_reason"]
    assert configurations["likhit-ocr-local"]["available"] is False
    assert (
        "LIKHIT_LOCAL_OCR_BASE_URL"
        in configurations["likhit-ocr-local"]["unavailable_reason"]
    )

    recorded = {
        run["config"] for document in artifact["documents"] for run in document["runs"]
    }
    assert recorded == {"likhit"}
    assert artifact["summary"]["skipped"] == synthetic_ocr_runs
    assert artifact["summary"]["fail"] == 0


def test_ocr_usage_reports_tokens_and_model_without_deriving_cost() -> None:
    before = {"calls": 4, "input_tokens": 1_000, "output_tokens": 100}
    after = {"calls": 9, "input_tokens": 3_000, "output_tokens": 700}

    record = generator._ocr_usage_record(before, after, "some-vision-model")

    # Tokens and calls only. No cost, and no price fields to derive one from:
    # vendor rates are not published for every model, so a built-in table would
    # silently produce wrong money.
    assert record == {
        "calls": 5,
        "input_tokens": 2_000,
        "output_tokens": 600,
        "total_tokens": 2_600,
        "model": "some-vision-model",
    }
    assert not [key for key in record if "cost" in key or "usd" in key]


def test_ocr_usage_is_absent_when_unknown_or_unspent() -> None:
    counters = {"calls": 2, "input_tokens": 10, "output_tokens": 5}

    # Endpoint unreachable at either edge: unknown, not zero.
    assert generator._ocr_usage_record(None, counters, "m") is None
    assert generator._ocr_usage_record(counters, None, "m") is None
    # No calls attributable to this run (e.g. a no-OCR configuration).
    assert generator._ocr_usage_record(counters, counters, "m") is None
    # Counters went backwards, so the endpoint restarted mid-build.
    assert generator._ocr_usage_record(counters, {**counters, "calls": 1}, "m") is None


def test_ocr_usage_endpoint_rejects_malformed_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __init__(self, body: str) -> None:
            self._body = body.encode("utf-8")

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    bodies = {
        "good": '{"calls": 1, "input_tokens": 2, "output_tokens": 3}',
        "missing": '{"calls": 1}',
        "negative": '{"calls": -1, "input_tokens": 2, "output_tokens": 3}',
        "wrong_type": '{"calls": "1", "input_tokens": 2, "output_tokens": 3}',
        "not_object": "[1, 2, 3]",
        "not_json": "<html>nope</html>",
    }
    current = {"body": bodies["good"]}
    monkeypatch.setattr(
        generator.urllib.request,
        "urlopen",
        lambda *_a, **_k: FakeResponse(current["body"]),
    )

    assert generator._read_ocr_usage("http://usage.invalid") == {
        "calls": 1,
        "input_tokens": 2,
        "output_tokens": 3,
    }
    for key in ("missing", "negative", "wrong_type", "not_object", "not_json"):
        current["body"] = bodies[key]
        assert generator._read_ocr_usage("http://usage.invalid") is None, key

    # No endpoint configured at all.
    assert generator._read_ocr_usage(None) is None
    assert generator._read_ocr_usage("") is None


def test_dashboard_shows_ocr_tokens_and_model_but_never_a_cost() -> None:
    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    schema = json.loads((SITE_DIR / "schema.json").read_text(encoding="utf-8"))

    assert "runCostBadge(run)" in app
    assert "OCR usage" in app
    assert "usage.model" in app
    assert "usage.total_tokens" in app
    # No currency anywhere in the dashboard: tokens are reported, cost is not.
    assert "formatUsd" not in app
    assert "cost_usd" not in app
    assert "USD" not in app

    usage = schema["$defs"]["run"]["properties"]["ocr_usage"]
    assert usage["type"] == ["object", "null"]
    assert set(usage["required"]) == {
        "calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "model",
    }
    assert "billing" not in schema["$defs"]["run"]["properties"]
    assert not [key for key in usage["properties"] if "cost" in key or "usd" in key]


def test_markdown_preview_toggle_renders_without_emitting_raw_html() -> None:
    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    # The toggle exists, defaults to the rendered preview, and both views are
    # reachable.
    assert 'transcriptView: "rendered"' in app
    assert 'data-view="rendered"' in app
    assert 'data-view="source"' in app
    assert "applyTranscriptView(transcript)" in app
    assert ".markdown-body" in styles
    assert ".view-toggle" in styles

    # Transcripts are machine output from untrusted third-party PDFs, so the
    # renderer must escape its input before constructing any markup. If this
    # ordering is ever inverted the preview becomes an injection sink.
    body = app[app.index("function renderMarkdown(") :]
    body = body[: body.index("\nfunction applyTranscriptView")]
    assert "escapeHtml(markdown).split" in body
    # Table markup is built by the renderer rather than passed through.
    assert 'class="md-table"' in body


def test_markdown_renderer_covers_the_constructs_likhit_emits() -> None:
    """The renderer is narrow by design; pin what it must handle.

    Likhit's transcripts of Nepali government documents are mostly headings,
    paragraphs and pipe tables, so those three are the ones that matter.
    """

    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    renderer = app[
        app.index("function renderInline(") : app.index("function applyTranscriptView")
    ]

    for construct in (
        "#{1,6}",  # headings
        "md-table",  # GFM pipe tables
        "blockquote",
        "<hr />",
        "md-code",  # fenced code
        "<strong>",
        "<em>",
        "<code>",
        "<li>",
    ):
        assert construct in renderer, construct

    # Links are restricted to http(s); javascript: and data: URLs are dropped
    # rather than rendered as anchors.
    assert "https?:" in renderer
    assert 'rel="noopener noreferrer"' in renderer


def test_source_pdf_opens_in_a_modal_dialog() -> None:
    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    # A native <dialog> is used, so Esc, focus trapping and the inert backdrop
    # come from the platform rather than hand-rolled key handling.
    assert '<dialog class="pdf-modal" id="pdf-modal"' in index
    assert "modal.showModal()" in app
    assert "openPdfModal(item)" in app
    assert "bindPdfModal();" in app
    assert ".pdf-modal::backdrop" in styles

    # Closing must release the PDF, otherwise a large document keeps rendering
    # behind the closed dialog.
    assert 'byId("pdf-modal-frame").src = "about:blank"' in app
    # The old behaviour -- expanding the inline panel -- must be gone.
    assert 'panel.classList.add("expanded")' not in app


def test_every_document_declares_every_configuration() -> None:
    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))
    configurations = list(catalog["configurations"])

    assert configurations == ["likhit", "likhit-ocr", "likhit-ocr-local"]
    for document in catalog["documents"]:
        assert [run["config"] for run in document["runs"]] == configurations, document[
            "id"
        ]


def test_configuration_may_raise_its_own_timeout(tmp_path: pathlib.Path) -> None:
    """A slow backend gets its own budget rather than the global default.

    A vision model served locally on CPU is far slower than a hosted API, and
    timing it out would record a Likhit failure for what is really a slow machine.
    """

    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))
    local = catalog["configurations"]["likhit-ocr-local"]

    assert local["timeout_s"] > 300, "local OCR needs more than the default budget"

    seen: list[int] = []

    def record_timeout(
        _source: pathlib.Path,
        _run: dict[str, Any],
        config: dict[str, Any],
        timeout_s: int,
    ) -> tuple[dict[str, Any], str]:
        seen.append(timeout_s)
        assert config is not None
        return {"status": "ok", "text": "x", "wall_s": 0.1, "max_rss_mb": 1.0}, ""

    spec = {"id": "d", "runs": [], "expectation": "pass"}
    run = {"id": "r", "config": "slow", "expectation": "pass", "checks": []}
    configurations = {
        "slow": {"label": "Slow", "environment": "no-ocr", "timeout_s": 999}
    }

    staging = tmp_path / "staging"
    (staging / "transcripts").mkdir(parents=True)
    (staging / "diagnostics").mkdir(parents=True)

    generator._generate_run(
        spec,
        pathlib.Path("unused.pdf"),
        run,
        configurations,
        staging,
        300,
        record_timeout,
    )

    assert seen == [999], "the configuration's own timeout must win over the default"


def test_configuration_labels_all_name_their_ocr_backend() -> None:
    """Every label says what OCR it uses, including the one that uses none.

    An unqualified "Likhit" next to "Likhit (with OCR)" reads as the default
    rather than as a distinct no-OCR configuration.
    """

    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))
    labels = {
        name: config["label"] for name, config in catalog["configurations"].items()
    }

    assert labels == {
        "likhit": "Likhit (no OCR)",
        "likhit-ocr": "Likhit (with OCR)",
        "likhit-ocr-local": "Likhit (offline OCR)",
    }
    # No label may be a bare product name; each must qualify its backend.
    for name, label in labels.items():
        assert label != "Likhit", name
        assert "(" in label and ")" in label, name


def test_every_css_variable_referenced_is_defined() -> None:
    """No rule may reference a custom property that does not exist.

    An undefined var() does not error -- it silently falls back to the initial
    value, so a colour becomes inherit and a background becomes transparent. The
    page still renders, which makes this class of typo easy to ship.
    """

    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"^\s+(--[a-z0-9-]+):", styles, re.MULTILINE))
    referenced = set(re.findall(r"var\((--[a-z0-9-]+)\)", styles))

    assert referenced, "expected the stylesheet to use custom properties"
    assert not (referenced - defined), sorted(referenced - defined)


def test_landing_page_introduces_likhit_above_the_benchmark() -> None:
    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")
    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    # The introduction precedes the results, not the other way round.
    assert index.index('class="intro"') < index.index('id="summary-strip"')
    assert index.index('id="benchmark-title"') < index.index('id="summary-strip"')

    # It says what Likhit is and how to install it, sourced from the README.
    assert "MarkItDown" in index
    assert "pip install likhit" in index
    assert "enable_plugins=True" in index
    assert "https://jawafdehi.org/" in index
    assert "https://pypi.org/project/likhit/" in index

    assert ".intro-features" in styles
    assert ".benchmark-intro" in styles
    # Copy-to-clipboard degrades honestly when the clipboard is unavailable.
    assert "bindCopyButtons();" in app
    assert "navigator.clipboard.writeText" in app
    assert "Press ⌘/Ctrl+C" in app


def test_page_scrolls_and_does_not_lock_content_to_the_viewport() -> None:
    """The page must scroll, now that the introduction sits above the dashboard.

    This was a real defect: the layout was a viewport-locked app shell
    (`body { overflow: hidden }`, `main { height: calc(100% - 58px) }`), so adding
    the introduction pushed the whole benchmark below the fold with no way to
    reach it. The page rendered fine in a screenshot, which is exactly why it
    shipped.
    """

    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    def rule(selector: str) -> str:
        """Every declaration that applies to a selector, across all its blocks.

        A selector can be styled by more than one rule (``html, body`` and a
        later ``body``), and the effective style is their union, so matching only
        the first block reads the wrong declarations.
        """

        blocks = re.findall(r"(?ms)^([^{}/@]+)\{([^}]*)\}", styles)
        return "\n".join(
            body
            for selectors, body in blocks
            if selector in [part.strip() for part in selectors.split(",")]
        )

    body = rule("body")
    assert "overflow: hidden" not in body, "body must not clip the page"
    assert "overflow-y: auto" in body

    # main must not be pinned to a fraction of the viewport.
    main = rule("main")
    assert "height: calc(100% -" not in main, "main must size to its content"

    # The workspace keeps a definite height so its panes scroll internally
    # instead of stretching the page to the longest transcript.
    workspace = rule(".workspace")
    assert "100vh" in workspace, "workspace needs a viewport-relative height"
    assert "min-height" in workspace

    # html/body may set a floor, never a ceiling.
    assert "min-height: 100%" in styles


def test_landing_page_documents_the_likhit_save_cli() -> None:
    """The helper CLI is the shortest path to using Likhit, so the page shows it.

    The commands are pinned against the README so the page cannot drift from the
    interface the package actually installs.
    """

    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")
    readme = (SITE_DIR.parent / "README.md").read_text(encoding="utf-8")

    assert "likhit-save" in index
    assert ".cli-examples" in styles

    # Each flag shown on the page must be one the README documents.
    for flag in ("--out", "--out-dir", "--pages"):
        assert flag in index, flag
        assert flag in readme, f"{flag} is shown on the page but absent from README"

    # The CLI section sits between the introduction and the benchmark.
    assert index.index('class="intro"') < index.index('id="cli-title"')
    assert index.index('id="cli-title"') < index.index('id="benchmark-title"')

    # Commands are copyable through the same handler as the install command.
    assert index.count("cli-copy-button") == 3


def test_generated_artifact_validates_against_its_published_schema(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The artifact must satisfy the schema shipped beside it.

    schema.json is published next to results.json as the contract for anyone
    consuming the benchmark, so asserting the schema's own contents is not
    enough -- the artifact has to actually conform, or the contract is fiction.
    """

    jsonschema = pytest.importorskip("jsonschema")
    _without_ocr_backends(monkeypatch)

    artifact = generator.generate(
        tmp_path / "site", include_public=False, run_case=_fake_run
    )
    schema = json.loads((SITE_DIR / "schema.json").read_text(encoding="utf-8"))

    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{'/'.join(str(part) for part in error.path)}: {error.message}"
        for error in validator.iter_errors(artifact)
    ]
    assert not errors, errors

    # And the copy written to disk, which is what consumers actually fetch.
    written = json.loads(
        (tmp_path / "site" / "data" / "results.json").read_text(encoding="utf-8")
    )
    assert not list(validator.iter_errors(written))


def test_ocr_accounting_is_scoped_to_the_backend_that_served_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each backend's tokens and model id are read from that backend's own source.

    Reading one ambient endpoint and one ambient model name would credit a locally
    served run with the hosted API's tokens and label it with the hosted model.
    """

    monkeypatch.setenv("LIKHIT_OCR_USAGE_URL", "http://hosted.invalid/usage")
    monkeypatch.setenv("MARKITDOWN_OCR_MODEL", "hosted-vision-model")
    monkeypatch.setenv("LIKHIT_LOCAL_OCR_USAGE_URL", "http://local.invalid/usage")
    monkeypatch.setenv("LIKHIT_LOCAL_OCR_MODEL", "local-vision-model")

    hosted = {"label": "api", "environment": "default", "requires": "ocr-api"}
    local = {"label": "local", "environment": "ocr-local", "requires": "ocr-local"}
    plain = {"label": "plain", "environment": "no-ocr"}

    assert generator._ocr_backend_accounting(hosted) == (
        "http://hosted.invalid/usage",
        "hosted-vision-model",
    )
    assert generator._ocr_backend_accounting(local) == (
        "http://local.invalid/usage",
        "local-vision-model",
    )
    # No backend means no counter to read and no model to name.
    assert generator._ocr_backend_accounting(plain) == (None, None)


def test_stylesheet_has_no_rules_for_classes_nothing_applies() -> None:
    """Guard against styling class names the application never sets.

    Dead rules outlive the behaviour they were written for -- the `expanded`
    state belonged to the inline source panel that the PDF modal replaced.
    """

    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")
    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    markup = app + index

    # State classes are toggled from JavaScript; a rule for one that is never
    # toggled is dead. Structural class names come from the markup.
    for state in ("expanded", "detail-open"):
        if f".{state}" in styles:
            assert state in markup, f".{state} is styled but never set"


def test_site_readme_documents_the_flags_and_configurations_that_exist() -> None:
    """The site README must describe the generator as it actually behaves.

    Stale documentation outlives the code that made it true, and this file is the
    first thing a contributor reads before running a build.
    """

    readme = (SITE_DIR / "README.md").read_text(encoding="utf-8")
    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))
    generate = (SITE_DIR / "generate.py").read_text(encoding="utf-8")

    # Every configuration id and label is described.
    for name, config in catalog["configurations"].items():
        assert name in readme, name
        assert config["label"] in readme, config["label"]

    # Every command-line flag the generator accepts is mentioned.
    flags = set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"', generate))
    assert flags, "expected to find generator flags"
    for flag in flags - {"--output", "--timeout", "--commit", "--ref"}:
        assert flag in readme, f"{flag} is accepted but undocumented"

    # Every environment variable the generator reads is documented.
    for variable in sorted(
        set(re.findall(r'environ\.get\("(LIKHIT_[A-Z_]+)"', generate))
    ):
        assert variable in readme, f"{variable} is read but undocumented"
