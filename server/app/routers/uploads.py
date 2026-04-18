import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.db import db
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])


class AssetUploadResponse(BaseModel):
    id: str
    type: str
    file_name: str
    content_type: str
    file_size_bytes: int
    gcs_url: str
    public_url: str
    signed_url: str | None


@router.post("/assets", response_model=AssetUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    asset_type: str = Form(..., pattern="^(MAGAZINE_PDF|MODEL_IMAGE)$"),
    user_id: str | None = Form(default=None),
    campaign_id: str | None = Form(default=None),
    description: str | None = Form(default=None),
    file: UploadFile = File(...),
) -> AssetUploadResponse:
    if user_id:
        user = await db.user.find_unique(where={"id": user_id})
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if campaign_id:
        campaign = await db.campaign.find_unique(where={"id": campaign_id})
        if campaign is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    stored_file = await StorageService().upload_asset(file, asset_type)
    asset = await db.asset.create(
        data={
            "type": asset_type,
            "fileName": stored_file.file_name,
            "contentType": stored_file.content_type,
            "fileSizeBytes": stored_file.file_size_bytes,
            "gcsUrl": stored_file.gcs_url,
            "gcsBucket": stored_file.gcs_bucket,
            "gcsObjectName": stored_file.gcs_object_name,
            "thumbnailUrl": stored_file.signed_url if asset_type == "MODEL_IMAGE" else None,
            "description": description,
            "createdById": user_id,
            "campaignId": campaign_id,
        }
    )

    logger.info("Uploaded asset %s to %s", asset.id, stored_file.gcs_url)
    return AssetUploadResponse(
        id=asset.id,
        type=asset.type,
        file_name=asset.fileName,
        content_type=asset.contentType,
        file_size_bytes=int(asset.fileSizeBytes or 0),
        gcs_url=asset.gcsUrl,
        public_url=stored_file.public_url,
        signed_url=stored_file.signed_url,
    )
