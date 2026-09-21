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

test('reached-only view expands every future branch and one parallel worker preview', async ({ page }) => {
  const errors = await mock(page);
  const run = fixture('branched', 'running', ['gate', 'worker_0', 'worker_1', 'worker_2', 'alternative', 'join']);
  run.pipeline.fanouts = { worker: ['worker_0', 'worker_1', 'worker_2'] };
  run.nodes.gate.status = 'running';
  for (const node of run.pipeline.nodes.slice(1)) {
    run.nodes[node.id] = { status: 'pending', current_attempt: 0, attempts: [] };
  }
  run.pipeline.nodes[5].depends_on = [...run.pipeline.fanouts.worker, 'alternative'];
  run.pipeline.nodes[4].depends_on = [];
  run.pipeline.nodes[4].depends_on_failure = ['gate'];
  run.pipeline.nodes[4].on_failure_restart = ['gate'];
  await page.route('**/api/runs**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runs') return route.fulfill({ json: [run] });
    if (path === '/api/runs/branched') return route.fulfill({ json: run });
    return route.fallback();
  });
  await page.reload();
  await expect(page.locator('[data-node-id="gate"]')).toBeVisible();
  await expect(page.locator('#graph g[data-node-id]')).toHaveCount(1);
  const toggle = page.getByRole('button', { name: 'Show default', exact: true });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  const preview = page.locator('[data-kind="worker-preview"]');
  await expect(preview).toHaveCount(1);
  await expect(preview).toContainText('worker ×3');
  await expect(page.locator('[data-node-id="alternative"]')).toBeVisible();
  await expect(page.locator('[data-node-id="join"]')).toBeVisible();
  await expect(page.locator('path[data-kind="potential"]').first()).toHaveAttribute('stroke-dasharray', '6,4');
  await expect(page.locator('path[data-kind="restart"]')).toHaveAttribute('data-to-node', 'gate');
  await page.evaluate(() => applyEvent({ type: 'node_started', node_id: 'worker_0', data: {} }));
  await expect(preview).toContainText('worker ×2');
  await expect(page.locator('[data-node-id="worker_0"]')).toHaveAttribute('data-kind', 'utility');
  await toggle.click();
  await expect(page.locator('#graph g[data-node-id]')).toHaveCount(2);
  await expect(page.locator('[data-node-id="worker_0"]')).toBeVisible();
  await expect(page.locator('path[data-to-node="worker_0"]')).not.toHaveAttribute('stroke-dasharray');
  expect(errors).toEqual([]);
});

test('projection retains execution history and omits skipped, cancelled and unrelated future nodes', async ({ page }) => {
  await mock(page);
  const result = await page.evaluate(() => {
    const nodes = [{ id: 'root' }, { id: 'skipped', depends_on: ['root'] },
      { id: 'cancelled', depends_on: ['root'] }, { id: 'reset', depends_on: ['root'] },
      { id: 'future', depends_on: ['reset'] }, { id: 'unrelated' }];
    const statuses = { root: { status: 'completed' }, skipped: { status: 'skipped' },
      cancelled: { status: 'cancelled' }, reset: { status: 'pending', current_attempt: 2 } };
    return { reached: projectMonitorNodes(nodes, statuses, {}, false).map(node => node.id),
      defaults: projectMonitorNodes(nodes, statuses, {}, true).map(node => node.id) };
  });
  expect(result).toEqual({ reached: ['root', 'reset'], defaults: ['root', 'reset', 'future'] });
});

test('an unstarted run still offers Show default without exposing execution controls', async ({ page }) => {
  await mock(page);
  await expect(page.locator('[data-node-id="prepare"]')).toBeVisible();
  await page.evaluate(() => {
    state.pipeline = { nodes: [{ id: 'root' }, { id: 'future', depends_on: ['root'] }] };
    state.nodes = {};
    renderGraph();
  });
  await expect(page.locator('#graph g[data-node-id]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Show default', exact: true }).click();
  await expect(page.locator('#graph g[data-node-id]')).toHaveCount(2);
});

test('progress counts the longest dependency path, not worker count or restart iterations', async ({ page }) => {
  await mock(page);
  await expect(page.locator('[data-node-id="prepare"]')).toBeVisible();
  const result = await page.evaluate(() => {
    const nodes = [{ id: 'root' }, ...Array.from({ length: 20 }, (_, i) => ({ id: `worker_${i}`, depends_on: ['root'] })),
      { id: 'join', depends_on: Array.from({ length: 20 }, (_, i) => `worker_${i}`), on_failure_restart: ['root'] },
      { id: 'end', depends_on: ['join'] }];
    const run = { ...state.runs[0], pipeline: { nodes }, nodes: Object.fromEntries(nodes.map(node => [node.id,
      { status: node.id === 'join' ? 'running' : node.id === 'end' ? 'pending' : 'completed', current_attempt: 5 }])) };
    state.runs = [run];
    state.nodes = run.nodes;
    renderRuns();
    const parallel = runPathProgress(run);
    run.nodes.worker_0.status = 'running';
    run.nodes.join.status = 'completed';
    const unfinishedDependency = runPathProgress(run);
    run.pipeline.nodes = [{ id: 'a', depends_on: ['b'] }, { id: 'b', depends_on: ['a'] }];
    const cyclic = runPathProgress(run);
    return { parallel, unfinishedDependency, cyclic };
  });
  expect(result.parallel).toEqual({ total: 4, done: 2 });
  expect(result.unfinishedDependency).toEqual({ total: 4, done: 2 });
  expect(result.cyclic.total).toBe(2);
  await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '4');
  await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '2');
});

test('self restart is a visible solid loop and Fit includes its path', async ({ page }) => {
  await mock(page);
  await expect(page.locator('[data-node-id="prepare"]')).toBeVisible();
  await page.evaluate(() => {
    state.pipeline = { name: 'self', nodes: [{ id: 'loop', agent: 'python', depends_on: [], on_failure_restart: ['loop'] }] };
    state.nodes = { loop: { status: 'running' } };
    state.selectedNodeId = null;
    renderGraph();
  });
  const loop = page.locator('path[data-kind="restart"]');
  await expect(loop).not.toHaveAttribute('stroke-dasharray');
  const geometry = await loop.evaluate(el => {
    const bounds = el.getBBox();
    const view = el.ownerSVGElement.viewBox.baseVal;
    return { width: bounds.width, height: bounds.height, inside: bounds.x >= view.x && bounds.y >= view.y
      && bounds.x + bounds.width <= view.x + view.width && bounds.y + bounds.height <= view.y + view.height };
  });
  expect(geometry.width).toBeGreaterThan(30);
  expect(geometry.height).toBeGreaterThan(30);
  expect(geometry.inside).toBe(true);
});


test('path progress follows streamed completions immediately', async ({ page }) => {
  await mock(page);
  await expect(page.locator('[data-node-id="prepare"]')).toBeVisible();
  const progress = page.locator('[data-open-run="first-run"] [role="progressbar"]');
  await expect(progress).toHaveAttribute('aria-valuenow', '1');
  await page.evaluate(() => applyEvent({ type: 'node_completed', node_id: 'execute', data: { exit_code: 0 } }));
  await expect(progress).toHaveAttribute('aria-valuenow', '2');
});

test('multi-node restart points back to its target and remains inside Fit', async ({ page }) => {
  await mock(page);
  await expect(page.locator('[data-node-id="prepare"]')).toBeVisible();
  await page.evaluate(() => {
    state.pipeline = { nodes: [{ id: 'a' }, { id: 'b', depends_on: ['a'] },
      { id: 'c', depends_on: ['b'], on_failure_restart: ['a'] }] };
    state.nodes = { a: { status: 'completed' }, b: { status: 'completed' }, c: { status: 'running' } };
    renderGraph();
  });
  const edge = page.locator('path[data-kind="restart"]');
  await expect(edge).toHaveAttribute('data-from-node', 'c');
  await expect(edge).toHaveAttribute('data-to-node', 'a');
  await expect(edge).toHaveAttribute('marker-end', 'url(#graph-arrow-cycle)');
  const geometry = await edge.evaluate(el => {
    const start = el.getPointAtLength(0), end = el.getPointAtLength(el.getTotalLength());
    const box = el.getBBox(), view = el.ownerSVGElement.viewBox.baseVal;
    return { backwards: end.x < start.x, inside: box.x >= view.x && box.y >= view.y
      && box.x + box.width <= view.x + view.width && box.y + box.height <= view.y + view.height };
  });
  expect(geometry).toEqual({ backwards: true, inside: true });
});
