# Changelog

## Unreleased

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
