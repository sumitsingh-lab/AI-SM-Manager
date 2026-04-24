import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import BinaryIO
from io import BytesIO
from pathlib import Path

import fitz
from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.db import db
from app.services.json_utils import prisma_json
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_]{2,50})")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
TITLE_CASE_RE = re.compile(r"\b(?:[A-Z][a-zA-Z0-9&'-]+(?:\s+|$)){1,4}")
WORD_RE = re.compile(r"\b[a-zA-Z][a-zA-Z'-]{2,}\b")

STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "but",
    "for",
    "from",
    "has",
    "have",
    "into",
    "its",
    "our",
    "that",
    "the",
    "their",
    "this",
    "with",
    "you",
    "your",
}


class MagazineMetadata(BaseModel):
    title: str | None = None
    page_count: int
    summary: str
    brand_tags: list[str] = Field(default_factory=list)
    existing_hashtags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source_excerpt: str
    page_excerpts: list[str] = Field(default_factory=list)
    extracted_image_asset_ids: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ParsedMagazine:
    text: str
    metadata: MagazineMetadata


class ParsingService:
    async def parse_pdf_asset(self, asset_id: str) -> ParsedMagazine:
        asset = await db.asset.find_unique(where={"id": asset_id})
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
        if asset.type != "MAGAZINE_PDF":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset must be a magazine PDF.")
        if not asset.gcsObjectName:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset does not have a GCS object name.")

        with StorageService().download_to_spooled_file(asset.gcsObjectName) as pdf_file:
            parsed = await self.parse_pdf_file(
                pdf_file,
                source_asset_id=asset.id,
                campaign_id=asset.campaignId,
                created_by_id=asset.createdById,
                source_file_name=asset.fileName,
            )

        await db.asset.update(
            where={"id": asset_id},
            data={"metadata": prisma_json(parsed.metadata.model_dump(mode="json"))},
        )
        return parsed

    async def parse_pdf_file(
        self,
        file: BinaryIO,
        *,
        source_asset_id: str | None = None,
        campaign_id: str | None = None,
        created_by_id: str | None = None,
        source_file_name: str | None = None,
    ) -> ParsedMagazine:
        pdf_bytes = self._read_pdf_bytes(file)
        reader: PdfReader | None = None
        pages: list[str] = []
        text_parse_failed = False
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            pages = [self._clean_text(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:
            logger.exception("Could not parse uploaded PDF")
            text_parse_failed = True

        extracted_image_asset_ids = await self._extract_embedded_images(
            pdf_bytes,
            source_asset_id=source_asset_id,
            campaign_id=campaign_id,
            created_by_id=created_by_id,
            source_file_name=source_file_name,
        )

        raw_text = "\n".join(pages)
        cleaned_text = self._clean_text(raw_text)
        if text_parse_failed or not cleaned_text:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PDF did not contain readable text.")

        metadata = MagazineMetadata(
            title=self._extract_title(reader, pages) if reader else None,
            page_count=len(reader.pages) if reader else 0,
            summary=self._summarize(cleaned_text),
            brand_tags=await self._extract_brand_tags(cleaned_text),
            existing_hashtags=self._extract_hashtags(cleaned_text),
            keywords=self._extract_keywords(cleaned_text),
            source_excerpt=cleaned_text[:2500],
            page_excerpts=self._build_page_excerpts(pages),
            extracted_image_asset_ids=extracted_image_asset_ids,
        )
        return ParsedMagazine(text=cleaned_text, metadata=metadata)

    async def _extract_embedded_images(
        self,
        pdf_bytes: bytes,
        *,
        source_asset_id: str | None,
        campaign_id: str | None,
        created_by_id: str | None,
        source_file_name: str | None,
    ) -> list[str]:
        extracted_image_asset_ids: list[str] = []
        seen_xrefs: set[int] = set()

        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception:
            logger.exception("Could not inspect PDF with PyMuPDF for embedded images.")
            return extracted_image_asset_ids

        storage = StorageService()
        try:
            for page_index in range(document.page_count):
                try:
                    page = document.load_page(page_index)
                    page_images = page.get_images(full=True)
                except Exception:
                    logger.exception("Could not inspect page %s for embedded images.", page_index + 1)
                    continue

                for image_index, image_info in enumerate(page_images, start=1):
                    xref = image_info[0]
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)

                    try:
                        image = document.extract_image(xref)
                        image_bytes = image.get("image")
                        if not image_bytes:
                            continue

                        ext = self._normalize_image_extension(image.get("ext"))
                        content_type = self._content_type_for_extension(ext)
                        file_name = self._build_extracted_image_name(
                            source_file_name=source_file_name,
                            page_number=page_index + 1,
                            image_index=image_index,
                            xref=xref,
                            extension=ext,
                        )
                        stored_file = storage.upload_generated_image(
                            image_bytes,
                            file_name,
                            prefix="extracted/pdf-images",
                            content_type=content_type,
                        )
                        asset = await db.asset.create(
                            data={
                                "type": "MODEL_IMAGE",
                                "fileName": stored_file.file_name,
                                "contentType": stored_file.content_type,
                                "fileSizeBytes": stored_file.file_size_bytes,
                                "gcsUrl": stored_file.gcs_url,
                                "gcsBucket": stored_file.gcs_bucket,
                                "gcsObjectName": stored_file.gcs_object_name,
                                "thumbnailUrl": stored_file.signed_url,
                                "description": self._build_extracted_image_description(
                                    source_asset_id=source_asset_id,
                                    source_file_name=source_file_name,
                                    page_number=page_index + 1,
                                    image_index=image_index,
                                ),
                                "metadata": prisma_json(
                                    {
                                        "extractedFromPdf": True,
                                        "sourcePdfAssetId": source_asset_id,
                                        "sourceFileName": source_file_name,
                                        "pageNumber": page_index + 1,
                                        "imageIndex": image_index,
                                        "xref": xref,
                                        "width": image.get("width"),
                                        "height": image.get("height"),
                                        "extension": ext,
                                    }
                                ),
                                "createdById": created_by_id,
                                "campaignId": campaign_id,
                            }
                        )
                        extracted_image_asset_ids.append(asset.id)
                    except Exception:
                        logger.exception(
                            "Could not extract embedded image xref %s from page %s.",
                            xref,
                            page_index + 1,
                        )
                        continue
        finally:
            document.close()

        return extracted_image_asset_ids

    async def _extract_brand_tags(self, text: str) -> list[str]:
        directory_tags = await self._match_tag_directory(text)
        candidates = []
        for match in TITLE_CASE_RE.findall(text[:12000]):
            candidate = " ".join(match.split()).strip(" .,:;")
            if 2 <= len(candidate) <= 60 and candidate.lower() not in STOP_WORDS:
                candidates.append(candidate)

        ranked = [tag for tag, _ in Counter(candidates).most_common(15)]
        merged = []
        for tag in [*directory_tags, *ranked]:
            if tag and tag.lower() not in {item.lower() for item in merged}:
                merged.append(tag)
        return merged[:12]

    async def _match_tag_directory(self, text: str) -> list[str]:
        tags = await db.tagdirectory.find_many(where={"isActive": True}, take=200)
        lower_text = text.lower()
        matches = []
        for tag in tags:
            names = [tag.displayName, tag.handle]
            if any(name and name.lower().lstrip("@") in lower_text for name in names):
                matches.append(tag.displayName)
        return matches

    @staticmethod
    def _clean_text(text: str) -> str:
        text = URL_RE.sub("", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _extract_title(reader: PdfReader, pages: list[str]) -> str | None:
        metadata_title = reader.metadata.title if reader.metadata else None
        if metadata_title:
            return metadata_title.strip()
        first_lines = [line.strip() for line in pages[0].splitlines() if line.strip()] if pages else []
        return first_lines[0][:120] if first_lines else None

    @staticmethod
    def _extract_hashtags(text: str) -> list[str]:
        seen = []
        for hashtag in HASHTAG_RE.findall(text):
            value = f"#{hashtag}"
            if value.lower() not in {item.lower() for item in seen}:
                seen.append(value)
        return seen[:25]

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        words = [
            word.lower().strip("'")
            for word in WORD_RE.findall(text)
            if word.lower() not in STOP_WORDS and len(word) > 3
        ]
        return [word for word, _ in Counter(words).most_common(20)]

    @staticmethod
    def _summarize(text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        summary = " ".join(sentence for sentence in sentences[:5] if sentence)
        return summary[:1200]

    @staticmethod
    def _build_page_excerpts(pages: list[str], limit: int = 1200) -> list[str]:
        excerpts = []
        for index, page_text in enumerate(pages, start=1):
            text = page_text.strip()
            if not text:
                continue
            excerpts.append(f"Page {index}: {text[:limit]}")
        return excerpts[:50]

    @staticmethod
    def _read_pdf_bytes(file: BinaryIO) -> bytes:
        if hasattr(file, "seek"):
            try:
                file.seek(0)
            except Exception:
                pass
        data = file.read()
        if not isinstance(data, bytes):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not read PDF bytes.")
        return data

    @staticmethod
    def _normalize_image_extension(extension: str | None) -> str:
        normalized = (extension or "png").lower().lstrip(".")
        return "jpg" if normalized == "jpeg" else normalized

    @staticmethod
    def _content_type_for_extension(extension: str) -> str:
        if extension in {"jpg", "jpeg"}:
            return "image/jpeg"
        if extension == "png":
            return "image/png"
        if extension == "webp":
            return "image/webp"
        return f"image/{extension or 'png'}"

    @staticmethod
    def _build_extracted_image_name(
        *,
        source_file_name: str | None,
        page_number: int,
        image_index: int,
        xref: int,
        extension: str,
    ) -> str:
        source_stem = Path(source_file_name or "magazine").stem.lower()
        source_stem = re.sub(r"[^a-z0-9._-]+", "-", source_stem).strip(".-") or "magazine"
        return f"{source_stem}-page{page_number:03d}-img{image_index:02d}-xref{xref}.{extension}"

    @staticmethod
    def _build_extracted_image_description(
        *,
        source_asset_id: str | None,
        source_file_name: str | None,
        page_number: int,
        image_index: int,
    ) -> str:
        source_label = source_file_name or source_asset_id or "uploaded pdf"
        return f"Extracted embedded image {image_index} from page {page_number} of {source_label}."
