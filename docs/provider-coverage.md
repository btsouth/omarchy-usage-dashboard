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

Settings accepts additional source folders, including already mounted copies from another computer. Source variables `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `GROK_HOME`, and `PI_CODING_AGENT_DIR` are respected. Custom session locations outside these roots must be placed within a configured source's expected directory structure.

Gemini records identify projects by hash, so the dashboard labels them as Gemini project IDs. It does not guess the original filesystem path. Deleted or rewound conversation content does not refund tokens: already recorded usage stays in the metric ledger. Ephemeral sessions and calls that never write usage cannot be recovered.

## Limits and pricing

History and quota are independent. An expired login can stop quota updates while token history remains readable. Stale quota snapshots retain their timestamp and an error; absence is not treated as zero usage.

Codex and Claude use Omarchy quota snapshots. Grok and OpenCode Go have collectors in this package. Gemini can display an existing Omarchy snapshot. General OpenCode, Pi, and Oh My Pi can use several accounts and providers, so the dashboard does not assign them one quota or subscription price automatically.

Grok's completed-turn dollar estimate is used directly. Positive recorded API estimates from OpenCode/Pi/Oh My Pi take precedence over catalog prices. Otherwise exact catalog matches are used and missing prices remain unpriced. See [pricing policy](pricing.md).

## Other frequently requested sources

- **Cursor:** Ceiling's local code-tracking database provides activity rather than token usage. Cursor's server-side usage events can provide token categories and costs, but require a separate dashboard/API integration and suitable account access.
- **Copilot:** current CLI telemetry can expose token usage, but that requires telemetry collection rather than substituting premium requests or AI credits for tokens. IDE and CLI coverage must be distinguished.
- **Windsurf / Antigravity:** quota support alone would not establish complete per-request token history. Neither is advertised as a token-history integration here.
- **OpenRouter, DeepSeek, Kimi, MiniMax, Z.ai, and other models through OpenCode/Pi:** their locally recorded usage is covered by the host app integration. That does not include API traffic from unrelated apps or establish account-wide balances.

## Validation

The new Gemini, Pi, and Oh My Pi parsers are tested against fixtures based on their public formats. There is no real history from those three apps on the development machine, so live session validation remains distinct from fixture coverage. Grok was reconciled against retained local events, including copied history. OpenCode Go and the two retained non-Go OpenCode messages were reconciled against the local database; multi-route and legacy migration behavior also has fixture coverage.

Sources reviewed September 5, 2026:

- [Gemini session management](https://geminicli.com/docs/cli/session-management/), [recording types](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingTypes.ts), and [recording service](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts)
- [Pi session manager](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts) and [usage types](https://github.com/earendil-works/pi/blob/main/packages/ai/src/types.ts)
- [Oh My Pi session format](https://github.com/can1357/oh-my-pi/blob/main/docs/session.md)
- [OpenCode session messages](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/message-v2.ts)
- [Cursor Admin API](https://prod.cursor.com/docs/account/teams/admin-api)
- [Copilot CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
