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

// Configuration labels arrive as "Likhit (no OCR)". Inside the dashboard every
// run is Likhit's, so the prefix is noise -- show the qualifier alone.
function shortLabel(label) {
  const match = /^Likhit\s*\((.+)\)$/.exec(label ?? "");
  if (!match) return label ?? "";
  return match[1].charAt(0).toLocaleUpperCase() + match[1].slice(1);
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


// Counted outcome wording: "1 known issue", "2 known issues", "3 passed".
function outcomeWord(key, count) {
  if (key === "known-issue") return count === 1 ? "known issue" : "known issues";
  return { pass: "passed", blocked: "blocked", fail: "failed", reference: "reference" }[key] ?? key;
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

function renderSummary() {
  const { summary, configurations, documents } = state.data;

  // One verdict, not seven equal numbers: the fraction that answers "does this
  // work?", a proportional outcome bar, and the counts as its legend.
  const segments = [
    ["pass", summary.pass, "passed"],
    ["known", summary.known_issue, "known issues"],
    ["blocked", summary.blocked, "blocked"],
    ["fail", summary.fail, "failed"],
  ];
  const total = segments.reduce((sum, [, count]) => sum + count, 0);
  const bar = segments
    .filter(([, count]) => count > 0)
    .map(
      ([key, count]) =>
        `<span class="seg seg-${key}" style="flex-grow: ${count}"></span>`,
    )
    .join("");
  const legend = segments
    .map(
      ([key, count, label]) =>
        `<span class="lgd lgd-${key}"><i aria-hidden="true"></i>${formatNumber(count)} ${label}</span>`,
    )
    .join("");
  const barLabel = segments
    .map(([, count, label]) => `${count} ${label}`)
    .join(", ");

  // The benchmark's true shape is documents × configurations. One row of dots
  // per configuration makes a blocked scan show up as the same gap in the same
  // position across rows; each dot deep-links to that exact run.
  const matrix = Object.entries(configurations)
    .map(([configId, config]) => {
      const cells = documents
        .map((doc) => ({
          doc,
          run: doc.runs.find((run) => run.config === configId),
        }))
        .filter((cell) => cell.run);
      const tally = new Map();
      for (const { run } of cells) {
        tally.set(run.outcome, (tally.get(run.outcome) || 0) + 1);
      }
      const tallyText = ["pass", "known-issue", "blocked", "fail"]
        .filter((key) => tally.get(key))
        .map((key) => `${tally.get(key)} ${outcomeWord(key, tally.get(key))}`)
        .join(" · ");
      const dots = cells
        .map(({ doc, run }) => {
          const outcome = outcomeMeta[run.outcome]?.label ?? run.outcome;
          return `
            <button
              class="m-dot m-${escapeHtml(run.outcome)}"
              type="button"
              data-document="${escapeHtml(doc.id)}"
              data-run="${escapeHtml(run.id)}"
              title="${escapeHtml(doc.title)} — ${escapeHtml(outcome)}"
              aria-label="${escapeHtml(doc.title)}: ${escapeHtml(outcome)}"
            ></button>
          `;
        })
        .join("");
      return `
        <div class="m-row">
          <span class="m-label">${escapeHtml(shortLabel(config.label))}</span>
          <span class="m-dots">${dots}</span>
          <span class="m-tally">${escapeHtml(tallyText)}</span>
        </div>
      `;
    })
    .join("");

  byId("summary-strip").innerHTML = `
    <div class="verdict">
      <div class="verdict-head">
        <p class="verdict-stat">
          <b>${formatNumber(summary.pass)}</b> of ${formatNumber(total)} runs pass
        </p>
      </div>
      ${total ? `<div class="verdict-bar" role="img" aria-label="${escapeHtml(barLabel)}">${bar}</div>` : ""}
      <div class="verdict-legend">${legend}</div>
    </div>
    <div class="matrix" role="group" aria-label="Results by configuration and document">
      ${matrix}
    </div>
  `;

  byId("summary-strip")
    .querySelectorAll(".m-dot")
    .forEach((dot) => {
      dot.addEventListener("click", () => {
        selectDocument(dot.dataset.document, dot.dataset.run);
        const reduceMotion = window.matchMedia(
          "(prefers-reduced-motion: reduce)",
        ).matches;
        byId("detail-pane").scrollIntoView({
          behavior: reduceMotion ? "auto" : "smooth",
          block: "start",
        });
      });
    });
}

function renderIntegration() {
  const integration = state.data.integration;
  // A replayed snapshot carries no junit report. "Suite not attached · 0 tests"
  // above the search box is noise, not information -- show nothing instead.
  if (integration.status === "not-run") {
    byId("integration-banner").innerHTML = "";
    return;
  }
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
              <span class="file-type kind-${escapeHtml(item.kind)}">${escapeHtml(item.kind)}</span>
            </span>
            <span class="result-publisher">${escapeHtml(item.publisher)}</span>
            <span class="run-outcomes">
              ${(() => {
                // One pill per outcome with its count -- "3 passed" reads at a
                // glance where three per-run dots needed a tooltip.
                const counts = new Map();
                runs.forEach((run) =>
                  counts.set(run.outcome, (counts.get(run.outcome) || 0) + 1),
                );
                return ["pass", "known-issue", "blocked", "fail", "reference"]
                  .filter((key) => counts.get(key))
                  .map(
                    (key) =>
                      `<span class="outcome outcome-${key}">${counts.get(key)} ${outcomeWord(key, counts.get(key))}</span>`,
                  )
                  .join("");
              })()}
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
          ${escapeHtml(shortLabel(run.label))}
        </button>
      `,
    )
    .join("");
  byId("run-segments")
    .querySelectorAll("[data-run]")
    .forEach((button) => {
      button.addEventListener("click", () => selectRun(button.dataset.run));
    });
}

// The source PDF opens in a modal rather than replacing the panel, so the run's
// transcript stays on screen behind it. <dialog> is used for the native Esc key,
// focus trapping and inert backdrop.
function openPdfModal(item) {
  const modal = byId("pdf-modal");
  const frame = byId("pdf-modal-frame");
  frame.src = `./${item.source.download}`;
  frame.title = `Source PDF — ${item.title}`;
  if (!modal.open) modal.showModal();
}

function closePdfModal() {
  const modal = byId("pdf-modal");
  if (modal.open) modal.close();
}

function bindPdfModal() {
  const modal = byId("pdf-modal");
  // The viewer is chromeless: Esc comes from the native <dialog>, and clicking
  // the backdrop closes it; clicks inside the dialog do not, which is why this
  // compares the target rather than just listening on the dialog.
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
    // Most runs produce no diagnostics; an empty panel saying so is noise. The
    // panel exists only for the runs that actually have something to report.
    const hasDiagnostics = Boolean(diagnostics.trim());
    // The transcript needs no title -- it IS the tab. Its only control, the
    // Preview/Source toggle, lives on the tab row instead of a panel header.
    byId("detail-body").innerHTML = `
      <div class="transcript-grid${hasDiagnostics ? "" : " solo"}">
        <section class="transcript-panel">
          <div id="transcript-copy"></div>
        </section>
        ${
          hasDiagnostics
            ? `
        <section class="transcript-panel diagnostic-panel">
          <header class="panel-heading">
            <h2>Diagnostics</h2>
          </header>
          <pre id="diagnostic-copy"></pre>
        </section>
      `
            : ""
        }
      </div>
    `;
    const toggle = byId("transcript-view");
    toggle.hidden = false;
    toggle.innerHTML = `
      <button type="button" data-view="rendered" aria-pressed="true">
        ${icon("book-open")} Preview
      </button>
      <button type="button" data-view="source" aria-pressed="false">
        ${icon("code")} Source
      </button>
    `;
    applyTranscriptView(transcript);
    toggle.querySelectorAll("[data-view]").forEach((button) => {
      button.addEventListener("click", () => {
        state.transcriptView = button.dataset.view;
        applyTranscriptView(transcript);
      });
    });
    if (hasDiagnostics) {
      byId("diagnostic-copy").textContent = diagnostics;
    }
    refreshIcons();
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
  // The Preview/Source toggle belongs to the transcript alone; renderTranscript
  // reveals it once the transcript has actually loaded.
  byId("transcript-view").hidden = true;
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
    // Both labels are stacked in one grid cell, so the button's size is fixed
    // by the widest label and never shifts. On success the resting label slides
    // up and out while the confirmation rises in from below.
    const resting = button.textContent.trim();
    button.innerHTML = `
      <span class="copy-swap">
        <span class="copy-face copy-face-resting">${escapeHtml(resting)}</span>
        <span class="copy-face copy-face-done" aria-hidden="true">Copied</span>
      </span>
    `;
    let timer = 0;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(copyPayload(button));
        button.querySelector(".copy-face-done").textContent = "Copied";
      } catch {
        button.querySelector(".copy-face-done").textContent = "Press ⌘/Ctrl+C";
      }
      button.classList.add("copied");
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        button.classList.remove("copied");
        // The hidden face defines the button's width; once the exit transition
        // ends, restore it so a long clipboard-failure hint can't keep the
        // button stretched.
        window.setTimeout(() => {
          button.querySelector(".copy-face-done").textContent = "Copied";
        }, 260);
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
