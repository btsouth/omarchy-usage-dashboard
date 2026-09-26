# Agent Pulse 1.8

This branch holds the dashboard and bar-panel redesign. The plugin manifest is `1.8.0`; the public installer still follows `main`, so it serves 1.7.0 until this branch is merged and tagged.

## Compatibility

| v1.7.0 workflow | Candidate behavior |
| --- | --- |
| Bar-panel limits, balance, daily tokens, and four top models across all routes | Retained. Limits and reset times appear before hourly history; the model list follows daily tokens. |
| Provider switching and quick access to analytics | Retained in the panel source picker and Analytics button. The picker sits at the top beside the button. |
| Source, account, and model filters; period and breakdown controls | Retained in analytics. Source exclusions live under Filters. Choosing All sources clears those exclusions, while other filters remain. |
| Existing settings, saved history, bar layout, and installer rollback record | Preserved by an isolated upgrade from the v1.7.0 tag to this candidate. |
| Quota collection and refresh schedule | Retained. The live counter has a separate local `pulse` path while a view is open. |

The candidate adds a recorded Today counter, event-timed hourly tokens, reset-aware limits to watch, and up to three independently pinned limits. Tokens without an exact event time stay in the day total and are labelled separately from hourly bars. The count advances when usage is recorded, usually after a response.

## Checks completed

- 177 unit tests, Python compilation, shell syntax checks, and QML syntax parsing passed.
- The restored model rows and panel source picker loaded in an isolated Quickshell runtime with synthetic provider records. The token components, tooltip totals, compact source labels, and top-row layout matched at 460 and 320 pixel widths. A simulated mouse drag on the scrollbar moved the panel content. The compositor popup was stubbed for this check.
- The offscreen dashboard check passed at 1000 × 640: source selection, 12/24-hour labels, three pins and individual unpin, live counter updates, and account editing.
- An isolated v1.7.0 to candidate install preserved settings, ledger bytes, the existing bar entry, and the installer registry, and staged the new QML files and restored model section.
- A synthetic 10,000-file Codex history took 0.41 seconds on the first local pulse and 0.16 seconds on two unchanged pulses on the development machine. Empty files measure discovery overhead, not real parsing or a user's full history.
- On the development machine's real 3 GB Codex history, a pulse with nothing new took about 0.12 seconds of wall and CPU time. A copy of its largest transcript (275 MB) cost 1.4 seconds on first read and about 0.65 seconds of CPU on each pulse after the file grew, since a changed file is read again from the start. That is roughly 4% of one core while a view is open and such a session is active. Peak memory was about 80 MB. Pulses that overlapped a full refresh waited on the collector lock instead of working.
- The candidate ran on the author's Omarchy desktop in daily use, including the bar panel, source picker, pinned limits, and analytics.
- The project page's panel image (`docs/media/panel.png`) is the real panel in an isolated Omarchy desktop with generated records, replacing the labelled section map.

## Before public release

1. Review PR #7 out of draft, including CodeRabbit's review, and resolve every comment.
2. Merge, tag `v1.8.0`, and verify the default installer path and the installed UI.
3. Later: read changed transcripts from the last parsed offset so an active long session is not reread in full on each pulse.
