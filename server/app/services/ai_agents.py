import logging
from typing import Literal

from fastapi import HTTPException, status
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, conlist

from app.config import settings
from app.services.parsing_service import MagazineMetadata

logger = logging.getLogger(__name__)

PlatformName = Literal["FACEBOOK", "TWITTER", "LINKEDIN"]


def require_openai_key() -> None:
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OPENAI_API_KEY is not configured.",
        )


class PlatformCaption(BaseModel):
    platform: PlatformName
    caption: str = Field(description="The generated caption for this platform.")
    hashtags: list[str] = Field(default_factory=list)
    rationale: str = Field(description="Brief reason this caption fits the platform.")


class TagDirectoryContext(BaseModel):
    id: str
    display_name: str
    handle: str | None = None
    platform: str | None = None


class PostConcept(BaseModel):
    concept_title: str = Field(description="Short concept title for internal dashboard context.")
    platform: PlatformName
    caption: str = Field(description="The generated caption for this post concept.")
    hashtags: list[str] = Field(default_factory=list)
    rationale: str = Field(description="Why this concept and caption are strong for the chosen platform.")
    page_numbers: list[int] = Field(default_factory=list)
    credit_mentions: list[str] = Field(default_factory=list)
    matched_tag_ids: list[str] = Field(default_factory=list)


class CopywriterOutput(BaseModel):
    post_concepts: conlist(PostConcept, min_length=6, max_length=9)


class CaptionReview(BaseModel):
    platform: PlatformName
    is_safe: bool
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    issues: list[str] = Field(default_factory=list)
    suggested_revision: str | None = None


class ReviewOutput(BaseModel):
    approved: bool
    reviews: list[CaptionReview]
    overall_notes: str


class CopywriterAgent:
    def __init__(self) -> None:
        require_openai_key()
        self._llm = ChatOpenAI(model=settings.ai_copywriter_model, temperature=0.7, api_key=settings.openai_api_key)
        self._structured_llm = self._llm.with_structured_output(CopywriterOutput)
        self._prompt = ChatPromptTemplate(
            [
                (
                    "system",
                    "You are a senior social media copywriter for premium magazine campaigns. "
                    "Write polished, brand-safe post concepts. Do not invent facts, prices, awards, dates, "
                    "or endorsements that are not supported by the provided metadata. "
                    "Your output must be grounded in the page excerpts, the magazine credits, and the tag directory.",
                ),
                (
                    "human",
                    "Create 6 to 9 distinct post concepts for the magazine campaign.\n\n"
                    "Rules:\n"
                    "- Each concept must be distinct in angle, not just a wording variation.\n"
                    "- Every caption must explicitly mention at least one page number from the provided page excerpts.\n"
                    "- Every caption must explicitly credit the creative team, models, photographers, stylists, or other names found in the magazine credits when present.\n"
                    "- Cross-reference any names found in the credits with the provided tag directory and populate matched_tag_ids with the UUIDs of exact matches only.\n"
                    "- If a credit name does not match the tag directory exactly, leave it out rather than inventing an id.\n"
                    "- Keep captions publication-ready for the chosen platform.\n"
                    "- Reuse relevant hashtags when they fit, but keep the hashtag list separate as structured data.\n"
                    "- Favor concepts that can map cleanly to extracted image assets and page references.\n\n"
                    "Magazine metadata:\n{metadata}\n\n"
                    "Page excerpts with explicit numbers:\n{page_excerpts}\n\n"
                    "Tag directory entries:\n{tag_directory}\n\n"
                    "Embedded images extracted from the PDF:\n{extracted_images}\n\n"
                    "Source excerpt:\n{source_excerpt}",
                ),
            ]
        )

    async def generate(
        self,
        metadata: MagazineMetadata,
        tag_directory: list[TagDirectoryContext],
        extracted_image_asset_ids: list[str] | None = None,
    ) -> CopywriterOutput:
        image_context = ", ".join(extracted_image_asset_ids or []) or "No embedded images were extracted."
        try:
            chain = self._prompt | self._structured_llm
            output = await chain.ainvoke(
                {
                    "metadata": metadata.model_dump_json(indent=2),
                    "page_excerpts": "\n".join(metadata.page_excerpts) if metadata.page_excerpts else "No page excerpts available.",
                    "tag_directory": "\n".join(
                        f"{tag.id} | {tag.display_name} | {tag.handle or ''} | {tag.platform or ''}".strip(" |")
                        for tag in tag_directory
                    )
                    or "No tag directory entries provided.",
                    "source_excerpt": metadata.source_excerpt,
                    "extracted_images": image_context,
                }
            )
        except Exception as exc:
            logger.exception("Copywriter agent failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Copywriter agent failed.") from exc

        return self._normalize_output(output)

    @staticmethod
    def _normalize_output(output: CopywriterOutput) -> CopywriterOutput:
        concepts = list(output.post_concepts)
        if not 6 <= len(concepts) <= 9:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Copywriter agent must return between 6 and 9 post concepts.",
            )
        for concept in concepts:
            concept.caption = concept.caption.strip()
            if concept.platform == "TWITTER":
                concept.caption = concept.caption[:240]
            concept.matched_tag_ids = list(dict.fromkeys(concept.matched_tag_ids))
            concept.page_numbers = [page for page in dict.fromkeys(concept.page_numbers) if page > 0]
            concept.credit_mentions = [credit.strip() for credit in dict.fromkeys(concept.credit_mentions) if credit.strip()]
            concept.hashtags = [hashtag.strip() for hashtag in dict.fromkeys(concept.hashtags) if hashtag.strip()]
        return CopywriterOutput(post_concepts=concepts)


class SupervisorReviewAgent:
    def __init__(self) -> None:
        require_openai_key()
        self._llm = ChatOpenAI(model=settings.ai_reviewer_model, temperature=0, api_key=settings.openai_api_key)
        self._structured_llm = self._llm.with_structured_output(ReviewOutput)
        self._prompt = ChatPromptTemplate(
            [
                (
                    "system",
                    "You are a cautious brand safety supervisor for social publishing. "
                    "Reject captions that include unsupported factual claims, sensitive or discriminatory language, "
                    "unsafe medical/legal/financial advice, explicit content, harassment, privacy violations, "
                    "or misleading calls to action.",
                ),
                (
                    "human",
                    "Review these draft captions before database persistence.\n\n"
                    "Brand/source metadata:\n{metadata}\n\n"
                    "Captions:\n{captions}\n\n"
                    "Return approved=false if any caption has MEDIUM or HIGH risk.",
                ),
            ]
        )

    async def review(
        self,
        metadata: MagazineMetadata,
        copy: CopywriterOutput,
        extracted_image_asset_ids: list[str] | None = None,
    ) -> ReviewOutput:
        image_context = ", ".join(extracted_image_asset_ids or []) or "No embedded images were extracted."
        try:
            chain = self._prompt | self._structured_llm
            output = await chain.ainvoke(
                {
                    "metadata": metadata.model_dump_json(indent=2),
                    "captions": copy.model_dump_json(indent=2),
                    "extracted_images": image_context,
                }
            )
        except Exception as exc:
            logger.exception("Supervisor review agent failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Review agent failed.") from exc

        if any(review.risk_level in {"MEDIUM", "HIGH"} for review in output.reviews):
            output.approved = False
        return output
