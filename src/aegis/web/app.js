"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  workspaces: [],
  workspace: null,
  modes: {},
  adapters: [],
  runs: [],
  jobs: [],
  artifacts: [],
  baselines: [],
  audit: [],
  scope: null,
  dataPolicy: null,
  principal: null,
  view: "overview",
  archived: false,
  offsets: {},
  more: {},
  workspaceOffset: 0,
  workspaceMore: false,
  inboxFilters: {},
};
const PAGE_SIZE = 50;
let frozenPages = false;
let listSnapshots = {};
async function listPage(resource) {
  if (!frozenPages) return api(pagePath(resource));
  const key = base() + "/" + resource + "/" + state.archived;
  const saved = listSnapshots[key];
  if (saved && saved.expires < Date.now()) {
    throw Error(
      "Frozen pages expired. Switch to live pages, then freeze again.",
    );
  }
  const params = new URLSearchParams({
    limit: PAGE_SIZE + 1,
    offset: state.offsets[resource] || 0,
    archived: state.archived,
  });
  if (saved) params.set("cursor", saved.cursor);
  const data = await api(base() + "/snapshots/" + resource + "?" + params);
  listSnapshots[key] = {
    cursor: data.cursor,
    expires: Date.now() + data.expires_in_seconds * 1000,
  };
  return data.items;
}
let pendingRefresh = false;
let polling = false,
  snapshot = "",
  editing = false;
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (["value", "checked", "disabled", "hidden"].includes(key))
      node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined)
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  }
  return node;
}
function notice(message, error = false) {
  $("notice").hidden = false;
  $("notice").className = error ? "error" : "";
  $("notice").textContent = message;
}
async function api(path, options = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Aegis-Request": "1",
      ...options.headers,
    },
    credentials: "same-origin",
  });
  if (response.status === 401) {
    showLogin();
    throw Error("Sign in with your local access token.");
  }
  if (!response.ok) {
    let detail = "Request failed";
    try {
      detail = (await response.json()).detail || detail;
    } catch {}
    throw Error(detail);
  }
  return response.status === 204 ? null : response.json();
}
function post(path, data, method = "POST") {
  return api(path, { method, body: JSON.stringify(data) });
}
function base() {
  return "/workspaces/" + state.workspace.id;
}
function showLogin() {
  $("shell").hidden = true;
  $("login").hidden = false;
}
function badge(value) {
  const good = [
    "PASS",
    "pass",
    "succeeded",
    "allowed",
    "healthy",
    "fixed",
    "verified",
  ].includes(value);
  const bad = ["FAIL", "fail", "failed", "new_regression"].includes(value);
  return el(
    "span",
    { class: "badge " + (good ? "good" : bad ? "bad" : "wait") },
    value,
  );
}
function formatDate(value) {
  return new Date(value).toLocaleString();
}
function runStatus(run) {
  return run.assertions.some((a) => ["ERROR", "UNKNOWN"].includes(a.outcome))
    ? "UNKNOWN"
    : run.findings.length
      ? "FAIL"
      : "PASS";
}
function panel(title, ...body) {
  return el("section", { class: "panel" }, el("h2", {}, title), ...body);
}
function table(headers, rows) {
  return el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      {},
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          headers.map((h) => el("th", {}, h)),
        ),
      ),
      el("tbody", {}, rows),
    ),
  );
}
function cell(...children) {
  return el("td", {}, ...children);
}
function button(text, handler, primary = false) {
  return el(
    "button",
    {
      type: "button",
      class: primary ? "primary" : "",
      disabled:
        (state.principal?.role === "reader" &&
          ["Archive run", "Restore run", "Cancel", "Evaluate CI gate"].includes(
            text,
          )) ||
        (state.principal?.role !== "admin" &&
          ["New workspace", "Load synthetic example"].includes(text)),
      onclick: async (event) => {
        const target = event.currentTarget;
        target.disabled = true;
        try {
          await handler();
        } catch (error) {
          notice(error.message, true);
        } finally {
          target.disabled = false;
        }
      },
    },
    text,
  );
}
function pagingKey() {
  return JSON.stringify([
    state.workspace?.id,
    state.archived,
    state.offsets,
    state.workspaceOffset,
  ]);
}
function pageControls(resource) {
  const offset = state.offsets[resource] || 0;
  const change = async (next) => {
    state.offsets[resource] = next;
    await refresh(resource !== "jobs");
  };
  const previous = button("Previous " + resource, () =>
    change(Math.max(0, offset - PAGE_SIZE)),
  );
  previous.disabled = offset === 0;
  const next = button("Next " + resource, () => change(offset + PAGE_SIZE));
  next.disabled = !state.more[resource];
  return el(
    "div",
    { class: "actions", "aria-label": resource + " pagination" },
    previous,
    el("span", { class: "muted small" }, "Page " + (offset / PAGE_SIZE + 1)),
    next,
  );
}
function pagePath(resource) {
  const params = new URLSearchParams({
    limit: PAGE_SIZE + 1,
    offset: state.offsets[resource] || 0,
  });
  if (resource === "runs") params.set("archived", state.archived);
  return base() + "/" + resource + "?" + params;
}
function navigate(view) {
  $("notice").hidden = true;
  const resetArchive = state.archived && view !== "runs";
  if (resetArchive) state.archived = false;
  const reload =
    resetArchive || Object.values(state.offsets).some((offset) => offset > 0);
  state.offsets = {};
  state.view = view;
  history.replaceState(null, "", "#" + view);
  editing = false;
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("selected", b.dataset.view === view));
  const names = {
    overview: "Overview",
    runs: "Runs & findings",
    artifacts: "Evidence & artifacts",
    baselines: "Baselines",
    workflows: "Run a workflow",
    scope: "Scope & policy",
    audit: "Audit history",
    governance: "Data policies",
    access: "Access management",
    inbox: "Findings inbox",
    coverage: "Requirements coverage",
    readiness: "Build & readiness",
    planning: "Backlogs & sprints",
  };
  $("page-title").textContent = names[view];
  $("breadcrumb").textContent = "WORKBENCH / " + names[view].toUpperCase();
  if (reload) {
    $("content").replaceChildren(el("p", {}, "Loading…"));
    refresh(true);
  } else render();
}
function scopeStatus(scope) {
  if (!scope) return "Scope required";
  if (Date.now() >= Date.parse(scope.expires_at)) return "Expired";
  if (Date.now() < Date.parse(scope.starts_at)) return "Not active yet";
  return scope.allowed_targets.length ? "Configured" : "No targets";
}
function render() {
  if (state.workspaceLoading) {
    $("content").replaceChildren(
      el("p", { class: "muted" }, "Loading workspace…"),
    );
    return;
  }
  const content = $("content");
  content.replaceChildren();
  if (state.workspace && state.view === "planning") {
    renderPlanning(content).catch((e) => notice(e.message, true));
    return;
  }
  if (state.view === "readiness") {
    renderReadiness(content).catch((e) => notice(e.message, true));
    return;
  }
  if (state.workspace && ["inbox", "coverage"].includes(state.view)) {
    renderAssuranceWorkflow(content).catch((e) => notice(e.message, true));
    return;
  }
  if (state.view === "access") {
    renderAccess(content).catch((e) => notice(e.message, true));
    return;
  }
  if (!state.workspace) {
    content.append(
      el(
        "section",
        { class: "empty" },
        el("div", { class: "empty-icon" }, "◈"),
        el("h2", {}, "Start with a workspace"),
        el(
          "p",
          {},
          "Keep policies, evidence and regression history together. Create your own workspace or explore a clearly labeled synthetic example.",
        ),
        el(
          "div",
          { class: "actions" },
          button("New workspace", () => openWorkspace(), true),
          button("Load synthetic example", demo),
        ),
      ),
    );
    return;
  }
  ({
    overview: renderOverview,
    runs: renderRuns,
    artifacts: renderArtifacts,
    baselines: renderBaselines,
    workflows: renderWorkflows,
    scope: renderScope,
    audit: renderAudit,
    governance: renderGovernance,
  })[state.view](content);
  if (["runs", "artifacts", "baselines", "audit"].includes(state.view))
    content.append(pageControls(state.view));
  if (state.view === "baselines") content.append(pageControls("runs"));
}
async function renderReadiness(content) {
  const data = await api("/readiness");
  if (state.view !== "readiness") return;
  content.replaceChildren(el("p", {}, data.notice));
  for (const area of [...new Set(data.criteria.map((c) => c.area))]) {
    const criteria = data.criteria.filter((c) => c.area === area);
    const count = criteria.filter((c) => c.status === "verified").length;
    content.append(
      panel(
        area,
        el(
          "p",
          {},
          `${count} of ${criteria.length} acceptance criteria verified`,
        ),
        el("progress", {
          max: criteria.length,
          value: count,
          "aria-label": area + " readiness",
        }),
        table(
          ["Acceptance criterion", "Status", "Evidence / remaining work"],
          criteria.map((c) =>
            el(
              "tr",
              {},
              cell(c.title),
              cell(badge(c.status)),
              cell(c.evidence),
            ),
          ),
        ),
      ),
    );
  }
}
async function renderAssuranceWorkflow(content) {
  const workspace = state.workspace.id;
  const view = state.view;
  const root = base();
  const data = await api(root + "/workflow");
  if (state.workspace?.id !== workspace || state.view !== view) return;
  content.replaceChildren(el("p", { class: "muted" }, data.limitations));
  const editable = state.principal?.role !== "reader";
  const select = (label, choices, value) => {
    const input = el(
      "select",
      { "aria-label": label },
      choices.map((s) => el("option", { value: s }, s)),
    );
    input.value = value;
    return input;
  };
  const input = (label, value = "", multiline = false) =>
    el(multiline ? "textarea" : "input", { "aria-label": label, value });
  if (view === "inbox") {
    const filters = (state.inboxFilters[workspace] ||= {
      search: "",
      status: "All",
    });
    const search = input("Search findings", filters.search);
    const status = select(
      "Filter status",
      ["All", "Open", "Triaged", "Fixing", "Retest", "Closed"],
      filters.status,
    );
    const results = el("div");
    let page = 0;
    const draw = () => {
      const filtered = data.findings.filter(
        (f) =>
          JSON.stringify([
            f.title,
            f.source,
            f.target,
            f.triage.owner,
            f.triage.tags,
          ])
            .toLowerCase()
            .includes(search.value.toLowerCase()) &&
          (status.value === "All" || f.triage.status === status.value),
      );
      results.replaceChildren(
        el("p", {}, `${filtered.length} findings · page ${page + 1}`),
      );
      for (const finding of filtered.slice(page * 50, (page + 1) * 50)) {
        const t = finding.triage;
        const owner = input("Owner", t.owner);
        const tags = input("Tags (comma separated)", t.tags.join(", "));
        const notes = input("Triage notes", t.notes, true);
        const linkedRetest = select(
          "Linked recorded retest",
          [
            "",
            ...(finding.retest_candidates || []).map(
              (c) => c.run_id + "/" + c.check_id,
            ),
          ],
          t.closure_check_id ? t.closure_run_id + "/" + t.closure_check_id : "",
        );
        const lifecycle = select(
          "Finding status",
          ["Open", "Triaged", "Fixing", "Retest", "Closed"],
          t.status,
        );
        const priority = select(
          "Priority",
          ["P0", "P1", "P2", "P3"],
          t.priority,
        );
        const save = button("Save triage", async () => {
          await post(
            root + "/findings/" + finding.id,
            {
              revision: t.revision,
              status: lifecycle.value,
              owner: owner.value,
              priority: priority.value,
              tags: tags.value
                .split(",")
                .map((x) => x.trim())
                .filter(Boolean),
              notes: notes.value,
              closure_run_id:
                lifecycle.value === "Closed"
                  ? (finding.source === "sarif"
                      ? linkedRetest.value.split("/")[0]
                      : finding.retest.run_id) || null
                  : null,
              closure_check_id:
                lifecycle.value === "Closed" && finding.source === "sarif"
                  ? linkedRetest.value.split("/")[1] || null
                  : null,
            },
            "PUT",
          );
          editing = false;
          await renderAssuranceWorkflow(content);
        });
        save.disabled = !editable;
        const form = el(
          "div",
          {},
          el("label", {}, "Owner", owner),
          el("label", {}, "Status", lifecycle),
          el("label", {}, "Priority", priority),
          el("label", {}, "Tags", tags),
          el("label", {}, "Notes", notes),
          finding.source === "sarif"
            ? el(
                "label",
                {},
                "Link a recorded retest and explain its relevance in Notes",
                linkedRetest,
              )
            : null,
          save,
        );
        form.addEventListener("input", () => {
          editing = true;
        });
        form.querySelectorAll("input,textarea,select").forEach((n) => {
          n.disabled = !editable;
        });
        results.append(
          panel(
            finding.title,
            el(
              "p",
              {},
              `${finding.source} · ${finding.severity} · ${finding.trend} · ${finding.target}`,
            ),
            el("p", {}, finding.verification),
            el("p", { class: "small muted" }, "Finding ID: " + finding.id),
            (() => {
              const create = button("Create backlog bug", async () => {
                const planning = await api(root + "/planning");
                if (!planning.items.some((i) => i.finding_id === finding.id)) {
                  await post(
                    root + "/items/" + crypto.randomUUID(),
                    {
                      title: finding.title.slice(0, 300),
                      kind: "bug",
                      finding_id: finding.id,
                      description:
                        finding.verification + " · " + finding.target,
                      priority: ["critical", "high", "error"].includes(
                        finding.severity,
                      )
                        ? "P1"
                        : "P2",
                    },
                    "PUT",
                  );
                }
                navigate("planning");
              });
              create.disabled = !editable;
              return create;
            })(),
            el("p", {}, `Latest compatible retest: ${finding.retest.outcome}`),
            finding.closure_stale
              ? el(
                  "p",
                  { class: "error" },
                  "Closure needs review: latest observation is not passing.",
                )
              : null,
            el(
              "details",
              {},
              el("summary", {}, "Evidence references"),
              el("pre", {}, JSON.stringify(finding.occurrences, null, 2)),
            ),
            el("details", {}, el("summary", {}, "Triage · " + t.status), form),
          ),
        );
      }
      const previous = button("Previous findings", () => {
        page--;
        draw();
      });
      const next = button("Next findings", () => {
        page++;
        draw();
      });
      previous.disabled = page === 0;
      next.disabled = (page + 1) * 50 >= filtered.length;
      results.append(el("div", { class: "actions" }, previous, next));
    };
    search.addEventListener("input", () => {
      filters.search = search.value;
      page = 0;
      draw();
    });
    status.addEventListener("change", () => {
      filters.status = status.value;
      page = 0;
      draw();
    });
    content.append(
      panel(
        "Triage and remediation",
        search,
        status,
        el(
          "a",
          {
            href: "/api" + root + "/stakeholder-report",
            target: "_blank",
            rel: "noopener",
          },
          "Open stakeholder report",
        ),
      ),
      results,
    );
    draw();
  } else {
    const form = el("div");
    const fields = {
      framework: input("Framework", "Internal requirements"),
      version: input("Framework version", "1"),
      control: input("Control ID"),
      title: input("Requirement title"),
      status: select(
        "Assessment status",
        ["Untested", "Passed", "Failed", "Needs review"],
        "Untested",
      ),
      evidence_run_id: input("Evidence run UUID"),
      rationale: input("Assessment rationale", "", true),
    };
    let record = null;
    for (const [name, field] of Object.entries(fields)) {
      field.disabled = !editable;
      form.append(el("label", {}, name.replaceAll("_", " "), field));
    }
    form.addEventListener("input", () => {
      editing = true;
    });
    const save = button("Save requirement", async () => {
      const value = Object.fromEntries(
        Object.entries(fields).map(([k, v]) => [k, v.value]),
      );
      value.evidence_run_id ||= null;
      value.revision = record?.revision || 0;
      await post(
        root + "/coverage/" + (record?.id || crypto.randomUUID()),
        value,
        "PUT",
      );
      editing = false;
      await renderAssuranceWorkflow(content);
    });
    save.disabled = !editable;
    form.append(save);
    content.append(
      panel(
        "Requirements coverage",
        el(
          "p",
          {},
          "Record the framework and version explicitly. ASVS mappings are user assessments; no certification is implied.",
        ),
        table(
          ["Requirement", "Framework", "Status", "Evidence", "Action"],
          data.coverage.map((c) =>
            el(
              "tr",
              {},
              cell(c.control + " · " + c.title),
              cell(c.framework + " " + c.version),
              cell(badge(c.status)),
              cell(c.evidence_run_id || "None"),
              cell(
                button("Edit assessment", () => {
                  record = c;
                  for (const [key, field] of Object.entries(fields))
                    field.value = c[key] || "";
                  editing = true;
                }),
              ),
            ),
          ),
        ),
      ),
      panel("Add or update requirement", form),
    );
  }
}
function renderOverview(content) {
  const findings = state.runs.reduce((n, r) => n + r.findings.length, 0);
  content.append(
    el(
      "div",
      { class: "metrics" },
      [
        ["Runs on this page", state.runs.length],
        ["Findings on this page", findings],
        ["Artifacts on this page", state.artifacts.length],
        [
          "Active jobs on this page",
          state.jobs.filter((j) => ["queued", "running"].includes(j.status))
            .length,
        ],
      ].map(([label, value]) =>
        el(
          "div",
          { class: "metric" },
          el("div", { class: "muted small" }, label),
          el("div", { class: "number" }, value),
        ),
      ),
    ),
  );
  const mode = state.modes[state.workspace.mode];
  content.append(
    el(
      "div",
      { class: "split" },
      panel(
        "Your assurance workspace",
        el("p", {}, mode?.description || ""),
        el(
          "p",
          { class: "helper" },
          "Workflows evaluate imported data and local observations. Every result records its source; no remote system is tested.",
        ),
        el(
          "div",
          { class: "actions" },
          button("Run a workflow", () => navigate("workflows"), true),
          button("Review scope", () => navigate("scope")),
        ),
      ),
      panel(
        "Policy status",
        badge(scopeStatus(state.scope)),
        el(
          "p",
          {},
          state.scope
            ? state.scope.allowed_targets.join(", ")
            : "Define exact fixture targets and a time window before running a workflow.",
        ),
        el(
          "p",
          { class: "helper" },
          state.scope
            ? "Expires " + formatDate(state.scope.expires_at)
            : "Scope can be edited from the Scope & policy page.",
        ),
      ),
    ),
  );
  content.append(
    panel(
      "Recent runs",
      state.runs.length
        ? runTable(state.runs.slice(0, 6))
        : el(
            "p",
            { class: "muted" },
            "No runs yet. Run a fixture, import JUnit results, or evaluate performance thresholds.",
          ),
    ),
  );
  content.append(panel("Latest jobs", jobsTable(state.jobs.slice(0, 5))));
}
function runTable(runs) {
  return table(
    ["Run / revision", "Outcome", "Assertions", "Findings", "Recorded", ""],
    runs.map((run) =>
      el(
        "tr",
        {},
        cell(
          el("div", { class: "mono" }, run.id.slice(0, 8)),
          el("div", { class: "muted small" }, run.target_version),
        ),
        cell(badge(runStatus(run))),
        cell(run.assertions.length),
        cell(run.findings.length),
        cell(el("span", { class: "small muted" }, formatDate(run.created_at))),
        cell(button("Inspect", () => showRun(run))),
      ),
    ),
  );
}
function renderRuns(content) {
  content.append(
    el(
      "div",
      { class: "panel-head" },
      el(
        "p",
        { class: "muted" },
        "Recorded assertions and evidence-backed findings.",
      ),
      button(
        state.archived ? "Show active runs" : "Show archived runs",
        async () => {
          state.archived = !state.archived;
          state.offsets.runs = 0;
          await refresh(true);
        },
      ),
    ),
  );
  content.append(
    panel(
      state.archived ? "Archived runs" : "Run history",
      state.runs.length
        ? runTable(state.runs)
        : el("p", { class: "muted" }, "No runs in this view."),
    ),
  );
}
async function showRun(run) {
  const workspaceId = state.workspace.id;
  const root = "/workspaces/" + workspaceId;
  const evidence = await api(root + "/runs/" + run.id + "/evidence");
  $("detail-title").textContent = "Run " + run.id.slice(0, 8);
  const detail = $("detail-content");
  detail.replaceChildren(
    el(
      "div",
      { class: "inline" },
      badge(runStatus(run)),
      el("span", { class: "mono" }, run.target),
    ),
    el(
      "p",
      { class: "muted" },
      "Revision " + run.target_version + " · " + formatDate(run.created_at),
    ),
    table(
      ["Check", "Expected", "Observed", "Outcome"],
      run.assertions.map((a) =>
        el(
          "tr",
          {},
          cell(a.title),
          cell(a.expected),
          cell(a.observed),
          cell(badge(a.outcome)),
        ),
      ),
    ),
    el("h3", {}, "Evidence and provenance"),
    el("pre", {}, JSON.stringify(evidence, null, 2)),
    el(
      "div",
      { class: "actions" },
      el(
        "a",
        {
          href: "/api" + root + "/runs/" + run.id + "/report",
          target: "_blank",
          rel: "noopener",
        },
        "Open HTML report ↗",
      ),
      button("Export JSON", () =>
        download("aegis-" + run.id + ".json", { run, evidence }),
      ),
      button(state.archived ? "Restore run" : "Archive run", async () => {
        await post(
          root + "/runs/" + run.id + "/archive",
          { archived: !state.archived },
          "PUT",
        );
        $("detail-dialog").close();
        await refresh(true);
      }),
    ),
  );
  $("detail-dialog").showModal();
}
function download(name, value) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function sarifResults(results) {
  const search = el("input", {
    placeholder: "Search rule, message or path",
    "aria-label": "Search SARIF results",
  });
  const status = el(
    "select",
    { "aria-label": "SARIF result state" },
    ...[
      "all",
      "fail",
      "suppressed",
      "absent",
      "review",
      "pass",
      "informational",
    ].map((value) => el("option", { value }, value)),
  );
  const body = el("div");
  let offset = 0;
  function update() {
    const query = search.value.toLowerCase();
    const filtered = results.filter(
      (result) =>
        (status.value === "all" || result.state === status.value) &&
        [
          result.rule_id,
          result.message,
          ...result.locations.map((location) => location.uri),
        ]
          .join(" ")
          .toLowerCase()
          .includes(query),
    );
    const previous = button("Previous results", () => {
      offset = Math.max(0, offset - 100);
      update();
    });
    previous.disabled = offset === 0;
    const next = button("Next results", () => {
      offset += 100;
      update();
    });
    next.disabled = offset + 100 >= filtered.length;
    body.replaceChildren(
      el(
        "p",
        { class: "muted" },
        filtered.length + " matching recorded results",
      ),
      table(
        ["Tool / rule", "Level / state", "Location", "Message"],
        filtered
          .slice(offset, offset + 100)
          .map((result) =>
            el(
              "tr",
              {},
              cell(result.tool + " / " + result.rule_id),
              cell(result.level + " / " + result.state),
              cell(
                result.locations
                  .map(
                    (location) =>
                      location.uri + (location.line ? ":" + location.line : ""),
                  )
                  .join(", "),
              ),
              cell(result.message),
            ),
          ),
      ),
      el("div", { class: "actions" }, previous, next),
    );
  }
  search.addEventListener("input", () => {
    offset = 0;
    update();
  });
  status.addEventListener("change", () => {
    offset = 0;
    update();
  });
  update();
  return el(
    "section",
    {},
    el("div", { class: "form-grid" }, search, status),
    body,
  );
}
function renderArtifacts(content) {
  content.append(
    el(
      "p",
      { class: "muted" },
      "Integrity-checked metadata, notes, inventories and research results. Run evidence is available from each run.",
    ),
  );
  if (!state.artifacts.length) {
    content.append(
      panel(
        "No standalone artifacts yet",
        el(
          "p",
          {},
          "Use a notebook, OpenAPI, file metadata, or research workflow.",
        ),
      ),
    );
    return;
  }
  content.append(
    el(
      "div",
      { class: "artifact-grid" },
      state.artifacts.map((item) =>
        panel(
          item.kind.replaceAll("_", " "),
          el("p", { class: "small muted" }, formatDate(item.created_at)),
          el(
            "p",
            { class: "mono" },
            "SHA-256 " + item.sha256.slice(0, 20) + "…",
          ),
          button("Inspect artifact", () => {
            $("detail-title").textContent = item.kind;
            $("detail-content").replaceChildren(
              ...(item.kind === "sarif_report"
                ? [
                    el("p", {}, item.data.verification),
                    el(
                      "p",
                      {},
                      "Active recorded failures by level: " +
                        JSON.stringify(item.data.active_failures_by_level),
                    ),
                    sarifResults(item.data.results),
                  ]
                : []),
              el(
                "details",
                {},
                el("summary", {}, "Complete artifact and provenance"),
                el("pre", {}, JSON.stringify(item, null, 2)),
              ),
              button("Export JSON", () =>
                download("aegis-artifact-" + item.id + ".json", item),
              ),
            );
            $("detail-dialog").showModal();
          }),
        ),
      ),
    ),
  );
}
function renderBaselines(content) {
  editing = true;
  const name = el("input", {
    required: "",
    maxlength: "80",
    pattern: "[A-Za-z0-9_.-]+",
    placeholder: "e.g. release-1.0",
  });
  const runSelect = el(
    "select",
    {},
    state.runs.map((r) =>
      el(
        "option",
        { value: r.id },
        r.target_version + " · " + runStatus(r) + " · " + r.id.slice(0, 8),
      ),
    ),
  );
  const form = el(
    "form",
    {},
    el(
      "div",
      { class: "form-grid" },
      el("label", {}, "Baseline name", name),
      el("label", {}, "Recorded run", runSelect),
    ),
    el(
      "p",
      { class: "helper" },
      "Baseline names are immutable. Comparisons require compatible targets, policies, check definitions and identity sets.",
    ),
    el(
      "button",
      {
        class: "primary",
        type: "submit",
        disabled: !state.runs.length || state.principal?.role === "reader",
      },
      "Save baseline",
    ),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await post(base() + "/baselines", {
        name: name.value,
        run_id: runSelect.value,
      });
      await refresh(true);
      notice("Baseline saved.");
    } catch (error) {
      notice(error.message, true);
    }
  });
  content.append(panel("Create a baseline", form));
  const result = el("div");
  content.append(
    panel(
      "Compare a run",
      table(
        ["Baseline", "Recorded run", ""],
        state.baselines.map((b) =>
          el(
            "tr",
            {},
            cell(b.name),
            cell(el("span", { class: "mono" }, b.run_id.slice(0, 8))),
            cell(
              button("Compare to selected run", async () => {
                if (!runSelect.value) throw Error("Select a run above.");
                const comparison = await api(
                  base() +
                    "/compare?baseline=" +
                    encodeURIComponent(b.name) +
                    "&run_id=" +
                    runSelect.value,
                );
                result.replaceChildren(
                  table(
                    ["Check", "Classification"],
                    Object.entries(comparison).map(([id, value]) =>
                      el("tr", {}, cell(id), cell(badge(value))),
                    ),
                  ),
                );
              }),
              button("Evaluate CI gate", async () => {
                if (!runSelect.value) throw Error("Select a run above.");
                const gate = await api(
                  base() +
                    "/gate?baseline=" +
                    encodeURIComponent(b.name) +
                    "&run_id=" +
                    runSelect.value,
                  { method: "POST", body: JSON.stringify({}) },
                );
                result.replaceChildren(
                  el(
                    "p",
                    {},
                    badge(gate.status),
                    " · " + gate.reason + " · exit " + gate.exit_code,
                  ),
                  el(
                    "p",
                    { class: "muted" },
                    "Default policy blocks new high or critical regressions. Incomplete observations require review.",
                  ),
                  table(
                    ["Check", "Classification"],
                    Object.entries(gate.classifications).map(([id, value]) =>
                      el("tr", {}, cell(id), cell(badge(value))),
                    ),
                  ),
                );
              }),
            ),
          ),
        ),
      ),
      result,
    ),
  );
}
function jobsTable(jobs) {
  if (!jobs.length) return el("p", { class: "muted" }, "No jobs recorded.");
  return table(
    ["Workflow", "Status", "Created", "Result"],
    jobs.map((job) =>
      el(
        "tr",
        {},
        cell(job.adapter_id),
        cell(badge(job.status)),
        cell(el("span", { class: "small muted" }, formatDate(job.created_at))),
        cell(
          ["queued", "running"].includes(job.status)
            ? button("Cancel", async () => {
                await post(base() + "/jobs/" + job.id + "/cancel", {});
                await refresh(true);
              })
            : job.error
              ? job.error.replaceAll("_", " ")
              : job.result.run_id
                ? button("Inspect run", async () => {
                    const run = await api(
                      base() + "/runs/" + job.result.run_id,
                    );
                    await showRun(run);
                  })
                : job.result.artifact_id
                  ? button("View evidence", () => navigate("artifacts"))
                  : "No result",
        ),
      ),
    ),
  );
}
const examplePayload = (adapter, target) =>
  ({
    notes: {
      title: "Observation",
      note: "Record an observation, supporting context, and remaining uncertainty.",
    },
    openapi: {
      document: {
        openapi: "3.0.3",
        info: { title: "Example API", version: "1" },
        paths: {
          "/documents": {
            get: {
              security: [{ bearerAuth: [] }],
              responses: { 200: { description: "Success" } },
            },
          },
        },
      },
    },
    "file-metadata": {
      name: "sample.txt",
      content_base64: btoa("AEGIS sample artifact"),
    },
    evaluation: {
      name: "Example labeled results",
      samples: [
        { truth: true, predicted: true },
        { truth: false, predicted: false },
        { truth: true, predicted: false },
        { truth: false, predicted: null },
      ],
    },
    sarif: {
      document: {
        version: "2.1.0",
        runs: [
          {
            tool: { driver: { name: "Imported analysis" } },
            results: [
              {
                ruleId: "example-rule",
                level: "warning",
                message: { text: "Example recorded observation" },
                locations: [
                  {
                    physicalLocation: {
                      artifactLocation: { uri: "src/example.py" },
                      region: { startLine: 10 },
                    },
                  },
                ],
              },
            ],
          },
        ],
      },
    },
    junit: {
      revision: "local-test-run",
      suite_version: "v1",
      xml: '<testsuite name="sample"><testcase name="checkout passes" classname="checkout"/></testsuite>',
    },
    thresholds: {
      revision: "build-1",
      metrics: { p95_ms: 310, error_rate: 0, coverage: 0.92 },
      minimums: { coverage: 0.8 },
      maximums: { p95_ms: 500, error_rate: 0.01 },
    },
    invariants: {
      revision: "build-1",
      observations: { response: { status: 403, records: [] } },
      checks: [
        {
          id: "recorded-denial",
          title: "Recorded response denies access",
          path: ["response", "status"],
          operator: "equals",
          expected: 403,
          severity: "high",
        },
      ],
    },
    fixture: {
      target,
      target_version: "revision-1",
      policy_version: "ownership-v1",
      identity_set: "alice-bob",
      checks: [
        {
          id: "non-owner-read",
          title: "Non-owner access is denied",
          expected: "deny",
          observed: "deny",
          severity: "high",
        },
      ],
    },
  })[adapter];
function renderWorkflows(content) {
  content.append(
    panel(
      "Workflow guide",
      el("p", {}, state.modes[state.workspace.mode]?.workflow_guide || ""),
    ),
  );
  const available = state.adapters.filter((a) =>
    a.modes.includes(state.workspace.mode),
  );
  const target = el("input", {
    value: state.scope?.allowed_targets[0] || "fixture://local/sample",
    required: "",
  });
  const adapter = el(
    "select",
    {},
    available.map((a) => el("option", { value: a.id }, a.name)),
  );
  const editor = el("textarea", {
    "aria-label": "Workflow input JSON",
    rows: "15",
  });
  const file = el("input", {
    type: "file",
    class: "file-input",
    "aria-label": "Import input file",
  });
  const hint = el("p", { class: "helper" });
  function loadExample() {
    editor.value = JSON.stringify(
      examplePayload(adapter.value, target.value),
      null,
      2,
    );
    hint.textContent =
      adapter.value === "file-metadata"
        ? "Select a file up to 512 KiB. Only hashes and basic metadata are retained; the file is never executed."
        : adapter.value === "junit"
          ? "Import a JUnit XML report. Error bodies and captured output are omitted."
          : "Edit the sample input, or import a JSON file. Inputs are processed locally.";
  }
  adapter.addEventListener("change", loadExample);
  loadExample();
  file.addEventListener("change", async () => {
    try {
      const f = file.files[0];
      if (!f) return;
      if (f.size > 524288) throw Error("Choose a file no larger than 512 KiB.");
      if (adapter.value === "file-metadata") {
        const bytes = new Uint8Array(await f.arrayBuffer());
        let binary = "";
        for (const b of bytes) binary += String.fromCharCode(b);
        editor.value = JSON.stringify(
          { name: f.name, content_base64: btoa(binary) },
          null,
          2,
        );
      } else if (adapter.value === "junit") {
        const payload = JSON.parse(editor.value);
        payload.xml = await f.text();
        editor.value = JSON.stringify(payload, null, 2);
      } else if (["openapi", "sarif"].includes(adapter.value)) {
        editor.value = JSON.stringify(
          { document: JSON.parse(await f.text()) },
          null,
          2,
        );
      } else editor.value = JSON.stringify(JSON.parse(await f.text()), null, 2);
    } catch (error) {
      notice(error.message, true);
    }
  });
  const submit = el(
    "button",
    {
      class: "primary",
      type: "submit",
      disabled: !state.scope || state.principal?.role === "reader",
    },
    "Queue workflow",
  );
  const form = el(
    "form",
    {},
    el(
      "div",
      { class: "form-grid" },
      el("label", {}, "Workflow", adapter),
      el("label", {}, "Declared fixture target", target),
    ),
    hint,
    el("label", {}, "Import input file (optional)", file),
    el("label", {}, "Workflow input JSON", editor),
    el(
      "div",
      { class: "actions" },
      submit,
      button("Reset example", loadExample),
    ),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    submit.disabled = true;
    try {
      const payload = JSON.parse(editor.value);
      if (adapter.value === "fixture") payload.target = target.value;
      const job = await post(base() + "/jobs", {
        adapter_id: adapter.value,
        target: target.value,
        payload,
      });
      notice("Job " + job.id.slice(0, 8) + " queued. Watch its status below.");
      await refresh(false);
      renderJobArea();
    } catch (error) {
      notice(error.message, true);
    } finally {
      submit.disabled = !state.scope || state.principal?.role === "reader";
    }
  });
  if (!state.scope)
    content.append(
      panel(
        "Scope required",
        el("p", {}, "Save scope before running this workflow."),
        button("Define scope", () => navigate("scope"), true),
      ),
    );
  content.append(
    panel("Run a local workflow", form),
    el(
      "section",
      {
        class: "panel",
        id: "job-area",
        "data-snapshot": JSON.stringify(state.jobs),
      },
      el("h2", {}, "Job history"),
      jobsTable(state.jobs),
      pageControls("jobs"),
    ),
  );
  editing = true;
}
function renderJobArea() {
  const area = $("job-area");
  const nextJobs = JSON.stringify(state.jobs);
  if (area && area.dataset.snapshot !== nextJobs) {
    area.replaceChildren(
      el("h2", {}, "Job history"),
      jobsTable(state.jobs),
      pageControls("jobs"),
    );
    area.dataset.snapshot = nextJobs;
  }
}
function localDate(value) {
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
function renderScope(content) {
  const s = state.scope;
  const allowed = el("textarea", {
    rows: "3",
    value: s?.allowed_targets.join("\n") || "fixture://local/sample",
  });
  const excluded = el("textarea", {
    rows: "2",
    value: s?.excluded_targets.join("\n") || "",
  });
  const start = el("input", {
    type: "datetime-local",
    required: "",
    value: localDate(s?.starts_at || Date.now() - 60000),
  });
  const expiry = el("input", {
    type: "datetime-local",
    required: "",
    value: localDate(s?.expires_at || Date.now() + 86400000),
  });
  const evaluation = el("input", {
    type: "checkbox",
    checked: s
      ? s.capabilities.includes("fixture.evaluate")
      : !["ctf", "bug_bounty", "forensics", "reverse"].includes(
          state.workspace.mode,
        ),
  });
  const imported = el("input", {
    type: "checkbox",
    checked: s ? s.capabilities.includes("evidence.import") : true,
  });
  const form = el(
    "form",
    {},
    el(
      "div",
      { class: "form-grid" },
      el("label", {}, "Allowed fixture targets · one per line", allowed),
      el("label", {}, "Excluded fixture targets · one per line", excluded),
      el("label", {}, "Starts at", start),
      el("label", {}, "Expires at", expiry),
    ),
    el(
      "label",
      { class: "check" },
      evaluation,
      "Evaluate fixture observations",
    ),
    el("label", { class: "check" }, imported, "Import offline evidence"),
    el(
      "p",
      { class: "helper" },
      "Only exact fixture:// identifiers are accepted. Exclusions override inclusions. Network actions are not implemented. Risk ceiling is low.",
    ),
    el(
      "button",
      {
        class: "primary",
        type: "submit",
        disabled: state.principal?.role !== "admin",
      },
      "Save scope",
    ),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const targets = (t) =>
        t
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean);
      const scope = {
        workspace_id: state.workspace.id,
        allowed_targets: targets(allowed.value),
        excluded_targets: targets(excluded.value),
        capabilities: [
          ...(evaluation.checked ? ["fixture.evaluate"] : []),
          ...(imported.checked ? ["evidence.import"] : []),
        ],
        risk_ceiling: 1,
        starts_at: new Date(start.value).toISOString(),
        expires_at: new Date(expiry.value).toISOString(),
      };
      state.scope = await post(base() + "/scope", scope, "PUT");
      notice("Scope saved. Decisions will use this policy.");
    } catch (error) {
      notice(error.message, true);
    }
  });
  content.append(panel("Workspace policy", form));
  editing = true;
}
function renderAudit(content) {
  const events = state.audit.map((event) =>
    el(
      "div",
      { class: "event" },
      el("time", {}, formatDate(event.timestamp)),
      el("p", {}, event.action),
      el("div", { class: "mono muted" }, JSON.stringify(event.metadata)),
    ),
  );
  content.append(
    panel(
      "Recorded activity",
      events.length
        ? el("div", { class: "timeline" }, events)
        : el("p", { class: "muted" }, "No activity recorded."),
    ),
  );
}
function renderGovernance(content) {
  editing = true;
  const policy = state.dataPolicy || {};
  const admin = state.principal?.role === "admin";
  const expiryArea = el("div", { "aria-live": "polite" });
  const expiryPreview = button("Preview recovery backup expiry", async () => {
    const preview = await api("/recovery/expiry");
    expiryArea.replaceChildren(
      el("p", {}, preview.notice),
      el(
        "p",
        {},
        preview.retention_days
          ? `Retention: ${preview.retention_days} days. Review removes one file at a time.`
          : "Recovery backup expiry is disabled.",
      ),
    );
    for (const record of preview.records) {
      const row = el(
        "div",
        {},
        el(
          "p",
          {},
          `${record.name} · ${record.bytes} bytes · ${record.eligible ? "eligible for expiry" : record.blockers.join(", ")}`,
        ),
      );
      if (record.eligible)
        row.append(
          button("Review expiry " + record.name, async () => {
            const approval = await post("/recovery/expiry/prepare", {
              name: record.name,
              fingerprint: preview.fingerprint,
            });
            const confirmation = el("input", {
              type: "text",
              autocomplete: "off",
            });
            const apply = button(
              "Permanently expire this recovery file",
              async () => {
                const result = await post("/recovery/expiry/apply", {
                  token: approval.token,
                  confirmation: confirmation.value,
                });
                expiryArea.replaceChildren(
                  el(
                    "p",
                    {},
                    result.status === "file_removed"
                      ? "Recovery file removed. Preview again before reviewing another file."
                      : "Recovery file removed; final audit is pending. Preview again to reconcile the recorded intent.",
                  ),
                );
              },
            );
            apply.disabled = true;
            confirmation.addEventListener("input", () => {
              apply.disabled = confirmation.value !== approval.confirmation;
            });
            row.append(
              el("p", {}, "SHA-256: " + record.sha256),
              el("label", {}, "Type " + approval.confirmation, confirmation),
              apply,
            );
          }),
        );
      expiryArea.append(row);
    }
    expiryArea.append(
      el(
        "details",
        {},
        el("summary", {}, "Recent recovery expiry activity"),
        el("pre", {}, JSON.stringify(preview.recent_activity, null, 2)),
      ),
    );
  });
  expiryPreview.disabled = !admin;
  content.append(
    panel(
      "Recovery backup expiry",
      el(
        "p",
        {},
        "Applies to server recovery files across all workspaces. Older unmanaged backups and exports are not deleted.",
      ),
      expiryPreview,
      expiryArea,
    ),
  );
  const deletionResult = el("div", { "aria-live": "polite" });
  const resource = el(
    "select",
    { disabled: !admin },
    el("option", { value: "runs" }, "Runs"),
    el("option", { value: "artifacts" }, "Artifacts"),
  );
  const artifactId = el("input", { type: "text", disabled: !admin });
  const markArtifact = (archived) =>
    button(archived ? "Archive artifact" : "Restore artifact", async () => {
      await post(
        base() +
          "/artifacts/" +
          encodeURIComponent(artifactId.value.trim()) +
          "/archive",
        { archived },
        "PUT",
      );
      deletionResult.replaceChildren();
      notice(
        archived
          ? "Artifact archived. It remains visible to preserve findings history."
          : "Artifact restored.",
      );
    });
  const archiveArtifact = markArtifact(true),
    restoreArtifact = markArtifact(false);
  archiveArtifact.disabled = restoreArtifact.disabled = !admin;

  const grace = el("input", {
    type: "number",
    min: "1",
    max: "36500",
    value: "30",
    disabled: !admin,
  });
  const assess = button("Assess archived runs for deletion", async () => {
    const preview = await post(base() + "/deletion/preview", {
      grace_days: Number(grace.value),
      resource: resource.value,
    });
    deletionResult.replaceChildren(
      el("p", {}, preview.notice),
      el(
        "p",
        {},
        `${preview.eligible_count} eligible in this assessment; ${preview.records.length} archived ${preview.resource} examined.`,
      ),
      ...preview.records.map((record) =>
        el(
          "p",
          {},
          `${record.run_id || record.artifact_id}: ${record.eligible ? "eligible for future review" : record.blockers.join(", ")} · ${record.estimated_payload_bytes} payload bytes`,
        ),
      ),
      ...(preview.more
        ? [el("p", {}, "Only the first 100 archived records are assessed.")]
        : []),
      el(
        "details",
        {},
        el("summary", {}, "Checksums and references"),
        el("pre", {}, JSON.stringify(preview, null, 2)),
      ),
    );
    if (preview.apply_enabled && preview.eligible_count > 0) {
      deletionResult.append(
        button("Prepare deletion and verify recovery", async () => {
          const root = base();
          const approval = await post(root + "/deletion/prepare", {
            fingerprint: preview.fingerprint,
            resource: preview.resource,
          });
          const confirmation = el("input", {
            type: "text",
            autocomplete: "off",
          });
          const apply = button(
            "Permanently delete reviewed " + preview.resource,
            async () => {
              const result = await post(root + "/deletion/apply", {
                token: approval.token,
                confirmation: confirmation.value,
              });
              deletionResult.replaceChildren(
                el(
                  "p",
                  {},
                  `${result["deleted_" + preview.resource]} archived ${preview.resource} permanently deleted. Recovery copies are retained.`,
                ),
              );
              notice("Deletion committed and recorded in the audit history.");
            },
          );
          apply.disabled = true;
          confirmation.addEventListener("input", () => {
            apply.disabled = confirmation.value !== approval.confirmation;
          });
          deletionResult.append(
            el("p", {}, approval.notice),
            el("p", {}, "Verified backup SHA-256: " + approval.backup_sha256),
            el(
              "label",
              {},
              `Type ${approval.confirmation} to confirm this selection`,
              confirmation,
            ),
            apply,
          );
        }),
      );
    }
  });
  assess.disabled = !admin;
  resource.addEventListener("change", () => {
    assess.textContent = "Assess archived " + resource.value + " for deletion";
    deletionResult.replaceChildren();
  });
  content.append(
    panel(
      "Permanent deletion assessment",
      el(
        "p",
        {},
        "Deletion is disabled by default. Configured deployments can prepare a reviewed selection and verify recovery before confirming deletion.",
      ),
      el("label", {}, "Record type for deletion", resource),
      el("label", {}, "Artifact ID to archive or restore", artifactId),
      archiveArtifact,
      restoreArtifact,
      el("label", {}, "Proposed archive grace period (days)", grace),
      assess,
      deletionResult,
    ),
  );
  const days = el("input", {
    type: "number",
    min: "1",
    max: "36500",
    value: policy.archive_after_days || "",
    disabled: !admin,
  });
  const exports = el("input", {
    type: "checkbox",
    checked: policy.export_allowed,
    disabled: !admin,
  });
  const interval = el("input", {
    type: "number",
    min: "1",
    max: "8760",
    value: policy.schedule_interval_hours || "",
    disabled: !admin,
  });
  const evidence = el("input", {
    type: "checkbox",
    checked: policy.include_evidence,
    disabled: !admin,
  });
  const previewArea = el("div");
  const save = button(
    "Save data policy",
    async () => {
      const updated = await post(
        base() + "/data-policy",
        {
          archive_after_days: days.value ? Number(days.value) : null,
          schedule_interval_hours: interval.value
            ? Number(interval.value)
            : null,
          export_allowed: exports.checked,
          include_evidence: evidence.checked,
        },
        "PUT",
      );
      state.dataPolicy = updated;
      previewArea.replaceChildren();
      notice("Data policy saved.");
    },
    true,
  );
  save.disabled = !admin;
  content.append(
    panel(
      "Retention and exports",
      el(
        "p",
        {},
        "Archive older runs while preserving their evidence. Baseline runs are protected. Leave the age blank to disable retention.",
      ),
      el("label", {}, "Archive runs older than this many days", days),
      el(
        "label",
        {},
        "Automatic archive interval (hours; blank disables)",
        interval,
      ),
      el(
        "p",
        { class: "helper" },
        "Scheduling runs while AEGIS is open, in batches of up to 500. Baselines stay protected.",
      ),
      el(
        "label",
        { class: "check" },
        exports,
        "Allow workspace bundle exports",
      ),
      el(
        "label",
        { class: "check" },
        evidence,
        "Include evidence and artifacts in bundles",
      ),
      el(
        "p",
        { class: "helper" },
        "Export controls apply to workspace bundles. People who can view a record can still copy it. Archiving is reversible and does not delete data.",
      ),
      save,
      button("Preview retention", async () => {
        const root = base();
        const preview = await post(root + "/retention/preview", {});
        const apply = button(
          "Archive these " + preview.count + " runs",
          async () => {
            const result = await post(root + "/retention/apply", {
              as_of: preview.as_of,
              fingerprint: preview.fingerprint,
              resource: preview.resource,
            });
            previewArea.replaceChildren(
              el("p", {}, result.archived + " runs archived."),
            );
            state.offsets.runs = 0;
            notice(
              "Retention applied. Archived runs can be restored from Runs & findings.",
            );
          },
        );
        apply.disabled = !admin || preview.count === 0;
        previewArea.replaceChildren(
          el(
            "p",
            {},
            preview.count + " eligible runs. Baseline runs are excluded.",
          ),
          el("pre", {}, preview.run_ids.join("\n") || "No eligible runs."),
          ...(preview.more
            ? [
                el(
                  "p",
                  {},
                  "More eligible runs remain; preview again after applying this batch.",
                ),
              ]
            : []),
          apply,
        );
      }),
      previewArea,
      button("Download workspace bundle", async () => {
        const identity = state.workspace.id;
        const bundle = await post(base() + "/export", {});
        download("aegis-workspace-" + identity + ".json", bundle);
        notice("Workspace bundle downloaded with integrity manifest.");
      }),
    ),
  );
}
async function renderAccess(content, offset = 0) {
  if (state.principal?.role !== "admin") return;
  editing = true;
  const entries = await api("/access-keys?limit=51&offset=" + offset);
  if (state.view !== "access") return;
  content.replaceChildren();
  const label = el("input", {
    required: "",
    maxlength: "100",
    placeholder: "e.g. QA reviewer",
  });
  const role = el(
    "select",
    {},
    ...["reader", "analyst", "admin"].map((value) =>
      el("option", { value }, value),
    ),
  );
  const ids = el("textarea", { rows: "3", value: state.workspace?.id || "" });
  const issued = el("div");
  const form = el(
    "form",
    {},
    el("label", {}, "Credential name", label),
    el("label", {}, "Role", role),
    el(
      "label",
      {},
      "Workspace IDs, one per line (leave empty for administrators)",
      ids,
    ),
    el(
      "p",
      { class: "helper" },
      "Readers inspect records. Analysts can run workflows and manage baselines. Administrators manage scope, retention and access across all workspaces.",
    ),
    el("button", { type: "submit", class: "primary" }, "Create access token"),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      const result = await post("/access-keys", {
        label: label.value,
        role: role.value,
        workspace_ids: ids.value.split(/\s+/).filter(Boolean),
      });
      issued.replaceChildren(
        el("h3", {}, "Save this token now"),
        el(
          "p",
          {},
          "It is shown only once. Use it on the sign-in screen; keep it private.",
        ),
        el("textarea", {
          readonly: "",
          rows: "2",
          value: result.token,
          "aria-label": "New access token",
        }),
      );
      notice("Access token created. Reload this list after saving the token.");
    } catch (error) {
      notice(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });
  const previous = button("Previous credentials", () =>
    renderAccess(content, Math.max(0, offset - PAGE_SIZE)),
  );
  previous.disabled = offset === 0;
  const next = button("Next credentials", () =>
    renderAccess(content, offset + PAGE_SIZE),
  );
  next.disabled = entries.length <= PAGE_SIZE;
  content.append(
    panel("Create scoped access", form, issued),
    panel(
      "Issued credentials",
      table(
        ["Name", "Role", "Workspaces", "Status", ""],
        entries.slice(0, PAGE_SIZE).map((entry) => {
          const revoke = button("Revoke", async () => {
            await api("/access-keys/" + entry.id, { method: "DELETE" });
            if (entry.id === state.principal.id) {
              showLogin();
              return;
            }
            await renderAccess(content, offset);
          });
          revoke.disabled = entry.revoked;
          return el(
            "tr",
            {},
            cell(entry.label),
            cell(entry.role),
            cell(entry.workspace_ids.join(", ") || "All"),
            cell(entry.revoked ? "Revoked" : "Active"),
            cell(revoke),
          );
        }),
      ),
      el("div", { class: "actions" }, previous, next),
    ),
  );
}
async function refresh(force = false) {
  if (polling) {
    pendingRefresh ||= force;
    return;
  }
  polling = true;
  try {
    const requestedPage = pagingKey();
    const workspacePage = await api(
      "/workspaces?limit=51&offset=" + state.workspaceOffset,
    );
    if (requestedPage !== pagingKey()) {
      pendingRefresh = true;
      return;
    }
    state.workspaceMore = workspacePage.length > PAGE_SIZE;
    state.workspaces = workspacePage.slice(0, PAGE_SIZE);
    const selected =
      state.workspace?.id || localStorage.getItem("aegis-workspace");
    if (selected && !state.workspaces.some((w) => w.id === selected)) {
      const selectedWorkspace = await api("/workspaces/" + selected);
      if (requestedPage !== pagingKey()) {
        pendingRefresh = true;
        return;
      }
      state.workspaces.unshift(selectedWorkspace);
    }
    state.workspace =
      state.workspaces.find((w) => w.id === selected) ||
      state.workspaces[0] ||
      null;
    const select = $("workspace-select");
    $("previous-workspaces").disabled = state.workspaceOffset === 0;
    $("next-workspaces").disabled = !state.workspaceMore;
    const workspaceOptions = JSON.stringify(
      state.workspaces.map((w) => [w.id, w.name]),
    );
    if (select.dataset.options !== workspaceOptions) {
      select.replaceChildren(
        ...state.workspaces.map((w) => el("option", { value: w.id }, w.name)),
      );
      select.dataset.options = workspaceOptions;
    }
    if (state.workspace) {
      select.value = state.workspace.id;
      localStorage.setItem("aegis-workspace", state.workspace.id);
      $("mode-label").textContent =
        state.modes[state.workspace.mode]?.name || state.workspace.mode;
      const loadingWorkspace = state.workspace.id;
      const loadingArchive = state.archived;
      const loadingPage = pagingKey();
      const values = await Promise.all([
        listPage("runs"),
        listPage("jobs"),
        listPage("artifacts"),
        listPage("baselines"),
        listPage("audit"),
        api(base() + "/scope"),
        api(base() + "/data-policy"),
      ]);
      if (
        state.workspace?.id !== loadingWorkspace ||
        state.archived !== loadingArchive ||
        loadingPage !== pagingKey()
      ) {
        pendingRefresh = true;
        return;
      }
      [
        state.runs,
        state.jobs,
        state.artifacts,
        state.baselines,
        state.audit,
        state.scope,
        state.dataPolicy,
      ] = values;
      state.workspaceLoading = false;
      for (const resource of [
        "runs",
        "jobs",
        "artifacts",
        "baselines",
        "audit",
      ]) {
        state.more[resource] = state[resource].length > PAGE_SIZE;
        state[resource] = state[resource].slice(0, PAGE_SIZE);
      }
    }
    const next = JSON.stringify([
      state.workspace,
      state.runs,
      state.jobs,
      state.artifacts,
      state.baselines,
      state.audit,
      state.scope,
      state.dataPolicy,
    ]);
    if (force || (!editing && next !== snapshot)) {
      snapshot = next;
      render();
    }
    if (editing && state.view === "workflows") renderJobArea();
    $("sync-state").textContent =
      (frozenPages ? "Frozen list pages · " : "Synced ") +
      new Date().toLocaleTimeString();
  } catch (error) {
    notice(error.message, true);
    $("sync-state").textContent = "Connection unavailable";
  } finally {
    polling = false;
    if (pendingRefresh) {
      pendingRefresh = false;
      queueMicrotask(() => refresh(true));
    }
  }
}
function openWorkspace() {
  $("workspace-mode").replaceChildren(
    ...Object.entries(state.modes).map(([id, m]) =>
      el("option", { value: id }, m.name),
    ),
  );
  $("workspace-dialog").showModal();
  $("workspace-name").focus();
}
async function demo() {
  notice("Creating the synthetic example and recording its runs…");
  const workspace = await post("/workspaces", {
    name: "Synthetic document assurance",
    mode: "appsec",
  });
  const b = "/workspaces/" + workspace.id;
  await post(
    b + "/scope",
    {
      workspace_id: workspace.id,
      allowed_targets: ["fixture://documents/read"],
      excluded_targets: [],
      capabilities: ["fixture.evaluate", "evidence.import"],
      risk_ceiling: 1,
      starts_at: new Date(Date.now() - 60000).toISOString(),
      expires_at: new Date(Date.now() + 86400000).toISOString(),
    },
    "PUT",
  );
  let first;
  for (const [revision, observed] of [
    ["secure", "deny"],
    ["regressed", "allow"],
    ["fixed", "deny"],
  ]) {
    const payload = examplePayload("fixture", "fixture://documents/read");
    payload.target_version = revision;
    payload.checks[0].observed = observed;
    const job = await post(b + "/jobs", {
      adapter_id: "fixture",
      target: payload.target,
      payload,
    });
    let completed;
    for (let i = 0; i < 100; i++) {
      await new Promise((resolve) => setTimeout(resolve, 150));
      const jobs = await api(b + "/jobs");
      completed = jobs.find((j) => j.id === job.id);
      if (["succeeded", "failed", "cancelled"].includes(completed.status))
        break;
    }
    if (completed?.status !== "succeeded")
      throw Error("Example job did not complete. Inspect job history.");
    if (!first) first = completed.result.run_id;
  }
  await post(b + "/baselines", { name: "secure", run_id: first });
  state.workspace = workspace;
  await refresh(true);
  notice(
    "Synthetic example ready: passing, regressed and fixed observations are recorded.",
  );
}
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await post("/session", { token: $("token").value });
    $("token").value = "";
    await boot();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});
$("new-workspace").addEventListener("click", openWorkspace);
$("workspace-close").addEventListener("click", () =>
  $("workspace-dialog").close(),
);
$("detail-close").addEventListener("click", () => $("detail-dialog").close());
$("workspace-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    state.workspace = await post("/workspaces", {
      name: $("workspace-name").value,
      mode: $("workspace-mode").value,
    });
    $("workspace-dialog").close();
    $("workspace-name").value = "";
    editing = false;
    state.view = "overview";
    state.archived = false;
    await refresh(true);
    navigate("overview");
    notice("Workspace created. Define scope to run a workflow.");
  } catch (error) {
    notice(error.message, true);
  }
});
$("workspace-select").addEventListener("change", async (e) => {
  state.workspaceLoading = true;
  state.workspace = state.workspaces.find((w) => w.id === e.target.value);
  state.offsets = {};
  state.runs = [];
  state.jobs = [];
  state.artifacts = [];
  state.baselines = [];
  state.audit = [];
  state.scope = null;
  editing = false;
  state.archived = false;
  $("content").replaceChildren(
    el("p", { class: "muted" }, "Loading workspace…"),
  );
  await refresh(true);
});
document
  .querySelectorAll("nav button")
  .forEach((b) => b.addEventListener("click", () => navigate(b.dataset.view)));
$("refresh").addEventListener("click", () => {
  state.offsets = {};
  refresh(true);
});
for (const [id, direction] of [
  ["previous-workspaces", -1],
  ["next-workspaces", 1],
]) {
  $(id).addEventListener("click", () => {
    state.workspaceOffset = Math.max(
      0,
      state.workspaceOffset + direction * PAGE_SIZE,
    );
    refresh(true);
  });
}
$("logout").addEventListener("click", async () => {
  await api("/session", { method: "DELETE" });
  showLogin();
});
async function boot() {
  const requestedView = location.hash.slice(1);
  api("/auth/providers")
    .then((providers) => {
      $("github-login").hidden = !providers.github;
    })
    .catch(() => {});
  try {
    const token = new URLSearchParams(location.hash.slice(1)).get("token");
    if (token) {
      history.replaceState(null, "", location.pathname);
      await post("/session", { token });
    } else await api("/session");
    state.principal = await api("/session");
    state.workspace = null;
    state.view = "overview";
    state.offsets = {};
    state.workspaceOffset = 0;
    localStorage.removeItem("aegis-workspace");
    $("new-workspace").hidden = state.principal.role !== "admin";
    $("access-navigation").hidden = state.principal.role !== "admin";
    [state.modes, state.adapters] = await Promise.all([
      api("/modes"),
      api("/adapters"),
    ]);
    $("login").hidden = true;
    $("shell").hidden = false;
    await refresh(true);
    navigate(
      [...document.querySelectorAll("nav button")].some(
        (b) => b.dataset.view === requestedView,
      )
        ? requestedView
        : "overview",
    );
  } catch (error) {
    showLogin();
    $("login-error").textContent = error.message;
  }
}
boot();
$("freeze-pages").addEventListener("click", () => {
  frozenPages = !frozenPages;
  listSnapshots = {};
  state.offsets = {};
  $("freeze-pages").textContent = frozenPages
    ? "Return to live pages"
    : "Freeze list pages";
  refresh(true);
});
setInterval(() => {
  if (!$("shell").hidden) refresh(false);
}, 2000);
