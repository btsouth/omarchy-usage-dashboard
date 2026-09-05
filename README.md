# AI Usage Dashboard for Omarchy

Compare Codex, Claude Code, and OpenCode Go usage from your desktop. Token history, usage limits, model and project breakdowns, and estimated API value in a native Omarchy dashboard.

![Dashboard with generated example data](docs/dashboard.png)

*Screenshot uses generated demo data.*

## Features

- Today, 7-day, 30-day, 90-day, and yearly views.
- Daily trends and hourly detail for a selected day.
- Input, output, and cache totals, with cached share beside the headline.
- Model, project, client, and session drilldowns.
- Estimated API value with missing prices clearly marked.
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

API value estimates what recorded tokens would cost at the bundled catalog's rates. It is not your subscription bill or quota consumption. Missing or incomplete rates stay visibly unpriced. See [pricing details](docs/pricing.md) for sources, cache accounting, and custom rates.

A background timer scans history and refreshes OpenCode Go quota every 15 minutes. Manual Refresh also requests fresh Codex and Claude limit snapshots from Omarchy's collectors. Go uses the API key already configured in OpenCode; no cookie setup is needed.

## Coverage and privacy

The dashboard reads:

- Codex active and archived rollouts, including desktop sessions that use the configured Codex home.
- Claude Code transcripts.
- OpenCode assistant records routed through the `opencode-go` provider.

Normal ChatGPT conversations and other OpenCode providers are not included. Add already mounted or synced history folders in Settings to include another computer. The dashboard does not perform remote sync.

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
