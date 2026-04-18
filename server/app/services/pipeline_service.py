import logging
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile, status

from app.db import db
from app.services.ai_agents import CaptionReview, CopywriterAgent, PlatformCaption, ReviewOutput, SupervisorReviewAgent
from app.services.image_composition_service import ImageCompositionService
from app.services.json_utils import prisma_json
from app.services.parsing_service import MagazineMetadata, ParsingService
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

DEFAULT_ASPECT_RATIO_BY_PLATFORM = {
    "FACEBOOK": "LANDSCAPE_16_9",
    "TWITTER": "LANDSCAPE_16_9",
    "LINKEDIN": "LINKEDIN_1_91_1",
}


@dataclass(frozen=True)
class DraftPostResult:
    id: str
    platform: str
    caption: str
    approval_status: str


@dataclass(frozen=True)
class PipelineResult:
    asset_id: str
    metadata: MagazineMetadata
    review: ReviewOutput
    posts: list[DraftPostResult]


class PipelineService:
    def __init__(self) -> None:
        self._parser = ParsingService()
        self._copywriter = CopywriterAgent()
        self._reviewer = SupervisorReviewAgent()

    async def process_existing_pdf_asset(
        self,
        asset_id: str,
        campaign_id: str | None = None,
        user_id: str | None = None,
        mockup_source_asset_id: str | None = None,
    ) -> PipelineResult:
        asset = await db.asset.find_unique(where={"id": asset_id})
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")

        resolved_campaign_id = campaign_id or asset.campaignId
        if not resolved_campaign_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A campaign_id is required.")

        await self._ensure_campaign_and_user(resolved_campaign_id, user_id)
        parsed = await self._parser.parse_pdf_asset(asset_id)
        copy = await self._copywriter.generate(parsed.metadata)
        review = await self._reviewer.review(parsed.metadata, copy)
        self._raise_if_not_approved(review)
        post_asset_id = await self._create_mockup_asset(
            source_asset_id=mockup_source_asset_id,
            campaign_id=resolved_campaign_id,
            user_id=user_id,
            metadata=parsed.metadata,
        ) or asset_id
        posts = await self._save_pending_posts(
            campaign_id=resolved_campaign_id,
            asset_id=post_asset_id,
            user_id=user_id,
            captions=copy.captions,
            metadata=parsed.metadata,
            review=review,
            source_pdf_asset_id=asset_id,
        )
        return PipelineResult(asset_id=asset_id, metadata=parsed.metadata, review=review, posts=posts)

    async def upload_and_process_pdf(
        self,
        file: UploadFile,
        model_image: UploadFile | None,
        campaign_id: str,
        user_id: str | None = None,
        description: str | None = None,
    ) -> PipelineResult:
        await self._ensure_campaign_and_user(campaign_id, user_id)

        stored_file = await StorageService().upload_asset(file, "MAGAZINE_PDF")
        asset = await db.asset.create(
            data={
                "type": "MAGAZINE_PDF",
                "fileName": stored_file.file_name,
                "contentType": stored_file.content_type,
                "fileSizeBytes": stored_file.file_size_bytes,
                "gcsUrl": stored_file.gcs_url,
                "gcsBucket": stored_file.gcs_bucket,
                "gcsObjectName": stored_file.gcs_object_name,
                "description": description,
                "createdById": user_id,
                "campaignId": campaign_id,
            }
        )
        logger.info("Uploaded pipeline PDF asset %s", asset.id)

        model_asset_id = None
        if model_image is not None:
            stored_model = await StorageService().upload_asset(model_image, "MODEL_IMAGE")
            model_asset = await db.asset.create(
                data={
                    "type": "MODEL_IMAGE",
                    "fileName": stored_model.file_name,
                    "contentType": stored_model.content_type,
                    "fileSizeBytes": stored_model.file_size_bytes,
                    "gcsUrl": stored_model.gcs_url,
                    "gcsBucket": stored_model.gcs_bucket,
                    "gcsObjectName": stored_model.gcs_object_name,
                    "thumbnailUrl": stored_model.signed_url,
                    "description": "Model image for generated magazine mockup.",
                    "createdById": user_id,
                    "campaignId": campaign_id,
                }
            )
            model_asset_id = model_asset.id

        return await self.process_existing_pdf_asset(
            asset.id,
            campaign_id=campaign_id,
            user_id=user_id,
            mockup_source_asset_id=model_asset_id,
        )

    async def _save_pending_posts(
        self,
        campaign_id: str,
        asset_id: str,
        user_id: str | None,
        captions: list[PlatformCaption],
        metadata: MagazineMetadata,
        review: ReviewOutput,
        source_pdf_asset_id: str,
    ) -> list[DraftPostResult]:
        reviews_by_platform = {item.platform: item for item in review.reviews}
        results = []

        for caption in captions:
            platform_review = reviews_by_platform.get(caption.platform)
            ai_metadata = {
                "parser": metadata.model_dump(mode="json"),
                "copywriter": caption.model_dump(mode="json"),
                "review": platform_review.model_dump(mode="json") if platform_review else None,
                "overallReviewNotes": review.overall_notes,
                "sourcePdfAssetId": source_pdf_asset_id,
            }
            post = await db.post.create(
                data={
                    "platform": caption.platform,
                    "generatedCaption": self._compose_caption(caption),
                    "selectedAspectRatio": DEFAULT_ASPECT_RATIO_BY_PLATFORM[caption.platform],
                    "approvalStatus": "PENDING",
                    "campaignId": campaign_id,
                    "assetId": asset_id,
                    "generatedById": user_id,
                    "aiMetadata": prisma_json(ai_metadata),
                }
            )
            results.append(
                DraftPostResult(
                    id=post.id,
                    platform=post.platform,
                    caption=post.generatedCaption,
                    approval_status=post.approvalStatus,
                )
            )

        return results

    async def _create_mockup_asset(
        self,
        source_asset_id: str | None,
        campaign_id: str,
        user_id: str | None,
        metadata: MagazineMetadata,
    ) -> str | None:
        if not source_asset_id:
            return None

        image, image_metadata = await ImageCompositionService().create_magazine_release_mockup(
            foreground_asset_id=source_asset_id,
            headline=metadata.title or "Magazine Release",
            subheadline=metadata.summary[:180],
            aspect_ratio="SQUARE_1_1",
            fit="crop",
        )
        asset, _stored = await ImageCompositionService().save_as_asset(
            image=image,
            metadata={**image_metadata, "sourceMagazineTitle": metadata.title},
            campaign_id=campaign_id,
            user_id=user_id,
        )
        return asset.id

    async def _ensure_campaign_and_user(self, campaign_id: str, user_id: str | None) -> None:
        campaign = await db.campaign.find_unique(where={"id": campaign_id})
        if campaign is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

        if user_id:
            user = await db.user.find_unique(where={"id": user_id})
            if user is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    @staticmethod
    def _compose_caption(caption: PlatformCaption) -> str:
        hashtags = " ".join(caption.hashtags)
        if hashtags and hashtags not in caption.caption:
            return f"{caption.caption.strip()}\n\n{hashtags}".strip()
        return caption.caption.strip()

    @staticmethod
    def _raise_if_not_approved(review: ReviewOutput) -> None:
        if review.approved:
            return

        unsafe_reviews: list[CaptionReview] = [
            item for item in review.reviews if not item.is_safe or item.risk_level in {"MEDIUM", "HIGH"}
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Generated captions failed brand safety review. No posts were saved.",
                "overall_notes": review.overall_notes,
                "reviews": [item.model_dump() for item in unsafe_reviews],
            },
        )
