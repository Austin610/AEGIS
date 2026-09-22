# SARIF import

Choose **SARIF analysis results** in **Run a workflow** and import a SARIF JSON file,
or paste `{"document": ...}` into the workflow editor. A valid fixture scope with
`evidence.import` is required. The adapter is available in AppSec, QA, API security,
research, disclosure-notebook and training modes.

The resulting artifact provides a searchable, paginated table of recorded results
and a full JSON export. It retains a source-document checksum, adapter version,
scope decision and job reference. It does not rerun the originating analyzer or
independently validate its findings. It does not create synthetic invariant runs.

The importer supports the SARIF 2.1.0 driver/rule result subset: explicit and default
levels, rule IDs/indices, primary physical locations, artifact indices, suppression
states, baseline states and invocation outcomes. Accepted suppressions and absent
baseline results are excluded from active-failure counts. Unknown suppression status
is retained as unknown. Non-failing result kinds use level `none`. These distinctions
follow the [SARIF specification](https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html).

External files/URLs, fixes, code flows and source snippets are never retrieved or
executed. Message templates are not expanded; unresolved template IDs are labeled.
Tool extensions, configuration overrides and full schema validation are not supported.
This is a bounded report viewer, not a complete SARIF implementation. Empty results
do not imply a successful scan or establish that the analyzed code is secure.

The API caps request bodies at 1 MiB; the browser importer accepts files up to
512 KiB. The parser caps documents at 100 tool runs and 10,000 total results and
rejects invalid indices, contradictory levels, nonfinite numbers and oversized input.
Common secret patterns are redacted before storage. Markdown is shown as text.

## Metric thresholds

The threshold workflow also accepts optional `minimums` and `maximums` maps. At
least one bound is required. A missing measurement yields an unknown observation;
contradictory bounds and nonfinite numbers are rejected. Measurements, bounds,
adapter version and job ID are stored in the evidence. Maximum-only definitions
preserve their previous compatibility hash; adding a minimum changes the policy.
