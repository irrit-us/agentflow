const { test, expect } = require('@playwright/test');

function fixture(id, status, nodeIds) {
  const created_at = new Date().toISOString();
  return {
    id, status, created_at, started_at: created_at,
    pipeline: { name: id === 'first-run' ? 'Parallel execution' : 'Another workflow', nodes: nodeIds.map((id, i) => ({
      id, agent: 'shell', prompt: 'Fixture only', depends_on: i ? [nodeIds[0]] : [],
    })) },
    nodes: Object.fromEntries(nodeIds.map((node_id, i) => [node_id, {
      node_id, status: i ? 'running' : 'completed', started_at: created_at,
      current_attempt: 1, exit_code: i ? null : 0, attempts: [], trace_events: [],
      output: 'Fixture output <script>not executable</script>',
    }])),
  };
}

async function mock(page, { empty = false, fail = false } = {}) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const runs = empty ? [] : [fixture('first-run', 'running', ['prepare', 'execute']), fixture('second-run', 'completed', ['different_node'])];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (fail) return route.fulfill({ status: 503, body: 'Monitor unavailable' });
    if (path.endsWith('/stream')) return route.fulfill({ contentType: 'text/event-stream', body: ': fixture\n\n' });
    const value = path === '/api/runs' ? runs : path.endsWith('/events') ? []
      : path.endsWith('/launch.json') ? { command: ['fixture-command', '--example'] }
      : path.includes('/artifacts/') ? 'Fixture log contents'
      : runs.find(run => path === `/api/runs/${run.id}`) || {};
    return route.fulfill({ contentType: typeof value === 'string' ? 'text/plain' : 'application/json', body: typeof value === 'string' ? value : JSON.stringify(value) });
  });
  await page.goto('/');
  return errors;
}

test('monitor links history, graph, inspector and artifact views', async ({ page }) => {
  const errors = await mock(page);
  await expect(page.locator('#run-meta')).toHaveText('Parallel execution');
  await expect(page.locator('#node-summary')).toContainText('1 Active');
  await expect(page.getByRole('tabpanel')).toContainText('<script>not executable</script>');
  await page.getByRole('tab', { name: 'Launch', exact: true }).click();
  await expect(page.getByRole('tabpanel')).toContainText('fixture-command');
  await page.getByRole('tab', { name: 'Launch', exact: true }).press('Home');
  await expect(page.getByRole('tab', { name: 'Output', exact: true })).toBeFocused();
  await page.getByRole('tab', { name: 'Stdout', exact: true }).click();
  await expect(page.getByRole('tabpanel')).toContainText('Fixture log contents');
  const execute = page.locator('[data-node-id="execute"]');
  const before = await execute.getAttribute('transform');
  const bounds = await execute.boundingBox();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2 + 30, { steps: 5 });
  await page.mouse.up();
  await expect(execute).not.toHaveAttribute('transform', before);
  await expect(page.locator('#selected-node')).toHaveText('prepare');
  await execute.click();
  await expect(page.locator('#selected-node')).toHaveText('execute');
  await page.locator('[data-open-run="second-run"]').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#run-meta')).toHaveText('Another workflow');
  await expect(page.locator('#selected-node')).toHaveText('different_node');
  await expect(page.locator('#cancel-run')).toBeDisabled();
  await page.locator('#run-search').fill('no match');
  await expect(page.locator('#runs')).toContainText('No matching runs');
  expect(errors).toEqual([]);
});

for (const width of [390, 768, 1024, 1440]) {
  test(`monitor fits a ${width}px viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const errors = await mock(page);
    await expect(page.locator('[data-node-id="prepare"]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator('#graph').evaluate(el => el.clientHeight)).toBeGreaterThan(250);
    await expect(page.getByRole('tab', { name: 'Launch', exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test('empty history and API failure remain understandable', async ({ page }) => {
  await mock(page, { empty: true });
  await expect(page.locator('#runs')).toContainText('No executions yet');
  await expect(page.locator('#graph')).toContainText('Your workflow, at a glance');
  await expect(page.locator('#detail')).toContainText('A closer look');
  await expect(page.locator('#rerun-run')).toBeDisabled();
  await page.unroute('**/api/**');
  await mock(page, { fail: true });
  await expect(page.locator('#banner')).toContainText('Monitor unavailable');
});
