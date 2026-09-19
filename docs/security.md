# Security model

QueryGuard is a portfolio demonstration over fictional data, not an authentication system.

The request carries a role and demo user identifier. This deliberately simplified boundary lets the sample focus on authorization enforcement: the server injects a predicate joining `customer_assignments` and `sales_reps` before every `sales_rep` query runs. It never relies solely on the provider prompt.

The SQL verifier rejects comments, multiple statements, write/DDL/transaction nodes, wildcard selection, dangerous functions, unknown catalog tables or qualified fields, dynamic limits, and sensitive contact fields. It adds or caps `LIMIT` at the configured maximum; the API also fetches only the configured maximum result rows.

Audit logs include a question digest, role, user id, allowed table names, and row count. They do not include secrets or raw questions. The default cache is process-local, keyed from a normalized request hash, and expires after five minutes. A future Redis adapter can implement the same `Cache` interface without changing the query pipeline.
