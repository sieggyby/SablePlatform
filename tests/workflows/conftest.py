"""Workflow test fixtures."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.compat_conn import CompatConnection
from sable_platform.db.schema import metadata as sa_metadata


@pytest.fixture
def wf_db():
    """In-memory DB with schema + a test org (CompatConnection)."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    sa_metadata.create_all(engine)
    sa_conn = engine.connect()
    conn = CompatConnection(sa_conn)
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", ("wf_org", "WF Test Org"))
    conn.commit()
    yield conn
    sa_conn.close()
    engine.dispose()
