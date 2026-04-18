import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from app.config import settings
from app.db import db
from app.services.crypto_service import token_crypto

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
TWITTER_AUTH_URL = "https://x.com/i/oauth2/authorize"
TWITTER_TOKEN_URL = "https://api.x.com/2/oauth2/token"


class OAuthService:
    async def build_authorization_url(
        self,
        provider: str,
        user_id: str,
        campaign_id: str | None = None,
        redirect_after: str | None = None,
    ) -> str:
        await self._ensure_user_and_campaign(user_id, campaign_id)

        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64) if provider == "TWITTER" else None
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        await db.oauthstate.create(
            data={
                "state": state,
                "provider": provider,
                "encryptedCodeVerifier": token_crypto.encrypt(code_verifier),
                "redirectAfter": redirect_after,
                "expiresAt": expires_at,
                "userId": user_id,
                "campaignId": campaign_id,
            }
        )

        if provider == "GOOGLE":
            return self._google_authorization_url(state)
        if provider == "TWITTER":
            return self._twitter_authorization_url(state, code_verifier or "")

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported OAuth provider.")

    async def handle_callback(self, provider: str, code: str, state: str) -> str:
        stored_state = await db.oauthstate.find_unique(where={"state": state})
        if stored_state is None or stored_state.provider != provider:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state.")

        now = datetime.now(timezone.utc)
        if stored_state.expiresAt < now:
            await db.oauthstate.delete(where={"state": state})
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth state has expired.")

        try:
            if provider == "GOOGLE":
                token_data = await self._exchange_google_code(code)
                provider_account_id = None
            elif provider == "TWITTER":
                verifier = token_crypto.decrypt(stored_state.encryptedCodeVerifier)
                token_data = await self._exchange_twitter_code(code, verifier or "")
                provider_account_id = None
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported OAuth provider.")

            await self._store_tokens(
                provider=provider,
                user_id=stored_state.userId,
                campaign_id=stored_state.campaignId,
                provider_account_id=provider_account_id,
                token_data=token_data,
            )
        finally:
            await db.oauthstate.delete(where={"state": state})

        return stored_state.redirectAfter or settings.oauth_success_redirect_url

    def _google_authorization_url(self, state: str) -> str:
        self._require(settings.google_client_id, "GOOGLE_CLIENT_ID")
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": self._callback_url("google"),
            "response_type": "code",
            "scope": settings.google_scopes,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    def _twitter_authorization_url(self, state: str, code_verifier: str) -> str:
        self._require(settings.twitter_client_id, "TWITTER_CLIENT_ID")
        code_challenge = self._pkce_challenge(code_verifier)
        params = {
            "response_type": "code",
            "client_id": settings.twitter_client_id,
            "redirect_uri": self._callback_url("twitter"),
            "scope": settings.twitter_scopes,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{TWITTER_AUTH_URL}?{urlencode(params)}"

    async def _exchange_google_code(self, code: str) -> dict[str, Any]:
        self._require(settings.google_client_id, "GOOGLE_CLIENT_ID")
        self._require(settings.google_client_secret, "GOOGLE_CLIENT_SECRET")
        data = {
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": self._callback_url("google"),
            "grant_type": "authorization_code",
        }
        return await self._post_token(GOOGLE_TOKEN_URL, data)

    async def _exchange_twitter_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        self._require(settings.twitter_client_id, "TWITTER_CLIENT_ID")
        data = {
            "code": code,
            "grant_type": "authorization_code",
            "client_id": settings.twitter_client_id,
            "redirect_uri": self._callback_url("twitter"),
            "code_verifier": code_verifier,
        }
        headers = {}
        if settings.twitter_client_secret:
            raw = f"{settings.twitter_client_id}:{settings.twitter_client_secret}".encode("utf-8")
            headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('utf-8')}"
        return await self._post_token(TWITTER_TOKEN_URL, data, headers=headers)

    async def _post_token(
        self,
        url: str,
        data: dict[str, str | None],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                data={key: value for key, value in data.items() if value is not None},
                headers=headers,
            )
        if response.is_error:
            logger.warning("OAuth token exchange failed: %s %s", response.status_code, response.text)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OAuth token exchange failed.")
        return response.json()

    async def _store_tokens(
        self,
        provider: str,
        user_id: str,
        campaign_id: str | None,
        provider_account_id: str | None,
        token_data: dict[str, Any],
    ) -> None:
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OAuth response did not include a token.")

        expires_at = None
        if token_data.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token_data["expires_in"]))

        context_key = campaign_id or "global"
        payload = {
            "providerAccountId": provider_account_id,
            "scope": token_data.get("scope"),
            "tokenType": token_data.get("token_type"),
            "encryptedAccessToken": token_crypto.encrypt(access_token),
            "encryptedRefreshToken": token_crypto.encrypt(token_data.get("refresh_token")),
            "expiresAt": expires_at,
            "contextKey": context_key,
            "userId": user_id,
            "campaignId": campaign_id,
        }

        await db.oauthcredential.upsert(
            where={"provider_userId_contextKey": {"provider": provider, "userId": user_id, "contextKey": context_key}},
            data={"create": payload, "update": payload},
        )

    async def _ensure_user_and_campaign(self, user_id: str, campaign_id: str | None) -> None:
        user = await db.user.find_unique(where={"id": user_id})
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
        if campaign_id:
            campaign = await db.campaign.find_unique(where={"id": campaign_id})
            if campaign is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    def _callback_url(self, provider: str) -> str:
        return f"{settings.oauth_redirect_base_url.rstrip('/')}/auth/{provider}/callback"

    @staticmethod
    def _pkce_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    @staticmethod
    def _require(value: str | None, name: str) -> None:
        if not value:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{name} is not configured.")
