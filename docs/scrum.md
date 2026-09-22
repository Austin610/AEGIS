# Scrum project management

Each workspace owns independent work items and sprints. Open **Backlogs & sprints**.
The product backlog shows all work; the unplanned view shows work with no sprint;
selecting a sprint shows its sprint backlog, board, progress and burndown.

Work items support stories, tasks, subtasks and bugs; description/user-story text;
P0–P3 priority; assignee; backlog ordering; story points; acceptance criteria; and
an explicit Definition of Done checklist. Subtasks require a parent. Children
inherit the parent's sprint, and child estimates never double-count the parent's
estimate. Tasks/subtasks have zero story points: estimate stories and bugs.
Findings can create a linked backlog bug from the findings inbox. This action does
not send anything to GitHub or create an external issue.

The board has To Do, In Progress, Review, Testing and Done columns. Use the item's
edit action to change its status. Done requires nonempty, fully checked acceptance
criteria and Definition of Done, plus completed child work. These checkboxes are
human assertions; they do not automatically prove the linked security finding was
fixed. Finding closure separately requires compatible passing retest evidence.

Sprints have a goal, start date, configurable 1–90 day duration, capacity, lifecycle,
review and retrospective/action notes. One sprint can be active per workspace.
Activation freezes its dates and original point commitment. Scope may change during
the sprint; burndown records expose scope and remaining points separately. Capacity
is advisory. A completed sprint cannot be reopened; unfinished work may be moved
to the product backlog or a new sprint without being marked Done.

Burndown records the last measurement on each date when work is saved. It does not
invent historical measurements for days before tracking began. The chart includes
the original ideal commitment line and recorded actual points; the table preserves
the numbers. Reviews and retrospective notes remain editable after completion.

Writes use optimistic revisions and transactional audit records. Readers can view
but cannot edit, and records cannot link to another workspace's parent, sprint or
finding. Backups and workspace exports include the planning records. No hard delete
is exposed; history remains auditable.

Architecture: `WorkItem` is independent of `Sprint`; sprint membership is optional,
and board columns are a separate catalog. A later Kanban board can reuse the same
items, statuses, permissions and audit trail while adding WIP limits and flow
metrics. Kanban-specific behavior is not currently implemented.
