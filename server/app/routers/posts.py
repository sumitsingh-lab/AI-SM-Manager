from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.db import db
from app.services.scheduler_service import publishing_scheduler
from app.services.storage_service import StorageService

router = APIRouter(prefix="/posts", tags=["posts"])

AspectRatioName = Literal["SQUARE_1_1", "PORTRAIT_4_5", "STORY_9_16", "LANDSCAPE_16_9", "LINKEDIN_1_91_1"]
PublishPlatform = Literal["INSTAGRAM", "FACEBOOK", "TWITTER"]


class TagResponse(BaseModel):
    id: str
    display_name: str
    handle: str | None
    platform: str | None


class AssetPreview(BaseModel):
    id: str
    file_name: str
    content_type: str
    preview_url: str | None


class PostResponse(BaseModel):
    id: str
    platform: str
    generated_caption: str
    selected_aspect_ratio: str
    approval_status: str
    publish_status: str
    scheduled_publish_time: datetime | None
    published_at: datetime | None
    rejection_reason: str | None
    last_publish_error: str | None
    asset: AssetPreview | None
    tags: list[TagResponse] = Field(default_factory=list)


class ReviewPostRequest(BaseModel):
    generated_caption: str | None = None
    selected_aspect_ratio: AspectRatioName | None = None
    tag_ids: list[str] = Field(default_factory=list)


class RejectPostRequest(BaseModel):
    rejection_reason: str


class SchedulePostRequest(BaseModel):
    scheduled_publish_time: datetime
    platforms: list[PublishPlatform]


@router.get("", response_model=list[PostResponse])
async def list_posts(
    approval_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = Query(default=None),
    publish_status: Literal["NOT_SCHEDULED", "QUEUED", "PUBLISHING", "PUBLISHED", "FAILED"] | None = Query(default=None),
) -> list[PostResponse]:
    where = {}
    if approval_status:
        where["approvalStatus"] = approval_status
    if publish_status:
        where["publishStatus"] = publish_status

    posts = await db.post.find_many(where=where, order={"createdAt": "desc"}, take=100)
    return [await _post_response(post) for post in posts]


@router.patch("/{post_id}/approve", response_model=PostResponse)
async def approve_post(post_id: str, request: ReviewPostRequest) -> PostResponse:
    post = await db.post.find_unique(where={"id": post_id})
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

    data = {
        "approvalStatus": "APPROVED",
        "rejectionReason": None,
    }
    if request.generated_caption is not None:
        data["generatedCaption"] = request.generated_caption
    if request.selected_aspect_ratio is not None:
        data["selectedAspectRatio"] = request.selected_aspect_ratio

    updated = await db.post.update(where={"id": post_id}, data=data)
    await _replace_tags(post_id, request.tag_ids)
    return await _post_response(updated)


@router.patch("/{post_id}/reject", response_model=PostResponse)
async def reject_post(post_id: str, request: RejectPostRequest) -> PostResponse:
    post = await db.post.find_unique(where={"id": post_id})
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

    updated = await db.post.update(
        where={"id": post_id},
        data={
            "approvalStatus": "REJECTED",
            "publishStatus": "NOT_SCHEDULED",
            "scheduledPublishTime": None,
            "rejectionReason": request.rejection_reason,
        },
    )
    return await _post_response(updated)


@router.patch("/{post_id}/schedule", response_model=list[PostResponse])
async def schedule_post(post_id: str, request: SchedulePostRequest, background_tasks: BackgroundTasks) -> list[PostResponse]:
    source = await db.post.find_unique(where={"id": post_id})
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")
    if source.approvalStatus != "APPROVED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only approved posts can be scheduled.")
    if not request.platforms:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one platform is required.")

    scheduled_posts = []
    for platform in request.platforms:
        if platform == source.platform:
            scheduled = await db.post.update(
                where={"id": source.id},
                data={
                    "scheduledPublishTime": request.scheduled_publish_time,
                    "publishStatus": "QUEUED",
                    "lastPublishError": None,
                },
            )
        else:
            scheduled = await db.post.create(
                data={
                    "platform": platform,
                    "generatedCaption": source.generatedCaption,
                    "selectedAspectRatio": _default_ratio(platform, source.selectedAspectRatio),
                    "approvalStatus": "APPROVED",
                    "publishStatus": "QUEUED",
                    "scheduledPublishTime": request.scheduled_publish_time,
                    "campaignId": source.campaignId,
                    "assetId": source.assetId,
                    "generatedById": source.generatedById,
                    "aiMetadata": source.aiMetadata,
                }
            )
        scheduled_posts.append(scheduled)

    if request.scheduled_publish_time <= datetime.now(timezone.utc):
        background_tasks.add_task(publishing_scheduler.run_once)

    return [await _post_response(post) for post in scheduled_posts]


@router.post("/scheduler/run-due")
async def run_due_scheduler() -> dict[str, int]:
    count = await publishing_scheduler.run_once()
    return {"processed": count}


async def _replace_tags(post_id: str, tag_ids: list[str]) -> None:
    await db.posttag.delete_many(where={"postId": post_id})
    for tag_id in tag_ids:
        tag = await db.tagdirectory.find_unique(where={"id": tag_id})
        if tag is not None:
            await db.posttag.create(data={"postId": post_id, "tagId": tag_id})


async def _post_response(post) -> PostResponse:
    asset = await _asset_preview(post.assetId)
    tags = await _post_tags(post.id)
    return PostResponse(
        id=post.id,
        platform=post.platform,
        generated_caption=post.generatedCaption,
        selected_aspect_ratio=post.selectedAspectRatio,
        approval_status=post.approvalStatus,
        publish_status=post.publishStatus,
        scheduled_publish_time=post.scheduledPublishTime,
        published_at=post.publishedAt,
        rejection_reason=post.rejectionReason,
        last_publish_error=post.lastPublishError,
        asset=asset,
        tags=tags,
    )


async def _asset_preview(asset_id: str | None) -> AssetPreview | None:
    if not asset_id:
        return None
    asset = await db.asset.find_unique(where={"id": asset_id})
    if asset is None:
        return None
    preview_url = asset.thumbnailUrl
    if not preview_url and asset.gcsObjectName:
        storage = StorageService()
        preview_url = storage.public_url_for_object(asset.gcsObjectName)
    return AssetPreview(id=asset.id, file_name=asset.fileName, content_type=asset.contentType, preview_url=preview_url)


async def _post_tags(post_id: str) -> list[TagResponse]:
    links = await db.posttag.find_many(where={"postId": post_id})
    tags = []
    for link in links:
        tag = await db.tagdirectory.find_unique(where={"id": link.tagId})
        if tag:
            tags.append(TagResponse(id=tag.id, display_name=tag.displayName, handle=tag.handle, platform=tag.platform))
    return tags


def _default_ratio(platform: str, fallback: str) -> str:
    return {
        "INSTAGRAM": "SQUARE_1_1",
        "FACEBOOK": "LANDSCAPE_16_9",
        "TWITTER": "LANDSCAPE_16_9",
    }.get(platform, fallback)
