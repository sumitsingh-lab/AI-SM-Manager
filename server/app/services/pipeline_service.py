import logging
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile, status

from app.db import db
from app.services.ai_agents import (
    CaptionReview,
    CopywriterAgent,
    PostConcept,
    ReviewOutput,
    SupervisorReviewAgent,
    TagDirectoryContext,
)
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
    extracted_image_asset_ids: list[str]
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
        tag_directory = await self._load_tag_directory_context()
        parsed = await self._parser.parse_pdf_asset(asset_id)
        extracted_image_asset_ids = parsed.metadata.extracted_image_asset_ids
        copy = await self._copywriter.generate(parsed.metadata, tag_directory, extracted_image_asset_ids)
        review = await self._reviewer.review(parsed.metadata, copy, extracted_image_asset_ids)
        self._raise_if_not_approved(review)
        fallback_post_asset_id = asset_id
        if not extracted_image_asset_ids:
            fallback_post_asset_id = (
                await self._create_mockup_asset(
                    source_asset_id=mockup_source_asset_id or asset_id,
                    campaign_id=resolved_campaign_id,
                    user_id=user_id,
                    metadata=parsed.metadata,
                )
                or asset_id
            )
        posts = await self._save_pending_posts(
            campaign_id=resolved_campaign_id,
            concepts=copy.post_concepts,
            image_asset_ids=extracted_image_asset_ids,
            fallback_asset_id=fallback_post_asset_id or asset_id,
            tag_directory=tag_directory,
            user_id=user_id,
            metadata=parsed.metadata,
            review=review,
            source_pdf_asset_id=asset_id,
        )
        return PipelineResult(
            asset_id=asset_id,
            metadata=parsed.metadata,
            extracted_image_asset_ids=extracted_image_asset_ids,
            review=review,
            posts=posts,
        )

    async def upload_and_process_pdf(
        self,
        file: UploadFile,
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

        return await self.process_existing_pdf_asset(
            asset.id,
            campaign_id=campaign_id,
            user_id=user_id,
        )

    async def _save_pending_posts(
        self,
        campaign_id: str,
        concepts: list[PostConcept],
        image_asset_ids: list[str],
        fallback_asset_id: str,
        tag_directory: list[TagDirectoryContext],
        user_id: str | None,
        metadata: MagazineMetadata,
        review: ReviewOutput,
        source_pdf_asset_id: str,
    ) -> list[DraftPostResult]:
        reviews_by_platform = {item.platform: item for item in review.reviews}
        tag_ids_by_name = self._tag_ids_by_name(tag_directory)
        results = []

        for index, concept in enumerate(concepts):
            platform_review = reviews_by_platform.get(concept.platform)
            asset_id = self._resolve_concept_asset_id(index, image_asset_ids, fallback_asset_id)
            matched_tag_ids = self._resolve_tag_ids(concept.matched_tag_ids, tag_directory, tag_ids_by_name)
            ai_metadata = {
                "parser": metadata.model_dump(mode="json"),
                "copywriter": concept.model_dump(mode="json"),
                "review": platform_review.model_dump(mode="json") if platform_review else None,
                "overallReviewNotes": review.overall_notes,
                "sourcePdfAssetId": source_pdf_asset_id,
                "extractedImageAssetIds": metadata.extracted_image_asset_ids,
                "selectedAssetId": asset_id,
                "matchedTagIds": matched_tag_ids,
            }
            post_data = {
                "platform": concept.platform,
                "generatedCaption": self._compose_caption(concept),
                "selectedAspectRatio": DEFAULT_ASPECT_RATIO_BY_PLATFORM[concept.platform],
                "approvalStatus": "PENDING",
                "campaignId": campaign_id,
                "assetId": asset_id,
                "generatedById": user_id,
                "aiMetadata": prisma_json(ai_metadata),
            }
            if matched_tag_ids:
                post_data["tags"] = {
                    "create": [
                        {"tag": {"connect": {"id": tag_id}}}
                        for tag_id in matched_tag_ids
                    ]
                }

            post = await db.post.create(data=post_data)
            results.append(
                DraftPostResult(
                    id=post.id,
                    platform=post.platform,
                    caption=post.generatedCaption,
                    approval_status=post.approvalStatus,
                )
            )

        return results

    async def _load_tag_directory_context(self) -> list[TagDirectoryContext]:
        tags = await db.tagdirectory.find_many(where={"isActive": True}, order={"displayName": "asc"}, take=500)
        return [
            TagDirectoryContext(
                id=tag.id,
                display_name=tag.displayName,
                handle=tag.handle,
                platform=tag.platform,
            )
            for tag in tags
        ]

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

    @staticmethod
    def _resolve_concept_asset_id(index: int, image_asset_ids: list[str], fallback_asset_id: str) -> str:
        if image_asset_ids:
            return image_asset_ids[index % len(image_asset_ids)]
        return fallback_asset_id

    @staticmethod
    def _tag_ids_by_name(tag_directory: list[TagDirectoryContext]) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for tag in tag_directory:
            lookup[tag.id.lower()] = tag.id
            lookup[tag.display_name.lower()] = tag.id
            if tag.handle:
                lookup[tag.handle.lower().lstrip("@")] = tag.id
        return lookup

    @staticmethod
    def _resolve_tag_ids(
        matched_tag_ids: list[str],
        tag_directory: list[TagDirectoryContext],
        tag_ids_by_name: dict[str, str],
    ) -> list[str]:
        allowed_tag_ids = {tag.id for tag in tag_directory}
        resolved: list[str] = []
        for tag_id in matched_tag_ids:
            candidate = tag_ids_by_name.get(tag_id.lower(), tag_id)
            if candidate in allowed_tag_ids and candidate not in resolved:
                resolved.append(candidate)
        return resolved

    async def _ensure_campaign_and_user(self, campaign_id: str, user_id: str | None) -> None:
        campaign = await db.campaign.find_unique(where={"id": campaign_id})
        if campaign is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

        if user_id:
            user = await db.user.find_unique(where={"id": user_id})
            if user is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    @staticmethod
    def _compose_caption(concept: PostConcept) -> str:
        hashtags = " ".join(concept.hashtags)
        caption = concept.caption.strip()
        if hashtags and hashtags not in caption:
            return f"{caption}\n\n{hashtags}".strip()
        return caption

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
