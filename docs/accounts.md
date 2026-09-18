# Accounts and history folders

Open Settings, name the default local history group if needed, then choose **Add account**. Give it a unique name and add one or more source folders. Save preferences to scan them.

| Source | Folder to select |
| --- | --- |
| Codex | Agent home containing `sessions` or `archived_sessions` |
| Claude Code | Agent home containing `projects` |
| Grok Build | Agent home containing `sessions` |
| Gemini CLI | Agent home containing `tmp` |
| OpenCode / OpenCode Go | Data folder containing `opencode.db` or `storage/message` |
| Pi / Oh My Pi | Agent folder containing `sessions` |
| Muse | Data home containing `sessions` |
| Hermes | Hermes home containing `state.db` (add one entry per route: OpenCode Go, Ollama Cloud, CommandCode, and ClinePass) |

An account can combine Codex, Claude, and other source folders. OpenCode and OpenCode Go need separate source entries to label both sets of routes in the same database. Folders must already be available locally or mounted. No remote sync or credentials are configured here. Missing folders appear in source coverage and can be connected later.

A Hermes home (`~/.hermes`) carries every route Hermes bills. Choose the route it belongs to (OpenCode Go, Ollama Cloud, CommandCode, or ClinePass) when you add the folder, and add further entries for the other routes if you want them labelled; the folder itself holds one database. Put it under the account whose history it belongs to, so the two clients' usage for that account compares on one card instead of splitting across two.

The account filter applies to charts, totals, and breakdowns. When a provider has more than one account, the daily and hourly charts draw one series per account in its own shade of the provider color: the largest account solid, the others dashed, each labelled in the hover card. The overview shows one card per account, and the Accounts table compares account/source pairs and opens their recorded sessions. Renaming an account changes its label without reimporting tokens.

## Copies and retained history

Stable event IDs deduplicate copied sessions across folders. Multiple copies under the same account count once. A named copy takes precedence over an unlabelled copy. If copies of an event belong to different named accounts, the event counts once under **Needs review**, with a warning. Move the mirrored folders into the same account to resolve it. For nested configured folders, the most specific folder wins.

The ledger retains previously recorded usage after source files disappear. Older records whose source cannot be recovered during migration appear as **Unassigned history**. They remain in overall totals. Removing an account label keeps its history and returns its known source paths to the local group unless another configured account matches them. Unlabelled additional folders also belong to the local group.

## Synced machines

Under **Settings → Synced machines**, point every machine at the same folder (Syncthing, Dropbox, a network share) and give each one a device id, for example `desktop` and `laptop`. Each machine writes a consistent snapshot of its ledger to `<device>.sqlite` in that folder and imports the other snapshots during its normal scan. Events keep their stable ids, so a history indexed on more than one machine counts once, and local provenance wins over an imported copy. Imported activity appears under a `machine:<device>` account that you can filter, price, or ignore like any other account.

Snapshots carry token counters, model and project names, session ids, and timestamps, the same fields the local ledger stores. They do not carry prompts, response bodies, credentials, or file contents. Quota stays tied to the current login on each machine; imported accounts do not show limits. Snapshots are written with SQLite's `VACUUM INTO`, so a sync client never copies a half-written database. Remove the folder setting to stop syncing; imported events remain in the ledger.

## Limits and comparisons

History labels are not verified login identities. The local card shows the current login's quota on this PC, with its scope stated. A labelled account also shows limits when an agent usage record under `~/.local/state/omarchy/agents/usage/` has the same record id as the account, or the same name as the account label; its card says where the numbers came from. Accounts without their own record keep the current-login note. This version does not switch authentication or fetch every account's limits.

Optional monthly prices can be set per provider (the local history group) or per labelled account. Imported, conflicting, and unassigned histories never inherit the local price. API estimates still use the same recorded costs and catalog rules in every account view.

Averages divide period activity by distinct recorded sessions. Sessions are not completed tasks, and differently sized tasks cannot establish relative model efficiency. API-value shares and averages include only priced usage; unknown rates remain visibly unpriced.
