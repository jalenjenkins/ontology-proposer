# Ontology Proposer

A local portfolio project for reviewing a semantic layer over synthetic healthcare data. Import Synthea CSV exports into DuckDB. Ship a small deterministic fixture for hermetic tests and a keyless demo.

1. Profile tables and infer candidate foreign keys by overlap with unique columns. Record SQL evidence and actual join multiplicity.
2. Propose entities, properties, joins and metrics through Claude Messages tools. Provide a deterministic demo adapter.
3. Validate each change before review. Compile scalar SUM, AVG, and COUNT metrics from a constrained definition. Probe the exact joined rows for duplicate base keys, row loss and null values. Execute the metric and preserve its diagnostics.
4. Stage diffs with per-change human decisions in SQLite. Publish immutable snapshots atomically against the reviewed parent.
5. Give query agents only role-visible semantic objects and a physically projected, isolated DuckDB workspace. Use structured references in semantic mode. Verify scalar answers via a separately implemented calculation and withhold disagreements.
6. Evaluate 32 fixed questions in four configurations against independent gold queries. Keep offline and live model results explicitly labeled and track regressions.

The React review is local. Authentication, deployment, and multiple connectors are outside scope. SQL and verification support a small, auditable scalar query grammar.
