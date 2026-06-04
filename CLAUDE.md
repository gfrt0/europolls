# europolls

Project-local rules for Claude.

## Docs sync on commit

Before any `git commit` / `git push`, verify two doc surfaces are still accurate vs. the change being committed:

1. **README.md** at repo root — coverage / country list / pipeline description.
2. **web/index.html** "Limits & cautions" modal (the block after `<h2>Limits &amp; cautions</h2>`) — Source, Coverage, Validation, Reported quantity, Smoothing, Party identities, Citation.

If the commit changes country coverage, drop logic, parser behavior, party-id stats, or the dataset shape, update the affected paragraph in both places **in the same commit**. Do not commit pipeline / data / config changes and the doc update separately — they should land together so the published site never lags the dataset.

The web docs live entirely in `web/index.html`; editing them does **not** require rebuilding `data/processed/` or per-country `web/polls_*.json`.
