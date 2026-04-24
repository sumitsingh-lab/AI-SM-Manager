from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.services.image_composition_service import ASPECT_RATIOS, TEMPLATES, ImageCompositionService

router = APIRouter(prefix="/images", tags=["image-composition"])

AspectRatioName = Literal["SQUARE_1_1", "PORTRAIT_4_5", "LANDSCAPE_16_9"]
FitMode = Literal["crop", "pad"]


class TemplateSummary(BaseModel):
    key: str
    size: tuple[int, int]
    text_fields: list[str]


class GeneratedImageResponse(BaseModel):
    asset_id: str
    file_name: str
    width: int
    height: int
    content_type: str
    gcs_url: str
    public_url: str
    signed_url: str | None
    metadata: dict


class TextOverlayRequest(BaseModel):
    image_asset_id: str
    template_key: str = "clean_release"
    text: dict[str, str] = Field(
        default_factory=dict,
        description="Template text values, for example headline, subheadline, and cta.",
    )
    aspect_ratio: AspectRatioName | None = None
    fit: FitMode = "crop"
    campaign_id: str | None = None
    user_id: str | None = None


class AspectVariantsRequest(BaseModel):
    image_asset_id: str
    aspect_ratios: list[AspectRatioName] = Field(default_factory=lambda: ["SQUARE_1_1", "PORTRAIT_4_5", "LANDSCAPE_16_9"])
    fit: FitMode = "crop"
    campaign_id: str | None = None
    user_id: str | None = None


class MagazineMockupRequest(BaseModel):
    foreground_asset_id: str
    headline: str
    subheadline: str | None = None
    aspect_ratio: AspectRatioName = "SQUARE_1_1"
    fit: FitMode = "crop"
    campaign_id: str | None = None
    user_id: str | None = None


class BrandCropRequest(BaseModel):
    source_asset_id: str
    aspect_ratio: AspectRatioName
    campaign_id: str | None = None
    user_id: str | None = None


class BrandCropResponse(BaseModel):
    asset_id: str
    local_url: str


@router.get("/templates", response_model=list[TemplateSummary])
async def list_templates() -> list[TemplateSummary]:
    return [
        TemplateSummary(
            key=template.key,
            size=template.size,
            text_fields=[box.key for box in template.text_boxes],
        )
        for template in TEMPLATES.values()
    ]


@router.get("/aspect-ratios")
async def list_aspect_ratios() -> dict[str, tuple[int, int]]:
    return ASPECT_RATIOS


@router.post("/compose/text-overlay", response_model=GeneratedImageResponse, status_code=status.HTTP_201_CREATED)
async def compose_text_overlay(request: TextOverlayRequest) -> GeneratedImageResponse:
    service = ImageCompositionService()
    image, metadata = await service.compose_text_overlay(
        image_asset_id=request.image_asset_id,
        template_key=request.template_key,
        text=request.text,
        aspect_ratio=request.aspect_ratio,
        fit=request.fit,
    )
    asset, stored = await service.save_as_asset(
        image=image,
        metadata=metadata,
        campaign_id=request.campaign_id,
        user_id=request.user_id,
    )
    return _response(asset, stored, image, metadata)


@router.post("/compose/aspect-variants", response_model=list[GeneratedImageResponse], status_code=status.HTTP_201_CREATED)
async def compose_aspect_variants(request: AspectVariantsRequest) -> list[GeneratedImageResponse]:
    service = ImageCompositionService()
    generated = await service.create_aspect_variants(
        image_asset_id=request.image_asset_id,
        ratios=request.aspect_ratios,
        fit=request.fit,
    )
    responses = []
    for ratio, image, metadata in generated:
        saved_metadata = {**metadata, "sourceAssetId": request.image_asset_id}
        asset, stored = await service.save_as_asset(
            image=image,
            metadata=saved_metadata,
            campaign_id=request.campaign_id,
            user_id=request.user_id,
        )
        responses.append(_response(asset, stored, image, saved_metadata))
    return responses


@router.post("/compose/magazine-mockup", response_model=GeneratedImageResponse, status_code=status.HTTP_201_CREATED)
async def compose_magazine_mockup(request: MagazineMockupRequest) -> GeneratedImageResponse:
    service = ImageCompositionService()
    image, metadata = await service.create_magazine_release_mockup(
        foreground_asset_id=request.foreground_asset_id,
        headline=request.headline,
        subheadline=request.subheadline,
        aspect_ratio=request.aspect_ratio,
        fit=request.fit,
    )
    asset, stored = await service.save_as_asset(
        image=image,
        metadata=metadata,
        campaign_id=request.campaign_id,
        user_id=request.user_id,
    )
    return _response(asset, stored, image, metadata)


@router.post("/brand-and-crop", response_model=BrandCropResponse, status_code=status.HTTP_201_CREATED)
async def brand_and_crop(request: BrandCropRequest) -> BrandCropResponse:
    service = ImageCompositionService()
    asset, stored = await service.apply_branding_and_crop(
        asset_id=request.source_asset_id,
        aspect_ratio=request.aspect_ratio,
        campaign_id=request.campaign_id,
        user_id=request.user_id,
    )
    return BrandCropResponse(asset_id=asset.id, local_url=stored.public_url)


def _response(asset, stored, image, metadata: dict) -> GeneratedImageResponse:
    return GeneratedImageResponse(
        asset_id=asset.id,
        file_name=asset.fileName,
        width=image.width,
        height=image.height,
        content_type=image.content_type,
        gcs_url=asset.gcsUrl,
        public_url=stored.public_url,
        signed_url=stored.signed_url,
        metadata=metadata,
    )
