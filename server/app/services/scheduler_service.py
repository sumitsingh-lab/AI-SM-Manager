import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.db import db
from app.services.social_publisher_service import SocialPublisherService

logger = logging.getLogger(__name__)


class PublishingScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="publishing-scheduler")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            await asyncio.wait([self._task], timeout=5)

    async def run_once(self) -> int:
        due_posts = await db.post.find_many(
            where={
                "approvalStatus": "APPROVED",
                "publishStatus": {"in": ["QUEUED", "FAILED"]},
                "scheduledPublishTime": {"lte": datetime.now(timezone.utc)},
                "publishedAt": None,
            },
            take=25,
        )
        for post in due_posts:
            await self._publish_due_post(post.id)
        return len(due_posts)

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                count = await self.run_once()
                if count:
                    logger.info("Processed %s scheduled publish job(s)", count)
            except Exception:
                logger.exception("Publishing scheduler tick failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=settings.scheduler_poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def _publish_due_post(self, post_id: str) -> None:
        await db.post.update(where={"id": post_id}, data={"publishStatus": "PUBLISHING", "lastPublishError": None})
        try:
            await SocialPublisherService().publish_post(post_id)
        except Exception as exc:
            logger.exception("Scheduled publish failed for post %s", post_id)
            await db.post.update(
                where={"id": post_id},
                data={"publishStatus": "FAILED", "lastPublishError": str(exc)[:1000]},
            )


publishing_scheduler = PublishingScheduler()
