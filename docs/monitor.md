# Core monitor previews

AgentFlow core monitor with the fixed [Catppuccin Mocha palette](https://catppuccin.com/palette/).
These screenshots were captured on 2026-09-21 using mocked run and artifact
data. They illustrate the interface, not a live execution or benchmark result.

The monitor uses one fixed theme, with square corners outside the main graph.
It supports draggable nodes, canvas panning and zooming, dependency arrows,
and a tabbed node inspector.

## Desktop

Viewport: 1512 × 982 pixels.

![AgentFlow core monitor in Catppuccin Mocha, showing run history, a dependency graph, and the node inspector](images/monitor/desktop-mocha.png)

## Mobile

Viewport: 390 × 844 pixels; the image captures the full page.

<a href="images/monitor/mobile-mocha.png"><img src="images/monitor/mobile-mocha.png" width="390" alt="AgentFlow core monitor in Catppuccin Mocha with history, graph, and inspector stacked vertically"></a>

## Validation

The interface is covered by mocked browser tests in
[`tests/e2e/monitor.spec.js`](../tests/e2e/monitor.spec.js), including responsive
layouts, the fixed palette and corner styling, node dragging, arrow direction, canvas panning,
and touch interaction. See [Testing and maintainer workflows](testing.md) for
the browser test setup.
