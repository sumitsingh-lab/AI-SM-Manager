import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from fastapi import HTTPException, status
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from app.config import settings
from app.db import db
from app.services.json_utils import prisma_json
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

AspectRatioName = Literal["SQUARE_1_1", "PORTRAIT_4_5", "LANDSCAPE_16_9"]
FitMode = Literal["crop", "pad"]

ASPECT_RATIOS: dict[AspectRatioName, tuple[int, int]] = {
    "SQUARE_1_1": (1, 1),
    "PORTRAIT_4_5": (4, 5),
    "LANDSCAPE_16_9": (16, 9),
}


@dataclass(frozen=True)
class TextBoxTemplate:
    key: str
    xy: tuple[int, int]
    width: int
    height: int
    font_size: int
    fill: str
    align: Literal["left", "center", "right"] = "left"
    stroke_fill: str | None = None
    stroke_width: int = 0


@dataclass(frozen=True)
class CompositionTemplate:
    key: str
    size: tuple[int, int]
    background: tuple[int, int, int]
    accent: tuple[int, int, int]
    text_boxes: tuple[TextBoxTemplate, ...]


@dataclass(frozen=True)
class ComposedImage:
    data: bytes
    width: int
    height: int
    content_type: str
    file_name: str


TEMPLATES: dict[str, CompositionTemplate] = {
    "clean_release": CompositionTemplate(
        key="clean_release",
        size=(1600, 1600),
        background=(247, 248, 246),
        accent=(17, 24, 39),
        text_boxes=(
            TextBoxTemplate("headline", (120, 1040), 1360, 180, 72, "#111827", "center"),
            TextBoxTemplate("subheadline", (220, 1235), 1160, 120, 38, "#374151", "center"),
            TextBoxTemplate("cta", (440, 1400), 720, 70, 34, "#ffffff", "center"),
        ),
    ),
    "editorial_story": CompositionTemplate(
        key="editorial_story",
        size=(1440, 1800),
        background=(242, 245, 247),
        accent=(30, 64, 175),
        text_boxes=(
            TextBoxTemplate("headline", (90, 1180), 1260, 210, 76, "#0f172a", "left"),
            TextBoxTemplate("subheadline", (90, 1415), 1080, 140, 42, "#334155", "left"),
            TextBoxTemplate("cta", (90, 1610), 620, 70, 34, "#ffffff", "center"),
        ),
    ),
}


class ImageCompositionService:
    async def compose_text_overlay(
        self,
        image_asset_id: str,
        template_key: str,
        text: dict[str, str],
        aspect_ratio: AspectRatioName | None = None,
        fit: FitMode = "crop",
    ) -> tuple[ComposedImage, dict]:
        template = self._get_template(template_key)
        base = await self._load_image_asset(image_asset_id)
        canvas = self._prepare_canvas(base, template)
        self._draw_template_text(canvas, template, text)
        if aspect_ratio:
            canvas = self.apply_aspect_ratio(canvas, aspect_ratio, fit)
        return self._encode(canvas, f"{template_key}-{aspect_ratio or 'source'}.png"), {
            "template": template_key,
            "text": text,
            "aspectRatio": aspect_ratio,
            "fit": fit,
        }

    async def create_aspect_variants(
        self,
        image_asset_id: str,
        ratios: list[AspectRatioName],
        fit: FitMode = "crop",
    ) -> list[tuple[AspectRatioName, ComposedImage, dict]]:
        source = await self._load_image_asset(image_asset_id)
        results = []
        for ratio in ratios:
            image = self.apply_aspect_ratio(source.copy(), ratio, fit)
            results.append((ratio, self._encode(image, f"variant-{ratio.lower()}.png"), {"aspectRatio": ratio, "fit": fit}))
        return results

    async def create_magazine_release_mockup(
        self,
        foreground_asset_id: str,
        headline: str,
        subheadline: str | None = None,
        aspect_ratio: AspectRatioName = "SQUARE_1_1",
        fit: FitMode = "crop",
    ) -> tuple[ComposedImage, dict]:
        foreground = await self._load_visual_asset(foreground_asset_id)
        canvas = self._magazine_background((1600, 1600))

        cover = ImageOps.contain(foreground, (740, 980))
        cover = self._add_shadow(cover)
        cover_x = (canvas.width - cover.width) // 2
        canvas.alpha_composite(cover, (cover_x, 170))

        template = TEMPLATES["clean_release"]
        self._draw_template_text(
            canvas,
            template,
            {
                "headline": headline,
                "subheadline": subheadline or "The latest issue is ready to share.",
                "cta": "Magazine Release",
            },
        )
        canvas = self.apply_aspect_ratio(canvas, aspect_ratio, fit)
        return self._encode(canvas, f"magazine-release-{aspect_ratio.lower()}.png"), {
            "template": "magazine_release_mockup",
            "foregroundAssetId": foreground_asset_id,
            "aspectRatio": aspect_ratio,
            "fit": fit,
        }

    async def save_as_asset(
        self,
        image: ComposedImage,
        metadata: dict,
        campaign_id: str | None = None,
        user_id: str | None = None,
    ):
        stored_file = StorageService().upload_generated_image(
            image.data,
            image.file_name,
            content_type=image.content_type,
        )
        return await db.asset.create(
            data={
                "type": "MODEL_IMAGE",
                "fileName": stored_file.file_name,
                "contentType": stored_file.content_type,
                "fileSizeBytes": stored_file.file_size_bytes,
                "gcsUrl": stored_file.gcs_url,
                "gcsBucket": stored_file.gcs_bucket,
                "gcsObjectName": stored_file.gcs_object_name,
                "thumbnailUrl": stored_file.signed_url,
                "metadata": prisma_json(metadata),
                "createdById": user_id,
                "campaignId": campaign_id,
            }
        ), stored_file

    @staticmethod
    def apply_aspect_ratio(image: Image.Image, ratio: AspectRatioName, fit: FitMode = "crop") -> Image.Image:
        width_ratio, height_ratio = ASPECT_RATIOS[ratio]
        target_ratio = width_ratio / height_ratio
        source_ratio = image.width / image.height

        if fit == "crop":
            if source_ratio > target_ratio:
                new_width = int(image.height * target_ratio)
                left = (image.width - new_width) // 2
                return image.crop((left, 0, left + new_width, image.height))
            new_height = int(image.width / target_ratio)
            top = (image.height - new_height) // 2
            return image.crop((0, top, image.width, top + new_height))

        if source_ratio > target_ratio:
            target_size = (image.width, int(image.width / target_ratio))
        else:
            target_size = (int(image.height * target_ratio), image.height)
        background = Image.new("RGBA", target_size, (255, 255, 255, 255))
        x = (target_size[0] - image.width) // 2
        y = (target_size[1] - image.height) // 2
        background.alpha_composite(image.convert("RGBA"), (x, y))
        return background

    async def _load_image_asset(self, asset_id: str) -> Image.Image:
        asset = await db.asset.find_unique(where={"id": asset_id})
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
        if not asset.contentType.startswith("image/") or not asset.gcsObjectName:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset must be an uploaded image.")

        with StorageService().download_to_spooled_file(asset.gcsObjectName) as image_file:
            try:
                image = Image.open(image_file)
                image.load()
                return image.convert("RGBA")
            except UnidentifiedImageError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not read image.") from exc

    async def _load_visual_asset(self, asset_id: str) -> Image.Image:
        asset = await db.asset.find_unique(where={"id": asset_id})
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
        if not asset.gcsObjectName:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset does not have a GCS object name.")

        if asset.contentType.startswith("image/"):
            return await self._load_image_asset(asset_id)

        if asset.contentType == "application/pdf":
            return await self._load_pdf_cover(asset.gcsObjectName)

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset must be an image or magazine PDF.")

    async def _load_pdf_cover(self, object_name: str) -> Image.Image:
        try:
            from pdf2image import convert_from_bytes
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="PDF cover mockups require pdf2image and Poppler. Use a model image asset or install PDF rendering support.",
            ) from exc

        with StorageService().download_to_spooled_file(object_name) as pdf_file:
            pages = convert_from_bytes(pdf_file.read(), first_page=1, last_page=1, fmt="png")
        if not pages:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not render PDF cover.")
        return pages[0].convert("RGBA")

    def _prepare_canvas(self, image: Image.Image, template: CompositionTemplate) -> Image.Image:
        canvas = Image.new("RGBA", template.size, (*template.background, 255))
        image_area = (120, 110, template.size[0] - 120, 990 if template.size[1] == 1600 else 1120)
        fitted = ImageOps.contain(image, (image_area[2] - image_area[0], image_area[3] - image_area[1]))
        x = image_area[0] + ((image_area[2] - image_area[0] - fitted.width) // 2)
        y = image_area[1] + ((image_area[3] - image_area[1] - fitted.height) // 2)
        canvas.alpha_composite(fitted.convert("RGBA"), (x, y))
        return canvas

    def _draw_template_text(self, image: Image.Image, template: CompositionTemplate, text: dict[str, str]) -> None:
        draw = ImageDraw.Draw(image)
        for box in template.text_boxes:
            value = text.get(box.key)
            if not value:
                continue
            if box.key == "cta":
                self._draw_cta(draw, box, template.accent)
            font = self._font(box.font_size)
            lines = self._wrap_text(draw, value, font, box.width)
            y = box.xy[1]
            line_height = int(box.font_size * 1.25)
            for line in lines:
                if y + line_height > box.xy[1] + box.height:
                    break
                x = self._aligned_x(draw, line, font, box)
                draw.text(
                    (x, y),
                    line,
                    font=font,
                    fill=box.fill,
                    stroke_width=box.stroke_width,
                    stroke_fill=box.stroke_fill,
                )
                y += line_height

    @staticmethod
    def _draw_cta(draw: ImageDraw.ImageDraw, box: TextBoxTemplate, accent: tuple[int, int, int]) -> None:
        x, y = box.xy
        draw.rounded_rectangle((x, y, x + box.width, y + box.height), radius=8, fill=accent)

    @staticmethod
    def _magazine_background(size: tuple[int, int]) -> Image.Image:
        canvas = Image.new("RGBA", size, (246, 247, 241, 255))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, size[0], 240), fill=(17, 24, 39, 255))
        draw.rectangle((0, size[1] - 180, size[0], size[1]), fill=(229, 231, 235, 255))
        for x in range(-200, size[0], 120):
            draw.line((x, 260, x + 900, size[1] - 220), fill=(220, 224, 230, 255), width=3)
        return canvas

    @staticmethod
    def _add_shadow(image: Image.Image) -> Image.Image:
        shadow = Image.new("RGBA", (image.width + 90, image.height + 90), (0, 0, 0, 0))
        shadow_layer = Image.new("RGBA", image.size, (0, 0, 0, 120)).filter(ImageFilter.GaussianBlur(18))
        shadow.alpha_composite(shadow_layer, (55, 55))
        shadow.alpha_composite(image.convert("RGBA"), (20, 20))
        return shadow

    @staticmethod
    def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, width: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    @staticmethod
    def _aligned_x(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, box: TextBoxTemplate) -> int:
        left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
        text_width = right - left
        if box.align == "center":
            return box.xy[0] + (box.width - text_width) // 2
        if box.align == "right":
            return box.xy[0] + box.width - text_width
        return box.xy[0]

    @staticmethod
    def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _get_template(template_key: str) -> CompositionTemplate:
        template = TEMPLATES.get(template_key)
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Composition template not found.")
        return template

    @staticmethod
    def _encode(image: Image.Image, file_name: str) -> ComposedImage:
        output = BytesIO()
        image.save(output, format=settings.image_output_format.upper(), optimize=True)
        data = output.getvalue()
        content_type = "image/png" if settings.image_output_format.upper() == "PNG" else "image/jpeg"
        return ComposedImage(
            data=data,
            width=image.width,
            height=image.height,
            content_type=content_type,
            file_name=file_name,
        )
