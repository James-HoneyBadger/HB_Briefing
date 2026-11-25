# Evidence-aware dossier reporting

Dossier reports distinguish feed delivery hosts, observed article hosts and approximate story groups. Several articles may reflect syndication, repeated coverage or a false clustering match. Neither a group nor multiple hosts prove independent corroboration.

Key developments select one representative per existing story group, ranked by severity and date, while the timeline retains every signal. JSON exports expose the additive story_evidence object; Markdown and HTML present the same coverage caveat. Invalid publication dates fall back to detection time; missing timestamps remain unknown. Malformed snapshot payloads are treated as an unavailable baseline.

No new dependencies, database migrations, model calls or outbound network requests are introduced. Article hosts are not validated ownership identities.

Verification: PYTHONPATH=src python -m unittest discover -s tests -v.
