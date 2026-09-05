# Contributing

Keep parser changes covered by small synthetic fixtures. Do not attach real transcripts, credentials, project paths, or unreviewed usage dumps to issues or pull requests.

Run `python3 -m unittest discover -s tests -v` and the syntax checks in the CI workflow. For QML changes, run `python3 demo.py` and check a light and dark Omarchy theme, narrow windows, tooltips, filters, and keyboard navigation. Run `python3 tools/check_ui.py` for an isolated account-editor save/reload check. Use `omarchy plugin validate ./plugin` on a compatible Omarchy installation.

The bar plugin is derived from Omarchy's Agents plugin. Preserve attribution and review upstream API changes before updating copied components. Pricing updates must use an explicit source revision and retain source attribution. Missing prices must remain visibly unavailable.

Keep changes focused and describe what users will notice and how it was checked.
