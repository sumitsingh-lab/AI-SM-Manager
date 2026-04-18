import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.config import settings
from app.services.oauth_service import OAuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google/start")
async def start_google_auth(
    user_id: str = Query(...),
    campaign_id: str | None = Query(default=None),
    redirect_after: str | None = Query(default=None),
) -> RedirectResponse:
    url = await OAuthService().build_authorization_url("GOOGLE", user_id, campaign_id, redirect_after)
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    return await _handle_callback("GOOGLE", code, state, error)


@router.get("/twitter/start")
async def start_twitter_auth(
    user_id: str = Query(...),
    campaign_id: str | None = Query(default=None),
    redirect_after: str | None = Query(default=None),
) -> RedirectResponse:
    url = await OAuthService().build_authorization_url("TWITTER", user_id, campaign_id, redirect_after)
    return RedirectResponse(url)


@router.get("/twitter/callback")
async def twitter_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    return await _handle_callback("TWITTER", code, state, error)


async def _handle_callback(provider: str, code: str | None, state: str | None, error: str | None) -> RedirectResponse:
    if error:
        logger.warning("%s OAuth returned error: %s", provider, error)
        return RedirectResponse(settings.oauth_error_redirect_url)

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth callback requires code and state.",
        )

    redirect_url = await OAuthService().handle_callback(provider, code, state)
    return RedirectResponse(redirect_url)
