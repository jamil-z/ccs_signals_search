"""
cache_manager.py — SQLite cache with per-source TTL, store delta, and job churn tracking.
"""
from __future__ import annotations
import hashlib, json, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import structlog

logger = structlog.get_logger(__name__)
CHURN_WINDOW_DAYS = 30

class CacheManager:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS search_cache (
                cache_key TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                source_name TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS store_snapshots (
                company_domain TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                store_count INTEGER NOT NULL DEFAULT 0,
                store_ids_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (company_domain, snapshot_date)
            );
            CREATE TABLE IF NOT EXISTS job_lifecycle (
                company_domain TEXT NOT NULL,
                job_hash TEXT NOT NULL,
                job_title TEXT DEFAULT '',
                store_ref TEXT DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                removal_date TEXT,
                repost_count INTEGER DEFAULT 0,
                PRIMARY KEY (company_domain, job_hash)
            );
        """)
        self._conn.commit()

    def _make_key(self, company: str, source: str) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return hashlib.sha256(f"{company.lower().strip()}:{source}:{today}".encode()).hexdigest()

    def get_cached(self, company: str, source: str) -> dict[str, Any] | None:
        key = self._make_key(company, source)
        now = datetime.now(timezone.utc).isoformat()
        row = self._conn.execute(
            "SELECT result_json FROM search_cache WHERE cache_key=? AND expires_at > ?", (key, now)
        ).fetchone()
        if row:
            logger.info("cache.hit", company=company, source=source)
            return json.loads(row["result_json"])
        logger.info("cache.miss", company=company, source=source)
        return None

    def set_cached(self, company: str, source: str, result: dict[str, Any], ttl_hours: int) -> None:
        key = self._make_key(company, source)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=ttl_hours)).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO search_cache (cache_key,company_name,source_name,result_json,created_at,expires_at) VALUES (?,?,?,?,?,?)",
            (key, company, source, json.dumps(result), now.isoformat(), expires),
        )
        self._conn.commit()

    def compute_store_delta(self, domain: str, current_count: int, current_ids: list[str]) -> tuple[int, list[str]]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prev = self._conn.execute(
            "SELECT store_count, store_ids_json FROM store_snapshots WHERE company_domain=? AND snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1",
            (domain, today),
        ).fetchone()
        delta, new_ids = 0, []
        if prev:
            delta = current_count - prev["store_count"]
            prev_ids: list[str] = json.loads(prev["store_ids_json"])
            if current_ids and prev_ids:
                new_ids = list(set(current_ids) - set(prev_ids))
        self._conn.execute(
            "INSERT OR REPLACE INTO store_snapshots (company_domain,snapshot_date,store_count,store_ids_json) VALUES (?,?,?,?)",
            (domain, today, current_count, json.dumps(current_ids)),
        )
        self._conn.commit()
        logger.info("store_delta.computed", domain=domain, count=current_count, delta=delta)
        return delta, new_ids

    def update_job_lifecycle(self, domain: str, current_jobs: list[dict[str, str]]) -> int:
        now_str = datetime.now(timezone.utc).isoformat()
        today = datetime.now(timezone.utc)
        current_hashes = {j["job_hash"] for j in current_jobs}
        churn_count = 0
        for row in self._conn.execute("SELECT job_hash FROM job_lifecycle WHERE company_domain=? AND removal_date IS NULL", (domain,)).fetchall():
            if row["job_hash"] not in current_hashes:
                self._conn.execute("UPDATE job_lifecycle SET removal_date=?,last_seen=? WHERE company_domain=? AND job_hash=?", (now_str, now_str, domain, row["job_hash"]))
        for job in current_jobs:
            existing = self._conn.execute("SELECT * FROM job_lifecycle WHERE company_domain=? AND job_hash=?", (domain, job["job_hash"])).fetchone()
            if existing is None:
                self._conn.execute("INSERT INTO job_lifecycle (company_domain,job_hash,job_title,store_ref,first_seen,last_seen) VALUES (?,?,?,?,?,?)",
                    (domain, job["job_hash"], job.get("title",""), job.get("store_ref",""), now_str, now_str))
            else:
                if existing["removal_date"]:
                    removal_dt = datetime.fromisoformat(existing["removal_date"])
                    if (today - removal_dt).days <= CHURN_WINDOW_DAYS:
                        churn_count += 1
                        logger.info("churn.anomaly", domain=domain, title=job.get("title",""))
                    self._conn.execute("UPDATE job_lifecycle SET last_seen=?,removal_date=NULL,repost_count=repost_count+1 WHERE company_domain=? AND job_hash=?", (now_str, domain, job["job_hash"]))
                else:
                    self._conn.execute("UPDATE job_lifecycle SET last_seen=? WHERE company_domain=? AND job_hash=?", (now_str, domain, job["job_hash"]))
        self._conn.commit()
        logger.info("job_lifecycle.updated", domain=domain, jobs=len(current_jobs), churn=churn_count)
        return churn_count

    def close(self) -> None:
        self._conn.close()
