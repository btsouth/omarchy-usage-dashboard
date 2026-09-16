# Changelog

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
