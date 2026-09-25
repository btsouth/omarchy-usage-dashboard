# Agent Pulse 1.8 candidate

This branch holds the dashboard and bar-panel redesign for review. The plugin manifest is `1.8.0-rc.4`; the public installer still follows `main`, so merge only after the candidate is accepted.

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

## Before public release

1. Run the candidate on an Omarchy desktop and inspect the real bar panel and analytics at narrow and wide widths, in light and dark themes. Check keyboard navigation, the source picker, reset labels, pinned limits, and the restored model rows. Replace the documentation's labelled panel section map with a fresh real-panel capture.
2. Try a representative large history with active transcripts to check pulse latency and CPU use over several minutes.
3. Review draft PR #7, its rendered diff and CI, and the opt-in install pinned to the candidate commit. Keep `main` and the latest stable release unchanged until acceptance.
4. After acceptance, update the manifest to the final release version, merge, tag, and verify the installer path and installed UI again.
