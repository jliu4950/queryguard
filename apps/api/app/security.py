import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from .catalog import SchemaCatalog
from .schemas import Role


class SafetyError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    tables: set[str]
    enforced_limit: int


INJECTION_RE = re.compile(
    r"(?:ignore\s+(?:all|previous)|system\s+prompt|jailbreak|\b(?:drop|delete|insert|update|alter|create)\b)",
    re.IGNORECASE,
)
# An allowlist, not a blocklist. sqlglot parses any function it does not know -- including
# load_file, pg_sleep and dblink -- as exp.Anonymous, whose sql_name() is the literal string
# "ANONYMOUS". A name-based blocklist therefore never matches the functions it exists to
# stop. Anything outside this set is refused, so an unknown function fails closed.
ALLOWED_FUNCTIONS = {
    "AVG",
    "CAST",
    "COALESCE",
    "COUNT",
    "LOWER",
    "MAX",
    "MIN",
    "NULLIF",
    "ROUND",
    "SUBSTRING",
    "SUM",
    "UPPER",
}
SENSITIVE_COLUMNS = {"email", "phone", "password", "token"}


def reject_prompt_injection(question: str) -> None:
    if INJECTION_RE.search(question):
        raise SafetyError(
            "prompt_injection", "The question contains a blocked instruction pattern."
        )


def validate_sql(sql: str, catalog: SchemaCatalog, role: Role, max_rows: int) -> ValidatedSQL:
    if "--" in sql or "/*" in sql or ";" in sql.rstrip(";") or sql.strip().endswith(";"):
        raise SafetyError("unsafe_sql", "SQL comments and multiple statements are not allowed.")
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception as exc:
        raise SafetyError("invalid_sql", "Generated SQL could not be parsed.") from exc
    if len(statements) != 1:
        raise SafetyError("unsafe_sql", "Only one SQL statement is allowed.")
    tree = statements[0]
    # CTEs are represented as a `with_` clause on a root Select in sqlglot.
    if not isinstance(tree, exp.Select):
        raise SafetyError("unsafe_sql", "Only read-only SELECT queries are allowed.")
    banned_nodes = tuple(
        getattr(exp, name)
        for name in (
            "Insert",
            "Update",
            "Delete",
            "Create",
            "Drop",
            "Alter",
            "Command",
            "Transaction",
        )
        if hasattr(exp, name)
    )
    if any(tree.find(node) for node in banned_nodes):
        raise SafetyError("unsafe_sql", "Write, DDL, and transaction commands are not allowed.")
    for function in tree.find_all(exp.Func):
        # sqlglot models AND/OR as Func subclasses; they are operators, not callable functions.
        if isinstance(function, exp.Connector):
            continue
        if isinstance(function, exp.Anonymous):
            raise SafetyError("unsafe_sql", "Unrecognized SQL functions are not allowed.")
        if function.sql_name().upper() not in ALLOWED_FUNCTIONS:
            raise SafetyError("unsafe_sql", "The query uses a function outside the approved set.")
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    tables = {
        table.name
        for table in tree.find_all(exp.Table)
        if table.name and table.name not in cte_names
    }
    unknown_tables = tables - catalog.allowed_tables
    if unknown_tables:
        raise SafetyError(
            "unknown_table", f"Unapproved table referenced: {sorted(unknown_tables)[0]}"
        )
    if not tables:
        raise SafetyError("unsafe_sql", "A query must read an approved catalog table.")
    aliases = {table.alias_or_name: table.name for table in tree.find_all(exp.Table)}
    selectable_aliases = {alias.alias for alias in tree.find_all(exp.Alias) if alias.alias}
    available_unqualified = set().union(*(catalog.allowed_columns(table) for table in tables))
    # `SELECT *` parses as a bare exp.Star with no exp.Column node, so checking columns
    # alone let it through -- and with no column to inspect, the sensitive-field rule below
    # never fired either. Aggregate stars such as COUNT(*) expose no column values.
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Func):
            raise SafetyError("unsafe_sql", "Wildcard selection is not allowed.")
    for column in tree.find_all(exp.Column):
        name = column.name
        if name == "*":
            raise SafetyError("unsafe_sql", "Wildcard selection is not allowed.")
        if name.lower() in SENSITIVE_COLUMNS:
            raise SafetyError("sensitive_field", "Sensitive fields cannot be selected.")
        if column.table:
            source = aliases.get(column.table, column.table)
            if source in catalog.allowed_tables and name not in catalog.allowed_columns(source):
                raise SafetyError("unknown_column", f"Unapproved field: {source}.{name}")
        elif name not in available_unqualified and name not in selectable_aliases:
            raise SafetyError("unknown_column", f"Unapproved field: {name}")
    if role == Role.SALES_REP and "sales_reps" in tables:
        raise SafetyError("authorization", "Sales representatives cannot query rep directory data.")
    if role == Role.SALES_REP:
        _require_single_authorizable_scope(tree)
    existing_limit = tree.args.get("limit")
    if existing_limit is None:
        tree = tree.limit(max_rows)
    else:
        literal = existing_limit.expression
        # `LIMIT -1` parses as Neg(Literal(1)), whose .name is "1". Reading the name alone
        # saw a positive 1, kept the negative limit in the executed SQL, and SQLite treats
        # a negative limit as no limit at all. Require a plain integer literal.
        if not isinstance(literal, exp.Literal) or not literal.is_int:
            raise SafetyError("unsafe_sql", "LIMIT must be a static integer literal.")
        requested = int(literal.name)
        if requested < 1:
            raise SafetyError("unsafe_sql", "LIMIT must be positive.")
        if requested > max_rows:
            tree = tree.limit(max_rows)
    return ValidatedSQL(sql=tree.sql(dialect="sqlite"), tables=tables, enforced_limit=max_rows)


def _require_single_authorizable_scope(tree: exp.Expression) -> None:
    """Reject sales-rep SQL whose scope cannot be statically verified.

    `apply_role_filter` enforces row-level access by appending one predicate to one WHERE
    clause. That is only sound when the statement has a single scope to constrain. A CTE, a
    subquery, or a second reference to the same table introduces a scope the predicate never
    reaches, and each one is enough to read another representative's customers. Rather than
    trying to rewrite every scope, refuse the shapes this validator cannot prove contained.
    """
    if tree.find(exp.With) is not None:
        raise SafetyError(
            "authorization",
            "Sales representative queries cannot use CTEs; authorization scope is unverifiable.",
        )
    if any(select is not tree for select in tree.find_all(exp.Select)):
        raise SafetyError(
            "authorization",
            "Sales representative queries cannot use subqueries; authorization scope is unverifiable.",
        )
    references = [table.name for table in tree.find_all(exp.Table) if table.name]
    duplicates = {name for name in references if references.count(name) > 1}
    if duplicates:
        raise SafetyError(
            "authorization",
            f"Sales representative queries cannot reference {sorted(duplicates)[0]} more than once.",
        )


def apply_role_filter(validated: ValidatedSQL, role: Role, user_id: str) -> str:
    """Server-side policy injection. Demo-generated sales SQL always has an orders scope."""
    if role == Role.ANALYST:
        return validated.sql
    if "orders" not in validated.tables:
        raise SafetyError(
            "authorization", "Sales representative queries need an order scope for authorization."
        )
    tree = sqlglot.parse_one(validated.sql, read="sqlite")
    order_tables = [table for table in tree.find_all(exp.Table) if table.name == "orders"]
    if len(order_tables) != 1:
        # Defence in depth: validate_sql already rejects this, but the predicate would
        # silently bind to only the first reference if it ever got here.
        raise SafetyError(
            "authorization", "Exactly one orders reference is required to enforce access scope."
        )
    order_table = order_tables[0]
    order_ref = order_table.alias_or_name
    predicate = f"{order_ref}.customer_id IN (SELECT ca.customer_id FROM customer_assignments AS ca JOIN sales_reps AS sr ON sr.id = ca.sales_rep_id WHERE sr.user_id = :authorized_user_id)"
    # AST mutation puts the predicate before GROUP BY/ORDER BY/LIMIT and combines an existing WHERE.
    return tree.where(predicate, append=True).sql(dialect="sqlite")
