# Engineering decisions

This project was AI-assisted. The initial project brief specified the components and guided the scope. The decisions below record proposals considered during implementation. They do not attribute human review to anyone who has yet to perform it.

| Proposal considered | Decision and rationale |
| --- | --- |
| Let Claude submit arbitrary metric SQL with a self-reported grain. | Use a small aggregate grammar and compile SQL. The primary query and cardinality probe can share the same relation and bound parameters. The reviewer sees compiled SQL and counted rows. |
| Detect inflation by comparing aggregate totals or using `SUM(DISTINCT cost)`. | Check base-key multiplicity and missing rows independently of the measure. Equal-valued claims are distinct events. Compare the joined grain with the filtered base count. |
| Hide unauthorized columns by stripping their names from the prompt. | Project authorized columns into a separate temporary database with external access disabled. Resolve semantic plans against role-specific metric and property lists. Omit reviewed evidence from model context because it may refer to other objects. |
| Make the proposer approve its own suggestions. | Staging validates mechanically. Publication requires explicit review decisions, rejects invalid approvals, and checks the parent version in a SQLite write transaction. |
| Use a second model call to verify every semantic answer. | Recompute from base rows with Python for the supported grain-preserving aggregate grammar. The live schema-only baseline uses a second SQL generation call. |
| Use only a fixture during development. | Build a keyless fixture for tests and exercise the same pipeline on the official Synthea sample. Keep handcrafted reference proposals and the unsafe deterministic baseline visible in source. |
| Claim an ontology accuracy improvement from the offline comparison. | Report the measured fixture and Synthea results as regression checks. A same-model live comparison needs a configured Claude account and an approved ontology. |
