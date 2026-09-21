# Core monitor previews

AgentFlow core monitor with the fixed [Catppuccin Mocha palette](https://catppuccin.com/palette/).
These screenshots were captured on 2026-09-21 using mocked run and artifact
data. They illustrate the interface, not a live execution or benchmark result.

The monitor uses one fixed theme, with square corners outside the main graph.
It supports draggable nodes, canvas panning and zooming, dependency arrows,
and a tabbed node inspector.

## Read-only behavior and live logs

The monitor service only allows GET and HEAD. It exposes no pipeline creation,
validation, cancellation, or rerun endpoints. Search, tabs, graph dragging, and
zooming only change the local view. Runs are managed through CLI or Python APIs.

Stdout and Stderr initially display the latest 50 lines. Trace displays the latest
50 normalized JSONL records, including their provider, kind, title, and content.
Scroll upward to prepend earlier windows; the viewport preserves its position.
At the bottom, the visible log refreshes every 1.5 seconds. While reading history,
new content does not move the viewport; scroll back to the bottom to follow it.
File byte cursors keep historical pages stable while producers append output.
Raw stdout and stderr remain available for inspection; terminal color escapes
are stripped only in the display and HTML is rendered as text.

Output compatibility is checked with representative mocked records for all
14 built-in agent kinds plus a custom provider, including attempt initialization
and retry resets, and the existing parser suite. The ZCode attempt initializer
was corrected to avoid a slotted-dataclass `super()` failure.
Codex, Claude, Kimi, Pi, OpenCode/Kilo, Goose, DeepSeek, and ZCode use specialized
parsers; remaining kinds retain generic text output. These checks validate the
implemented formats, not every version of real third-party CLI software.

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
