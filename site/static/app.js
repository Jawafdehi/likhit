// SPDX-License-Identifier: Hippocratic-3.0

const state = {
  data: null,
  query: "",
  kind: "all",
  config: "all",
  status: "all",
  selectedDocument: null,
  selectedRun: null,
  tab: "transcript",
  transcriptView: "rendered",
  loadToken: 0,
};

const outcomeMeta = {
  pass: { label: "Passed", icon: "circle-check" },
  fail: { label: "Failed", icon: "circle-x" },
  "known-issue": { label: "Known issue", icon: "circle-alert" },
  blocked: { label: "Blocked", icon: "circle-pause" },
  reference: { label: "Reference", icon: "baseline" },
};

const byId = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function icon(name) {
  return `<i data-lucide="${name}" aria-hidden="true"></i>`;
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
  }
}

function formatNumber(value) {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(seconds >= 10 ? 1 : 2)} s`;
}

function shortSha(value) {
  return value ? value.slice(0, 8) : "unknown";
}

function safeHttpUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function documentById(id) {
  return state.data.documents.find((item) => item.id === id);
}

function activeDocument() {
  return documentById(state.selectedDocument);
}

function activeRun() {
  return activeDocument()?.runs.find((item) => item.id === state.selectedRun);
}

function outcomeChip(run, includeLabel = true) {
  const meta = outcomeMeta[run.outcome] || outcomeMeta.fail;
  const label = includeLabel ? escapeHtml(run.label) : meta.label;
  return `
    <span class="outcome outcome-${run.outcome}" title="${escapeHtml(run.label)}: ${meta.label}">
      ${icon(meta.icon)}
      <span>${label}</span>
    </span>
  `;
}

// Nepal Time (UTC+05:45). This benchmark is read by people working on Nepali
// government documents, so timestamps are pinned to Asia/Kathmandu and labelled,
// rather than rendered in whatever zone the viewer's browser happens to be in.
const NPT_ZONE = "Asia/Kathmandu";

function formatNpt(value, options = { dateStyle: "medium", timeStyle: "short" }) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "unknown";
  }
  const formatted = date.toLocaleString("en-GB", { ...options, timeZone: NPT_ZONE });
  return `${formatted} NPT`;
}

function renderIdentity() {
  const { build, generated_at: generatedAt } = state.data;
  byId("run-identity").innerHTML = `
    <code>${escapeHtml(shortSha(build.commit))}</code>
    · Likhit ${escapeHtml(build.likhit)}
    · ${escapeHtml(formatNpt(generatedAt))}
  `;
}

// The benchmark cannot run on every commit: CI has no vision backend, and the
// full corpus takes far longer than a Pages build. So a recorded snapshot is
// replayed instead -- which is only honest if the page says so, names the commit
// the numbers were measured at, and flags the case where that commit is no longer
// the one being published.
function renderMeasured() {
  const banner = byId("measured-banner");
  const measured = state.data.measured;
  if (!measured) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  banner.hidden = false;
  const build = measured.build || {};
  const missing = measured.missing_runs || [];
  const notes = [];
  if (measured.stale) {
    notes.push(
      `These numbers predate the published commit ${escapeHtml(shortSha(state.data.build.commit))}.`,
    );
  }
  if (missing.length) {
    notes.push(
      `${formatNumber(missing.length)} catalog ${missing.length === 1 ? "run is" : "runs are"} newer than the recording and were skipped.`,
    );
  }
  banner.classList.toggle("stale", Boolean(measured.stale) || missing.length > 0);
  banner.innerHTML = `
    ${icon(measured.stale || missing.length ? "triangle-alert" : "history")}
    <span>
      <strong>Recorded results.</strong>
      Measured at <code>${escapeHtml(shortSha(build.commit))}</code>
      on ${escapeHtml(formatNpt(measured.recorded_at))}
      with Likhit ${escapeHtml(build.likhit ?? "unknown")}.
      This build replayed them rather than re-running the benchmark.
      ${notes.join(" ")}
    </span>
  `;
  refreshIcons();
}

function renderSummary() {
  const { summary, integration } = state.data;
  const integrationClass =
    integration.status === "passed"
      ? "pass"
      : integration.status === "failed"
        ? "fail"
        : "";
  const executedTests = integration.tests - integration.skipped;
  const passedTests =
    executedTests - integration.failures - integration.errors;
  const items = [
    ["Failed", summary.fail, "needs attention", summary.fail ? "fail" : ""],
    [
      "Integration",
      integration.status === "not-run"
        ? "—"
        : `${passedTests}/${executedTests}`,
      integration.status,
      integrationClass,
    ],
    ["Documents", summary.documents, "source files", ""],
    ["Runs", summary.runs, "configurations", ""],
    ["Passed", summary.pass, "verified", "pass"],
    ["Known", summary.known_issue, "tracked issue", "known"],
    ["Blocked", summary.blocked, "missing dependency", "blocked"],
  ];
  byId("summary-strip").innerHTML = items
    .map(
      ([label, value, note, className]) => `
        <div class="summary-item">
          <span class="summary-label">${escapeHtml(label)}</span>
          <span class="summary-value ${className}">
            ${escapeHtml(value)}
            <small>${escapeHtml(note)}</small>
          </span>
        </div>
      `,
    )
    .join("");
}

function renderIntegration() {
  const integration = state.data.integration;
  const status =
    integration.status === "passed"
      ? { icon: "circle-check", label: "Integration suite passed" }
      : integration.status === "failed"
        ? { icon: "circle-x", label: "Integration suite failed" }
        : { icon: "circle-minus", label: "Integration suite not attached" };
  const notable = integration.cases.filter((item) => item.status !== "passed");
  const details = notable.length
    ? notable
        .map(
          (item) => `
            <div class="suite-case">
              <span>${escapeHtml(item.status)}</span>
              <span title="${escapeHtml(item.classname)}">${escapeHtml(item.name)}</span>
            </div>
          `,
        )
        .join("")
    : '<div class="suite-case"><span>passed</span><span>All recorded cases</span></div>';
  byId("integration-banner").innerHTML = `
    <details>
      <summary>
        ${icon(status.icon)}
        <span>${status.label}</span>
        <span>· ${formatNumber(integration.tests)} tests · ${formatDuration(integration.duration_s)}</span>
      </summary>
      <div class="suite-cases">${details}</div>
    </details>
  `;
}

function populateConfigFilter() {
  const select = byId("config-filter");
  const options = Object.entries(state.data.configurations)
    .map(
      ([id, config]) =>
        `<option value="${escapeHtml(id)}">${escapeHtml(config.label)}</option>`,
    )
    .join("");
  select.insertAdjacentHTML("beforeend", options);
}

function filteredRuns(documentRecord) {
  return documentRecord.runs.filter((run) => {
    const configMatches = state.config === "all" || run.config === state.config;
    const statusMatches = state.status === "all" || run.outcome === state.status;
    return configMatches && statusMatches;
  });
}

function filteredDocuments() {
  const query = state.query.trim().toLocaleLowerCase();
  return state.data.documents.filter((item) => {
    const text = [
      item.title,
      item.summary,
      item.publisher,
      item.kind,
      ...item.tags,
    ]
      .join(" ")
      .toLocaleLowerCase();
    const queryMatches = !query || text.includes(query);
    const kindMatches = state.kind === "all" || item.kind === state.kind;
    return queryMatches && kindMatches && filteredRuns(item).length > 0;
  });
}

function renderResults() {
  const documents = filteredDocuments();
  byId("result-count").textContent = `${documents.length} of ${state.data.documents.length} documents`;
  if (!documents.length) {
    byId("result-list").innerHTML =
      '<div class="no-results">No documents match these filters</div>';
    return;
  }

  byId("result-list").innerHTML = documents
    .map((item) => {
      const thumbnail = item.source.thumbnail
        ? `<img src="./${escapeHtml(item.source.thumbnail)}" alt="" loading="lazy" />`
        : `<span>${escapeHtml(item.kind)}</span>`;
      const runs = filteredRuns(item);
      return `
        <button
          class="result-row ${item.id === state.selectedDocument ? "selected" : ""}"
          type="button"
          data-document="${escapeHtml(item.id)}"
          aria-pressed="${item.id === state.selectedDocument}"
        >
          <span class="document-thumb">${thumbnail}</span>
          <span class="result-main">
            <span class="result-title-line">
              <span class="result-title">${escapeHtml(item.title)}</span>
              <span class="file-type">${escapeHtml(item.kind)}</span>
            </span>
            <span class="result-publisher">${escapeHtml(item.publisher)}</span>
            <span class="run-outcomes">
              ${runs.map((run) => outcomeChip(run)).join("")}
            </span>
          </span>
          <span class="result-chevron">${icon("chevron-right")}</span>
        </button>
      `;
    })
    .join("");

  byId("result-list")
    .querySelectorAll("[data-document]")
    .forEach((button) => {
      button.addEventListener("click", () => selectDocument(button.dataset.document));
    });
  refreshIcons();
}

function selectDocument(id, preferredRun = null) {
  const item = documentById(id);
  if (!item) return;
  state.selectedDocument = id;
  state.selectedRun =
    item.runs.find((run) => run.id === preferredRun)?.id ||
    item.runs.find((run) => run.outcome !== "reference")?.id ||
    item.runs[0].id;
  document.body.classList.add("detail-open");
  updateUrl();
  renderResults();
  renderDetail();
}

function selectRun(id) {
  state.selectedRun = id;
  updateUrl();
  renderRunSegments();
  renderDetailBody();
}

function selectTab(tab) {
  state.tab = tab;
  renderDetailBody();
}

function updateUrl() {
  const url = new URL(window.location.href);
  if (state.selectedDocument) {
    url.searchParams.set("document", state.selectedDocument);
  } else {
    url.searchParams.delete("document");
  }
  if (state.selectedRun) {
    url.searchParams.set("run", state.selectedRun);
  } else {
    url.searchParams.delete("run");
  }
  window.history.replaceState({}, "", url);
}

function renderDetail() {
  const item = activeDocument();
  if (!item) {
    byId("empty-state").hidden = false;
    byId("detail-content").hidden = true;
    return;
  }
  byId("empty-state").hidden = true;
  byId("detail-content").hidden = false;
  byId("detail-kicker").textContent =
    item.origin === "synthetic"
      ? `Synthetic · ${item.kind.toUpperCase()}`
      : `Public institutional · ${item.kind.toUpperCase()}`;
  byId("detail-name").textContent = item.title;
  byId("detail-summary").textContent = item.summary;

  const originalUrl = safeHttpUrl(item.source.original_url);
  const original = originalUrl
    ? `
      <a class="command-link" href="${escapeHtml(originalUrl)}" target="_blank" rel="noreferrer">
        ${icon("external-link")} Original
      </a>
    `
    : "";
  const viewPdf =
    item.kind === "pdf"
      ? `
        <button class="command-link" type="button" data-view-pdf>
          ${icon("eye")} View PDF
        </button>
      `
      : "";
  byId("detail-actions").innerHTML = `
    ${viewPdf}
    <a class="command-link" href="./${escapeHtml(item.source.download)}" download>
      ${icon("download")} Download
    </a>
    ${original}
  `;
  byId("detail-actions")
    .querySelector("[data-view-pdf]")
    ?.addEventListener("click", () => openPdfModal(item));
  renderSource(item);
  renderRunSegments();
  renderDetailBody();
  refreshIcons();
}

function configurationOf(run) {
  return state.data.configurations?.[run.config] ?? {};
}

// The model a run used. Held per configuration rather than per run, because most
// runs make no vision call at all and so carry no usage record to read it from --
// yet "which model would this have used" is exactly what a reader wants to know.
function runModel(run) {
  return configurationOf(run).model ?? run.ocr_usage?.model ?? null;
}

// Tokens an OCR run spent, on the configuration chip itself, so the cost of the
// OCR column is visible without opening a tab.
//
// Zero is a result, not a blank: Likhit only calls a vision model for pages a text
// layer cannot serve, so most documents spend nothing even with OCR configured.
// Showing "0 tok" for those distinguishes them from a run whose spend nobody
// counted, which renders as no badge at all.
function runCostBadge(run) {
  const usage = run.ocr_usage;
  if (!usage) return "";
  const label = usage.calls
    ? `${formatTokens(usage.total_tokens)} tok`
    : "no OCR call";
  const className = usage.calls ? "run-cost" : "run-cost run-cost-idle";
  return ` <span class="${className}">${escapeHtml(label)}</span>`;
}

function renderRunSegments() {
  const item = activeDocument();
  if (!item) return;
  byId("run-segments").innerHTML = item.runs
    .map(
      (run) => `
        <button
          class="${run.id === state.selectedRun ? "active" : ""}"
          type="button"
          data-run="${escapeHtml(run.id)}"
          aria-pressed="${run.id === state.selectedRun}"
        >
          ${escapeHtml(run.label)}${runCostBadge(run)}
        </button>
      `,
    )
    .join("");
  byId("run-segments")
    .querySelectorAll("[data-run]")
    .forEach((button) => {
      button.addEventListener("click", () => selectRun(button.dataset.run));
    });
  renderRunBackend();
}

// The backend line under the configuration selector: which vision model served
// this run, and what it spent. Every branch here is a distinct, honest state --
// conflating them is how "no OCR was needed" ends up looking like "we lost the
// numbers".
function renderRunBackend() {
  const target = byId("run-backend");
  const run = activeRun();
  if (!target || !run) return;
  const model = runModel(run);
  if (!model) {
    target.innerHTML = `
      ${icon("file-text")}
      <span>No vision model — this configuration reads the text layer only.</span>
    `;
    refreshIcons();
    return;
  }

  const usage = run.ocr_usage;
  let spend;
  if (!usage) {
    spend = "token usage was not recorded for this run";
  } else if (!usage.calls) {
    spend = "no OCR call — Likhit read this document without the model";
  } else {
    spend =
      `${formatNumber(usage.calls)} ${usage.calls === 1 ? "call" : "calls"} · ` +
      `${formatNumber(usage.total_tokens)} tokens ` +
      `(${formatNumber(usage.input_tokens)} in / ${formatNumber(usage.output_tokens)} out)`;
  }
  target.innerHTML = `
    ${icon("scan-text")}
    <span>
      Vision model <code>${escapeHtml(model)}</code> · ${escapeHtml(spend)}
    </span>
  `;
  refreshIcons();
}

// The source PDF opens in a modal rather than replacing the panel, so the run's
// transcript stays on screen behind it. <dialog> is used for the native Esc key,
// focus trapping and inert backdrop.
function openPdfModal(item) {
  const modal = byId("pdf-modal");
  const frame = byId("pdf-modal-frame");
  const href = `./${item.source.download}`;
  byId("pdf-modal-title").textContent = item.title;
  byId("pdf-modal-open").href = href;
  frame.src = href;
  if (!modal.open) modal.showModal();
}

function closePdfModal() {
  const modal = byId("pdf-modal");
  if (modal.open) modal.close();
}

function bindPdfModal() {
  const modal = byId("pdf-modal");
  byId("pdf-modal-close").addEventListener("click", closePdfModal);
  // Clicking the backdrop closes it; clicks inside the dialog do not, which is
  // why this compares the target rather than just listening on the dialog.
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closePdfModal();
  });
  // Drop the src on close so a large PDF stops rendering and does not keep
  // playing/scrolling behind the scenes.
  modal.addEventListener("close", () => {
    byId("pdf-modal-frame").src = "about:blank";
  });
}

// A deliberately narrow Markdown renderer for the transcript preview.
//
// Transcripts are machine output derived from untrusted third-party PDFs, so this
// escapes the input *first* and never emits a tag it did not construct itself.
// That removes the injection surface a general-purpose library would add, at the
// cost of covering only what Likhit actually emits: headings, GFM pipe tables,
// lists, blockquotes, rules, fenced code, and inline emphasis, code and links.
function renderInline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, (match, label, href) => {
      // Only absolute http(s) survives. javascript:, data: and anything else --
      // including the base64 images Likhit emits for .docx -- degrade to their
      // label text. The href is already HTML-escaped by the caller.
      if (!/^https?:\/\//.test(href)) return label;
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
}

function renderMarkdown(markdown) {
  const lines = escapeHtml(markdown).split("\n");
  const out = [];
  let index = 0;

  const isTableSeparator = (line) => /^\s*\|?[\s:-]*-[\s|:-]*\|?\s*$/.test(line) && line.includes("-");
  const cells = (line) =>
    line
      .replace(/^\s*\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map((cell) => renderInline(cell.trim()));

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (/^```/.test(line)) {
      const block = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) {
        block.push(lines[index]);
        index += 1;
      }
      index += 1;
      out.push(`<pre class="md-code"><code>${block.join("\n")}</code></pre>`);
      continue;
    }

    // A pipe table needs its delimiter row; without one these are just lines of
    // text that happen to contain a pipe.
    if (line.includes("|") && isTableSeparator(lines[index + 1] || "")) {
      const head = cells(line);
      index += 2;
      const body = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        body.push(cells(lines[index]));
        index += 1;
      }
      const headRow = head.map((cell) => `<th>${cell}</th>`).join("");
      const bodyRows = body
        .map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`)
        .join("");
      out.push(
        `<div class="md-table-scroll"><table class="md-table">` +
          `<thead><tr>${headRow}</tr></thead><tbody>${bodyRows}</tbody></table></div>`,
      );
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) {
      out.push("<hr />");
      index += 1;
      continue;
    }

    if (/^\s*&gt;\s?/.test(line)) {
      const block = [];
      while (index < lines.length && /^\s*&gt;\s?/.test(lines[index])) {
        block.push(lines[index].replace(/^\s*&gt;\s?/, ""));
        index += 1;
      }
      out.push(`<blockquote>${renderInline(block.join(" "))}</blockquote>`);
      continue;
    }

    const bullet = /^\s*([-*+])\s+/;
    const numbered = /^\s*\d+\.\s+/;
    if (bullet.test(line) || numbered.test(line)) {
      const ordered = numbered.test(line);
      const pattern = ordered ? numbered : bullet;
      const items = [];
      while (index < lines.length && pattern.test(lines[index])) {
        items.push(renderInline(lines[index].replace(pattern, "")));
        index += 1;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((li) => `<li>${li}</li>`).join("")}</${tag}>`);
      continue;
    }

    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^```/.test(lines[index]) &&
      !/^(#{1,6})\s/.test(lines[index]) &&
      !/^\s*(---+|\*\*\*+|___+)\s*$/.test(lines[index]) &&
      !/^\s*&gt;\s?/.test(lines[index]) &&
      !bullet.test(lines[index]) &&
      !numbered.test(lines[index]) &&
      !(lines[index].includes("|") && isTableSeparator(lines[index + 1] || ""))
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    if (paragraph.length) {
      out.push(`<p>${renderInline(paragraph.join("<br />"))}</p>`);
    }
  }

  return out.join("\n") || "<p class=\"md-empty\">No text was extracted for this run.</p>";
}

function applyTranscriptView(markdown) {
  const target = byId("transcript-copy");
  if (!target) return;
  const rendered = state.transcriptView === "rendered";
  target.classList.toggle("markdown-body", rendered);
  target.classList.toggle("transcript-source", !rendered);
  if (rendered) {
    target.innerHTML = renderMarkdown(markdown);
  } else {
    target.textContent = markdown || "No text was extracted for this run.";
  }
  byId("transcript-view")
    ?.querySelectorAll("[data-view]")
    .forEach((button) => {
      const selected = button.dataset.view === state.transcriptView;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
  refreshIcons();
}

async function renderTranscript(run, token) {
  byId("detail-body").innerHTML =
    '<div class="loading-copy">Loading transcript…</div>';
  try {
    const [transcriptResponse, diagnosticsResponse] = await Promise.all([
      fetch(`./${run.transcript}`),
      fetch(`./${run.diagnostics}`),
    ]);
    if (!transcriptResponse.ok || !diagnosticsResponse.ok) {
      throw new Error("Artifact request failed");
    }
    const [transcript, diagnostics] = await Promise.all([
      transcriptResponse.text(),
      diagnosticsResponse.text(),
    ]);
    if (token !== state.loadToken) return;
    byId("detail-body").innerHTML = `
      <div class="transcript-grid">
        <section class="transcript-panel">
          <header class="panel-heading">
            <h2>Extracted Markdown</h2>
            <div class="view-toggle" id="transcript-view" role="group" aria-label="Markdown view">
              <button type="button" data-view="rendered" aria-pressed="true">
                ${icon("book-open")} Preview
              </button>
              <button type="button" data-view="source" aria-pressed="false">
                ${icon("code")} Source
              </button>
            </div>
            <code title="${escapeHtml(run.transcript_sha256)}">${escapeHtml(shortSha(run.transcript_sha256))}</code>
          </header>
          <div id="transcript-copy"></div>
        </section>
        <section class="transcript-panel diagnostic-panel">
          <header class="panel-heading">
            <h2>Diagnostics</h2>
            <code title="${escapeHtml(run.diagnostics_sha256)}">${escapeHtml(shortSha(run.diagnostics_sha256))}</code>
          </header>
          <pre id="diagnostic-copy"></pre>
        </section>
      </div>
    `;
    applyTranscriptView(transcript);
    byId("transcript-view")
      .querySelectorAll("[data-view]")
      .forEach((button) => {
        button.addEventListener("click", () => {
          state.transcriptView = button.dataset.view;
          applyTranscriptView(transcript);
        });
      });
    byId("diagnostic-copy").textContent =
      diagnostics || "No diagnostic output.";
  } catch (error) {
    if (token !== state.loadToken) return;
    byId("detail-body").innerHTML = `<div class="error-copy">${escapeHtml(error.message)}</div>`;
  }
}

function renderSource(item) {
  const sourceThumbnail = item.source.thumbnail
    ? `
      <img
        src="./${escapeHtml(item.source.thumbnail)}"
        alt="First page of ${escapeHtml(item.title)}"
      />
    `
    : "";
  const preview =
    item.kind === "pdf"
      ? `
        <div class="source-preview">
          <header class="source-preview-bar">
            <strong>PDF preview</strong>
            <a
              href="./${escapeHtml(item.source.download)}"
              target="_blank"
              rel="noreferrer"
            >
              ${icon("external-link")} Open in new tab
            </a>
          </header>
          <object
            class="source-frame"
            data="./${escapeHtml(item.source.download)}#page=1&view=FitH"
            type="application/pdf"
            aria-label="Source PDF preview"
          >
            <div class="source-fallback">
              ${sourceThumbnail}
              <p>
                First-page preview
                <span>This browser cannot display the inline PDF.</span>
                <a
                  href="./${escapeHtml(item.source.download)}"
                  target="_blank"
                  rel="noreferrer"
                >Open the complete PDF in a new tab</a>.
              </p>
            </div>
          </object>
        </div>
      `
      : `
        <div class="source-placeholder">
          ${icon("file-type-2")}
          <strong>Word document</strong>
          <span>${escapeHtml(formatBytes(item.source.bytes))}</span>
        </div>
      `;
  // Document-level, not run-level: every configuration converts the same bytes.
  byId("document-source").innerHTML = `
    <div class="source-layout">
      ${preview}
      <dl class="source-facts">
        <div><dt>Publisher</dt><dd>${escapeHtml(item.publisher)}</dd></div>
        <div><dt>Origin</dt><dd>${escapeHtml(item.origin)}</dd></div>
        <div><dt>Privacy</dt><dd>${escapeHtml(item.privacy)}</dd></div>
        <div><dt>Content note</dt><dd>${escapeHtml(item.content_note || "Synthetic, PII-free fixture")}</dd></div>
        <div><dt>Published file</dt><dd>${escapeHtml(
          item.source.sanitization ||
            (item.origin === "synthetic"
              ? "Original synthetic fixture"
              : "Original public file"),
        )}</dd></div>
        <div><dt>Pages</dt><dd>${escapeHtml(item.source.pages ?? "—")}</dd></div>
        <div><dt>Size</dt><dd>${escapeHtml(formatBytes(item.source.bytes))}</dd></div>
        <div><dt>SHA-256</dt><dd><code>${escapeHtml(item.source.sha256)}</code></dd></div>
        ${
          item.source.original_sha256
            ? `<div><dt>Original hash</dt><dd><code>${escapeHtml(item.source.original_sha256)}</code></dd></div>`
            : ""
        }
      </dl>
    </div>
  `;
  refreshIcons();
}

function renderChecks(run) {
  const meta = outcomeMeta[run.outcome] || outcomeMeta.fail;
  const checkRows = run.checks.length
    ? run.checks
        .map(
          (check) => `
            <li class="check-row ${check.passed ? "pass" : "fail"}">
              ${icon(check.passed ? "circle-check" : "circle-x")}
              <span class="check-label">${escapeHtml(check.label)}</span>
              <span class="check-detail">${escapeHtml(check.detail)}</span>
            </li>
          `,
        )
        .join("")
    : `
      <li class="check-row pass">
        ${icon("baseline")}
        <span class="check-label">Reference run</span>
        <span class="check-detail">Captured for configuration comparison.</span>
      </li>
    `;
  const metrics = [
    ["Characters", formatNumber(run.metrics.chars)],
    ["Devanagari", formatNumber(run.metrics.devanagari)],
    ["Malformed", formatNumber(run.metrics.malformed)],
    ["Wall time", formatDuration(run.wall_s)],
    ["Peak RSS", run.max_rss_mb ? `${run.max_rss_mb} MB` : "—"],
    ["Replacement", formatNumber(run.metrics.replacement)],
    ["CID markers", formatNumber(run.metrics.cid_garbage)],
    ["Status", run.status],
  ];
  byId("detail-body").innerHTML = `
    <div class="checks-layout">
      <div class="outcome-summary">
        ${outcomeChip(run, false)}
        <strong>${escapeHtml(meta.label)}</strong>
      </div>
      <div class="metrics-grid">
        ${metrics
          .map(
            ([label, value]) => `
              <div class="metric">
                <span>${escapeHtml(label)}</span>
                <strong>${escapeHtml(value)}</strong>
              </div>
            `,
          )
          .join("")}
      </div>
      <h2>Assertions</h2>
      <ul class="check-list">${checkRows}</ul>
    </div>
  `;
  refreshIcons();
}

// Token usage for the OCR configurations. Tokens and calls only -- no cost is
// derived, because vendor token rates are not published for every model and a
// guessed rate would render a confidently wrong number. The model is shown so the
// tokens can be priced elsewhere.
function formatTokens(value) {
  if (typeof value !== "number") return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
}

function renderOcrUsageBlock(run) {
  const model = runModel(run);
  // A configuration with no vision model has nothing to report here; one that has
  // a model always does, even when the answer is "it made no call".
  if (!model) return "";
  const usage = run.ocr_usage;
  const rows = [["Model", `<code>${escapeHtml(model)}</code>`]];
  if (!usage) {
    rows.push(["Token usage", "not recorded for this run"]);
  } else {
    rows.push(
      ["Vision calls", formatNumber(usage.calls)],
      ["Total tokens", `<strong>${formatNumber(usage.total_tokens)}</strong>`],
      ["Input tokens", formatNumber(usage.input_tokens)],
      ["Output tokens", formatNumber(usage.output_tokens)],
    );
    if (!usage.calls) {
      rows.push([
        "Why zero",
        "Likhit extracted this document without calling the model",
      ]);
    }
  }
  return `
      <h2>OCR usage</h2>
      <dl class="metadata-block">
        ${rows
          .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`)
          .join("")}
      </dl>
  `;
}

function renderMetadata(item, run) {
  const measured = state.data.measured;
  // These rows describe the conversion, so they must name the environment that
  // performed it. On a replayed build that is the recorded environment, not the
  // one that assembled the page -- attributing a recorded run to the publishing
  // commit's Likhit version would be a plain misstatement.
  const build = measured?.build ?? state.data.build;
  const generated = formatNpt(
    measured ? measured.recorded_at : state.data.generated_at,
    { dateStyle: "full", timeStyle: "medium" },
  );
  const publishedFrom = measured
    ? `
        <div>
          <dt>Published from</dt>
          <dd><code>${escapeHtml(state.data.build.commit)}</code></dd>
        </div>
        <div>
          <dt>Published at</dt>
          <dd>${escapeHtml(formatNpt(state.data.generated_at, { dateStyle: "full", timeStyle: "medium" }))}</dd>
        </div>
      `
    : "";
  byId("detail-body").innerHTML = `
    <div class="metadata-layout">
      ${renderOcrUsageBlock(run)}
      <h2>Run metadata</h2>
      <dl class="metadata-block">
        <div><dt>Configuration</dt><dd>${escapeHtml(run.label)}</dd></div>
        <div><dt>Outcome</dt><dd>${escapeHtml(run.outcome)}</dd></div>
        <div><dt>Pages</dt><dd>${escapeHtml(run.pages || "all")}</dd></div>
        <div><dt>${measured ? "Measured" : "Generated"}</dt><dd>${escapeHtml(generated)}</dd></div>
        <div><dt>Commit</dt><dd><code>${escapeHtml(build.commit)}</code></dd></div>
        <div><dt>Branch</dt><dd>${escapeHtml(build.ref)}</dd></div>
        <div><dt>Likhit</dt><dd>${escapeHtml(build.likhit)}</dd></div>
        <div><dt>MarkItDown</dt><dd>${escapeHtml(build.markitdown)}</dd></div>
        <div><dt>Python</dt><dd>${escapeHtml(build.python)}</dd></div>
        ${publishedFrom}
        <div><dt>Transcript hash</dt><dd><code>${escapeHtml(run.transcript_sha256)}</code></dd></div>
        <div><dt>Diagnostic hash</dt><dd><code>${escapeHtml(run.diagnostics_sha256)}</code></dd></div>
        <div><dt>Source hash</dt><dd><code>${escapeHtml(item.source.sha256)}</code></dd></div>
      </dl>
    </div>
  `;
}

function renderDetailBody() {
  const token = ++state.loadToken;
  const item = activeDocument();
  const run = activeRun();
  if (!item || !run) return;
  byId("detail-tabs")
    .querySelectorAll("[data-tab]")
    .forEach((button) => {
      const selected = button.dataset.tab === state.tab;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
  if (state.tab === "transcript") {
    renderTranscript(run, token);
  } else if (state.tab === "checks") {
    renderChecks(run);
  } else {
    renderMetadata(item, run);
  }
}

function bindControls() {
  byId("search-input").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderResults();
  });
  byId("kind-filter").addEventListener("change", (event) => {
    state.kind = event.target.value;
    renderResults();
  });
  byId("config-filter").addEventListener("change", (event) => {
    state.config = event.target.value;
    renderResults();
  });
  byId("status-filter")
    .querySelectorAll("[data-status]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        state.status = button.dataset.status;
        byId("status-filter")
          .querySelectorAll("[data-status]")
          .forEach((candidate) => {
            const selected = candidate === button;
            candidate.classList.toggle("active", selected);
            candidate.setAttribute("aria-pressed", String(selected));
          });
        renderResults();
      });
    });
  byId("detail-tabs")
    .querySelectorAll("[data-tab]")
    .forEach((button) => {
      button.addEventListener("click", () => selectTab(button.dataset.tab));
    });
  byId("mobile-back").addEventListener("click", () => {
    document.body.classList.remove("detail-open");
  });
}

// The install command is the one thing a visitor is most likely to want to take
// away, so make it copyable without selecting text. Clipboard access can be
// refused (insecure origin, denied permission), in which case say so rather than
// silently pretending it worked.
// A multi-line setup block is copied from the <pre> it sits beside rather than
// from a duplicate in a data attribute, so the text shown and the text copied
// cannot drift apart.
function copyPayload(button) {
  if (button.dataset.copy !== undefined) return button.dataset.copy;
  return button.parentElement?.querySelector("pre")?.textContent ?? "";
}

function bindCopyButtons() {
  document.querySelectorAll("[data-copy], [data-copy-block]").forEach((button) => {
    button.addEventListener("click", async () => {
      const original = button.textContent.trim();
      try {
        await navigator.clipboard.writeText(copyPayload(button));
        button.textContent = "Copied";
      } catch {
        button.textContent = "Press ⌘/Ctrl+C";
      }
      window.setTimeout(() => {
        button.textContent = original;
      }, 1800);
    });
  });
}

async function initialize() {
  bindControls();
  bindPdfModal();
  bindCopyButtons();
  try {
    const response = await fetch("./data/results.json");
    if (!response.ok) throw new Error(`Results request failed: ${response.status}`);
    state.data = await response.json();
    renderIdentity();
    renderMeasured();
    renderSummary();
    renderIntegration();
    populateConfigFilter();

    const url = new URL(window.location.href);
    const documentId = url.searchParams.get("document");
    const runId = url.searchParams.get("run");
    if (documentId && documentById(documentId)) {
      selectDocument(documentId, runId);
    } else {
      renderResults();
      const first = filteredDocuments()[0];
      if (first && !window.matchMedia("(max-width: 760px)").matches) {
        selectDocument(first.id);
        document.body.classList.remove("detail-open");
      }
    }
    refreshIcons();
  } catch (error) {
    byId("run-identity").textContent = "Benchmark unavailable";
    byId("result-list").innerHTML = `<div class="error-copy">${escapeHtml(error.message)}</div>`;
  }
}

initialize();
