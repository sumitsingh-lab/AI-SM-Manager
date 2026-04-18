from fastapi import APIRouter
from pydantic import BaseModel

from app.db import db

router = APIRouter(prefix="/tags", tags=["tags"])


class TagDirectoryResponse(BaseModel):
    id: str
    display_name: str
    handle: str | None
    platform: str | None
    notes: str | None


@router.get("", response_model=list[TagDirectoryResponse])
async def list_tags() -> list[TagDirectoryResponse]:
    tags = await db.tagdirectory.find_many(where={"isActive": True}, order={"displayName": "asc"}, take=500)
    return [
        TagDirectoryResponse(
            id=tag.id,
            display_name=tag.displayName,
            handle=tag.handle,
            platform=tag.platform,
            notes=tag.notes,
        )
        for tag in tags
    ]
