import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from google.cloud import storage
from google.auth.exceptions import DefaultCredentialsError
from google.cloud.exceptions import GoogleCloudError

from app.config import settings

logger = logging.getLogger(__name__)
SERVER_ROOT = Path(__file__).resolve().parents[2]
LOCAL_UPLOAD_ROOT = SERVER_ROOT / "local_uploads"

ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


@dataclass(frozen=True)
class StoredFile:
    file_name: str
    content_type: str
    file_size_bytes: int
    gcs_url: str
    gcs_bucket: str
    gcs_object_name: str
    public_url: str
    signed_url: str | None


class StorageService:
    def __init__(self) -> None:
        self._client: storage.Client | None = None
        self._bucket: storage.Bucket | None = None
        self._local_upload_root = LOCAL_UPLOAD_ROOT
        self._local_upload_root.mkdir(parents=True, exist_ok=True)

        if not settings.gcs_bucket_name:
            logger.warning("GCS_BUCKET_NAME is not configured. Using local upload storage.")
            return

        try:
            self._client = storage.Client(project=settings.gcs_project_id)
            self._bucket = self._client.bucket(settings.gcs_bucket_name)
        except DefaultCredentialsError:
            logger.warning("GCS credentials are not configured. Using local upload storage.")
        except Exception:
            logger.warning("GCS client initialization failed. Using local upload storage.")

    @property
    def using_local_storage(self) -> bool:
        return self._bucket is None

    async def upload_asset(self, file: UploadFile, asset_type: str) -> StoredFile:
        content_type = file.content_type or "application/octet-stream"
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Only PDF, JPEG, PNG, and WebP uploads are supported.",
            )

        if asset_type == "MAGAZINE_PDF" and content_type != "application/pdf":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Magazine assets must be PDFs.")

        if asset_type == "MODEL_IMAGE" and not content_type.startswith("image/"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Model assets must be images.")

        safe_name = self._safe_file_name(file.filename or f"upload.{ALLOWED_CONTENT_TYPES[content_type]}")
        object_name = f"assets/{asset_type.lower()}/{uuid4()}-{safe_name}"
        size = 0

        with tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024, mode="w+b") as buffer:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload exceeds {settings.max_upload_bytes} bytes.",
                    )
                buffer.write(chunk)

            buffer.seek(0)
            if self.using_local_storage:
                return self._save_local_file(buffer, object_name, safe_name, content_type, size)

            blob = self._bucket.blob(object_name)  # type: ignore[union-attr]
            try:
                self._upload_to_gcs_blob(blob, buffer, content_type)
            except GoogleCloudError:
                logger.warning("GCS upload failed for %s. Falling back to local storage.", object_name)
                buffer.seek(0)
                return self._save_local_file(buffer, object_name, safe_name, content_type, size)

        signed_url = self._signed_read_url(blob)
        return StoredFile(
            file_name=safe_name,
            content_type=content_type,
            file_size_bytes=size,
            gcs_url=f"gs://{self._bucket.name}/{object_name}",  # type: ignore[union-attr]
            gcs_bucket=self._bucket.name,  # type: ignore[union-attr]
            gcs_object_name=object_name,
            public_url=blob.public_url,
            signed_url=signed_url,
        )

    def upload_generated_image(
        self,
        data: bytes,
        file_name: str,
        prefix: str = "generated/compositions",
        content_type: str = "image/png",
    ) -> StoredFile:
        safe_name = self._safe_file_name(file_name)
        object_name = f"{prefix.rstrip('/')}/{uuid4()}-{safe_name}"

        if self.using_local_storage:
            return self._save_local_bytes(data, object_name, safe_name, content_type)

        blob = self._bucket.blob(object_name)  # type: ignore[union-attr]
        try:
            with tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024, mode="w+b") as buffer:
                buffer.write(data)
                buffer.seek(0)
                self._upload_to_gcs_blob(blob, buffer, content_type)
        except GoogleCloudError:
            logger.warning("GCS generated image upload failed for %s. Falling back to local storage.", object_name)
            return self._save_local_bytes(data, object_name, safe_name, content_type)

        return StoredFile(
            file_name=safe_name,
            content_type=content_type,
            file_size_bytes=len(data),
            gcs_url=f"gs://{self._bucket.name}/{object_name}",  # type: ignore[union-attr]
            gcs_bucket=self._bucket.name,  # type: ignore[union-attr]
            gcs_object_name=object_name,
            public_url=blob.public_url,
            signed_url=self._signed_read_url(blob),
        )

    def _signed_read_url(self, blob: storage.Blob) -> str | None:
        try:
            return blob.generate_signed_url(
                version="v4",
                expiration=timedelta(minutes=settings.gcs_signed_url_minutes),
                method="GET",
            )
        except Exception:
            logger.exception("Could not generate signed URL for %s", blob.name)
            return None

    def download_to_spooled_file(self, object_name: str) -> SpooledTemporaryFile:
        buffer = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024, mode="w+b")
        if self.using_local_storage or object_name.startswith("local/"):
            local_path = self._local_path_for_object(object_name)
            if not local_path.exists():
                buffer.close()
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local uploaded file not found.")
            with local_path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    buffer.write(chunk)
            buffer.seek(0)
            return buffer

        blob = self._bucket.blob(object_name)  # type: ignore[union-attr]
        try:
            blob.download_to_file(buffer, timeout=120)
            buffer.seek(0)
            return buffer
        except GoogleCloudError as exc:
            buffer.close()
            logger.exception("GCS download failed for %s", object_name)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Storage provider failed while downloading the file.",
            ) from exc

    def public_url_for_object(self, object_name: str) -> str | None:
        if object_name.startswith("local/") or self.using_local_storage:
            return self._local_url(object_name)
        if not self._bucket:
            return None
        blob = self._bucket.blob(object_name)
        return blob.public_url if settings.gcs_make_public else self._signed_read_url(blob)

    def _upload_to_gcs_blob(self, blob: storage.Blob, buffer, content_type: str) -> None:
        blob.upload_from_file(
            buffer,
            rewind=True,
            content_type=content_type,
            timeout=120,
        )
        if settings.gcs_make_public:
            blob.make_public()

    def _save_local_file(
        self,
        buffer,
        object_name: str,
        safe_name: str,
        content_type: str,
        size: int,
    ) -> StoredFile:
        local_path = self._local_path_for_object(object_name)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with local_path.open("wb") as target:
            while chunk := buffer.read(1024 * 1024):
                target.write(chunk)
        return self._local_stored_file(object_name, safe_name, content_type, size)

    def _save_local_bytes(self, data: bytes, object_name: str, safe_name: str, content_type: str) -> StoredFile:
        local_path = self._local_path_for_object(object_name)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        return self._local_stored_file(object_name, safe_name, content_type, len(data))

    def _local_stored_file(self, object_name: str, safe_name: str, content_type: str, size: int) -> StoredFile:
        local_url = self._local_url(object_name)
        return StoredFile(
            file_name=safe_name,
            content_type=content_type,
            file_size_bytes=size,
            gcs_url=local_url,
            gcs_bucket="local",
            gcs_object_name=f"local/{object_name}",
            public_url=local_url,
            signed_url=local_url,
        )

    def _local_path_for_object(self, object_name: str) -> Path:
        relative = object_name.removeprefix("local/").replace("\\", "/")
        local_path = (self._local_upload_root / relative).resolve()
        root = self._local_upload_root.resolve()
        if root != local_path and root not in local_path.parents:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid local storage path.")
        return local_path

    @staticmethod
    def _local_url(object_name: str) -> str:
        relative = object_name.removeprefix("local/").replace("\\", "/")
        return f"{settings.api_base_url.rstrip('/')}/uploads/{relative}"

    @staticmethod
    def _safe_file_name(file_name: str) -> str:
        suffix = Path(file_name).suffix.lower()
        stem = Path(file_name).stem.lower()
        stem = re.sub(r"[^a-z0-9._-]+", "-", stem).strip(".-") or "asset"
        suffix = re.sub(r"[^a-z0-9.]+", "", suffix)
        return f"{stem}{suffix}"
