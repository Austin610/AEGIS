const { test, expect } = require('@playwright/test');
const owner = 'aegis-browser-tests-only-token-000000000000';

test('readiness, frozen pages and Scrum planning are usable', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page);
  await page.getByRole('button', {name: 'Build & readiness', exact: true}).click();
  await expect(page.getByText('Acceptance checkpoints, not percentage completion of the full blueprint.')).toBeVisible();
  await expect(page.getByRole('progressbar')).toHaveCount(4);
  await page.getByRole('button', {name: 'Freeze list pages', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Return to live pages', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Return to live pages', exact: true}).click();
  await page.getByRole('button', {name: 'Backlogs & sprints', exact: true}).click();
  await page.getByRole('button', {name: 'Plan sprint', exact: true}).click();
  await page.getByLabel('Sprint name', {exact: true}).fill('Browser sprint');
  await page.getByLabel('Sprint goal', {exact: true}).fill('Ship the triage workflow');
  await page.getByLabel('Sprint duration (days)', {exact: true}).fill('10');
  await page.getByLabel('Sprint capacity (points)', {exact: true}).fill('8');
  await page.getByLabel('Sprint status', {exact: true}).selectOption('Active');
  await page.getByRole('button', {name: 'Save sprint', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Browser sprint · Ship the triage workflow'})).toBeVisible();
  await page.getByRole('button', {name: 'New story / task / bug', exact: true}).click();
  await page.getByLabel('Work item title', {exact: true}).fill('Review a finding');
  await page.getByLabel('Story points', {exact: true}).fill('5');
  await page.getByLabel('Planned sprint', {exact: true}).selectOption({label: 'Browser sprint'});
  await page.getByLabel('New Acceptance criterion', {exact: true}).fill('Finding is reviewed');
  await page.getByRole('button', {name: 'Add Acceptance criterion', exact: true}).click();
  await page.getByRole('button', {name: 'Save work item', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Edit Review a finding', exact: true})).toBeVisible();
  await expect(page.getByText(/5\/5 points remaining/)).toBeVisible();
  await page.getByRole('button', {name: 'Edit Review a finding', exact: true}).click();
  await page.getByLabel('Board status', {exact: true}).selectOption('Done');
  await page.getByLabel('Finding is reviewed', {exact: true}).check();
  await page.getByLabel('Acceptance criteria reviewed', {exact: true}).check();
  await page.getByLabel('Validation completed and evidence reviewed', {exact: true}).check();
  await page.getByRole('button', {name: 'Save work item', exact: true}).click();
  await expect(page.getByText(/0\/5 points remaining/)).toBeVisible();
  await page.getByRole('button', {name: 'Edit sprint / review', exact: true}).click();
  await page.getByLabel('Sprint review', {exact: true}).fill('Delivered and demonstrated');
  await page.getByLabel('Sprint retrospective and actions', {exact: true}).fill('Keep tasks small');
  await page.getByRole('button', {name: 'Save sprint', exact: true}).click();
  await expect(page.getByText('Delivered and demonstrated', {exact: true})).toBeVisible();
  expect(errors).toEqual([]);
});

test('requirements coverage can be recorded and edited', async ({page}) => {
  await login(page);
  await page.getByRole('button', {name: 'Requirements coverage', exact: true}).click();
  await page.getByLabel('Control ID', {exact: true}).fill('AUTH-LOCAL');
  await page.getByLabel('Requirement title', {exact: true}).fill('Review authorization evidence');
  await page.getByLabel('Assessment rationale', {exact: true}).fill('Pending evidence review');
  await page.getByRole('button', {name: 'Save requirement', exact: true}).click();
  await expect(page.getByText('AUTH-LOCAL · Review authorization evidence', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Edit assessment', exact: true}).click();
  await page.getByLabel('Assessment status', {exact: true}).selectOption('Needs review');
  await page.getByRole('button', {name: 'Save requirement', exact: true}).click();
  await expect(page.locator('td').filter({hasText: /^Needs review$/})).toBeVisible();
});

test('browse older artifacts and workspaces without losing selection', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page);
  await page.getByRole('button', {name: 'Evidence & artifacts', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Inspect artifact'})).toHaveCount(50);
  await page.getByRole('button', {name: 'Next artifacts', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Inspect artifact'})).toHaveCount(5);
  await expect(page.getByRole('button', {name: 'Next artifacts', exact: true})).toBeDisabled();
  await page.getByRole('button', {name: 'Previous artifacts', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Inspect artifact'})).toHaveCount(50);
  const selected = await page.locator('#workspace-select').inputValue();
  await page.getByRole('button', {name: 'Next workspaces', exact: true}).click();
  await expect(page.locator('#workspace-select option')).toHaveCount(8);
  await expect(page.locator('#workspace-select')).toHaveValue(selected);
  await page.locator('#workspace-select').selectOption({label: 'Older workspace 0'});
  await expect(page.getByText('No standalone artifacts yet')).toBeVisible();
  expect(errors).toEqual([]);
});

test('preview retention protects baseline runs and exports a manifest', async ({page}) => {
  await login(page);
  await page.getByRole('button', {name: 'Data policies', exact: true}).click();
  await page.getByLabel('Archive runs older than this many days').fill('30');
  await page.getByRole('button', {name: 'Save data policy', exact: true}).click();
  await page.getByRole('button', {name: 'Preview retention', exact: true}).click();
  await expect(page.getByText('2 eligible runs. Baseline runs are excluded.')).toBeVisible();
  await page.getByRole('button', {name: 'Archive these 2 runs'}).click();
  await expect(page.getByText('2 runs archived.', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Assess archived runs for deletion', exact: true}).click();
  await expect(page.getByText('0 eligible in this assessment; 2 archived runs examined.')).toBeVisible();
  await expect(page.getByText('Deletion is disabled by default. Configured deployments can prepare a reviewed selection and verify recovery before confirming deletion.')).toBeVisible();
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Download workspace bundle'}).click();
  const download = await downloading;
  const fs = require('node:fs');
  const bundle = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
  expect(bundle.manifest_sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(bundle.bundle.records.runs).toHaveLength(3);
  expect(bundle.bundle.records.archives).toHaveLength(2);
});

test('create a reader credential and revoke its browser session', async ({page, request, browser}) => {
  await login(page);
  await page.getByRole('button', {name: 'Access management', exact: true}).click();
  await page.getByLabel('Credential name').fill('Browser reader');
  await page.getByRole('button', {name: 'Create access token', exact: true}).click();
  const token = await page.getByLabel('New access token').inputValue();
  const readerContext = await browser.newContext();
  const reader = await readerContext.newPage();
  await reader.goto('http://127.0.0.1:8878');
  await reader.getByLabel('Local access token', {exact: true}).fill(token);
  await reader.getByRole('button', {name: 'Open workbench'}).click();
  await expect(reader.locator('#shell')).toBeVisible();
  await expect(reader.locator('#access-navigation')).toBeHidden();
  const response = await request.get('/api/access-keys', {headers: {Authorization: 'Bearer ' + owner}});
  const key = (await response.json()).find(key => key.label === 'Browser reader');
  await request.delete('/api/access-keys/' + key.id, {headers: {Authorization: 'Bearer ' + owner}});
  await expect(reader.locator('#login')).toBeVisible({timeout: 10000});
  await readerContext.close();
});


test('import SARIF through the workflow form and inspect its report', async ({page}) => {
  await login(page);
  await page.getByRole('navigation').getByRole('button', {name: 'Run a workflow', exact: true}).click();
  await page.getByRole('combobox', {name:'Workflow', exact: true}).selectOption('sarif');
  await page.getByRole('button', {name: 'Queue workflow', exact: true}).click();
  await expect(page.locator('#job-area')).toContainText('sarif');
  await expect(page.locator('#job-area tr').filter({hasText:'sarif'})).toContainText('succeeded');
  await page.getByRole('button', {name:'Evidence & artifacts', exact:true}).click();
  const card = page.locator('section.panel').filter({has:page.getByRole('heading',{name:'sarif report',exact:true})});
  await card.getByRole('button',{name:'Inspect artifact'}).click();
  await expect(page.locator('#detail-dialog')).toContainText('example-rule');
  await expect(page.locator('#detail-dialog')).toContainText('src/example.py:10');
  await expect(page.locator('#detail-dialog')).toContainText('not independently verified');
});

test('a finding can be triaged and linked to the product backlog', async ({page, request}) => {
  await login(page);
  const workspace = await page.locator('#workspace-select').inputValue();
  const headers = {Authorization: 'Bearer ' + owner};
  const root = '/api/workspaces/' + workspace;
  const response = await request.post(root + '/jobs', {headers, data: {
    adapter_id: 'fixture', target: 'fixture://local/sample', payload: {
      target: 'fixture://local/sample', target_version: 'triage-1', policy_version: 'triage-test', identity_set: 'browser',
      checks: [{id: 'triage', title: 'Browser triage failure', expected: 'deny', observed: 'allow', severity: 'high'}],
    },
  }});
  expect(response.ok()).toBeTruthy();
  const job = await response.json();
  await expect.poll(async () => (await (await request.get(root + '/jobs', {headers})).json()).find((j) => j.id === job.id).status).toBe('succeeded');
  await page.getByRole('button', {name: 'Findings inbox', exact: true}).click();
  await page.getByLabel('Search findings', {exact: true}).fill('Browser triage failure');
  await page.getByText('Triage · Open', {exact: true}).click();
  await page.getByLabel('Owner', {exact: true}).fill('QA reviewer');
  await page.getByLabel('Finding status', {exact: true}).selectOption('Triaged');
  await page.getByLabel('Triage notes', {exact: true}).fill('Review the recorded fixture evidence');
  await page.getByRole('button', {name: 'Save triage', exact: true}).click();
  await expect(page.getByText('Triage · Triaged', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Create backlog bug', exact: true}).click();
  await page.getByLabel('View backlog or sprint', {exact: true}).selectOption('');
  await expect(page.getByRole('button', {name: 'Edit Browser triage failure', exact: true})).toBeVisible();
});

async function login(page, token = owner) {
  await page.goto('/');
  await page.getByLabel('Local access token', {exact: true}).fill(token);
  await page.getByRole('button', {name: 'Open workbench'}).click();
  await expect(page.locator('#shell')).toBeVisible();
  await expect(page.locator('#sync-state')).toContainText('Synced');
  await page.getByLabel('Current workspace', {exact: true}).selectOption({label: 'Browser verification'});
}

test('reviewed deletion verifies recovery and requires typed confirmation', async ({page}) => {
  await login(page);
  await page.getByLabel('Current workspace', {exact: true}).selectOption({label: 'Deletion browser verification'});
  await page.getByRole('button', {name: 'Data policies', exact: true}).click();
  await page.getByRole('button', {name: 'Assess archived runs for deletion', exact: true}).click();
  await expect(page.getByText('1 eligible in this assessment; 1 archived runs examined.')).toBeVisible();
  await page.getByRole('button', {name: 'Prepare deletion and verify recovery', exact: true}).click();
  const apply = page.getByRole('button', {name: 'Permanently delete reviewed runs', exact: true});
  await expect(apply).toBeDisabled();
  await page.getByLabel('Type DELETE 1 ARCHIVED RUNS to confirm this selection').fill('DELETE 1 ARCHIVED RUNS');
  await apply.click();
  await expect(page.getByText('1 archived runs permanently deleted. Recovery copies are retained.')).toBeVisible();
});

test('archived artifact deletion uses a separate reviewed selection', async ({page}) => {
  await login(page);
  await page.getByLabel('Current workspace', {exact: true}).selectOption({label: 'Deletion browser verification'});
  await page.getByRole('button', {name: 'Data policies', exact: true}).click();
  await page.getByRole('combobox', {name: 'Record type for deletion', exact: true}).selectOption('artifacts');
  await page.getByRole('button', {name: 'Assess archived artifacts for deletion', exact: true}).click();
  await expect(page.getByText('1 eligible in this assessment; 1 archived artifacts examined.')).toBeVisible();
  await page.getByRole('button', {name: 'Prepare deletion and verify recovery', exact: true}).click();
  await page.getByLabel('Type DELETE 1 ARCHIVED ARTIFACTS to confirm this selection').fill('DELETE 1 ARCHIVED ARTIFACTS');
  await page.getByRole('button', {name: 'Permanently delete reviewed artifacts', exact: true}).click();
  await expect(page.getByText('1 archived artifacts permanently deleted. Recovery copies are retained.')).toBeVisible();
});

test('backup expiry reviews exactly one managed recovery file', async ({page}) => {
  await login(page);
  await page.getByRole('button', {name: 'Data policies', exact: true}).click();
  await page.getByRole('button', {name: 'Preview recovery backup expiry', exact: true}).click();
  const review = page.getByRole('button', {name: /^Review expiry /}).first();
  const name = (await review.textContent()).replace('Review expiry ', '');
  await review.click();
  const apply = page.getByRole('button', {name: 'Permanently expire this recovery file', exact: true});
  await expect(apply).toBeDisabled();
  await page.getByLabel('Type EXPIRE ' + name, {exact: true}).fill('EXPIRE ' + name);
  await apply.click();
  await expect(page.getByText('Recovery file removed. Preview again before reviewing another file.')).toBeVisible();
});
