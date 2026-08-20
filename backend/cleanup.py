import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from ingestion import _purge_repo
from state import state

logger = logging.getLogger(__name__)


def _check_and_purge_expired():
    now = datetime.now(timezone.utc)
    # snapshot first - _purge_repo mutates state.repos
    expired = [r.repo_name for r in list(state.repos.values()) if now >= r.expires_at]
    for repo_name in expired:
        logger.info("Repo %s hit its TTL - purging to protect free-tier limits.", repo_name,
                    extra={"component": "cleanup"})
        _purge_repo(repo_name)


_scheduler = BackgroundScheduler(daemon=True)


def start_cleanup_scheduler(interval_minutes: int = 2):
    _scheduler.add_job(_check_and_purge_expired, "interval", minutes=interval_minutes,
                        id="ttl_cleanup", replace_existing=True)
    _scheduler.start()
    logger.info("Cleanup scheduler started (checks every %d min).", interval_minutes,
                extra={"component": "cleanup"})


def stop_cleanup_scheduler():
    _scheduler.shutdown(wait=False)
