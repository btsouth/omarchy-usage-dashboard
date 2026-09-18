# Changelog

## Unreleased

- Leave a source out of the view by switching its chip off in the source row. Its history leaves the summary, the chart, the cards, the Go allowance card, and every breakdown at once, and the chip goes struck through with the filter line naming it, so a view never quietly hides something. **Overview** brings every source back. It is a view filter: Settings still decides what this machine collects, so nothing stops being counted. The source chips are a filter now rather than a focus control, and a source that a row had drilled into keeps its highlight until the filter is cleared.

- Keep the summary card's session line inside its border. It was one unwrapped line, so a history long enough to add "· partial value" drew past the card's right edge and into the gap before the chart card. It wraps now, and the card takes its height from the column inside it, so the extra line cannot push the rest of the text below the bottom edge either.

- Keep the bar panel's provider row on one line as providers are added. The row shares the width evenly, so a fixed panel width squeezes it once a sixth provider appears and the labels clip, because those chips do not elide. The panel now asks for the width the chip row actually needs (measured against the real Button and Style tokens: 425 for five chips, 511 for six) and never exceeds the screen, so a new provider needs no width change by hand.

- Filter the whole page by model, so the summary cards, the chart, the provider cards, and every breakdown describe one model across all the routes that served it: its cached and uncached input, its output, its cache savings, and its API value, all its own. Pick one from the row under the accounts, or open a Models-table row, which also jumps to that model's sessions. Changing the period keeps the filter, so one model over 30 days is a single extra click.
- Show the period comparison under a filter instead of the words "Filtered activity", since both periods are narrowed the same way and the figure is valid, and say plainly when a filter matches nothing rather than suggesting a history folder is missing.

- Count a model once in the Models breakdown, however many routes served it. The four routes that carry DeepSeek V4.1 Flash each record a different model string, so the table used to show one entry per route with two of them reading identically. A row now answers one question, with the split by route and the string each route recorded in its detail, and prices are unchanged: each route's tokens are priced at that route's own rates before they are added up.
- Follow that grouping when a model is selected, so choosing the row, or asking a report for any of the spellings it covers, shows the whole model rather than one route's share. Pricing coverage still names the route and the exact recorded string, which is what identifies a rate table that has not caught up.
- ClinePass usage: the plan's three windows, a rolling five hours, the calendar week, and the calendar month, read from an API key you supply, each with the reset time the endpoint publishes, so these meters carry a countdown. Add the key under **Settings**, or export `CLINE_API_KEY`, or drop it in `~/.config/omarchy/ai-usage/clinepass.key`.
- Count ClinePass token history through the same agent ledger as the other routes Hermes bills, since the subscription keeps no local history of its own.
- ClinePass reference rates, transcribed from the rates its own documentation publishes for a flat-rate subscription, with the published peak window for the DeepSeek models and DeepSeek's Flash rate for `cline-pass/deepseek-v4.1-flash`, which Cline's own model table omits.

- Fix saving preferences: the save waited for its input stream to close, which never happens when the dashboard hands the payload over, so the first click left the button disabled for the rest of the window's life and wrote nothing. The save now finishes as soon as the payload arrives, it still reads a payload a scripted caller writes a moment later, a save that receives no payload reports the failure instead of closing the window as if it had worked, and a save that never returns is asked to stop after fifteen seconds and then killed, so the button cannot stay dead.
- Refuse a price or an opacity that is not a number with a fixed sentence instead of repeating the value, so a key pasted into the wrong field cannot come back in the message the window shows, and answer a payload that is not a JSON object with that same channel instead of a traceback.

## 1.3.0 - 2026-09-17

- CommandCode usage: plan windows read from an API key you supply, with the resets they publish. Its allowance is measured in credit value rather than tokens (GOAT allows $14 in any 5 hours, $35 in any 7 days, and $70 a month), and each window reports its own spend, cap, and reset time, so these meters carry a countdown where the other providers cannot. Add the key under **Settings**, or export `COMMANDCODE_API_KEY`, or drop it in `~/.config/omarchy/ai-usage/commandcode.key`.
- Count CommandCode token history through the same agent ledger as OpenCode Go and Ollama Cloud. Its two profiles for one account, one OpenAI-shaped and one Anthropic-shaped, collapse onto one card; the raw route is kept in the Routes breakdown.
- CommandCode model rates, transcribed from its own resale table rather than the labs' list prices, with its published peak window for the DeepSeek models. Free-while-capacity-lasts models price at zero instead of staying unpriced.
- One row of provider tabs: the chips share the width evenly and keep their own text width, so four or five providers sit on one line instead of wrapping, and the panel has more room on both sides.

## 1.2.0 - 2026-09-17

- Ollama Cloud usage: local token history alongside the plan's usage limits, read from an API key you supply. Add it under **Settings**, or export `OLLAMA_API_KEY`, or drop a key in `~/.config/omarchy/ai-usage/ollama.key`. The key is never written back, never leaves the request it authenticates, and never appears in anything the dashboard renders.
- Ollama Cloud model rates, including the published peak window that doubles the DeepSeek rates on weekday afternoons.
- Count the agent's own OpenCode Go and Ollama Cloud spending. Running those routes through Hermes used to hide that usage; both are now included, and they reconcile to the agent's own ledger exactly.
- Split agent-sourced usage by what it was for: typed prompts, title generation, context compression, vision, approvals, and background review.
- Treat a Hermes home as a source folder, so it can be labelled per account or imported from another machine.
- Show up to four model rows on each overview card, from local token totals. Providers whose usage endpoint reports one aggregate number cannot supply per-model shares, so the local figures are the honest view there.
- Fixes to the above before release: reasoning was counted twice for agent rows (it is a subset of output there, not a separate counter), and an agent row's accumulated total could move to a later day on each scan and rewrite the daily history. Existing installs correct themselves on the next scan.

## 1.1.1 - 2026-09-15

- Follow Omarchy theme swaps live again: the dashboard watches the stable theme directory, which survives the swap that replaces it by rename.
- Repaint theme changes from a fast palette read instead of a full history scan, removing the two to three second lag after `omarchy theme set`.
- Derive the Pi provider color from the theme's bright foreground, since no shipped theme defines `bright_white`.
- Let the demo enable a subset of agents with `--agents` for quieter screenshots.

## 1.1.0 - 2026-09-15

- Synced machine ledgers: point every machine at one shared folder and each imports the others' snapshots, so totals, charts, and model breakdowns cover all of them.
- Show synced machines in the data coverage card, and warn once per broken snapshot instead of every scan.
- Fix release pinning in the one-line installer: tags download correctly, not only branches.

## 1.0.0 - 2026-09-15

- Named history accounts with multiple folders, account comparisons, and filters.
- Deduplicate mirrored history and flag conflicting account assignments.
- API-value shares and tokens/value per recorded session.
- Grok Build history, recorded API value, model calls, and weekly quota.
- Gemini CLI, general OpenCode, Pi, and Oh My Pi history, with source-route breakdowns.
- Detect active sources on first use and adapt layouts to the enabled providers.
- Refresh Grok quota after login and request fresh limits on manual Refresh.
- Reopen unmapped dashboard windows and focus the correct instance across workspaces.
- Keep provider colors readable on light and dark backgrounds.
- Wrap bar-widget provider chips when many sources are enabled.
- One overview card per account, with optional monthly prices per provider or labelled account.
- Daily and hourly charts split into one series per account when a provider has more than one, each in its own shade of the provider color, largest solid and the others dashed.
- Labelled accounts read their own agent usage record by matching id or name, so a second Codex account shows its quota beside the current login.
- OpenCode Go prices the full documented model table, doubles DeepSeek rates during peak hours, and falls back to OpenCode's recorded cost for unlisted models.
- The Go card shows value used against each model's monthly allowance, including the DeepSeek V4.1 Flash 4x promo through Sep 20.
- Price Codex `gpt-5.3-codex-spark` and mark `codex-auto-review` internal, clearing the missing-price warning.

## Initial package

Local metric ledger, provider comparisons, period and session drilldowns, API-value estimates, and optional Omarchy bar integration. Includes an offline pricing snapshot, synthetic demo, and user-owned install/rollback workflow.
