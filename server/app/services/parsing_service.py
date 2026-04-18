import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import BinaryIO

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
            parsed = await self.parse_pdf_file(pdf_file)

        await db.asset.update(
            where={"id": asset_id},
            data={"metadata": prisma_json(parsed.metadata.model_dump(mode="json"))},
        )
        return parsed

    async def parse_pdf_file(self, file: BinaryIO) -> ParsedMagazine:
        try:
            reader = PdfReader(file)
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            logger.exception("Could not parse uploaded PDF")
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not parse PDF.") from exc

        raw_text = "\n".join(pages)
        cleaned_text = self._clean_text(raw_text)
        if not cleaned_text:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PDF did not contain readable text.")

        metadata = MagazineMetadata(
            title=self._extract_title(reader, pages),
            page_count=len(reader.pages),
            summary=self._summarize(cleaned_text),
            brand_tags=await self._extract_brand_tags(cleaned_text),
            existing_hashtags=self._extract_hashtags(cleaned_text),
            keywords=self._extract_keywords(cleaned_text),
            source_excerpt=cleaned_text[:2500],
        )
        return ParsedMagazine(text=cleaned_text, metadata=metadata)

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
