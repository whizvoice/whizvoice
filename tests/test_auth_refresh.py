"""Tests for refresh-token rotation with a sliding 7-day window and 30-day absolute cap."""
import time
import unittest
from unittest.mock import patch, MagicMock

import jwt as pyjwt
from fastapi import HTTPException

import auth
from auth import (
    create_refresh_token,
    SECRET_KEY,
    ALGORITHM,
    REFRESH_TOKEN_EXPIRE_DAYS,
    REFRESH_SESSION_MAX_DAYS,
)

DAY = 24 * 60 * 60


def decode(token):
    return pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def _mock_supabase(email="user@example.com"):
    sb = MagicMock()
    result = MagicMock()
    result.data = [{"email": email, "user_id": "user-1"}]
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = result
    return sb


class TestCreateRefreshToken(unittest.TestCase):
    def test_login_token_has_session_start_of_now(self):
        before = int(time.time())
        payload = decode(create_refresh_token({"sub": "user-1"}))
        self.assertGreaterEqual(payload["session_start"], before)
        self.assertLessEqual(payload["session_start"], int(time.time()))

    def test_fresh_token_expires_in_seven_days(self):
        payload = decode(create_refresh_token({"sub": "user-1"}))
        self.assertAlmostEqual(
            payload["exp"], time.time() + REFRESH_TOKEN_EXPIRE_DAYS * DAY, delta=5
        )

    def test_expiry_is_clamped_to_session_cap(self):
        session_start = int(time.time()) - 28 * DAY
        payload = decode(
            create_refresh_token({"sub": "user-1", "session_start": session_start})
        )
        self.assertEqual(payload["session_start"], session_start)
        self.assertAlmostEqual(
            payload["exp"], session_start + REFRESH_SESSION_MAX_DAYS * DAY, delta=5
        )


class TestRefreshEndpoint(unittest.IsolatedAsyncioTestCase):
    async def _refresh(self, refresh_token):
        from app import refresh_access_token, RefreshTokenRequest
        with patch("app.supabase", _mock_supabase()):
            return await refresh_access_token(
                RefreshTokenRequest(refresh_token=refresh_token)
            )

    async def test_refresh_returns_rotated_token_with_same_session_start(self):
        session_start = int(time.time()) - 3 * DAY
        original = create_refresh_token({"sub": "user-1", "session_start": session_start})

        response = await self._refresh(original)

        self.assertIsNotNone(response.refresh_token)
        rotated = decode(response.refresh_token)
        self.assertEqual(rotated["type"], "refresh")
        self.assertEqual(rotated["sub"], "user-1")
        self.assertEqual(rotated["session_start"], session_start)
        self.assertAlmostEqual(
            rotated["exp"], time.time() + REFRESH_TOKEN_EXPIRE_DAYS * DAY, delta=5
        )
        self.assertEqual(decode(response.access_token)["type"], "access")

    async def test_refresh_rejects_session_older_than_cap(self):
        session_start = int(time.time()) - (REFRESH_SESSION_MAX_DAYS + 1) * DAY
        # Bypass the clamp so we get a token that is still signature-valid but past the cap.
        token = pyjwt.encode(
            {
                "sub": "user-1",
                "type": "refresh",
                "session_start": session_start,
                "exp": int(time.time()) + DAY,
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )

        with self.assertRaises(HTTPException) as ctx:
            await self._refresh(token)
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_legacy_token_without_session_start_gets_fresh_clock(self):
        legacy = pyjwt.encode(
            {"sub": "user-1", "type": "refresh", "exp": int(time.time()) + DAY},
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        before = int(time.time())

        response = await self._refresh(legacy)

        rotated = decode(response.refresh_token)
        self.assertGreaterEqual(rotated["session_start"], before)
        self.assertLessEqual(rotated["session_start"], int(time.time()))


if __name__ == "__main__":
    unittest.main()
