# Working conventions

Use Python 3.11+, DuckDB, SQLite, pytest, and React. Configure Claude via ANTHROPIC_API_KEY and ANTHROPIC_MODEL. Keep credentials and generated databases out of Git.

Write validator and permission tests before implementation. Review adversarial cases with a human before citing them as hiring evidence. Commit vertical slices and record design decisions. Run pytest and the four-way benchmark before updating reported numbers.

Keep query permissions independent of prompt compliance. Separate the trusted synthetic-data proposer from the role-scoped query interface. Persist exact probe evidence and never publish failed changes.
