# AI Usage Dashboard for Omarchy

Compare Codex, Claude Code, Grok Build, Gemini CLI, OpenCode, Pi, and Oh My Pi usage from your desktop. Token history, usage limits, model and project breakdowns, and estimated API value in a native Omarchy dashboard.

![Dashboard with generated example data](docs/dashboard.png)

*Screenshot uses generated demo data.*

## Features

- Today, 7-day, 30-day, 90-day, and yearly views.
- Daily trends and hourly detail for a selected day.
- Input, output, and cache totals, with cached share beside the headline.
- Model, project, client, source-route, and session drilldowns.
- Named accounts with multiple history folders, account filters, and deduplication.
- Estimated API value, source shares, and averages per recorded session, with missing prices clearly marked.
- Source coverage, available subscription limits, and Omarchy theme colors.

## Install

Requires **Omarchy 4 with its Quickshell shell**, Python 3.11+, and a systemd user session. No Python packages to install. Older Waybar-based Omarchy versions are not supported.

```sh
git clone https://github.com/btsouth/omarchy-usage-dashboard.git
cd omarchy-usage-dashboard
python3 install.py --with-plugin
~/.local/bin/omarchy-usage-dashboard
```

The installer adds a separate bar widget and application launcher. Existing widgets and settings stay in place. If the widget does not appear, run:

```sh
omarchy-shell shell rescanPlugins
```

The bar icon appears once usage is available. The application launcher works with an empty history. The first scan can take longer for large histories.

Omit `--with-plugin` to install only the dashboard and background collector. The app is copied into your user data directory, so it keeps working if you move or delete the checkout. No sudo or changes to packaged Omarchy files are needed.

## Update or uninstall

From the checkout:

```sh
git pull --ff-only
python3 install.py --with-plugin
```

Use the same installer options as your initial install. Updates stop before overwriting installed files you have edited.

To uninstall, quit the dashboard with **Ctrl+Q**, then run:

```sh
python3 install.py --uninstall
```

Uninstall stops the refresh timer and removes or restores the files it manages. Your usage history, preferences, and later file edits are preserved. See [installation details](docs/installation.md) for paths and rollback behavior.

## Understanding the numbers

**Processed tokens include reused context.** Sending a large cached context on every request counts it again each time. The headline shows the cached share and output total so processed usage is not mistaken for unique text.

API value uses the bundled catalog rates or the estimate recorded by the coding app. It is not your subscription bill or quota consumption. Missing or incomplete rates stay visibly unpriced. See [pricing details](docs/pricing.md) for sources, cache accounting, and custom rates.

A background timer scans history and refreshes OpenCode Go and enabled Grok quota every 15 minutes. Manual Refresh also requests fresh Codex and Claude limit snapshots from Omarchy's collectors. Go uses the API key already configured in OpenCode; no cookie setup is needed. Enable Grok Build in Settings to show its history and fetch weekly quota using the existing Grok login. If that login expires, run `grok login`. The dashboard does not refresh or change credentials.

## Multiple accounts

In Settings, add an account name and its agent home folders, such as `/mnt/work/.codex` and `/mnt/work/.claude`. Each account can contain several sources or synced copies. Use the account filter or Accounts breakdown to compare them. See [account setup](docs/accounts.md) for folder formats and deduplication.

These labels group history. They do not switch credentials or collect quota for every account. All accounts shows the current login’s quota separately; filtered account views hide it. Monthly plan comparisons are hidden when the view contains imported or unassigned account history.

A recorded session is not a completed task. Per-session averages describe activity in the selected period, not which model finished equivalent work more efficiently.

## Coverage and privacy

| Source | Local history | Account limits |
| --- | --- | --- |
| Codex | CLI and Codex desktop rollouts, including archives | Omarchy collector |
| Claude Code | Assistant usage in project transcripts | Omarchy collector |
| Grok Build | Completed turns, model calls, recorded API value | Existing Grok login |
| Gemini CLI | JSON/JSONL sessions and nested subagents | If an Omarchy quota snapshot is available |
| OpenCode Go | Records routed through `opencode-go` | Existing OpenCode Go connection |
| OpenCode | Other routes, with a separate route breakdown | Not collected |
| Pi / Oh My Pi | Assistant usage, including saved branches | Not collected |

On a fresh install, sources with recorded usage appear automatically. Use Settings to choose visible sources, set optional monthly prices, and add mounted or synced history folders. Existing preferences are preserved. The dashboard does not perform remote sync.

Normal ChatGPT, Grok web, and Gemini web conversations are not included. Cursor, Copilot, Windsurf, and Antigravity need separate integrations. Missing telemetry is never converted into estimated token counts. See [provider coverage](docs/provider-coverage.md) for formats, limitations, and validation.

Metrics stay on your machine. Stored records include token counters, model, project path, client, session ID, and time. Conversation bodies and credentials are not copied. There is no telemetry. Review project names and paths before sharing screenshots or reports.

## Demo

Try the UI with generated data, without reading your history or credentials:

```sh
python3 demo.py
```

Press Ctrl+C in the terminal to stop the demo. To render a screenshot offscreen:

```sh
python3 demo.py --capture /tmp/usage-dashboard.png
```

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile collector.py install.py demo.py
omarchy plugin validate ./plugin
```

Tests cover token accounting, deduplication, provider filters, pricing, installation, and rollback. See [CONTRIBUTING.md](CONTRIBUTING.md) for UI checks.

## Credits

Independent community project. The bar plugin is adapted from Omarchy's Agents plugin, and the bundled pricing snapshot comes from LiteLLM. Both retain their original license notices.

[MIT license](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)
