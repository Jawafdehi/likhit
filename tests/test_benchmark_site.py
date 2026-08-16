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
        "oag-banglachuli-audit-cover",
        "oag-rapti-audit-scope",
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
        "oag-banglachuli-audit-cover",
        "oag-rapti-audit-scope",
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

    result, recording = generator._generate_run(
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

    # The recording carries the raw stderr, not the composed diagnostic artifact,
    # so replaying it composes the error lines exactly once.
    assert recording["diagnostics"] == "OCR is required"
    assert recording["exc_type"] == "ExtractionError"


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


def test_ocr_usage_separates_an_unknown_spend_from_a_measured_zero() -> None:
    """ "Nobody was counting" and "it spent nothing" are different facts.

    Likhit only calls a vision model for pages a text layer cannot serve, so most
    documents spend nothing even with OCR configured. Collapsing that into the
    same null as an unreachable counter loses the more common and more
    interesting of the two, and the dashboard can then only draw a blank.
    """

    counters = {"calls": 2, "input_tokens": 10, "output_tokens": 5}

    # Endpoint unreachable at either edge: genuinely unknown.
    assert generator._ocr_usage_record(None, counters, "m") is None
    assert generator._ocr_usage_record(counters, None, "m") is None
    # Counters went backwards, so the endpoint restarted mid-build.
    assert generator._ocr_usage_record(counters, {**counters, "calls": 1}, "m") is None

    # Unchanged counters mean this run made no call -- a measurement, reported as
    # zero, and still carrying the model it would have used.
    idle = generator._ocr_usage_record(counters, counters, "m")
    assert idle == {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model": "m",
    }


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

    assert "OCR usage" in app
    assert "usage.total_tokens" in app
    # The model is resolved per configuration, with the per-run usage record only
    # as a fallback -- a run that made no call still has to be able to name it.
    assert "runModel(run)" in app
    assert "run.ocr_usage?.model" in app
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


# --------------------------------------------------------------------------- #
# Recorded snapshots
#
# CI cannot measure this benchmark: a runner has no vision backend, and the full
# corpus takes far longer than a Pages build. So a full local run is recorded and
# replayed. These tests cover the property that makes that safe -- a replay is
# indistinguishable from the run it recorded -- and the provenance that keeps it
# honest.
# --------------------------------------------------------------------------- #

SUMMARIZE_SPEC = importlib.util.spec_from_file_location(
    "likhit_site_summarize", SITE_DIR / "summarize.py"
)
assert SUMMARIZE_SPEC and SUMMARIZE_SPEC.loader
summarize = importlib.util.module_from_spec(SUMMARIZE_SPEC)
SUMMARIZE_SPEC.loader.exec_module(summarize)

RECORDED_COMMIT = "a" * 40


def _synthetic_snapshot() -> dict[str, Any]:
    """A recording covering every configuration of the PII-free synthetic corpus.

    Hand-built rather than measured, so a test can pose the exact situation CI is
    in: a machine with no OCR backend at all, replaying a recording that has all
    three.
    """

    catalog = generator._load_catalog()
    runs: dict[str, Any] = {}
    for spec in catalog["documents"]:
        if spec["origin"] != "synthetic":
            continue
        for run in spec["runs"]:
            runs[generator._snapshot_key(spec["id"], run["id"])] = {
                "status": "ok",
                "text": "नेपाल सरकार डिजिटल अभिलेख " * 20,
                "diagnostics": "recorded stderr",
                "wall_s": 1.5,
                "max_rss_mb": 80.0,
                "exc_type": None,
                "exc_msg": None,
                "traceback": None,
                "ocr_usage": (
                    None
                    if run["config"] == "likhit"
                    else {
                        "calls": 2,
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "total_tokens": 120,
                        "model": "recorded-vision-model",
                    }
                ),
            }
    return {
        "snapshot_version": generator.SNAPSHOT_VERSION,
        "recorded_at": "2026-08-01T00:00:00+00:00",
        "build": {
            "commit": RECORDED_COMMIT,
            "ref": "main",
            "python": "3.12.0",
            "likhit": "0.1.8",
            "markitdown": "0.1.7",
        },
        "configurations": {
            name: {"label": config["label"], "available": True}
            for name, config in catalog["configurations"].items()
        },
        "runs": runs,
    }


def _write_snapshot(path: pathlib.Path, snapshot: dict[str, Any]) -> pathlib.Path:
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return path


def _unreachable_run_case(
    _source: pathlib.Path,
    _run: dict[str, Any],
    _config: dict[str, Any],
    _timeout_s: int,
) -> tuple[dict[str, Any], str]:
    raise AssertionError("a replayed build must not convert anything")


def _fake_run_including_a_failure(
    source: pathlib.Path,
    run: dict[str, Any],
    config: dict[str, Any],
    timeout_s: int,
) -> tuple[dict[str, Any], str]:
    """Like `_fake_run`, but one document raises.

    A run that fails is the case where the diagnostic artifact is *composed* --
    raw stderr plus the error line plus the traceback. A corpus of successes only
    would let a replay that re-composes that text pass unnoticed, because with
    nothing to append composition is the identity function.
    """

    if source.stem == "pure-scan":
        return {
            "status": "error",
            "text": "",
            "wall_s": 0.2,
            "max_rss_mb": 12.0,
            "exc_type": "ExtractionError",
            "exc_msg": "No extractable text found in PDF.",
            "traceback": "Traceback (most recent call last):\n  ...",
        }, "OCR appears necessary, but OCR is not configured."
    return _fake_run(source, run, config, timeout_s)


def _run_index(artifact: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    fields = (
        "label",
        "outcome",
        "status",
        "wall_s",
        "max_rss_mb",
        "metrics",
        "ocr_usage",
        "checks",
        "transcript_sha256",
        "diagnostics_sha256",
        "error",
        "excerpt",
    )
    return {
        (document["id"], run["id"]): {field: run[field] for field in fields}
        for document in artifact["documents"]
        for run in document["runs"]
    }


def test_snapshot_replay_reproduces_the_recorded_build_exactly(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replaying a recording must be indistinguishable from the run it recorded.

    This is the property the whole mechanism rests on. If a replay drifts -- a
    recomposed diagnostic, a re-derived metric, a lost timing -- then the
    published page is not the measurement it claims to be.
    """

    _without_ocr_backends(monkeypatch)
    snapshot = tmp_path / "snapshot.json"

    live = generator.generate(
        tmp_path / "live",
        include_public=False,
        run_case=_fake_run_including_a_failure,
        write_snapshot=snapshot,
    )
    replayed = generator.generate(
        tmp_path / "replay",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
    )

    assert live["summary"] == replayed["summary"]
    assert _run_index(live) == _run_index(replayed)

    # The corpus has to contain a run whose diagnostics were composed, or the
    # comparison below cannot catch a replay that composes them a second time.
    errored = [
        run
        for document in live["documents"]
        for run in document["runs"]
        if run["error"]
    ]
    assert errored, "expected the fixture to produce a failing run"

    # Down to the bytes of the published artifacts, not just their recorded
    # hashes -- the diagnostics file is composed from the raw stderr plus the
    # error lines, and composing it twice is the obvious way to get this wrong.
    for directory in ("transcripts", "diagnostics"):
        live_files = sorted((tmp_path / "live" / directory).iterdir())
        replayed_files = sorted((tmp_path / "replay" / directory).iterdir())
        assert [path.name for path in live_files] == [
            path.name for path in replayed_files
        ]
        for original, copy in zip(live_files, replayed_files, strict=True):
            assert original.read_bytes() == copy.read_bytes(), original.name


def test_snapshot_publishes_ocr_configurations_where_no_backend_exists(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason the snapshot exists: CI has no vision backend, yet must publish.

    A replay takes availability from the recording, not from the machine doing
    the replaying. Deriving it from the environment here would silently drop the
    two OCR columns -- exactly the measurements CI cannot make for itself.
    """

    _without_ocr_backends(monkeypatch)
    snapshot = _write_snapshot(tmp_path / "snapshot.json", _synthetic_snapshot())

    artifact = generator.generate(
        tmp_path / "site",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
    )

    published = {
        run["config"] for document in artifact["documents"] for run in document["runs"]
    }
    assert published == {"likhit", "likhit-ocr", "likhit-ocr-local"}
    assert artifact["summary"]["skipped"] == 0
    for config in artifact["configurations"].values():
        assert config["available"] is True
        assert config["unavailable_reason"] is None

    # Token usage is the recording's, not a live counter read against a backend
    # that is not even configured here.
    hosted = [
        run["ocr_usage"]
        for document in artifact["documents"]
        for run in document["runs"]
        if run["config"] == "likhit-ocr"
    ]
    assert hosted and all(
        usage and usage["model"] == "recorded-vision-model" for usage in hosted
    )


def test_snapshot_run_absent_from_the_recording_is_skipped_and_named(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalog that outgrew its recording must say so, not quietly shrink.

    Adding a document without re-recording leaves runs with no measurement. The
    only honest options are to skip them and report it, or to invent numbers.
    """

    _without_ocr_backends(monkeypatch)
    data = _synthetic_snapshot()
    dropped = sorted(data["runs"])[0]
    del data["runs"][dropped]
    snapshot = _write_snapshot(tmp_path / "snapshot.json", data)

    artifact = generator.generate(
        tmp_path / "site",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
    )

    assert artifact["measured"]["missing_runs"] == [dropped]
    assert artifact["summary"]["skipped"] == 1
    published = {
        generator._snapshot_key(document["id"], run["id"])
        for document in artifact["documents"]
        for run in document["runs"]
    }
    assert dropped not in published

    # The job summary has to surface it too, or the warning dies in the artifact.
    # This is the one provenance case that still warns -- an older recorded commit
    # is the normal case and deliberately does not -- so prove it warns.
    rendered = summarize.render(artifact)
    assert dropped in rendered
    assert "[!WARNING]" in rendered


def test_snapshot_configuration_absent_from_the_recording_is_named_not_just_skipped(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole configuration the recording predates must be as visible as one run.

    A configuration missing from the recording resolves to unavailable, and an
    unavailable configuration's runs are dropped ahead of the lookup that names an
    uncovered run. Left alone, that makes `skipped` rise with nothing to explain
    it -- and pins the blame on absent credentials, which is a claim about this
    machine rather than about the gap in the recording.
    """

    _without_ocr_backends(monkeypatch)
    data = _synthetic_snapshot()
    dropped = "likhit-ocr-local"
    del data["configurations"][dropped]
    expected = sorted(key for key in data["runs"] if key.endswith(f"--{dropped}"))
    assert expected, "the fixture must record runs of the configuration it drops"
    snapshot = _write_snapshot(tmp_path / "snapshot.json", data)

    artifact = generator.generate(
        tmp_path / "site",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
    )

    assert artifact["measured"]["missing_runs"] == expected
    assert artifact["summary"]["skipped"] == len(expected)

    config = artifact["configurations"][dropped]
    assert config["available"] is False
    assert config["unavailable_reason"] == "not covered by the recording"

    # A configuration the recording *does* cover as unavailable keeps its own
    # reason, so the two cases stay distinguishable rather than both reading as
    # a recording gap.
    data = _synthetic_snapshot()
    data["configurations"][dropped]["available"] = False
    artifact = generator.generate(
        tmp_path / "site2",
        include_public=False,
        snapshot=_write_snapshot(tmp_path / "snapshot2.json", data),
        run_case=_unreachable_run_case,
    )
    recorded_unavailable = artifact["configurations"][dropped]["unavailable_reason"]
    assert recorded_unavailable != "not covered by the recording"
    assert "LIKHIT_LOCAL_OCR" in recorded_unavailable
    assert artifact["measured"]["missing_runs"] == []


def test_generate_refuses_to_record_a_replay(tmp_path: pathlib.Path) -> None:
    """Replaying while recording would launder a recording as a fresh measurement.

    The CLI's mutually-exclusive group covers the command line only. Held in
    `generate` too, the invariant survives every other caller -- otherwise the
    written snapshot would carry the replaying build's commit and a new
    `recorded_at` over numbers nothing re-measured.
    """

    snapshot = _write_snapshot(tmp_path / "snapshot.json", _synthetic_snapshot())
    with pytest.raises(ValueError, match="mutually exclusive"):
        generator.generate(
            tmp_path / "site",
            include_public=False,
            snapshot=snapshot,
            write_snapshot=tmp_path / "out.json",
            run_case=_unreachable_run_case,
        )
    assert not (tmp_path / "out.json").exists()


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda data: data.update(snapshot_version=99), "snapshot_version"),
        (lambda data: data.pop("runs"), "runs"),
        (lambda data: data.pop("configurations"), "configurations"),
        (lambda data: data.pop("build"), "build"),
        (lambda data: data.update(runs="not-a-mapping"), "runs"),
        # A run recorded as something other than an object: `_replayed_payload`
        # calls `.get` on it, so without this check the failure surfaces as an
        # AttributeError mid-build instead of naming the file and the key.
        (
            lambda data: data["runs"].update(
                {next(iter(data["runs"])): "not-an-object"}
            ),
            "not an object",
        ),
    ],
)
def test_snapshot_refuses_a_shape_it_cannot_replay(
    tmp_path: pathlib.Path,
    mutate: Any,
    expected: str,
) -> None:
    """Refuse an unreadable recording rather than replaying part of it.

    A snapshot is committed data that outlives the code that wrote it. Silently
    treating a future version as empty would publish a page of zero runs.
    """

    data = _synthetic_snapshot()
    mutate(data)
    snapshot = _write_snapshot(tmp_path / "snapshot.json", data)

    with pytest.raises(ValueError, match=expected):
        # `run_case` is supplied so that a regression in *when* validation runs
        # shows up as this assertion failing, not as a slow real conversion of
        # the whole synthetic corpus before the same failure.
        generator.generate(
            tmp_path / "site",
            include_public=False,
            snapshot=snapshot,
            run_case=_unreachable_run_case,
        )


def test_replayed_artifact_keeps_measured_provenance_apart_from_the_build(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two commits, two meanings: one published the page, one produced the numbers.

    Collapsing them would present a recording as a fresh measurement, which is
    the single most misleading thing this feature could do.
    """

    _without_ocr_backends(monkeypatch)
    snapshot = _write_snapshot(tmp_path / "snapshot.json", _synthetic_snapshot())

    artifact = generator.generate(
        tmp_path / "site",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
        commit="b" * 40,
        ref="feature",
    )

    assert artifact["build"]["commit"] == "b" * 40
    assert artifact["measured"]["build"]["commit"] == RECORDED_COMMIT
    assert artifact["measured"]["recorded_at"] == "2026-08-01T00:00:00+00:00"
    assert artifact["measured"]["stale"] is True, (
        "a recording from another commit must be recorded as such"
    )

    # Same commit, same numbers: nothing to warn about.
    current = generator.generate(
        tmp_path / "current",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
        commit=RECORDED_COMMIT,
    )
    assert current["measured"]["stale"] is False

    # A build that measured its own numbers claims no recorded provenance at all.
    live = generator.generate(
        tmp_path / "live", include_public=False, run_case=_fake_run
    )
    assert live["measured"] is None


def test_committed_snapshot_covers_every_published_run_and_configuration() -> None:
    """The committed recording must cover what the deployed site publishes.

    CI replays this file. A recording that misses runs, or that records a backend
    as unavailable, degrades the published page -- and does so quietly, because a
    skipped run is not a failure.
    """

    snapshot = json.loads((SITE_DIR / "snapshot.json").read_text(encoding="utf-8"))
    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))

    assert snapshot["snapshot_version"] == generator.SNAPSHOT_VERSION

    # The deployed build passes --skip-synthetic, so the real documents are what
    # has to be covered.
    required = {
        generator._snapshot_key(spec["id"], run["id"])
        for spec in catalog["documents"]
        if spec["origin"] != "synthetic"
        for run in spec["runs"]
    }
    assert required, "expected the catalog to publish real documents"
    assert not sorted(required - set(snapshot["runs"])), sorted(
        required - set(snapshot["runs"])
    )

    for name in catalog["configurations"]:
        assert name in snapshot["configurations"], name
        assert snapshot["configurations"][name]["available"] is True, (
            f"{name} is recorded as unavailable, so replaying publishes no runs "
            f"for it -- re-record on a machine where its backend works"
        )

    for key, run in snapshot["runs"].items():
        assert set(run) >= {
            "status",
            "text",
            "diagnostics",
            "wall_s",
            "max_rss_mb",
            "ocr_usage",
        }, key


def test_benchmark_workflow_replays_the_snapshot_and_reports_the_numbers() -> None:
    """CI must publish recorded numbers, and surface them on the run itself."""

    workflow = yaml.safe_load(
        (SITE_DIR.parent / ".github" / "workflows" / "benchmark-pages.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = workflow["jobs"]["build"]["steps"]
    named = {step.get("name"): step for step in steps}

    assert "--snapshot site/snapshot.json" in named["Generate Pages artifact"]["run"]

    summary = named["Publish benchmark numbers"]
    assert "site/summarize.py" in summary["run"]
    assert "GITHUB_STEP_SUMMARY" in summary["run"]
    # The numbers are most wanted precisely when the gate failed.
    assert summary["if"] == "always()"


def test_job_summary_names_both_commits_without_warning_about_the_older_one(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Actions summary is where a regression gets noticed, so it must be legible.

    The recorded commit is older than the published one on effectively every
    deploy -- committing a recording creates a commit later than the one it was
    recorded on -- so warning about it would fire every time and train readers to
    scroll past the warnings that matter. It is named, not flagged.
    """

    _without_ocr_backends(monkeypatch)
    snapshot = _write_snapshot(tmp_path / "snapshot.json", _synthetic_snapshot())
    artifact = generator.generate(
        tmp_path / "site",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
        commit="b" * 40,
    )

    rendered = summarize.render(artifact)

    for config in artifact["configurations"].values():
        assert config["label"] in rendered, config["label"]
    assert f"{artifact['summary']['runs']} runs" in rendered
    assert RECORDED_COMMIT[:8] in rendered, "the measured commit must be named"
    assert "b" * 8 in rendered, "the publishing commit must be named too"
    assert artifact["measured"]["stale"] is True
    assert "[!WARNING]" not in rendered, (
        "an older recorded commit is the normal case and must not warn"
    )

    # A build that measured its own numbers says so, and warns about nothing.
    live = generator.generate(
        tmp_path / "live", include_public=False, run_case=_fake_run
    )
    live_summary = summarize.render(live)
    assert "Measured by this build" in live_summary
    assert "[!WARNING]" not in live_summary


def test_job_summary_keeps_a_measured_zero_apart_from_an_uncounted_one() -> None:
    """The distinction the dashboard makes has to hold in the Actions summary too.

    Likhit adds an OCR candidate only for pages a text layer cannot serve, so most
    documents spend nothing even with a backend configured -- `calls: 0` is the
    common case. Reporting it the way an unreachable counter is reported would make
    the ordinary result indistinguishable from lost measurement, in the one place a
    reviewer actually looks.
    """

    def artifact(usage: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "configurations": {
                "likhit-ocr": {
                    "label": "Likhit (with OCR)",
                    "available": True,
                    "model": "recorded-vision-model",
                }
            },
            "documents": [
                {
                    "runs": [
                        {"config": "likhit-ocr", "outcome": "pass", "ocr_usage": usage}
                    ]
                }
            ],
        }

    measured_zero = summarize._configuration_rows(
        artifact(
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "model": "recorded-vision-model",
            }
        )
    )[0]
    uncounted = summarize._configuration_rows(artifact(None))[0]

    assert measured_zero != uncounted
    assert "0 vision call(s), 0 tokens" in measured_zero
    assert "not recorded" in uncounted

    # A configuration with no vision model has no usage to report and reports none,
    # rather than reading as a counter that failed.
    no_ocr = artifact(None)
    no_ocr["configurations"]["likhit-ocr"]["model"] = None
    assert "not recorded" not in summarize._configuration_rows(no_ocr)[0]


def test_job_summary_survives_a_build_that_produced_no_artifact(
    tmp_path: pathlib.Path,
) -> None:
    """It runs with `if: always()`, so the artifact may not exist.

    Failing here would replace the real error on the run page with a traceback
    about a missing file.
    """

    assert summarize.main([str(tmp_path / "absent.json")]) == 0


def test_replayed_provenance_lives_in_run_metadata() -> None:
    """Replayed provenance lives in run metadata, not in a global banner.

    The banner above the summary was retired by design -- it repeated what the
    Metadata tab records per run. What must never regress is the attribution:
    run metadata names the environment that performed the conversion -- the
    recorded one -- and separately names the publishing build.
    """

    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="measured-banner"' not in index, "the banner must stay retired"

    # Run metadata describes the conversion, so it must name the environment that
    # performed it -- the recorded one -- not the environment that built the page.
    assert "measured?.build ?? state.data.build" in app
    assert "measured.recorded_at" in app
    assert "Published from" in app


def test_landing_page_ocr_recipes_use_only_variables_likhit_reads() -> None:
    """Every variable the page tells people to export must be one Likhit honours.

    Setup instructions that name a variable nothing reads fail silently: OCR
    simply never engages, and the page is the reason.
    """

    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")
    converter = (
        SITE_DIR.parent / "src" / "likhit" / "converters" / "nepali_pdf.py"
    ).read_text(encoding="utf-8")

    section = index[index.index('id="ocr-title"') : index.index('id="benchmark-title"')]
    exported = set(re.findall(r"export ([A-Z0-9_]+)=", section))
    assert exported, "expected the page to show exportable variables"
    for name in exported:
        assert name in converter, (
            f"{name} is shown on the page but Likhit never reads it"
        )

    # All three backends the benchmark measures are documented, local first.
    assert "ollama" in section.lower()
    assert "bedrock" in section.lower()
    assert "GEMINI_API_KEY" in section

    # Bedrock does not speak the OpenAI API, so the recipe must show a gateway
    # rather than implying Likhit can reach it directly.
    assert "litellm" in section.lower()
    assert "OPENAI_BASE_URL" in section

    assert ".ocr-recipes" in styles
    assert section.count("data-copy-block") == 3
    assert index.index('id="cli-title"') < index.index('id="ocr-title"')


def test_measured_is_always_emitted_but_optional_in_the_published_schema(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`measured` is a schema addition, so it must not invalidate older artifacts.

    schema.json ships beside results.json as the published contract. Making a new
    field *required* breaks validation of every artifact produced before it
    existed, for no gain -- the generator emitting it is what callers rely on, and
    that is asserted here rather than in the schema.
    """

    jsonschema = pytest.importorskip("jsonschema")
    _without_ocr_backends(monkeypatch)
    schema = json.loads((SITE_DIR / "schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    live = generator.generate(
        tmp_path / "live", include_public=False, run_case=_fake_run
    )
    assert "measured" in live, "the generator must always emit the field"
    assert live["measured"] is None
    assert not list(validator.iter_errors(live))

    snapshot = _write_snapshot(tmp_path / "snapshot.json", _synthetic_snapshot())
    replayed = generator.generate(
        tmp_path / "replay",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
    )
    assert not list(validator.iter_errors(replayed))

    # An artifact from before the field existed still validates.
    legacy = {key: value for key, value in live.items() if key != "measured"}
    assert not list(validator.iter_errors(legacy))


def test_configuration_records_the_model_it_ran_against(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model id belongs to the configuration, not to a usage record.

    Most runs make no vision call, so a per-run usage record is absent for them --
    yet the model those runs were configured against is exactly what a reader
    wants named. Recording it per configuration makes it reportable for every run,
    including a backend with no token counter at all.
    """

    _without_ocr_backends(monkeypatch)
    monkeypatch.setenv("MARKITDOWN_OCR_MODEL", "hosted-vision-model")
    monkeypatch.setenv("OPENAI_API_KEY", "hosted-key")
    monkeypatch.setenv("LIKHIT_LOCAL_OCR_MODEL", "local-vision-model")

    catalog = generator._load_catalog()["configurations"]
    assert generator._ocr_backend_accounting(catalog["likhit-ocr"])[1] == (
        "hosted-vision-model"
    )
    assert generator._ocr_backend_accounting(catalog["likhit-ocr-local"])[1] == (
        "local-vision-model"
    )
    # A configuration that uses no OCR has no model, rather than inheriting the
    # ambient one from whichever backend happens to be exported.
    assert generator._ocr_backend_accounting(catalog["likhit"])[1] is None

    artifact = generator.generate(
        tmp_path / "site", include_public=False, run_case=_fake_run
    )
    assert artifact["configurations"]["likhit"]["model"] is None
    assert artifact["configurations"]["likhit-ocr"]["model"] == "hosted-vision-model"


def test_replayed_configuration_model_comes_from_the_recording(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replay must name the recorded model, not the replaying machine's.

    CI exports no model at all, so deriving it from the environment would blank
    the field on exactly the build that publishes it.
    """

    _without_ocr_backends(monkeypatch)
    data = _synthetic_snapshot()
    data["configurations"]["likhit-ocr"]["model"] = "recorded-hosted-model"
    data["configurations"]["likhit-ocr-local"]["model"] = "recorded-local-model"
    snapshot = _write_snapshot(tmp_path / "snapshot.json", data)

    artifact = generator.generate(
        tmp_path / "site",
        include_public=False,
        snapshot=snapshot,
        run_case=_unreachable_run_case,
    )

    configurations = artifact["configurations"]
    assert configurations["likhit-ocr"]["model"] == "recorded-hosted-model"
    assert configurations["likhit-ocr-local"]["model"] == "recorded-local-model"
    assert configurations["likhit"]["model"] is None


def test_snapshot_carries_the_model_of_every_available_ocr_configuration() -> None:
    """The committed recording must name each OCR backend's model.

    Without it the published page cannot answer "which model produced this
    column", which is the whole point of showing the configurations side by side.
    """

    snapshot = json.loads((SITE_DIR / "snapshot.json").read_text(encoding="utf-8"))
    catalog = json.loads((SITE_DIR / "catalog.json").read_text(encoding="utf-8"))

    for name, config in catalog["configurations"].items():
        recorded = snapshot["configurations"][name]
        if not config.get("requires"):
            assert recorded.get("model") is None, (
                f"{name} uses no OCR, so it must not claim a model"
            )
            continue
        if not recorded["available"]:
            continue
        assert recorded.get("model"), (
            f"{name} was recorded as available but names no model -- re-record "
            f"with {config['requires']} configured"
        )


def test_model_and_token_usage_live_only_in_the_metadata_tab() -> None:
    """The vision model and its spend are metadata, not detail-pane chrome.

    They previously rendered twice outside the Metadata tab -- a persistent
    caption under the configuration selector and a token chip inside each
    selector button -- duplicating the tab and crowding the header. The facts
    stay; the caption and chips must not come back.
    """

    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    app = (SITE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="run-backend"' not in index, "the caption line must stay retired"
    assert "runCostBadge" not in app, "the selector chips must stay retired"

    # The Metadata tab still carries the full story.
    assert "renderOcrUsageBlock(run)" in app
    # The model is read per configuration, so it survives a run with no usage.
    assert "configurationOf(run).model" in app
    # A run whose spend nobody counted stays distinct from one that spent zero.
    assert "not recorded for this run" in app
    assert "without calling the model" in app


# --------------------------------------------------------------------------- #
# Token counting proxy
# --------------------------------------------------------------------------- #

PROXY_SPEC = importlib.util.spec_from_file_location(
    "likhit_ocr_usage_proxy", SITE_DIR / "ocr_usage_proxy.py"
)
assert PROXY_SPEC and PROXY_SPEC.loader
usage_proxy = importlib.util.module_from_spec(PROXY_SPEC)
PROXY_SPEC.loader.exec_module(usage_proxy)


def test_usage_proxy_serves_the_shape_the_generator_reads() -> None:
    """The counter must be readable by `_read_ocr_usage`, not merely well-formed.

    The two live at opposite ends of a plain HTTP contract, so a renamed field
    would leave every run silently reporting "usage unknown".
    """

    counter = usage_proxy.Counter()
    counter.record({"prompt_tokens": 100, "completion_tokens": 20})
    counter.record({"input_tokens": 5, "output_tokens": 1})

    snapshot = counter.snapshot()
    assert snapshot == {"calls": 2, "input_tokens": 105, "output_tokens": 21}
    assert set(snapshot) == {"calls", "input_tokens", "output_tokens"}


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        # Both spellings, because gateways differ.
        ({"prompt_tokens": 7, "completion_tokens": 3}, (7, 3)),
        ({"input_tokens": 7, "output_tokens": 3}, (7, 3)),
        # Absent, malformed, negative and boolean values count as zero rather
        # than crashing the proxy mid-conversion.
        ({}, (0, 0)),
        ({"prompt_tokens": "7"}, (0, 0)),
        ({"prompt_tokens": -7}, (0, 0)),
        ({"prompt_tokens": True}, (0, 0)),
    ],
)
def test_usage_proxy_reads_either_token_spelling(
    usage: dict[str, Any], expected: tuple[int, int]
) -> None:
    counter = usage_proxy.Counter()
    counter.record(usage)
    snapshot = counter.snapshot()
    assert (snapshot["input_tokens"], snapshot["output_tokens"]) == expected


def test_usage_proxy_counts_only_successful_completions() -> None:
    """An upstream failure spent no tokens, so it must not inflate the count."""

    assert usage_proxy._usage_of(b'{"usage": {"prompt_tokens": 1}}') == {
        "prompt_tokens": 1
    }
    # A body with no usage block, and one that is not JSON at all.
    assert usage_proxy._usage_of(b'{"choices": []}') is None
    assert usage_proxy._usage_of(b"upstream exploded") is None
    assert usage_proxy._usage_of(b'{"usage": "lots"}') is None


def test_usage_proxy_forwards_and_counts_a_real_request(
    tmp_path: pathlib.Path,
) -> None:
    """End to end over a socket, against a stub upstream.

    The counting happens in the response path of a proxied request, so unit-testing
    the counter alone would not catch a proxy that never reaches it.
    """

    import http.server
    import threading
    import urllib.request

    class Upstream(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps(
                {
                    "choices": [{"message": {"content": "ओके"}}],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 6},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    counter = usage_proxy.Counter()
    proxy = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        usage_proxy.build_handler(
            f"http://127.0.0.1:{upstream.server_address[1]}", counter
        ),
    )
    for server in (upstream, proxy):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    port = proxy.server_address[1]

    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=b'{"model": "m", "messages": []}',
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
        # The upstream's own body is returned unchanged.
        assert payload["choices"][0]["message"]["content"] == "ओके"

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/usage", timeout=30
        ) as read:
            assert json.loads(read.read()) == {
                "calls": 1,
                "input_tokens": 40,
                "output_tokens": 6,
            }
        # And the counter is exactly what the generator knows how to parse.
        assert generator._read_ocr_usage(f"http://127.0.0.1:{port}/usage") == {
            "calls": 1,
            "input_tokens": 40,
            "output_tokens": 6,
        }
    finally:
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()


def test_usage_proxy_does_not_leave_a_rejected_body_in_the_socket() -> None:
    """A refused request must close the connection, not poison the next one.

    The handler speaks HTTP/1.1, so the connection is reusable by default, and the
    413 path deliberately never reads the body. Those bytes stay in the socket and
    the next request line is parsed out of the middle of them -- so the failure
    lands on a *later*, valid request, which is the hard kind to trace.
    """

    import http.server
    import socket
    import threading

    proxy = http.server.ThreadingHTTPServer(
        # Nothing is ever forwarded: both requests are refused before that.
        ("127.0.0.1", 0),
        usage_proxy.build_handler("http://127.0.0.1:1", usage_proxy.Counter()),
    )
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    port = proxy.server_address[1]

    try:
        for label, headers, body, status in (
            # Too large to accept: the body is deliberately never read.
            (
                "oversized",
                b"Content-Length: %d\r\n" % (usage_proxy.MAX_BODY_BYTES + 1),
                b"",
                b"413",
            ),
            (
                "chunked",
                b"Transfer-Encoding: chunked\r\n",
                b"4\r\nnope\r\n0\r\n\r\n",
                b"400",
            ),
            # Any transfer coding, not only a bare "chunked" -- matching the exact
            # string forwards an empty body and leaves the encoded one behind.
            (
                "chunked with a coding",
                b"Transfer-Encoding: gzip, chunked\r\n",
                b"4\r\nnope\r\n0\r\n\r\n",
                b"400",
            ),
            # Unparseable: `int()` used to raise straight out of the handler, so
            # the client got a dropped connection and no response at all.
            ("malformed length", b"Content-Length: abc\r\n", b"", b"400"),
            # Negative: reached `rfile.read(-1)`, which blocks the worker thread
            # until the client disconnects. The 30s socket timeout below is what
            # catches a regression here -- it would hang, not fail an assertion.
            ("negative length", b"Content-Length: -1\r\n", b"", b"400"),
        ):
            with socket.create_connection(("127.0.0.1", port), timeout=30) as client:
                client.sendall(
                    b"POST /v1/chat/completions HTTP/1.1\r\nHost: proxy\r\n"
                    + headers
                    + b"\r\n"
                    + body
                )
                received = b""
                while b"\r\n\r\n" not in received:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    received += chunk
                head = received.split(b"\r\n\r\n", 1)[0].lower()
                assert status in head, (label, head)
                assert b"connection: close" in head, (label, head)
    finally:
        proxy.shutdown()
        proxy.server_close()


def test_usage_proxy_refuses_an_oversized_upstream_body_rather_than_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truncating a response would lose the usage block silently.

    Reading exactly `MAX_BODY_BYTES` cannot tell a body at the limit from one over
    it, so an oversized response came back under the upstream's 200 with the JSON
    cut mid-object -- unparseable for the caller, and with no `usage` left to
    count. An undercount that reports success is the one failure a counting proxy
    must not have.
    """

    import http.server
    import threading
    import urllib.error
    import urllib.request

    class Upstream(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps(
                {
                    "usage": {"prompt_tokens": 40, "completion_tokens": 6},
                    "pad": "x" * 512,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    monkeypatch.setattr(usage_proxy, "MAX_BODY_BYTES", 64)
    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    counter = usage_proxy.Counter()
    proxy = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        usage_proxy.build_handler(
            f"http://127.0.0.1:{upstream.server_address[1]}", counter
        ),
    )
    for server in (upstream, proxy):
        threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{proxy.server_address[1]}/v1/chat/completions",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=30)
        assert caught.value.code == 502
        assert b"too large" in caught.value.read()
        # Nothing counted, and nothing claimed to have been counted.
        assert counter.snapshot() == {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    finally:
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize(
    "upstream",
    [
        # No scheme: urllib reads this as a URL of scheme "127.0.0.1", so every
        # proxied request answers 502 -- accurate about the symptom, misleading
        # about the cause.
        "127.0.0.1:11434",
        "ftp://127.0.0.1:11434",
        # Scheme but no host: `_proxy` would build "http:/v1/chat/completions".
        "http://",
        "https://",
        # A path, query or fragment silently prefixes every route -- `/usage` and
        # `/v1/models` included -- which is not the server root this flag promises.
        "http://127.0.0.1:11434/v1",
        "http://127.0.0.1:11434?target=/v1",
        "http://127.0.0.1:11434#fragment",
    ],
)
def test_usage_proxy_rejects_an_upstream_that_is_not_a_server_root(
    upstream: str,
) -> None:
    """Point at the wrong flag, not at an innocent upstream.

    A prefix check on "http://" passes every value here, and each one fails later:
    either as a 502 on every request, or -- worse -- by quietly forwarding to a
    path nobody asked for.
    """

    assert usage_proxy._upstream_error(upstream), upstream
    # And the wiring: main() must actually consult it rather than start serving.
    with pytest.raises(SystemExit):
        usage_proxy.main(["--port", "0", "--upstream", upstream])


def test_usage_proxy_accepts_the_server_roots_the_docs_recommend() -> None:
    """The validation must not reject the forms actually documented.

    `site/README.md`, this module's docstring and the root README all show a bare
    host root, and a trailing slash is the same thing. Tightening the check until
    it rejects those would trade one broken startup for another.
    """

    for upstream in (
        "http://127.0.0.1:11434",
        "http://127.0.0.1:11434/",
        "http://127.0.0.1:8141",
        "https://gateway.example",
    ):
        assert usage_proxy._upstream_error(upstream) is None, upstream

    # And the documented example really does reach the handler's target builder.
    documented = re.findall(
        r"--upstream (\S+)", (SITE_DIR / "README.md").read_text(encoding="utf-8")
    )
    assert documented, "expected site/README.md to show an --upstream value"
    for upstream in documented:
        assert usage_proxy._upstream_error(upstream) is None, upstream


def test_readme_and_landing_page_document_the_same_ocr_backends() -> None:
    """The page and the README are two copies of the same setup instructions.

    Whichever a reader finds first has to work, and a variable renamed in one
    place has no way of announcing itself in the other.
    """

    readme = (SITE_DIR.parent / "README.md").read_text(encoding="utf-8")
    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    converter = (
        SITE_DIR.parent / "src" / "likhit" / "converters" / "nepali_pdf.py"
    ).read_text(encoding="utf-8")

    section = index[index.index('id="ocr-title"') : index.index('id="benchmark-title"')]
    page_variables = set(re.findall(r"export ([A-Z0-9_]+)=", section))
    readme_variables = set(re.findall(r"export ([A-Z0-9_]+)=", readme))

    assert page_variables, "expected the page to show exportable variables"
    # Everything the page tells people to export is documented in the README too,
    # and both are variables Likhit actually reads.
    assert page_variables <= readme_variables, sorted(page_variables - readme_variables)
    for name in readme_variables:
        assert name in converter, (
            f"{name} is documented in the README but Likhit never reads it"
        )

    # Both name the same two concrete deployments, and both are explicit that
    # Bedrock needs a gateway rather than implying Likhit can reach it directly.
    for marker in ("ollama", "bedrock", "litellm"):
        assert marker in readme.lower(), f"README does not cover {marker}"
        assert marker in section.lower(), f"landing page does not cover {marker}"

    # The README points at the published benchmark, which is where the
    # configurations are actually compared.
    assert "jawafdehi.github.io/likhit" in readme


def _direct_children_of(html: str, element_id: str) -> list[str]:
    """Tag names and ids of an element's immediate children.

    Regex cannot tell a direct child from a nested one, and the pane's children
    are what the grid rows have to line up with.
    """

    from html.parser import HTMLParser

    void = {"br", "hr", "img", "input", "meta", "link", "source"}

    class Walker(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.depth: int | None = None
            self.children: list[str] = []

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            if tag in void:
                return
            mapping = dict(attrs)
            if self.depth is None:
                if mapping.get("id") == element_id:
                    self.depth = 0
                return
            self.depth += 1
            if self.depth == 1:
                self.children.append(mapping.get("id") or tag)

        def handle_endtag(self, tag: str) -> None:
            if tag in void or self.depth is None:
                return
            if self.depth == 0:
                self.depth = None  # left the element entirely
            else:
                self.depth -= 1

    walker = Walker()
    walker.feed(html)
    return walker.children


def test_detail_pane_declares_one_grid_row_per_visible_child() -> None:
    """The detail pane's row count must match the children that actually flow.

    `.detail-content` is a fixed grid, so the count is load-bearing in both
    directions: one row short and the tab bar shares a cell with the body, which
    silently makes the tabs unclickable; one row too many and the body collapses
    while the flexible row sits empty. Adding a child without a row is the easy
    mistake -- this catches it without needing a browser.
    """

    index = (SITE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    styles = (SITE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    rule = re.search(
        r"\.detail-content\s*\{[^}]*grid-template-rows:\s*([^;]+);", styles
    )
    assert rule, "expected .detail-content to declare grid-template-rows"
    # minmax(0, 1fr) is one track despite containing a comma.
    tracks = re.sub(r"minmax\([^)]*\)", "minmax", rule.group(1)).split()
    assert tracks, "expected at least one grid track"

    children = _direct_children_of(index, "detail-content")
    assert children, "expected to find the pane's direct children"

    # `#document-source` is `display: none` -- the PDF modal replaced it -- so it
    # takes no grid row. Any other hidden child must be added here.
    assert "document-source" in children
    assert re.search(r"\.document-source\s*\{[^}]*display:\s*none", styles), (
        "document-source is excluded from the row count because it is hidden; "
        "if it is visible again it needs a row"
    )
    flowing = [child for child in children if child != "document-source"]

    assert len(tracks) == len(flowing), (
        f"{len(tracks)} grid rows for {len(flowing)} in-flow children "
        f"({', '.join(flowing)})"
    )
