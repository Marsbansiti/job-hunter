"""
Main orchestrator - Coordinates scraping, deduplication, and notification.

Usage:
    python -m src.main
    python -m src.main --source topcv
    python -m src.main --source topcv --no-notify
    python -m src.main --source topcv --all-levels
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from src import config
from src.scraper import JobScraper, Job
from src.database import JobDatabase
from src.notifier import TelegramNotifier, create_notifier

logger = logging.getLogger(__name__)


async def run(
    source: str = "all",
    dry_run: bool = False,
    allow_all_levels: bool = False,
    keywords: Optional[list[str]] = None,
    export_json: Optional[str] = None,
) -> list[Job]:
    """Main pipeline: scrape → dedup → notify."""
    # ── Step 1: Setup ──────────────────────────
    config.setup_logging()
    logger.info("=" * 60)
    logger.info("Job Hunter started [source=%s, dry_run=%s, all_levels=%s]", source, dry_run, allow_all_levels)
    logger.info("=" * 60)

    has_telegram = config.validate_config()
    if not has_telegram:
        if not dry_run:
            logger.warning("Telegram credentials not configured in .env. Defaulting to local/dry-run mode.")
            dry_run = True

    db = JobDatabase(config.DB_PATH)
    notifier = create_notifier() if (has_telegram and not dry_run) else None
    scraper = JobScraper()
    all_jobs: list[Job] = []

    try:
        # ── Step 2: Scrape selected source(s) ──────
        await scraper.start()

        if source == "topcv":
            all_jobs = await scraper.scrape_topcv(keywords=keywords, allow_all_levels=allow_all_levels)
        elif source == "itviec":
            all_jobs = await scraper.scrape_itviec()
        elif source == "vietnamworks":
            all_jobs = await scraper.scrape_vietnamworks()
        else:
            all_jobs = await scraper.scrape_all()

        # ── Step 3: Filter new jobs via DB ─────
        new_jobs = []
        for job in all_jobs:
            if db.save_if_new(job):
                new_jobs.append(job)

        logger.info(
            "Results: %d total scraped, %d new, %d already in DB",
            len(all_jobs), len(new_jobs), db.count(),
        )

        # Print console summary of found jobs
        print("\n" + "=" * 60)
        print(f"🎯 JOB HUNTER RESULTS ({source.upper()}):")
        print(f"Total Scraped: {len(all_jobs)} | New: {len(new_jobs)} | Total in DB: {db.count()}")
        print("=" * 60)
        for i, j in enumerate(all_jobs, start=1):
            status = " [NEW]" if j in new_jobs else ""
            print(f"[{i}] [{j.source.upper()}]{status} {j.title}")
            print(f"    🔗 {j.link}")
        print("=" * 60 + "\n")

        # ── Optional: Export to JSON file ──────
        if export_json:
            export_path = Path(export_json)
            export_path.parent.mkdir(parents=True, exist_ok=True)
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump([j.to_dict() for j in all_jobs], f, ensure_ascii=False, indent=2)
            logger.info("Exported %d jobs to %s", len(all_jobs), export_path)

        # ── Step 4: Send Telegram notifications ─
        if not dry_run and notifier:
            if new_jobs:
                logger.info("Sending %d new job notifications...", len(new_jobs))
                sent = await notifier.send_jobs(new_jobs)
                logger.info("Successfully sent %d/%d notifications", sent, len(new_jobs))
            else:
                logger.info("No new jobs found this run")

            # ── Step 5: Send summary ───────────
            await notifier.send_summary(
                total_scraped=len(all_jobs),
                new_count=len(new_jobs),
                total_in_db=db.count(),
            )
        else:
            logger.info("Dry-run/local mode: Telegram notifications skipped.")

    except Exception as e:
        logger.critical("Pipeline failed: %s", e, exc_info=True)
        if not dry_run and notifier:
            try:
                await notifier.send_error_report(str(e))
            except Exception:
                logger.error("Could not send error report to Telegram")
        raise

    finally:
        await scraper.stop()
        if notifier and hasattr(notifier, "close"):
            await notifier.close()
        db.close()

    logger.info("Job Hunter finished")
    logger.info("=" * 60)
    return all_jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Job Hunter - Automated Java/Tech Job Finder")
    parser.add_argument(
        "--source",
        choices=["topcv", "itviec", "vietnamworks", "all"],
        default="all",
        help="Job source site to scrape (default: all)",
    )
    parser.add_argument(
        "--no-notify",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Skip Telegram notification and run locally",
    )
    parser.add_argument(
        "--all-levels",
        action="store_true",
        help="Scrape all tech developer levels, not just intern/fresher",
    )
    parser.add_argument(
        "--keywords",
        type=str,
        default=None,
        help="Comma-separated custom search keywords (e.g. 'java,spring,thuc-tap-sinh-it')",
    )
    parser.add_argument(
        "--export-json",
        type=str,
        default=None,
        help="Path to export scraped jobs as JSON file",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()
    kw_list = [k.strip() for k in args.keywords.split(",")] if args.keywords else None
    asyncio.run(
        run(
            source=args.source,
            dry_run=args.dry_run,
            allow_all_levels=args.all_levels,
            keywords=kw_list,
            export_json=args.export_json,
        )
    )


if __name__ == "__main__":
    main()
