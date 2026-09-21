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
    if (!['GET', 'HEAD'].includes(route.request().method())) errors.push('Mutating request');
    if (fail) return route.fulfill({ status: 503, body: 'Monitor unavailable' });
    if (path.endsWith('/stream')) return route.fulfill({ contentType: 'text/event-stream', body: ': fixture\n\n' });
    const value = path === '/api/runs' ? runs : path.endsWith('/events') ? []
      : path.endsWith('/tail') ? { lines: ['Fixture log contents'], before: 0, end: 20, has_more: false }
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
  await expect(page.locator('#cancel-run, #rerun-run, #run-pipeline')).toHaveCount(0);
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
  await expect(page.locator('#rerun-run')).toHaveCount(0);
  await page.unroute('**/api/**');
  await mock(page, { fail: true });
  await expect(page.locator('#banner')).toContainText('Monitor unavailable');
});

test('Catppuccin is fixed and only the main graph has rounded surfaces', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.addInitScript(() => localStorage.setItem('agentflow-theme', 'light'));
  await mock(page);
  await expect(page.locator('#theme-select')).toHaveCount(0);
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(24, 24, 37)');
  await expect(page.locator('#refresh-runs')).toHaveCSS('background-color', 'rgb(49, 50, 68)');
  for (const selector of ['.history-panel', '.detail-panel', '.topbar', '.run-item', '#refresh-runs', '#detail pre', '.brand-symbol']) {
    await expect(page.locator(selector).first()).toHaveCSS('border-radius', '0px');
  }
  await expect(page.locator('.graph-stage')).toHaveCSS('border-radius', '10px');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.reload();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(24, 24, 37)');
});

test('dragging reverses arrow ports, pans the canvas and survives refresh', async ({ page }) => {
  await mock(page);
  const node = page.locator('[data-node-id="execute"]');
  await expect(node).toBeVisible();
  const edge = page.locator('[data-from-node="prepare"][data-to-node="execute"]');
  const original = await edge.getAttribute('d');
  const box = await node.boundingBox();
  const source = await page.locator('[data-node-id="prepare"]').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(source.x - box.width, source.y + source.height / 2, { steps: 12 });
  await node.evaluate(el => { window.draggedNode = el; });
  await page.evaluate(() => applyEvent({ type: 'node_started', node_id: 'execute', data: {} }));
  expect(await node.evaluate(el => el === window.draggedNode)).toBe(true);
  await page.mouse.up();
  await expect(edge).not.toHaveAttribute('d', original);
  const direction = await edge.evaluate(el => {
    const length = el.getTotalLength();
    return el.getPointAtLength(length).x - el.getPointAtLength(length - 1).x;
  });
  expect(direction).toBeLessThan(0);
  const position = await node.getAttribute('transform');
  await page.locator('#refresh-runs').click();
  await expect(node).toHaveAttribute('transform', position);
  const svg = page.locator('#graph > svg');
  const view = await svg.getAttribute('viewBox');
  const canvas = await svg.boundingBox();
  await page.mouse.move(canvas.x + 20, canvas.y + 90);
  await page.mouse.down();
  await page.mouse.move(canvas.x + 65, canvas.y + 120, { steps: 8 });
  await page.mouse.up();
  await expect(svg).not.toHaveAttribute('viewBox', view);
  await page.getByRole('button', { name: 'Fit', exact: true }).click();
  await expect(node).toBeVisible();
});

test('touch dragging moves a node without scrolling the page', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await mock(page);
  const node = page.locator('[data-node-id="prepare"]');
  await node.scrollIntoViewIfNeeded();
  const box = await node.boundingBox();
  const before = await node.getAttribute('transform');
  const scroll = await page.evaluate(() => scrollY);
  const cdp = await page.context().newCDPSession(page);
  const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: point.x, y: point.y + 45 }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect(node).not.toHaveAttribute('transform', before);
  expect(await page.evaluate(() => scrollY)).toBe(scroll);
  await cdp.detach();
});


for (const tab of ['Stdout', 'Stderr', 'Trace']) {
  test(`${tab} follows the newest 50 entries and prepends history without jumping`, async ({ page }) => {
    await mock(page);
    let count = 120;
    const name = tab === 'Trace' ? 'trace.jsonl' : `${tab.toLowerCase()}.log`;
    await page.route(`**/artifacts/**/${name}/tail?**`, async route => {
      const url = new URL(route.request().url());
      const end = url.searchParams.has('before') ? Number(url.searchParams.get('before')) : count;
      const start = Math.max(0, end - 50);
      const lines = Array.from({ length: end - start }, (_, i) => {
        const content = `entry ${start + i} <script>safe</script>`;
        return tab === 'Trace' ? JSON.stringify({ agent: 'custom-agent', kind: 'tool', title: 'Result', content }) : content;
      });
      await route.fulfill({ json: { lines, before: start, end, has_more: start > 0 } });
    });
    await page.getByRole('tab', { name: tab, exact: true }).click();
    const viewport = page.locator('.log-viewport');
    await expect(viewport.locator('.log-line')).toHaveCount(50);
    await expect(viewport).toContainText('entry 119');
    await expect(viewport).not.toContainText('entry 69 ');
    count = 121;
    await expect(viewport).toContainText('entry 120', { timeout: 4000 });
    await viewport.evaluate(el => { el.scrollTop = 0; });
    await expect(viewport.locator('.log-line')).toHaveCount(100);
    expect(await viewport.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
    count = 122;
    await page.waitForTimeout(1700);
    await expect(viewport).not.toContainText('entry 121');
    await viewport.evaluate(el => { el.scrollTop = 0; });
    await expect(viewport).toContainText('entry 0 ');
    await viewport.evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(viewport).toContainText('entry 121', { timeout: 4000 });
    expect(await page.locator('.log-viewport script').count()).toBe(0);
  });
}
