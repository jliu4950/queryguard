# Security model

QueryGuard is a portfolio demonstration over fictional data, not an authentication system.

The request carries a role and demo user identifier. This deliberately simplified boundary lets the sample focus on authorization enforcement: the server injects a predicate joining `customer_assignments` and `sales_reps` before every `sales_rep` query runs. It never relies solely on the provider prompt.

The SQL verifier rejects comments, multiple statements, write/DDL/transaction nodes, wildcard selection, unknown catalog tables or qualified fields, dynamic limits, and sensitive contact fields. Functions are checked against an allowlist rather than a blocklist, so an unrecognized function fails closed. It adds or caps `LIMIT` at the configured maximum; the API also fetches only the configured maximum result rows.

Because row-level authorization is enforced by appending one predicate to one `WHERE` clause, it is only sound when the statement has a single scope to constrain. For `sales_rep` the verifier therefore also rejects CTEs, subqueries, and repeated references to the same table: each introduces a scope the predicate never reaches. `apply_role_filter` independently refuses an ambiguous `orders` target rather than binding to the first one it finds.

These rules are not theoretical. Each was added after `evals/run_adversarial.py` demonstrated a working bypass of the control above it; see [adversarial-evaluation.md](adversarial-evaluation.md) for the findings, the fixes, and the gaps that remain.

Audit logs include a question digest, role, user id, allowed table names, and row count. They do not include secrets or raw questions. The default cache is process-local, keyed from a normalized request hash, and expires after five minutes. A future Redis adapter can implement the same `Cache` interface without changing the query pipeline.
