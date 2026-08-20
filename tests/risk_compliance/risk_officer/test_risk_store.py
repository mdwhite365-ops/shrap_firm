def test_decision_quantities_are_fractional_in_schema_and_migration() -> None:
    """The audit trail must agree with the book about how much was approved.

    The columns were INTEGER when quantities were whole shares. #195 made them
    fractional and Postgres kept accepting the writes by ROUNDING, so a
    0.1875-share approval that filled for 0.1875 was recorded as 0. Nothing
    failed and nothing was logged, which is why it survived nine days.
    """

    from shrap.risk_compliance.risk_officer.store import (
        ALTER_DECISIONS_FRACTIONAL_QUANTITY_SQL,
        CREATE_DECISIONS_TABLE_SQL,
    )

    # A fresh database gets it right from the start.
    assert "requested_quantity DOUBLE PRECISION" in CREATE_DECISIONS_TABLE_SQL
    assert "approved_quantity DOUBLE PRECISION" in CREATE_DECISIONS_TABLE_SQL
    assert "INTEGER" not in CREATE_DECISIONS_TABLE_SQL

    # An existing one is widened, and only when it needs it: ALTER COLUMN TYPE
    # rewrites the table and this runs on every service start.
    assert "data_type = 'integer'" in ALTER_DECISIONS_FRACTIONAL_QUANTITY_SQL
    assert "ALTER COLUMN requested_quantity TYPE DOUBLE PRECISION" in (
        ALTER_DECISIONS_FRACTIONAL_QUANTITY_SQL
    )
    assert "ALTER COLUMN approved_quantity TYPE DOUBLE PRECISION" in (
        ALTER_DECISIONS_FRACTIONAL_QUANTITY_SQL
    )
