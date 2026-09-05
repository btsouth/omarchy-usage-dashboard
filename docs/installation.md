# Installation details

The installer runs without sudo. It checks all tracked destinations before changing files, records rollback information, and writes replacements atomically. It does not modify `/usr/share/omarchy`.

| Item | Default location |
| --- | --- |
| Installed application | `~/.local/share/omarchy-usage-dashboard/app` |
| Launcher | `~/.local/bin/omarchy-usage-dashboard` |
| Refresh wrapper | `~/.local/bin/omarchy-usage-dashboard-refresh` |
| Desktop entry | `~/.local/share/applications/omarchy-usage-dashboard.desktop` |
| Optional plugin | `~/.config/omarchy/plugins/community.ai-usage-dashboard` |
| Refresh timer/service | `~/.config/systemd/user/omarchy-usage-dashboard.{timer,service}` |
| Installer registry | `~/.local/state/omarchy-usage-dashboard/installation.json` |
| Metric ledger and quota cache | `~/.local/state/omarchy/ai-usage/` |
| Preferences | `~/.config/omarchy/ai-usage/settings.json` |

XDG config, data, and state paths are respected. The launchers remain under `~/.local/bin`, which should be on PATH. Set custom source-home variables such as CODEX_HOME consistently for your shell and systemd user manager if you use them.

`--with-plugin` adds a separate widget, never replaces the stock Agents widget. You may remove the old widget through Omarchy's bar settings if you prefer a single icon. On uninstall, an entry changed after installation is left alone; remove it through bar settings if needed. Other shell configuration is preserved.

`--no-systemd` writes files without enabling, stopping, or reloading services. It is intended for staged installs and automated tests. Enable the installed timer manually when staging is complete.

Omarchy's native collectors maintain Codex/Claude quota snapshots. If those are unavailable, local token history still works and the UI shows missing quota information. Existing OpenCode Go credentials are read only to request quota from OpenCode's service.
