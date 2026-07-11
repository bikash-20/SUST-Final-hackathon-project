from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import Response, status

from app.infrastructure.database import _build_dsn, _build_provider_dsn
from app.main import _cors_origins, create_app


class DatabaseConfigurationTests(unittest.TestCase):
    def test_render_url_supplies_location_but_never_owner_credentials(self) -> None:
        env = {
            "DATABASE_URL": (
                "postgresql://render_owner:owner-secret@render-db.internal:5439/"
                "liquiguard"
            ),
            "DB_APP_USER": "app_shared",
            "DB_APP_PASSWORD": "shared-secret",
            "DB_BKASH_USER": "app_bkash",
            "DB_BKASH_PASSWORD": "bkash-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            shared = _build_dsn()
            bkash = _build_provider_dsn("bkash")

        self.assertEqual(shared.drivername, "postgresql+asyncpg")
        self.assertEqual(
            (shared.host, shared.port, shared.database),
            ("render-db.internal", 5439, "liquiguard"),
        )
        self.assertEqual(
            (shared.username, shared.password), ("app_shared", "shared-secret")
        )
        self.assertEqual(
            (bkash.username, bkash.password), ("app_bkash", "bkash-secret")
        )
        self.assertNotIn("render_owner", shared.render_as_string(hide_password=False))
        self.assertNotIn("owner-secret", shared.render_as_string(hide_password=False))

    def test_individual_database_variables_remain_the_local_fallback(self) -> None:
        env = {
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "55432",
            "DB_NAME": "local_demo",
        }
        with patch.dict(os.environ, env, clear=True):
            dsn = _build_dsn()

        self.assertEqual(
            (dsn.host, dsn.port, dsn.database),
            ("127.0.0.1", 55432, "local_demo"),
        )

    def test_database_url_requires_postgresql_host_and_database(self) -> None:
        invalid_urls = (
            "sqlite:///tmp/demo.db",
            "postgresql:///missing-host",
            "postgresql://render-db.internal",
        )
        for configured_url in invalid_urls:
            with self.subTest(configured_url=configured_url):
                with patch.dict(
                    os.environ, {"DATABASE_URL": configured_url}, clear=True
                ):
                    with self.assertRaises(ValueError):
                        _build_dsn()


class HttpDeploymentConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_cors_origins_are_trimmed_and_normalized(self) -> None:
        with patch.dict(
            os.environ,
            {"CORS_ALLOWED_ORIGINS": " https://one.example/,https://two.example "},
            clear=True,
        ):
            self.assertEqual(
                _cors_origins(),
                ["https://one.example", "https://two.example"],
            )

    async def test_health_is_lightweight_and_readiness_fails_on_database(self) -> None:
        app = create_app()
        endpoints = {route.path: route.endpoint for route in app.routes}

        self.assertEqual(await endpoints["/health"](), {"ok": True})

        response = Response()
        with (
            patch("app.main.ping", AsyncMock(return_value=False)),
            patch(
                "app.main.get_engine",
                return_value=SimpleNamespace(is_running=True),
            ),
        ):
            payload = await endpoints["/healthz"](response)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(payload, {"ok": False, "engine_running": True})

    async def test_stopped_simulation_does_not_fail_database_readiness(self) -> None:
        app = create_app()
        endpoint = next(
            route.endpoint for route in app.routes if route.path == "/healthz"
        )
        response = Response()
        with (
            patch("app.main.ping", AsyncMock(return_value=True)),
            patch(
                "app.main.get_engine",
                return_value=SimpleNamespace(is_running=False),
            ),
        ):
            payload = await endpoint(response)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(payload, {"ok": True, "engine_running": False})


if __name__ == "__main__":
    unittest.main()
