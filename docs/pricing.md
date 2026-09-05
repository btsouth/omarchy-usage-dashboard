# Pricing policy

API value uses token categories and the price snapshot shipped with this version. The bundled LiteLLM subset contains OpenAI, Anthropic, and Gemini entries and the price fields consumed by the collector. Its source revision, URL, and retrieval time are embedded in `catalog.json`.

The OpenCode Go model override is kept separately in `pricing.json`, with its documentation source and verification date. Matching is exact or uses the provider-prefixed catalog key. There is no guessed price for an unknown model.

Grok Build uses `usage.costUsdTicks` from completed turns, divided by 10 billion to obtain USD. Per-model values take precedence; an aggregate value is used only for a single-model turn. Missing or zero ticks remain unpriced, even if a similarly named catalog model exists. This is Grok’s API-equivalent estimate, not subscription spending. Cache savings cannot be derived from this figure and are excluded from known cache savings.

A catalog-priced record is unpriced if any nonzero token category lacks an applicable rate. Its tokens remain counted, its estimated value is omitted, and the UI identifies incomplete pricing. A catalog's explicit zero rate is distinct from a missing rate.

Cache reads and cache writes use their respective rates. Claude's one-hour write counter is a subset of cache writes, and is charged separately when reported. Context-tier thresholds are applied only where the catalog defines them. Reasoning is not added again to Codex or Grok output; OpenCode's separate reasoning counter is included in output.

OpenCode, Pi, and Oh My Pi use a positive API estimate recorded by the app when present. Zero or missing recorded costs fall back to exact catalog matches; they are not assumed to mean free usage. Gemini output includes its separate thinking count. Pi reasoning is already included in output. These app estimates are not invoices and do not establish remaining subscription allowance.

User override: place a JSON object containing `source`, optional `fetchedAtMs`, and a `document` map in `$XDG_STATE_HOME/omarchy/ai-usage/rates.json`. User entries override matching bundled entries; other bundled models remain available. The documented Go override is then applied. Delete the override to return to the bundled snapshot. The app does not download catalog updates automatically.

Maintainers can run `python3 tools/update_catalog.py COMMIT_SHA` to regenerate the LiteLLM subset from an explicit public revision. Review price changes and upstream licensing before release. Go overrides are reviewed against their own documented source.

Grok completed turns can contain many model calls. The dashboard labels aggregated message/turn rows as usage records and shows Grok model calls separately. They are not interchangeable request counts.

Gemini 3.8 Flash standard rates were checked against [Google’s pricing page](https://ai.google.dev/gemini-api/docs/pricing) on September 5, 2026: $0.75 input, $3.75 output, and $0.075 cache reads per million tokens. These promotional rates end December 31, 2026. Cache storage, grounding tools, and service-tier premiums are not calculated from local token categories. Other snapshot entries are attributed catalog data, not a claim that every rate was independently verified.
