"use strict";
function burndownChart(sprint, history) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 640 240");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    "Sprint burndown: recorded remaining points and ideal original commitment",
  );
  const maximum = Math.max(
    1,
    sprint.committed_points || 0,
    ...history.map((h) => h.scope_points),
  );
  const start = Date.parse(sprint.starts_on + "T00:00:00Z");
  const day = 86400000;
  const x = (date) =>
    40 +
    (Math.max(
      0,
      Math.min(
        sprint.duration_days,
        (Date.parse(date + "T00:00:00Z") - start) / day,
      ),
    ) /
      sprint.duration_days) *
      560;
  const y = (points) => 205 - (points / maximum) * 175;
  const line = (points, color, dashed = false) => {
    const node = document.createElementNS(svg.namespaceURI, "polyline");
    node.setAttribute("points", points);
    node.setAttribute("fill", "none");
    node.setAttribute("stroke", color);
    node.setAttribute("stroke-width", "3");
    if (dashed) node.setAttribute("stroke-dasharray", "6 5");
    svg.append(node);
  };
  line("40,25 40,205 605,205", "#536778");
  line(`40,${y(sprint.committed_points || 0)} 600,205`, "#93a4b5", true);
  if (history.length) {
    line(
      history.map((h) => `${x(h.date)},${y(h.remaining_points)}`).join(" "),
      "#94e4bd",
    );
    for (const h of history) {
      const dot = document.createElementNS(svg.namespaceURI, "circle");
      dot.setAttribute("cx", x(h.date));
      dot.setAttribute("cy", y(h.remaining_points));
      dot.setAttribute("r", "4");
      dot.setAttribute("fill", "#94e4bd");
      svg.append(dot);
    }
  }
  for (const [label, px, py] of [
    [maximum + " points", 40, 18],
    ["Start", 40, 228],
    ["End", 575, 228],
  ]) {
    const node = document.createElementNS(svg.namespaceURI, "text");
    node.setAttribute("x", px);
    node.setAttribute("y", py);
    node.setAttribute("fill", "#a6b7c4");
    node.setAttribute("font-size", "12");
    node.textContent = label;
    svg.append(node);
  }
  return svg;
}
async function renderPlanning(content) {
  const workspace = state.workspace.id;
  const root = base();
  const data = await api(root + "/planning");
  if (state.workspace?.id !== workspace || state.view !== "planning") return;
  const editable = state.principal?.role !== "reader";
  const reload = async () => {
    editing = false;
    await renderPlanning(content);
  };
  const text = (label, value = "", type = "text") =>
    el(type === "textarea" ? "textarea" : "input", {
      "aria-label": label,
      value,
      ...(type === "textarea" ? { rows: 3 } : { type }),
      disabled: !editable,
    });
  const choose = (label, choices, value) => {
    const node = el(
      "select",
      { "aria-label": label, disabled: !editable },
      choices.map(([id, name]) => el("option", { value: id }, name)),
    );
    node.value = value;
    return node;
  };
  const formArea = el("div");
  const detailArea = el("div");
  const bind = (fields) => {
    const form = el(
      "div",
      {},
      Object.entries(fields).map(([name, field]) =>
        el("label", {}, name.replaceAll("_", " "), field),
      ),
    );
    form.addEventListener("input", () => {
      editing = true;
    });
    return form;
  };
  const checklist = (label, values = []) => {
    const area = el("div");
    const entries = values.map((c) => ({ ...c }));
    const draw = () => {
      area.replaceChildren(
        ...entries.map((c) => {
          const checked = el("input", {
            type: "checkbox",
            checked: c.done,
            disabled: !editable,
            onchange: (e) => {
              c.done = e.target.checked;
              editing = true;
            },
          });
          return el("label", { class: "check" }, checked, c.text);
        }),
      );
      const addText = text("New " + label);
      const add = button("Add " + label, () => {
        if (!addText.value.trim()) return;
        entries.push({ text: addText.value.trim(), done: false });
        editing = true;
        draw();
      });
      add.disabled = !editable;
      area.append(addText, add);
    };
    draw();
    return { area: panel(label, area), values: entries };
  };
  const editItem = (item = {}) => {
    editing = true;
    const fields = {
      title: text("Work item title", item.title || ""),
      kind: choose(
        "Work item type",
        ["story", "task", "subtask", "bug"].map((s) => [s, s]),
        item.kind || "story",
      ),
      description: text(
        "User story or description",
        item.description || "",
        "textarea",
      ),
      status: choose(
        "Board status",
        data.columns.map((s) => [s, s]),
        item.status || "To Do",
      ),
      priority: choose(
        "Work item priority",
        ["P0", "P1", "P2", "P3"].map((s) => [s, s]),
        item.priority || "P2",
      ),
      assignee: text("Assignee", item.assignee || ""),
      story_points: text("Story points", item.story_points || 0, "number"),
      rank: text("Backlog order", item.rank || 0, "number"),
      parent_id: choose(
        "Parent item",
        [
          ["", "No parent"],
          ...data.items
            .filter((i) => i.id !== item.id)
            .map((i) => [i.id, i.title]),
        ],
        item.parent_id || "",
      ),
      sprint_id: choose(
        "Planned sprint",
        [
          ["", "Product backlog / inherit parent"],
          ...data.sprints.map((s) => [s.id, s.name]),
        ],
        item.sprint_id || "",
      ),
      finding_id: text("Linked finding ID", item.finding_id || ""),
    };
    const acceptance = checklist(
      "Acceptance criterion",
      item.acceptance_criteria,
    );
    const done = checklist(
      "Definition of Done",
      item.definition_of_done || [
        { text: "Acceptance criteria reviewed", done: false },
        { text: "Validation completed and evidence reviewed", done: false },
      ],
    );
    const save = button(
      "Save work item",
      async () => {
        const body = Object.fromEntries(
          Object.entries(fields).map(([key, value]) => [key, value.value]),
        );
        for (const key of ["parent_id", "sprint_id", "finding_id"])
          body[key] ||= null;
        body.story_points = Number(body.story_points);
        body.rank = Number(body.rank);
        body.revision = item.revision || 0;
        body.acceptance_criteria = acceptance.values;
        body.definition_of_done = done.values;
        await post(
          root + "/items/" + (item.id || crypto.randomUUID()),
          body,
          "PUT",
        );
        await reload();
      },
      true,
    );
    save.disabled = !editable;
    formArea.replaceChildren(
      panel(
        item.id ? "Edit work item" : "New work item",
        el(
          "p",
          { class: "helper" },
          "Stories: As a … I want … so that … . Child tasks inherit the parent sprint; estimate story points on stories and bugs.",
        ),
        bind(fields),
        acceptance.area,
        done.area,
        save,
        button("Cancel editing", reload),
      ),
    );
    formArea.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  const editSprint = (sprint = {}) => {
    editing = true;
    const fields = {
      name: text("Sprint name", sprint.name || ""),
      goal: text("Sprint goal", sprint.goal || "", "textarea"),
      starts_on: text(
        "Sprint start",
        sprint.starts_on || new Date().toISOString().slice(0, 10),
        "date",
      ),
      duration_days: text(
        "Sprint duration (days)",
        sprint.duration_days || 14,
        "number",
      ),
      capacity_points: text(
        "Sprint capacity (points)",
        sprint.capacity_points || 0,
        "number",
      ),
      status: choose(
        "Sprint status",
        ["Planned", "Active", "Completed"].map((s) => [s, s]),
        sprint.status || "Planned",
      ),
      review: text("Sprint review", sprint.review || "", "textarea"),
      retrospective: text(
        "Sprint retrospective and actions",
        sprint.retrospective || "",
        "textarea",
      ),
    };
    const save = button(
      "Save sprint",
      async () => {
        const body = Object.fromEntries(
          Object.entries(fields).map(([key, value]) => [key, value.value]),
        );
        body.duration_days = Number(body.duration_days);
        body.capacity_points = Number(body.capacity_points);
        body.revision = sprint.revision || 0;
        await post(
          root + "/sprints/" + (sprint.id || crypto.randomUUID()),
          body,
          "PUT",
        );
        await reload();
      },
      true,
    );
    save.disabled = !editable;
    formArea.replaceChildren(
      panel(
        "Sprint planning, review and retrospective",
        bind(fields),
        save,
        button("Cancel editing", reload),
      ),
    );
    formArea.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  const sprintSelect = choose(
    "View backlog or sprint",
    [
      ["", "Product backlog (all work)"],
      ["unplanned", "Unplanned work"],
      ...data.sprints.map((s) => [s.id, s.name + " · " + s.status]),
    ],
    data.sprints.find((s) => s.status === "Active")?.id || "",
  );
  sprintSelect.disabled = false;
  const drawBoard = () => {
    const selected = sprintSelect.value;
    const sprint = data.sprints.find((s) => s.id === selected);
    const items = data.items.filter(
      (i) =>
        selected === "" ||
        (selected === "unplanned"
          ? !i.effective_sprint_id
          : i.effective_sprint_id === selected),
    );
    detailArea.replaceChildren();
    if (sprint) {
      detailArea.append(
        panel(
          sprint.name + " · " + sprint.goal,
          el(
            "p",
            {},
            `${sprint.starts_on} → ${sprint.ends_on} · ${sprint.duration_days} days · capacity ${sprint.capacity_points} points`,
          ),
          el(
            "p",
            {},
            `${sprint.completed_items}/${sprint.total_items} items done · ${sprint.remaining_points}/${sprint.total_points} points remaining · original commitment ${sprint.committed_points ?? "not started"}`,
          ),
          el("progress", {
            max: Math.max(1, sprint.total_items),
            value: sprint.completed_items,
            "aria-label": "Sprint progress",
          }),
          sprint.total_points > sprint.capacity_points
            ? el(
                "p",
                { class: "muted" },
                "Planned points exceed the configured capacity.",
              )
            : null,
          button("Edit sprint / review", () => editSprint(sprint)),
        ),
      );
      const history = data.burndown
        .filter((b) => b.sprint_id === selected)
        .sort((a, b) => a.date.localeCompare(b.date));
      detailArea.append(
        panel(
          "Burndown",
          burndownChart(sprint, history),
          el(
            "p",
            { class: "small muted" },
            "Green: recorded remaining points. Dashed: ideal burn of the original commitment.",
          ),
          el(
            "p",
            { class: "helper" },
            "Daily recorded points reflect work changes. Scope changes remain visible; child tasks do not double-count story points.",
          ),
          table(
            ["Date", "Scope points", "Remaining points"],
            history.map((b) =>
              el(
                "tr",
                {},
                cell(b.date),
                cell(b.scope_points),
                cell(b.remaining_points),
              ),
            ),
          ),
          el("h3", {}, "Sprint review"),
          el("p", {}, sprint.review || "Not recorded"),
          el("h3", {}, "Retrospective and actions"),
          el("p", {}, sprint.retrospective || "Not recorded"),
        ),
      );
    }
    const columns = el("div", { class: "scrum-board" });
    for (const status of data.columns) {
      const cards = items
        .filter((i) => i.status === status)
        .map((i) =>
          el(
            "article",
            { class: "scrum-card" },
            el("strong", {}, i.title),
            el("p", {}, `${i.kind} · ${i.priority} · ${i.story_points} points`),
            el("p", { class: "muted small" }, i.assignee || "Unassigned"),
            i.parent_id
              ? el(
                  "p",
                  { class: "small" },
                  "Parent: " +
                    (data.items.find((p) => p.id === i.parent_id)?.title ||
                      i.parent_id),
                )
              : null,
            button("Edit " + i.title, () => editItem(i)),
          ),
        );
      columns.append(
        el(
          "section",
          { class: "scrum-column" },
          el("h3", {}, status + " · " + cards.length),
          ...cards,
        ),
      );
    }
    detailArea.append(columns);
  };
  sprintSelect.addEventListener("change", drawBoard);
  const newItem = button("New story / task / bug", () => editItem(), true);
  const newSprint = button("Plan sprint", () => editSprint());
  newItem.disabled = newSprint.disabled = !editable;
  content.replaceChildren(
    panel(
      "Product and sprint backlogs",
      el(
        "p",
        {},
        "Prioritize work, plan capacity, and move items through review and testing. Done requires checked acceptance criteria and Definition of Done.",
      ),
      sprintSelect,
      el("div", { class: "actions" }, newItem, newSprint),
    ),
    detailArea,
    formArea,
  );
  drawBoard();
}
