"""Idempotent demo-data setup. All records are fictional."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import (
    Base,
    Customer,
    CustomerAssignment,
    Order,
    OrderItem,
    Product,
    Return,
    SalesRep,
    make_engine,
)


def seed_database(database_url: str | None = None) -> None:
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if session.scalar(select(func.count()).select_from(Customer)):
            return
        customers = [
            Customer(
                id=1,
                name="Northstar Books",
                email="contact@northstar.invalid",
                region="Northeast",
                created_at=datetime(2025, 1, 3),
            ),
            Customer(
                id=2,
                name="Juniper Goods",
                email="orders@juniper.invalid",
                region="West",
                created_at=datetime(2025, 1, 9),
            ),
            Customer(
                id=3,
                name="Cedar Market",
                email="team@cedar.invalid",
                region="Central",
                created_at=datetime(2025, 2, 2),
            ),
            Customer(
                id=4,
                name="Harbor Supply",
                email="sales@harbor.invalid",
                region="South",
                created_at=datetime(2025, 2, 15),
            ),
        ]
        reps = [
            SalesRep(id=1, user_id="rep_alex", name="Alex Rivera", region="Northeast"),
            SalesRep(id=2, user_id="rep_sam", name="Sam Lee", region="West"),
        ]
        products = [
            Product(id=1, name="Atlas Notebook", category="Stationery", unit_price=12, active=True),
            Product(id=2, name="Orbit Pen Set", category="Stationery", unit_price=18, active=True),
            Product(id=3, name="Summit Mug", category="Home", unit_price=24, active=True),
        ]
        orders = [
            Order(
                id=1,
                customer_id=1,
                sales_rep_id=1,
                status="completed",
                ordered_at=datetime(2025, 1, 15),
                total_amount=42,
            ),
            Order(
                id=2,
                customer_id=2,
                sales_rep_id=2,
                status="completed",
                ordered_at=datetime(2025, 1, 23),
                total_amount=48,
            ),
            Order(
                id=3,
                customer_id=1,
                sales_rep_id=1,
                status="completed",
                ordered_at=datetime(2025, 2, 8),
                total_amount=60,
            ),
            Order(
                id=4,
                customer_id=3,
                sales_rep_id=1,
                status="completed",
                ordered_at=datetime(2025, 2, 20),
                total_amount=24,
            ),
            Order(
                id=5,
                customer_id=4,
                sales_rep_id=2,
                status="refunded",
                ordered_at=datetime(2025, 3, 5),
                total_amount=36,
            ),
            Order(
                id=6,
                customer_id=2,
                sales_rep_id=2,
                status="completed",
                ordered_at=datetime(2025, 3, 18),
                total_amount=54,
            ),
        ]
        items = [
            OrderItem(id=1, order_id=1, product_id=1, quantity=2, unit_price=12),
            OrderItem(id=2, order_id=1, product_id=2, quantity=1, unit_price=18),
            OrderItem(id=3, order_id=2, product_id=3, quantity=2, unit_price=24),
            OrderItem(id=4, order_id=3, product_id=2, quantity=2, unit_price=18),
            OrderItem(id=5, order_id=3, product_id=1, quantity=2, unit_price=12),
            OrderItem(id=6, order_id=4, product_id=3, quantity=1, unit_price=24),
            OrderItem(id=7, order_id=5, product_id=1, quantity=3, unit_price=12),
            OrderItem(id=8, order_id=6, product_id=2, quantity=3, unit_price=18),
        ]
        session.add_all(customers + reps + products + orders + items)
        session.add_all(
            [
                CustomerAssignment(sales_rep_id=1, customer_id=1),
                CustomerAssignment(sales_rep_id=1, customer_id=3),
                CustomerAssignment(sales_rep_id=2, customer_id=2),
                CustomerAssignment(sales_rep_id=2, customer_id=4),
            ]
        )
        session.add_all(
            [
                Return(
                    id=1,
                    order_id=5,
                    reason="damaged",
                    returned_at=datetime(2025, 3, 10),
                    refund_amount=36,
                ),
                Return(
                    id=2,
                    order_id=2,
                    reason="changed mind",
                    returned_at=datetime(2025, 2, 1),
                    refund_amount=48,
                ),
            ]
        )
        session.commit()


if __name__ == "__main__":
    seed_database()
