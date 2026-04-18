import logging
from typing import Literal

from fastapi import HTTPException, status
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

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


class CopywriterOutput(BaseModel):
    captions: list[PlatformCaption]


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
                    "Write polished, brand-safe draft captions. Do not invent facts, prices, awards, dates, "
                    "or endorsements that are not supported by the provided metadata.",
                ),
                (
                    "human",
                    "Create one caption for each platform: FACEBOOK, TWITTER, LINKEDIN.\n\n"
                    "Rules:\n"
                    "- Facebook: warm, community-focused, up to 700 characters.\n"
                    "- Twitter: concise, punchy, 240 characters or fewer including hashtags.\n"
                    "- LinkedIn: professional, insight-led, up to 700 characters.\n"
                    "- Reuse relevant existing hashtags when they fit.\n"
                    "- Include platform-specific hashtags separately in the hashtags field.\n\n"
                    "Magazine metadata:\n{metadata}\n\n"
                    "Source excerpt:\n{source_excerpt}",
                ),
            ]
        )

    async def generate(self, metadata: MagazineMetadata) -> CopywriterOutput:
        try:
            chain = self._prompt | self._structured_llm
            output = await chain.ainvoke(
                {
                    "metadata": metadata.model_dump_json(indent=2),
                    "source_excerpt": metadata.source_excerpt,
                }
            )
        except Exception as exc:
            logger.exception("Copywriter agent failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Copywriter agent failed.") from exc

        return self._normalize_output(output)

    @staticmethod
    def _normalize_output(output: CopywriterOutput) -> CopywriterOutput:
        by_platform = {caption.platform: caption for caption in output.captions}
        missing = {"FACEBOOK", "TWITTER", "LINKEDIN"} - set(by_platform)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Copywriter agent missed platforms: {', '.join(sorted(missing))}.",
            )
        by_platform["TWITTER"].caption = by_platform["TWITTER"].caption[:240]
        return CopywriterOutput(captions=[by_platform["FACEBOOK"], by_platform["TWITTER"], by_platform["LINKEDIN"]])


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

    async def review(self, metadata: MagazineMetadata, copy: CopywriterOutput) -> ReviewOutput:
        try:
            chain = self._prompt | self._structured_llm
            output = await chain.ainvoke(
                {
                    "metadata": metadata.model_dump_json(indent=2),
                    "captions": copy.model_dump_json(indent=2),
                }
            )
        except Exception as exc:
            logger.exception("Supervisor review agent failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Review agent failed.") from exc

        if any(review.risk_level in {"MEDIUM", "HIGH"} for review in output.reviews):
            output.approved = False
        return output
