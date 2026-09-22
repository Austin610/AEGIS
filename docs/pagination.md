# API pagination

Collection endpoints accept `limit` (1-200) and `offset` (zero or greater):

- `/api/workspaces`
- `/api/workspaces/{workspace_id}/runs` (including `archived=true`)
- `/api/workspaces/{workspace_id}/jobs`
- `/api/workspaces/{workspace_id}/artifacts`
- `/api/workspaces/{workspace_id}/audit`
- `/api/workspaces/{workspace_id}/baselines`

Example: `/api/workspaces/{workspace_id}/artifacts?limit=50&offset=200`.
Responses remain JSON arrays. Advance offset by the requested limit; stop when
fewer items than the requested limit are returned. Invalid parameters return 422.
Default limits remain 200, or 100 for jobs. Baselines now default to 200 entries.

Records are ordered newest first by insertion order; baselines use ascending name.
Scope and archive filtering happen before pagination. Artifact hashes are verified
on every retrieved page. Authentication requirements are unchanged.

Offset pages are not a snapshot: insertions, archive changes or deletion between
requests can shift results. Restart at offset zero after mutations when enumerating
records. The dashboard uses 50-record pages with
Previous/Next controls and a one-record lookahead. Changing workspace or archive
view resets page offsets. Workspace selection is preserved while browsing workspace
pages. Slow responses for an outdated selection are discarded.

## Frozen pages

**Freeze list pages** captures runs, jobs, artifacts, baselines and audit lists in
separate read transactions. Existing frozen pages retain their exact content during
subsequent writes. **Return to live pages** starts live enumeration again. The workspace
selector remains a live offset list. The resources are individually consistent;
freezing does not promise a shared transaction across all five resources.

API: `GET /api/workspaces/{workspace_id}/snapshots/{resource}` returns `items`, `cursor`,
`total`, `offset`, and `expires_in_seconds`. Pass the cursor with later `limit`/`offset`
requests. A cursor is bound to principal, workspace, resource and archive selection.
Authorization is checked on every page, including revoked credentials.

Snapshots expire after five minutes or a restart. Capacity is at most 32 snapshots
and 50 MiB across the process, with 10 MiB/10,000 records per snapshot; older snapshots
may be evicted. An expired/evicted cursor requires a new enumeration, never a silent
switch to changing data. Large workspaces must use bounded live pages or narrower
workspaces. The API does not silently truncate a supposedly complete snapshot.

Verified September 14, 2026: 113 tests pass, Ruff and Mypy pass. Tests enumerate
205 artifacts/jobs and 207 workspaces across pages, verify workspace isolation,
run ordering, empty trailing pages, and invalid parameter rejection.
