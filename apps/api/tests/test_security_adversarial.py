"""Regression guards for bypasses found by the adversarial evaluation suite.

Each test corresponds to a technique in `evals/adversarial_cases.json` and to a real
bypass that existed before the suite was written. The eval suite proves the whole pipeline
holds end to end; these pin the individual control so a failure names the cause directly.
"""

import pytest

from app.catalog import SchemaCatalog
from app.schemas import Role
from app.security import SafetyError, apply_role_filter, validate_sql

CATALOG = SchemaCatalog()
SCOPED_ORDERS = "FROM orders JOIN customers ON customers.id = orders.customer_id"


def rep(sql: str, max_rows: int = 50):
    return validate_sql(sql, CATALOG, Role.SALES_REP, max_rows)


def analyst(sql: str, max_rows: int = 50):
    return validate_sql(sql, CATALOG, Role.ANALYST, max_rows)


# --- Bare star bypassed both the wildcard rule and the sensitive-field rule -------------
# `SELECT *` parses as exp.Star with no exp.Column node, so a column-only scan saw nothing
# to reject and every customer email was returned.


def test_bare_star_is_rejected() -> None:
    with pytest.raises(SafetyError) as error:
        analyst("SELECT * FROM customers LIMIT 50")
    assert error.value.code == "unsafe_sql"


def test_qualified_star_is_rejected() -> None:
    with pytest.raises(SafetyError):
        analyst("SELECT customers.* FROM customers LIMIT 50")


def test_aggregate_star_is_still_allowed() -> None:
    """COUNT(*) exposes no column values; the fix must not be 'reject every star'."""
    assert "COUNT(*)" in analyst("SELECT COUNT(*) AS n FROM customers LIMIT 1").sql.upper()


# --- The function blocklist never matched the functions it named ------------------------
# sqlglot parses unknown functions as exp.Anonymous, whose sql_name() is "ANONYMOUS", so
# comparing sql_name() against {"load_file", ...} could never match.


def test_unknown_function_is_rejected() -> None:
    with pytest.raises(SafetyError) as error:
        analyst("SELECT load_file('/etc/passwd') AS f FROM customers LIMIT 1")
    assert error.value.code == "unsafe_sql"


def test_approved_functions_still_work() -> None:
    validated = analyst("SELECT ROUND(SUM(orders.total_amount), 2) AS revenue FROM orders LIMIT 1")
    assert "ROUND" in validated.sql.upper()
