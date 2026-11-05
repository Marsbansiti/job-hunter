"""
Notifier module - Send job alerts to Telegram via Bot API using httpx.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from src.scraper import Job

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramNotifier:
    """Send job notifications to a Telegram chat."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._api_base = TELEGRAM_API_BASE.format(token=bot_token)

    def _format_job_message(self, job: Job) -> str:
        """Format a single job into a Telegram-friendly message (MarkdownV2)."""
        # Escape special chars for MarkdownV2
        title = self._escape_md(job.title)
        source = self._escape_md(job.source.upper())

        return (
            f"🔔 *Job Intern mới\\!*\n\n"
            f"📌 *{title}*\n"
            f"🏢 Nguồn: `{source}`\n"
            f"🔗 [Xem chi tiết]({self._escape_url(job.link)})"
        )

    @staticmethod
    def _escape_md(text: str) -> str:
        """Escape special characters for Telegram MarkdownV2."""
        special_chars = r"_*[]()~`>#+-=|{}.!"
        escaped = ""
        for char in text:
            if char in special_chars:
                escaped += f"\\{char}"
            else:
                escaped += char
        return escaped

    @staticmethod
    def _escape_url(url: str) -> str:
        """Escape special characters inside MarkdownV2 inline URL parentheses.

        Inside [text](URL), only ')' and '\\' need escaping.
        """
        return url.replace("\\", "\\\\").replace(")", "\\)")

    async def send_message(self, text: str, parse_mode: str | None = "MarkdownV2") -> bool:
        """Send a single message to the configured chat. Returns True on success."""
        url = f"{self._api_base}/sendMessage"
        payload: dict = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": False,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload)

                if response.status_code == 200:
                    logger.debug("Telegram message sent successfully")
                    return True
                else:
                    logger.error(
                        "Telegram API error: %d - %s",
                        response.status_code,
                        response.text,
                    )
                    return False

        except httpx.TimeoutException:
            logger.error("Telegram API timeout")
            return False
        except Exception as e:
            logger.error("Failed to send Telegram message: %s", e)
            return False

    async def send_job(self, job: Job) -> bool:
        """Send a single job notification."""
        message = self._format_job_message(job)
        return await self.send_message(message)

    def _format_batch_message(self, jobs: list[Job], page: int, total_pages: int) -> str:
        """Format a batch of jobs into a single Telegram message (MarkdownV2)."""
        header = f"🔔 *Tìm thấy {len(jobs)} job Intern mới\\!*"
        if total_pages > 1:
            header += f" \\(trang {page}/{total_pages}\\)"
        header += "\n"

        lines = [header]
        for i, job in enumerate(jobs, start=1):
            title = self._escape_md(job.title)
            source = self._escape_md(job.source.upper())
            escaped_link = self._escape_url(job.link)
            lines.append(
                f"{i}\\. [{title}]({escaped_link})\n"
                f"    🏢 `{source}`\n"
            )

        return "\n".join(lines)

    async def send_jobs(self, jobs: list[Job]) -> int:
        """Send job notifications in batched messages (max ~10 jobs per message).

        Telegram has a ~4096 char limit per message, so we chunk into pages.
        Returns count of jobs in successfully sent messages.
        """
        if not jobs:
            return 0

        # Chunk jobs into batches (10 jobs per message ≈ ~2000 chars, safely under 4096)
        BATCH_SIZE = 10
        batches = [jobs[i:i + BATCH_SIZE] for i in range(0, len(jobs), BATCH_SIZE)]
        total_pages = len(batches)
        sent_count = 0

        for page_num, batch in enumerate(batches, start=1):
            message = self._format_batch_message(batch, page_num, total_pages)
            success = await self.send_message(message)
            if success:
                sent_count += len(batch)

            # Rate limit between batches
            if page_num < total_pages:
                await asyncio.sleep(1.0)

        logger.info("Sent %d/%d job notifications in %d messages", sent_count, len(jobs), total_pages)
        return sent_count

    async def send_summary(self, total_scraped: int, new_count: int, total_in_db: int) -> bool:
        """Send a run summary message."""
        text = (
            f"📊 *Job Hunter \\- Tổng kết*\n\n"
            f"🔍 Đã quét: `{total_scraped}` jobs\n"
            f"🆕 Job mới: `{new_count}`\n"
            f"💾 Tổng trong DB: `{total_in_db}`\n\n"
            f"⏰ Lần chạy tiếp: \\~4 tiếng nữa"
        )
        return await self.send_message(text)

    async def send_error_report(self, error_msg: str) -> bool:
        """Send an error notification (plain text, no markdown)."""
        text = f"⚠️ Job Hunter - Lỗi:\n\n{error_msg}"
        return await self.send_message(text, parse_mode=None)


class TelegramUserNotifier:
    """Send job notifications directly through a Telegram user session (Telethon)."""

    def __init__(self, api_id: int, api_hash: str, session_file: str, target_chat: str | int = "me") -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_file = session_file
        self._target_chat = target_chat
        self._client = None

    async def _get_client(self):
        from telethon import TelegramClient
        if self._client is None or not self._client.is_connected():
            self._client = TelegramClient(self._session_file, self._api_id, self._api_hash)
            await self._client.connect()
        return self._client

    async def send_message(self, text: str, parse_mode: str = "md") -> bool:
        try:
            client = await self._get_client()
            if not await client.is_user_authorized():
                logger.error("Telethon user session is not authorized.")
                return False
            await client.send_message(self._target_chat, text, parse_mode=parse_mode)
            logger.debug("Telegram user message sent successfully to %s", self._target_chat)
            return True
        except Exception as e:
            logger.error("Failed to send Telethon message: %s", e)
            return False

    async def send_jobs(self, jobs: list[Job]) -> int:
        if not jobs:
            return 0
        BATCH_SIZE = 10
        batches = [jobs[i:i + BATCH_SIZE] for i in range(0, len(jobs), BATCH_SIZE)]
        total_pages = len(batches)
        sent_count = 0

        for page_num, batch in enumerate(batches, start=1):
            header = f"🔔 **Tìm thấy {len(batch)} job mới!**"
            if total_pages > 1:
                header += f" (trang {page_num}/{total_pages})"
            header += "\n"

            lines = [header]
            for i, job in enumerate(batch, start=1):
                lines.append(
                    f"{i}. [{job.title}]({job.link})\n"
                    f"    🏢 `{job.source.upper()}`\n"
                )
            msg = "\n".join(lines)
            if await self.send_message(msg):
                sent_count += len(batch)
            if page_num < total_pages:
                await asyncio.sleep(1.0)
        return sent_count

    async def send_summary(self, total_scraped: int, new_count: int, total_in_db: int) -> bool:
        text = (
            f"📊 **Job Hunter - Tổng kết**\n\n"
            f"🔍 Đã quét: `{total_scraped}` jobs\n"
            f"🆕 Job mới: `{new_count}`\n"
            f"💾 Tổng trong DB: `{total_in_db}`\n\n"
            f"⏰ Lần chạy tiếp: ~4 tiếng nữa"
        )
        return await self.send_message(text)

    async def send_error_report(self, error_msg: str) -> bool:
        text = f"⚠️ **Job Hunter - Lỗi:**\n\n{error_msg}"
        return await self.send_message(text)

    async def close(self) -> None:
        if self._client and self._client.is_connected():
            await self._client.disconnect()


def create_notifier():
    """Create either Bot notifier or User (Telethon) notifier based on config."""
    import os
    from src import config
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        return TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    if config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH:
        session_path = str(config.BASE_DIR / config.TELEGRAM_SESSION) if not os.path.isabs(config.TELEGRAM_SESSION) else config.TELEGRAM_SESSION
        target = config.TELEGRAM_TARGET_CHAT or config.TELEGRAM_CHAT_ID or "me"
        try:
            target = int(target)
        except (ValueError, TypeError):
            pass
        return TelegramUserNotifier(
            api_id=int(config.TELEGRAM_API_ID),
            api_hash=config.TELEGRAM_API_HASH,
            session_file=session_path,
            target_chat=target,
        )
    return None
