from unittest.mock import patch

import pytest
from django.db.utils import InterfaceError


@pytest.mark.django_db
def test_any_database_error_is_reported_as_503_not_a_500(client):
    with patch("config.health.connections") as connections, patch("config.health.redis"):
        connections.__getitem__.return_value.cursor.side_effect = InterfaceError("connection already closed")
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["checks"]["database"].startswith("error")
