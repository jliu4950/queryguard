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


# --- Row-level authorization rewrites one WHERE clause, so extra scopes escape it -------


def test_cte_is_rejected_for_sales_rep() -> None:
    with pytest.raises(SafetyError) as error:
        rep(
            "WITH allc AS (SELECT name FROM customers) "
            "SELECT allc.name AS customer, orders.id AS order_id "
            "FROM allc JOIN orders ON 1 = 1 LIMIT 50"
        )
    assert error.value.code == "authorization"


def test_subquery_is_rejected_for_sales_rep() -> None:
    with pytest.raises(SafetyError) as error:
        rep(
            "SELECT orders.id AS order_id, "
            "(SELECT customers.name FROM customers WHERE customers.id = 2) AS leaked "
            "FROM orders LIMIT 50"
        )
    assert error.value.code == "authorization"


def test_duplicate_table_reference_is_rejected_for_sales_rep() -> None:
    with pytest.raises(SafetyError) as error:
        rep(
            "SELECT c.name AS customer, o2.id AS order_id FROM orders AS o "
            "JOIN orders AS o2 ON 1 = 1 JOIN customers AS c ON c.id = o2.customer_id LIMIT 50"
        )
    assert error.value.code == "authorization"


def test_analyst_may_still_use_subqueries() -> None:
    """The scope restriction applies only where row-level authorization is enforced."""
    validated = analyst(
        "SELECT customers.name AS customer FROM customers "
        "WHERE customers.id IN (SELECT customer_id FROM orders) LIMIT 50"
    )
    assert "customers" in validated.tables


def test_role_filter_refuses_an_ambiguous_orders_reference() -> None:
    """Defence in depth at the enforcement point itself, not only in the validator."""
    validated = analyst(
        "SELECT o.id AS a, o2.id AS b FROM orders AS o JOIN orders AS o2 ON 1 = 1 LIMIT 50"
    )
    with pytest.raises(SafetyError) as error:
        apply_role_filter(validated, Role.SALES_REP, "rep_alex")
    assert error.value.code == "authorization"


# --- Scoped queries must keep working ---------------------------------------------------


def test_legitimate_scoped_query_still_runs() -> None:
    validated = rep(
        f"SELECT customers.name AS customer, orders.id AS order_id {SCOPED_ORDERS} LIMIT 50"
    )
    sql = apply_role_filter(validated, Role.SALES_REP, "rep_alex")
    assert ":authorized_user_id" in sql
    assert "customer_assignments" in sql


# --- A negative LIMIT survived the row cap ----------------------------------------------
# `LIMIT -1` parses as Neg(Literal(1)); reading .name saw "1" and let it through, and
# SQLite treats a negative limit as no limit.


def test_negative_limit_is_rejected() -> None:
    with pytest.raises(SafetyError) as error:
        analyst("SELECT customers.name AS n FROM customers LIMIT -1")
    assert error.value.code == "unsafe_sql"


def test_non_literal_limit_is_rejected() -> None:
    with pytest.raises(SafetyError):
        analyst("SELECT customers.name AS n FROM customers LIMIT 1 + 1")


def test_oversized_limit_is_clamped_not_rejected() -> None:
    validated = analyst("SELECT customers.name AS n FROM customers LIMIT 100000", max_rows=50)
    assert "LIMIT 50" in validated.sql
