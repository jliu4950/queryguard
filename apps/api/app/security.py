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
BANNED_FUNCTIONS = {
    "pg_sleep",
    "dblink",
    "load_file",
    "read_file",
    "write_file",
    "copy",
    "current_setting",
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
        if function.sql_name().lower() in BANNED_FUNCTIONS:
            raise SafetyError("unsafe_sql", "The query uses a blocked function.")
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
    existing_limit = tree.args.get("limit")
    if existing_limit is None:
        tree = tree.limit(max_rows)
    else:
        literal = existing_limit.expression
        try:
            requested = int(literal.name)
        except (ValueError, TypeError) as exc:
            raise SafetyError("unsafe_sql", "LIMIT must be a static integer.") from exc
        if requested < 1:
            raise SafetyError("unsafe_sql", "LIMIT must be positive.")
        if requested > max_rows:
            tree = tree.limit(max_rows)
    return ValidatedSQL(sql=tree.sql(dialect="sqlite"), tables=tables, enforced_limit=max_rows)


def apply_role_filter(validated: ValidatedSQL, role: Role, user_id: str) -> str:
    """Server-side policy injection. Demo-generated sales SQL always has an orders scope."""
    if role == Role.ANALYST:
        return validated.sql
    if "orders" not in validated.tables:
        raise SafetyError(
            "authorization", "Sales representative queries need an order scope for authorization."
        )
    tree = sqlglot.parse_one(validated.sql, read="sqlite")
    order_table = next(table for table in tree.find_all(exp.Table) if table.name == "orders")
    order_ref = order_table.alias_or_name
    predicate = f"{order_ref}.customer_id IN (SELECT ca.customer_id FROM customer_assignments AS ca JOIN sales_reps AS sr ON sr.id = ca.sales_rep_id WHERE sr.user_id = :authorized_user_id)"
    # AST mutation puts the predicate before GROUP BY/ORDER BY/LIMIT and combines an existing WHERE.
    return tree.where(predicate, append=True).sql(dialect="sqlite")
