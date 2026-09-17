# Provider coverage

This dashboard measures recorded coding activity. A source is the app that saved a request, not necessarily the company serving its model. Native Codex/Claude usage is separate from OpenCode and Pi. The Routes table shows the underlying provider when the app records it. OpenCode Go is excluded from general OpenCode totals.

## Supported history

| Source | Default location | Accounting |
| --- | --- | --- |
| Codex | `~/.codex/sessions`, `~/.codex/archived_sessions` | Cache reads are part of input; reasoning is part of output. Repeated cumulative snapshots and inherited fork history are deduplicated. |
| Claude Code | `~/.claude/projects` | Stream updates merge by message/request ID. Cache writes are separate from input; one-hour writes are a subset of cache writes. |
| Grok Build | `~/.grok/sessions/**/updates.jsonl` | Completed-turn model usage takes precedence over the aggregate. Stable event IDs deduplicate copied history. Reasoning is part of output. Turns without detailed usage are excluded. |
| Gemini CLI | `~/.gemini/tmp/*/chats` | Legacy JSON, current JSONL, and nested subagent files. Stable message IDs deduplicate migrations and updates. Thinking is added to output. Tool-prompt tokens are added only when the reported total confirms they are separate. |
| OpenCode / Go | `~/.local/share/opencode/opencode.db` and `storage/message` | Read-only database access and legacy message files. Stable message IDs merge migrated copies. Go uses its own provider ID; other routes remain under OpenCode. Reasoning is added to output. |
| Pi | `~/.pi/agent/sessions` | Assistant message usage across all saved branches. Entry IDs plus original timestamps deduplicate copied branches. Cache counters are separate; reasoning is part of output. |
| Oh My Pi | `~/.omp/agent/sessions` | The same usage categories as Pi, including current title-slot session files. Kept separate from Pi. |
| Muse | `~/.local/share/muse/sessions` | Completed model responses (`model_completed` events) across dated and subagent session files. The retained-frame envelope is unwrapped; duplicate attribution rows are ignored. Reasoning is recorded separately and is not added to output. Responses without detailed usage are excluded. |
| Hermes (OpenCode Go, Ollama Cloud, CommandCode) | `~/.hermes/state.db` | The agent's own per-route totals from `session_model_usage`, read-only. Rows are keyed by session, model, route, and task, and the table accumulates in place, so a rescan updates a row rather than adding one. Counting starts at the row's first sighting, so an accumulating total never migrates to a later day and rewrites the daily history. Reasoning stays a separate column and is never added to output: Hermes stores the provider's completion_tokens, which already include it, unlike OpenCode's disjoint counter. The task dimension becomes the client label. |

Settings accepts additional source folders, including already mounted copies from another computer. Source variables `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `GROK_HOME`, `PI_CODING_AGENT_DIR`, and `MUSE_HOME` are respected. Custom session locations outside these roots must be placed within a configured source's expected directory structure.

OpenCode Go, Ollama Cloud, and CommandCode are the routes Hermes also bills, and it writes nothing to the CLI apps' own histories. CommandCode has two profiles for the same account and product, one OpenAI-shaped and one Anthropic-shaped; both collapse onto one card, with the raw route kept for the Routes breakdown. Their rows are therefore added alongside those routes. The two never describe the same request: OpenCode records one row per assistant message under its own session ids, while Hermes records one accumulated row per session, model, route, and task under its own ids. Because Hermes reports at session-and-task granularity, its rows count as usage records rather than per-request rows, and its `api_call_count` is shown as model calls where a card reports them.

Hermes stores no dollar estimate for these routes, so its rows price from the catalog exactly as OpenCode Go rows do, and stay marked unpriced where no rate exists.

Gemini records identify projects by hash, so the dashboard labels them as Gemini project IDs. It does not guess the original filesystem path. Deleted or rewound conversation content does not refund tokens: already recorded usage stays in the metric ledger. Ephemeral sessions and calls that never write usage cannot be recovered.

## Limits and pricing

History and quota are independent. An expired login can stop quota updates while token history remains readable. Stale quota snapshots retain their timestamp and an error; absence is not treated as zero usage.

Codex and Claude use Omarchy quota snapshots. Grok and OpenCode Go have collectors in this package. Gemini can display an existing Omarchy snapshot. General OpenCode, Pi, and Oh My Pi can use several accounts and providers, so the dashboard does not assign them one quota or subscription price automatically. Muse quota (current session window and weekly allowance) is read from the existing Muse login and refreshed on scan. If the sign-in expires, run `muse login` to restore quota.

Ollama Cloud usage is read from `GET https://ollama.com/api/usage` with an API key. This endpoint is not documented by Ollama and the web settings page is its only other consumer, so it can change or disappear; when it fails, the previous snapshot stays with its timestamp and an error, and nothing else in the dashboard is affected. The response reports a fraction of each plan window under `limits`, which the meter shows directly. Legacy plans carry a session window that resets every 5 hours and a weekly one; credit plans carry a monthly window. Only the windows a plan actually reports are shown, and the endpoint supplies no reset time, so those meters show a share without a countdown. It counts GPU time on request counts rather than tokens, so the Go card's value-against-allowance view does not apply here.

CommandCode usage is read from `GET https://api.commandcode.ai/alpha/billing/credits` and `/alpha/billing/subscriptions` with an API key, the same endpoints its own CLI uses. These are not documented for third parties and can change; when a call fails, the previous snapshot stays with its timestamp and an error, and nothing else in the dashboard is affected. The plan's allowance is measured in credit value, not tokens: GOAT allows $14 in any 5 hours, $35 in any 7 days, and $70 a month. Each window reports its own spend, cap, and reset time, so unlike the other providers these meters carry a countdown. The monthly cap is read as remaining credits plus what the period has spent, which the two endpoints agree on exactly, so a plan change needs no code change. Token history comes from the Hermes ledger and is priced with CommandCode's own resale rates, not the labs' list prices. Its key is read from Settings, `COMMANDCODE_API_KEY`, or `~/.config/omarchy/ai-usage/commandcode.key`.

The Ollama Cloud key is read from Settings, then `OLLAMA_API_KEY`, then `~/.config/omarchy/ai-usage/ollama.key`, then the key file an Ollama CLI install writes (`$XDG_DATA_HOME/ollama/api_key`). It is never written anywhere but the settings file, never refreshed, and never leaves the request it authenticates. Reports the dashboard renders carry a mask in place of a stored key, and the settings channel masks it the same way, so no code path that a window or panel reads returns the secret itself. The quota cache throttles on a digest of the key, never the key.

Grok's completed-turn dollar estimate is used directly. Positive recorded API estimates from OpenCode/Pi/Oh My Pi take precedence over catalog prices. Otherwise exact catalog matches are used and missing prices remain unpriced. See [pricing policy](pricing.md).

## Other frequently requested sources

- **Cursor:** Ceiling's local code-tracking database provides activity rather than token usage. Cursor's server-side usage events can provide token categories and costs, but require a separate dashboard/API integration and suitable account access.
- **Copilot:** current CLI telemetry can expose token usage, but that requires telemetry collection rather than substituting premium requests or AI credits for tokens. IDE and CLI coverage must be distinguished.
- **Windsurf / Antigravity:** quota support alone would not establish complete per-request token history. Neither is advertised as a token-history integration here.
- **OpenRouter, DeepSeek, Kimi, MiniMax, Z.ai, and other models through OpenCode/Pi:** their locally recorded usage is covered by the host app integration. That does not include API traffic from unrelated apps or establish account-wide balances.

## Validation

The new Gemini, Pi, and Oh My Pi parsers are tested against fixtures based on their public formats. There is no real history from those three apps on the development machine, so live session validation remains distinct from fixture coverage. Grok was reconciled against retained local events, including copied history. OpenCode Go and the two retained non-Go OpenCode messages were reconciled against the local database; multi-route and legacy migration behavior also has fixture coverage. Hermes-sourced rows were reconciled against the agent's own ledger totals for both routes to the token, and the two ledgers were confirmed to share no session ids.

A correction to the Hermes parser after its first release re-reads those rows once: the parser had added Hermes' reasoning count on top of output, which double counted because Hermes stores the provider's completion_tokens (reasoning is a subset there, unlike OpenCode's disjoint counter). It had also anchored each row at its last activity, which moved an accumulating total to a later day on every rescan and rewrote the daily history. Existing installs repair themselves on the next scan through a provenance version bump, which deletes and re-reads only the affected rows.

Sources reviewed September 5, 2026:

- [Gemini session management](https://geminicli.com/docs/cli/session-management/), [recording types](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingTypes.ts), and [recording service](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts)
- [Pi session manager](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts) and [usage types](https://github.com/earendil-works/pi/blob/main/packages/ai/src/types.ts)
- [Oh My Pi session format](https://github.com/can1357/oh-my-pi/blob/main/docs/session.md)
- [OpenCode session messages](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/message-v2.ts)
- [Cursor Admin API](https://prod.cursor.com/docs/account/teams/admin-api)
- [Copilot CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
