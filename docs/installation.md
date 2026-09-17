# Installation details

The quickest install downloads the current archive and runs the same installer:

```sh
curl -fsSL https://raw.githubusercontent.com/btsouth/omarchy-usage-dashboard/main/install.sh | bash -s -- --with-plugin
```

`OMARCHY_USAGE_REF=v1.2.0` pins a release, and `OMARCHY_USAGE_TARBALL=/path/file.tar.gz` installs a local archive. From a checkout, run `python3 install.py --with-plugin` directly; both paths produce the same installation.

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
| Optional Ollama Cloud API key | `~/.config/omarchy/ai-usage/ollama.key` |
| Optional CommandCode API key | `~/.config/omarchy/ai-usage/commandcode.key` |

XDG config, data, and state paths are respected. The launchers remain under `~/.local/bin`, which should be on PATH. Set custom source-home variables such as CODEX_HOME consistently for your shell and systemd user manager if you use them.

`--with-plugin` installs the AI Usage Dashboard widget alongside the built-in Agents widget. To use it as your only AI widget, remove Agents from your bar layout and keep AI Usage Dashboard. This hides the old widget without changing its packaged files. The application menu entry and the widget's Open analytics button open the same dashboard. On uninstall, an entry changed after installation is left alone; remove it through bar settings if needed. Other shell configuration is preserved.

`--no-systemd` writes files without enabling, stopping, or reloading services. It is intended for staged installs and automated tests. Enable the installed timer manually when staging is complete.

Omarchy's native collectors maintain Codex/Claude quota snapshots. If those are unavailable, local token history still works and the UI shows missing quota information. Existing OpenCode Go credentials are read only to request quota from OpenCode's service. Ollama Cloud reads a key you supply, from `~/.config/omarchy/ai-usage/ollama.key`, the `OLLAMA_API_KEY` environment variable, or the Settings field; it is never written back or refreshed, and it stays out of any report the dashboard renders. CommandCode does the same, from `~/.config/omarchy/ai-usage/commandcode.key`, `COMMANDCODE_API_KEY`, or the Settings field.

Grok quota uses the existing CLI access token without refreshing credentials. Manual Refresh bypasses quota caches; a changed Grok login also invalidates its previous cache. Pi, Oh My Pi, and general OpenCode histories do not supply a shared account quota. Gemini can display an existing Omarchy quota snapshot, but this package does not exchange Gemini OAuth credentials.
