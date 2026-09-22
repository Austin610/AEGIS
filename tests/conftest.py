import os
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aegis.api import create_app


@pytest.fixture
def client(tmp_path: Path, request):
    token = secrets.token_urlsafe(32)
    database = tmp_path / "api.db"
    schema = None
    postgres_url = os.environ.get("AEGIS_TEST_POSTGRES_URL")
    if postgres_url:
        from uuid import uuid4

        import psycopg
        from psycopg import sql

        schema = "aegis_test_" + uuid4().hex
        with psycopg.connect(postgres_url) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        # Store accepts URL syntax; append a URL-encoded search_path option instead.
        from urllib.parse import quote

        database = (
            postgres_url
            + ("&" if "?" in postgres_url else "?")
            + "options="
            + quote("-csearch_path=" + schema)
        )
    try:
        durable = request.node.get_closest_marker("durable") is not None
        with TestClient(
            create_app(database, token, durable_jobs=durable, embedded_worker=not durable),
            base_url="http://127.0.0.1",
        ) as client:
            client.headers["Authorization"] = "Bearer " + token
            client.app.state.test_directory = tmp_path
            yield client
    finally:
        if schema:
            with psycopg.connect(postgres_url) as db:
                db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
