const { test, expect } = require('@playwright/test');

test('the real monitor rejects browser-origin control requests', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#run-pipeline, #cancel-run, #rerun-run, #pipeline-input')).toHaveCount(0);
  const statuses = await page.evaluate(async () => {
    return Promise.all(['/api/runs', '/api/runs/validate', '/api/runs/example/cancel', '/api/runs/example/rerun']
      .map(async path => (await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status));
  });
  expect(statuses).toEqual([405, 405, 405, 405]);
});
