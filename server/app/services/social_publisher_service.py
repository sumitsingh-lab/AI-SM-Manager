import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.config import settings
from app.db import db
from app.services.crypto_service import token_crypto
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)


class SocialPublisherService:
    async def publish_post(self, post_id: str) -> dict[str, Any]:
        post = await db.post.find_unique(where={"id": post_id})
        if post is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

        if post.platform == "TWITTER":
            result = await self._publish_twitter(post)
        elif post.platform == "FACEBOOK":
            result = await self._publish_facebook(post)
        elif post.platform == "INSTAGRAM":
            result = await self._publish_instagram(post)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Publishing is not implemented for {post.platform}.",
            )

        await db.post.update(
            where={"id": post.id},
            data={
                "publishStatus": "PUBLISHED",
                "publishedAt": datetime.now(timezone.utc),
                "externalPostId": result.get("external_post_id"),
                "lastPublishError": None,
            },
        )
        return result

    async def _publish_twitter(self, post) -> dict[str, Any]:
        access_token = await self._twitter_access_token(post.generatedById, post.campaignId)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.x.com/2/tweets",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"text": post.generatedCaption[:280]},
            )
        if response.is_error:
            raise RuntimeError(f"X publish failed: {response.status_code} {response.text}")
        payload = response.json()
        return {"provider": "TWITTER", "external_post_id": payload.get("data", {}).get("id"), "raw": payload}

    async def _publish_facebook(self, post) -> dict[str, Any]:
        if not settings.meta_access_token or not settings.meta_page_id:
            raise RuntimeError("META_ACCESS_TOKEN and META_PAGE_ID are required for Facebook publishing.")

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"https://graph.facebook.com/v20.0/{settings.meta_page_id}/feed",
                data={"message": post.generatedCaption, "access_token": settings.meta_access_token},
            )
        if response.is_error:
            raise RuntimeError(f"Facebook publish failed: {response.status_code} {response.text}")
        payload = response.json()
        return {"provider": "FACEBOOK", "external_post_id": payload.get("id"), "raw": payload}

    async def _publish_instagram(self, post) -> dict[str, Any]:
        if not settings.meta_access_token or not settings.meta_instagram_user_id:
            raise RuntimeError("META_ACCESS_TOKEN and META_INSTAGRAM_USER_ID are required for Instagram publishing.")

        image_url = await self._post_image_url(post.assetId)
        if not image_url:
            raise RuntimeError("Instagram publishing requires an image asset with a public or signed URL.")

        async with httpx.AsyncClient(timeout=30) as client:
            container = await client.post(
                f"https://graph.facebook.com/v20.0/{settings.meta_instagram_user_id}/media",
                data={
                    "image_url": image_url,
                    "caption": post.generatedCaption,
                    "access_token": settings.meta_access_token,
                },
            )
            if container.is_error:
                raise RuntimeError(f"Instagram media container failed: {container.status_code} {container.text}")
            creation_id = container.json().get("id")
            publish = await client.post(
                f"https://graph.facebook.com/v20.0/{settings.meta_instagram_user_id}/media_publish",
                data={"creation_id": creation_id, "access_token": settings.meta_access_token},
            )
        if publish.is_error:
            raise RuntimeError(f"Instagram publish failed: {publish.status_code} {publish.text}")
        payload = publish.json()
        return {"provider": "INSTAGRAM", "external_post_id": payload.get("id"), "raw": payload}

    async def _twitter_access_token(self, user_id: str | None, campaign_id: str | None) -> str:
        if not user_id:
            raise RuntimeError("Twitter publishing requires a generatedById user on the post.")

        context_keys = [campaign_id, "global"] if campaign_id else ["global"]
        for context_key in context_keys:
            credential = await db.oauthcredential.find_unique(
                where={
                    "provider_userId_contextKey": {
                        "provider": "TWITTER",
                        "userId": user_id,
                        "contextKey": context_key,
                    }
                }
            )
            if credential and credential.encryptedAccessToken:
                return token_crypto.decrypt(credential.encryptedAccessToken) or ""
        raise RuntimeError("No Twitter OAuth credential found for this user/campaign.")

    async def _post_image_url(self, asset_id: str | None) -> str | None:
        if not asset_id:
            return None
        asset = await db.asset.find_unique(where={"id": asset_id})
        if asset is None or not asset.gcsObjectName:
            return None
        if asset.thumbnailUrl:
            return asset.thumbnailUrl
        storage = StorageService()
        return storage.public_url_for_object(asset.gcsObjectName)
