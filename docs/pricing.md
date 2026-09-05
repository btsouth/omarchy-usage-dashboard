# Pricing policy

API value uses token categories and the price snapshot shipped with this version. The bundled LiteLLM subset contains OpenAI and Anthropic entries and the price fields consumed by the collector. Its source revision, URL, and retrieval time are embedded in `catalog.json`.

The OpenCode Go model override is kept separately in `pricing.json`, with its documentation source and verification date. Matching is exact or uses the provider-prefixed catalog key. There is no guessed price for an unknown model.

A record is unpriced if any nonzero token category lacks an applicable rate. Its tokens remain counted, its estimated value is omitted, and the UI identifies incomplete pricing. A catalog's explicit zero rate is distinct from a missing rate.

Cache reads and cache writes use their respective rates. Claude's one-hour write counter is a subset of cache writes, and is charged separately when reported. Context-tier thresholds are applied only where the catalog defines them. Reasoning is not added again to Codex output; OpenCode's separate reasoning counter is included in output.

User override: place a JSON object containing `source`, optional `fetchedAtMs`, and a `document` map in `$XDG_STATE_HOME/omarchy/ai-usage/rates.json`. This replaces the bundled catalog; the documented Go override is then applied. Delete the override to return to the bundled snapshot. The app does not download catalog updates automatically.

Maintainers can run `python3 tools/update_catalog.py COMMIT_SHA` to regenerate the LiteLLM subset from an explicit public revision. Review price changes and upstream licensing before release. Go overrides are reviewed against their own documented source.
