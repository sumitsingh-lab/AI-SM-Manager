from fastapi import APIRouter, File, Form, UploadFile, status
from pydantic import BaseModel

from app.services.parsing_service import MagazineMetadata
from app.services.pipeline_service import PipelineService

router = APIRouter(prefix="/ai", tags=["ai-pipeline"])


class ReviewResponse(BaseModel):
    approved: bool
    overall_notes: str


class DraftPostResponse(BaseModel):
    id: str
    platform: str
    caption: str
    approval_status: str


class PipelineResponse(BaseModel):
    asset_id: str
    metadata: MagazineMetadata
    review: ReviewResponse
    posts: list[DraftPostResponse]


@router.post("/pipeline/assets/{asset_id}/draft-posts", response_model=PipelineResponse)
async def generate_drafts_from_asset(
    asset_id: str,
    campaign_id: str | None = None,
    user_id: str | None = None,
) -> PipelineResponse:
    result = await PipelineService().process_existing_pdf_asset(asset_id, campaign_id=campaign_id, user_id=user_id)
    return _to_response(result)


@router.post("/pipeline/uploads/draft-posts", response_model=PipelineResponse, status_code=status.HTTP_201_CREATED)
async def upload_pdf_and_generate_drafts(
    campaign_id: str = Form(...),
    user_id: str | None = Form(default=None),
    description: str | None = Form(default=None),
    file: UploadFile = File(...),
    model_image: UploadFile | None = File(default=None),
) -> PipelineResponse:
    result = await PipelineService().upload_and_process_pdf(
        file=file,
        model_image=model_image,
        campaign_id=campaign_id,
        user_id=user_id,
        description=description,
    )
    return _to_response(result)


def _to_response(result) -> PipelineResponse:
    return PipelineResponse(
        asset_id=result.asset_id,
        metadata=result.metadata,
        review=ReviewResponse(approved=result.review.approved, overall_notes=result.review.overall_notes),
        posts=[
            DraftPostResponse(
                id=post.id,
                platform=post.platform,
                caption=post.caption,
                approval_status=post.approval_status,
            )
            for post in result.posts
        ],
    )
