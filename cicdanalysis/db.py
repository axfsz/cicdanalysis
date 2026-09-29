from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, jenkins_username TEXT NOT NULL UNIQUE,
 display_name TEXT, telegram_username TEXT, telegram_user_id TEXT,
 status INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id),
 job_name TEXT NOT NULL UNIQUE, job_url TEXT, service_name TEXT, service_type TEXT,
 environment TEXT, namespace TEXT, enabled INTEGER NOT NULL DEFAULT 1,
 last_build_number INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS builds (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 build_number INTEGER NOT NULL, build_url TEXT, queue_id INTEGER,
 trigger_user_id INTEGER REFERENCES users(id), trigger_source TEXT,
 branch TEXT, commit_sha TEXT, commit_message TEXT, agent_name TEXT,
 image_ref TEXT, started_at TEXT, finished_at TEXT, duration_seconds REAL,
 result TEXT NOT NULL, building INTEGER NOT NULL DEFAULT 0,
 raw_summary TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(job_id, build_number)
);
CREATE INDEX IF NOT EXISTS idx_builds_started ON builds(started_at);
CREATE INDEX IF NOT EXISTS idx_builds_result ON builds(result);
CREATE INDEX IF NOT EXISTS idx_builds_user ON builds(trigger_user_id);
CREATE INDEX IF NOT EXISTS idx_builds_commit ON builds(commit_sha);
CREATE TABLE IF NOT EXISTS trigger_events (
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
 job_id INTEGER NOT NULL REFERENCES jobs(id), build_number INTEGER, queue_id INTEGER,
 trigger_user_id INTEGER NOT NULL REFERENCES users(id), trigger_source TEXT NOT NULL DEFAULT 'TELEGRAM_PANEL',
 telegram_chat_id TEXT, branch TEXT, service_type TEXT, service_name TEXT,
 namespace TEXT, environment TEXT, trigger_status TEXT, http_status INTEGER,
 queue_url TEXT, build_url TEXT, triggered_at TEXT NOT NULL,
 matched_build_id INTEGER REFERENCES builds(id), raw_payload TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trigger_events_build ON trigger_events(job_id,build_number);
CREATE INDEX IF NOT EXISTS idx_trigger_events_queue ON trigger_events(queue_id);
CREATE INDEX IF NOT EXISTS idx_trigger_events_time ON trigger_events(triggered_at);
CREATE TABLE IF NOT EXISTS build_stages (
 id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
 name TEXT NOT NULL, status TEXT, started_at TEXT, duration_seconds REAL,
 UNIQUE(build_id, name)
);
CREATE TABLE IF NOT EXISTS build_failures (
 id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL UNIQUE REFERENCES builds(id) ON DELETE CASCADE,
 failed_stage TEXT, error_category TEXT NOT NULL, error_code TEXT,
 root_cause TEXT, suggestion TEXT, responsibility_type TEXT,
 error_fingerprint TEXT NOT NULL, confidence REAL NOT NULL,
 normalized_error TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failures_fingerprint ON build_failures(error_fingerprint);
CREATE TABLE IF NOT EXISTS report_deliveries (
 id INTEGER PRIMARY KEY, report_type TEXT NOT NULL, environment TEXT NOT NULL,
 period_key TEXT NOT NULL, chat_id TEXT NOT NULL, sent_at TEXT NOT NULL,
 UNIQUE(report_type, environment, period_key, chat_id)
);
CREATE TABLE IF NOT EXISTS release_results (
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
 job_id INTEGER NOT NULL REFERENCES jobs(id), build_number INTEGER NOT NULL, result TEXT NOT NULL,
 telegram_chat_id TEXT, telegram_message_id INTEGER, build_url TEXT, finished_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_release_results_build ON release_results(job_id,build_number);
CREATE TABLE IF NOT EXISTS failure_notifications (
 id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL UNIQUE REFERENCES builds(id) ON DELETE CASCADE,
 chat_id TEXT NOT NULL, sent_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id BIGSERIAL PRIMARY KEY, jenkins_username TEXT NOT NULL UNIQUE,
 display_name TEXT, telegram_username TEXT, telegram_user_id TEXT,
 status INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
 id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id BIGSERIAL PRIMARY KEY, project_id BIGINT REFERENCES projects(id),
 job_name TEXT NOT NULL UNIQUE, job_url TEXT, service_name TEXT, service_type TEXT,
 environment TEXT, namespace TEXT, enabled INTEGER NOT NULL DEFAULT 1,
 last_build_number INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS builds (
 id BIGSERIAL PRIMARY KEY, job_id BIGINT NOT NULL REFERENCES jobs(id),
 build_number INTEGER NOT NULL, build_url TEXT, queue_id BIGINT,
 trigger_user_id BIGINT REFERENCES users(id), trigger_source TEXT,
 branch TEXT, commit_sha TEXT, commit_message TEXT, agent_name TEXT,
 image_ref TEXT, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, duration_seconds DOUBLE PRECISION,
 result TEXT NOT NULL, building INTEGER NOT NULL DEFAULT 0,
 raw_summary TEXT, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
 UNIQUE(job_id, build_number)
);
CREATE INDEX IF NOT EXISTS idx_builds_started ON builds(started_at);
CREATE INDEX IF NOT EXISTS idx_builds_result ON builds(result);
CREATE INDEX IF NOT EXISTS idx_builds_user ON builds(trigger_user_id);
CREATE INDEX IF NOT EXISTS idx_builds_commit ON builds(commit_sha);
CREATE TABLE IF NOT EXISTS trigger_events (
 id BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
 job_id BIGINT NOT NULL REFERENCES jobs(id), build_number INTEGER, queue_id BIGINT,
 trigger_user_id BIGINT NOT NULL REFERENCES users(id), trigger_source TEXT NOT NULL DEFAULT 'TELEGRAM_PANEL',
 telegram_chat_id TEXT, branch TEXT, service_type TEXT, service_name TEXT,
 namespace TEXT, environment TEXT, trigger_status TEXT, http_status INTEGER,
 queue_url TEXT, build_url TEXT, triggered_at TIMESTAMPTZ NOT NULL,
 matched_build_id BIGINT REFERENCES builds(id), raw_payload TEXT,
 created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trigger_events_build ON trigger_events(job_id,build_number);
CREATE INDEX IF NOT EXISTS idx_trigger_events_queue ON trigger_events(queue_id);
CREATE INDEX IF NOT EXISTS idx_trigger_events_time ON trigger_events(triggered_at);
CREATE TABLE IF NOT EXISTS build_stages (
 id BIGSERIAL PRIMARY KEY, build_id BIGINT NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
 name TEXT NOT NULL, status TEXT, started_at TIMESTAMPTZ, duration_seconds DOUBLE PRECISION,
 UNIQUE(build_id, name)
);
CREATE TABLE IF NOT EXISTS build_failures (
 id BIGSERIAL PRIMARY KEY, build_id BIGINT NOT NULL UNIQUE REFERENCES builds(id) ON DELETE CASCADE,
 failed_stage TEXT, error_category TEXT NOT NULL, error_code TEXT,
 root_cause TEXT, suggestion TEXT, responsibility_type TEXT,
 error_fingerprint TEXT NOT NULL, confidence DOUBLE PRECISION NOT NULL,
 normalized_error TEXT, created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failures_fingerprint ON build_failures(error_fingerprint);
CREATE TABLE IF NOT EXISTS report_deliveries (
 id BIGSERIAL PRIMARY KEY, report_type TEXT NOT NULL, environment TEXT NOT NULL,
 period_key TEXT NOT NULL, chat_id TEXT NOT NULL, sent_at TIMESTAMPTZ NOT NULL,
 UNIQUE(report_type, environment, period_key, chat_id)
);
CREATE TABLE IF NOT EXISTS release_results (
 id BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
 job_id BIGINT NOT NULL REFERENCES jobs(id), build_number INTEGER NOT NULL, result TEXT NOT NULL,
 telegram_chat_id TEXT, telegram_message_id BIGINT, build_url TEXT, finished_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_release_results_build ON release_results(job_id,build_number);
CREATE TABLE IF NOT EXISTS failure_notifications (
 id BIGSERIAL PRIMARY KEY, build_id BIGINT NOT NULL UNIQUE REFERENCES builds(id) ON DELETE CASCADE,
 chat_id TEXT NOT NULL, sent_at TIMESTAMPTZ NOT NULL
);
"""


# Columns added after 0.5.0. SQLite has no ADD COLUMN IF NOT EXISTS, so each
# statement runs on its own and "duplicate column" errors are ignored.
MIGRATIONS = [
    ("build_failures", "error_excerpt", "TEXT"),
    ("build_failures", "analysis_source", "TEXT"),
    ("build_failures", "analysis_model", "TEXT"),
]


def parse_time(value) -> datetime | None:
    """Accept ISO strings (SQLite) or datetimes (PostgreSQL); naive values are UTC."""
    if value in (None, ""): return None
    if isinstance(value, datetime): parsed = value
    else:
        try: parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError: return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, target: str):
        self.target = target
        self.postgres = target.startswith(("postgresql://", "postgres://"))
        self.path = "" if self.postgres else target

    def init(self) -> None:
        if not self.postgres:
            parent = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(parent, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(POSTGRES_SCHEMA if self.postgres else SQLITE_SCHEMA)
        for table, column, kind in MIGRATIONS:
            if self.postgres:
                with self.connect() as conn:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {kind}")
                continue
            try:
                with self.connect() as conn:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower(): raise

    @contextmanager
    def connect(self):
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("PostgreSQL requires psycopg; install the project dependencies") from exc
            raw = psycopg.connect(self.target, connect_timeout=10, row_factory=dict_row)
            conn = _CompatConnection(raw, True)
        else:
            raw = sqlite3.connect(self.path, timeout=30)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys=ON")
            conn = _CompatConnection(raw, False)
        try:
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def ping(self) -> bool:
        try:
            with self.connect() as c:
                c.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def upsert_job(self, meta: dict) -> int:
        ts = now()
        with self.connect() as c:
            c.execute("INSERT INTO projects(name,created_at) VALUES(?,?) ON CONFLICT(name) DO NOTHING", (meta["project"], ts))
            project_id = c.execute("SELECT id FROM projects WHERE name=?", (meta["project"],)).fetchone()["id"]
            c.execute("""INSERT INTO jobs(project_id,job_name,job_url,service_name,service_type,environment,namespace,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(job_name) DO UPDATE SET project_id=excluded.project_id,job_url=excluded.job_url,
              service_name=excluded.service_name,service_type=excluded.service_type,
              environment=CASE WHEN excluded.environment='unknown' THEN jobs.environment ELSE excluded.environment END,
              namespace=excluded.namespace,updated_at=excluded.updated_at""",
              (project_id, meta["job_name"], meta.get("job_url"), meta["service_name"], meta["service_type"],
               meta["environment"], meta["namespace"], ts, ts))
            return c.execute("SELECT id FROM jobs WHERE job_name=?", (meta["job_name"],)).fetchone()["id"]

    def upsert_user(self, username: str, display: str = "", telegram: str = "", telegram_user_id: str = "") -> int | None:
        if not username:
            return None
        ts = now()
        with self.connect() as c:
            c.execute("""INSERT INTO users(jenkins_username,display_name,telegram_username,telegram_user_id,created_at,updated_at)
              VALUES(?,?,?,?,?,?) ON CONFLICT(jenkins_username) DO UPDATE SET
              display_name=COALESCE(NULLIF(excluded.display_name,''),users.display_name),
              telegram_username=COALESCE(NULLIF(excluded.telegram_username,''),users.telegram_username),
              telegram_user_id=COALESCE(NULLIF(excluded.telegram_user_id,''),users.telegram_user_id),updated_at=excluded.updated_at""",
              (username, display or username, telegram.lstrip("@"), str(telegram_user_id or ""), ts, ts))
            return c.execute("SELECT id FROM users WHERE jenkins_username=?", (username,)).fetchone()["id"]

    def save_trigger_event(self, job_id: int, user_id: int, event: dict) -> tuple[int, int | None]:
        """Persist the trigger-panel identity and link an already collected build."""
        ts = now()
        with self.connect() as c:
            c.execute("""INSERT INTO trigger_events(event_id,job_id,build_number,queue_id,trigger_user_id,trigger_source,
              telegram_chat_id,branch,service_type,service_name,namespace,environment,trigger_status,http_status,
              queue_url,build_url,triggered_at,raw_payload,created_at,updated_at)
              VALUES(?,?,?,?,?,'TELEGRAM_PANEL',?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(event_id) DO UPDATE SET build_number=COALESCE(excluded.build_number,trigger_events.build_number),
              queue_id=COALESCE(excluded.queue_id,trigger_events.queue_id),trigger_user_id=excluded.trigger_user_id,
              telegram_chat_id=COALESCE(NULLIF(excluded.telegram_chat_id,''),trigger_events.telegram_chat_id),
              branch=COALESCE(NULLIF(excluded.branch,''),trigger_events.branch),
              trigger_status=COALESCE(NULLIF(excluded.trigger_status,''),trigger_events.trigger_status),
              http_status=COALESCE(excluded.http_status,trigger_events.http_status),
              queue_url=COALESCE(NULLIF(excluded.queue_url,''),trigger_events.queue_url),
              build_url=COALESCE(NULLIF(excluded.build_url,''),trigger_events.build_url),
              raw_payload=excluded.raw_payload,updated_at=excluded.updated_at""",
              (event["event_id"],job_id,event.get("build_number"),event.get("queue_id"),user_id,
               event.get("telegram_chat_id"),event.get("branch"),event.get("service_type"),event.get("service_name"),
               event.get("namespace"),event.get("environment"),event.get("status"),event.get("http_status"),
               event.get("queue_url"),event.get("build_url"),event["triggered_at"],event.get("raw_payload"),ts,ts))
            trigger_id = c.execute("SELECT id FROM trigger_events WHERE event_id=?", (event["event_id"],)).fetchone()["id"]
            conditions=[]; params=[]
            if event.get("build_number") is not None:
                conditions.append("build_number=?"); params.append(event["build_number"])
            if event.get("queue_id") is not None:
                conditions.append("queue_id=?"); params.append(event["queue_id"])
            build_id = None
            if conditions:
                row=c.execute(f"SELECT id FROM builds WHERE job_id=? AND ({' OR '.join(conditions)}) ORDER BY id DESC LIMIT 1",(job_id,*params)).fetchone()
                build_id=row["id"] if row else None
            if build_id:
                c.execute("""UPDATE builds SET trigger_user_id=?,trigger_source='TELEGRAM_PANEL',
                  branch=COALESCE(NULLIF(?,''),branch),updated_at=? WHERE id=?""",(user_id,event.get("branch"),ts,build_id))
                c.execute("UPDATE trigger_events SET matched_build_id=?,updated_at=? WHERE id=?",(build_id,ts,trigger_id))
            return trigger_id, build_id

    # A "Jenkins 发布触发通知" carries no build number or queue id, so such a
    # trigger is matched to the first build of the same job that starts shortly
    # after it. Jenkins normally starts the build within seconds; the window
    # tolerates a busy queue and small clock differences.
    PENDING_BEFORE = timedelta(minutes=1)
    PENDING_AFTER = timedelta(minutes=30)

    def apply_trigger_event(self, job_id: int, build_id: int, build_number: int, queue_id: int | None,
                            started_at=None) -> bool:
        """Attach an earlier trigger-panel event to a newly collected Jenkins build."""
        with self.connect() as c:
            clauses=["build_number=?"]; params=[build_number]
            if queue_id is not None:
                clauses.append("queue_id=?"); params.append(queue_id)
            row=c.execute(f"""SELECT id,trigger_user_id,branch FROM trigger_events
              WHERE job_id=? AND matched_build_id IS NULL AND ({' OR '.join(clauses)})
              ORDER BY CASE WHEN build_number=? THEN 0 ELSE 1 END,triggered_at DESC LIMIT 1""",
              (job_id,*params,build_number)).fetchone()
            if not row and not c.execute("SELECT id FROM trigger_events WHERE matched_build_id=? LIMIT 1",(build_id,)).fetchone():
                row=self._pending_trigger(c, job_id, parse_time(started_at))
            if not row: return False
            ts=now()
            c.execute("""UPDATE builds SET trigger_user_id=?,trigger_source='TELEGRAM_PANEL',
              branch=COALESCE(NULLIF(?,''),branch),updated_at=? WHERE id=?""",(row["trigger_user_id"],row["branch"],ts,build_id))
            c.execute("""UPDATE trigger_events SET matched_build_id=?,build_number=COALESCE(build_number,?),updated_at=?
              WHERE id=?""",(build_id,build_number,ts,row["id"]))
            return True

    def _pending_trigger(self, c, job_id: int, started: datetime | None, upto: datetime | None = None):
        """Closest unmatched trigger without build/queue id before a build start (or result time)."""
        anchor = started or upto
        if anchor is None: return None
        rows=c.execute("""SELECT id,trigger_user_id,branch,triggered_at FROM trigger_events
          WHERE job_id=? AND matched_build_id IS NULL AND build_number IS NULL AND queue_id IS NULL""",(job_id,)).fetchall()
        low = anchor - (self.PENDING_AFTER if started else timedelta(hours=3))
        high = anchor + self.PENDING_BEFORE
        candidates=[(t,r) for r in rows for t in [parse_time(r["triggered_at"])] if t and low <= t <= high]
        return max(candidates, key=lambda x: x[0])[1] if candidates else None

    def link_pending_trigger(self, job_id: int, build_number: int, finished_at=None) -> bool:
        """Give a number-less trigger the build number announced by "Jenkins 发布通知"."""
        with self.connect() as c:
            if c.execute("SELECT id FROM trigger_events WHERE job_id=? AND build_number=? LIMIT 1",(job_id,build_number)).fetchone():
                return False
            row=self._pending_trigger(c, job_id, None, parse_time(finished_at) or datetime.now(timezone.utc))
            if not row: return False
            c.execute("UPDATE trigger_events SET build_number=?,updated_at=? WHERE id=?",(build_number,now(),row["id"]))
            build=c.execute("SELECT id FROM builds WHERE job_id=? AND build_number=?",(job_id,build_number)).fetchone()
        if build:
            self.apply_trigger_event(job_id, build["id"], build_number, None)
        return True

    def save_release_result(self, job_id: int, event: dict) -> int:
        ts=now()
        with self.connect() as c:
            c.execute("""INSERT INTO release_results(event_id,job_id,build_number,result,telegram_chat_id,telegram_message_id,
              build_url,finished_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(event_id) DO UPDATE SET result=excluded.result,build_url=excluded.build_url,
              finished_at=excluded.finished_at,updated_at=excluded.updated_at""",
              (event["event_id"],job_id,event["build_number"],event["result"],event.get("telegram_chat_id"),
               event.get("telegram_message_id"),event.get("build_url"),event.get("finished_at") or None,ts,ts))
            return c.execute("SELECT id FROM release_results WHERE event_id=?",(event["event_id"],)).fetchone()["id"]

    def release_result_for(self, job_id: int, build_number: int) -> dict | None:
        rows=self.query("""SELECT telegram_chat_id,telegram_message_id,result FROM release_results
          WHERE job_id=? AND build_number=? ORDER BY id DESC LIMIT 1""",(job_id,build_number))
        return rows[0] if rows else None

    def claim_failure_notification(self, build_id: int, chat_id: str) -> bool:
        """Reserve the single failure report for a build; False if already sent or in flight."""
        with self.connect() as c:
            cur=c.execute("""INSERT INTO failure_notifications(build_id,chat_id,sent_at) VALUES(?,?,?)
              ON CONFLICT(build_id) DO NOTHING""",(build_id,str(chat_id),now()))
            return (cur.rowcount or 0) > 0

    def release_failure_notification(self, build_id: int) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM failure_notifications WHERE build_id=?",(build_id,))

    def save_build(self, job_id: int, build: dict) -> tuple[int, bool]:
        ts = now()
        keys = (job_id, build["build_number"])
        with self.connect() as c:
            old = c.execute("SELECT id,result,building FROM builds WHERE job_id=? AND build_number=?", keys).fetchone()
            c.execute("""INSERT INTO builds(job_id,build_number,build_url,queue_id,trigger_user_id,trigger_source,branch,commit_sha,
              commit_message,agent_name,image_ref,started_at,finished_at,duration_seconds,result,building,raw_summary,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,build_number) DO UPDATE SET
              build_url=excluded.build_url,queue_id=excluded.queue_id,
              trigger_user_id=CASE WHEN builds.trigger_source='TELEGRAM_PANEL' THEN builds.trigger_user_id ELSE COALESCE(excluded.trigger_user_id,builds.trigger_user_id) END,
              trigger_source=CASE WHEN builds.trigger_source='TELEGRAM_PANEL' THEN builds.trigger_source ELSE excluded.trigger_source END,
              branch=CASE WHEN builds.trigger_source='TELEGRAM_PANEL' AND builds.branch!='' THEN builds.branch ELSE excluded.branch END,commit_sha=excluded.commit_sha,
              commit_message=excluded.commit_message,agent_name=excluded.agent_name,image_ref=excluded.image_ref,
              started_at=excluded.started_at,finished_at=excluded.finished_at,duration_seconds=excluded.duration_seconds,
              result=excluded.result,building=excluded.building,raw_summary=excluded.raw_summary,updated_at=excluded.updated_at""",
              (job_id, build["build_number"], build.get("build_url"), build.get("queue_id"), build.get("trigger_user_id"),
               build.get("trigger_source"), build.get("branch"), build.get("commit_sha"), build.get("commit_message"),
               build.get("agent_name"), build.get("image_ref"), build.get("started_at"), build.get("finished_at"),
               build.get("duration_seconds"), build["result"], int(build.get("building", False)), build.get("raw_summary"), ts, ts))
            row_id = c.execute("SELECT id FROM builds WHERE job_id=? AND build_number=?", keys).fetchone()["id"]
            greatest = "GREATEST" if self.postgres else "MAX"
            c.execute(f"UPDATE jobs SET last_build_number={greatest}(last_build_number,?),updated_at=? WHERE id=?", (build["build_number"], ts, job_id))
            became_final = not build.get("building") and (old is None or old["building"] or old["result"] != build["result"])
            return row_id, became_final

    def save_stages(self, build_id: int, stages: list[dict]) -> None:
        with self.connect() as c:
            for s in stages:
                c.execute("""INSERT INTO build_stages(build_id,name,status,started_at,duration_seconds) VALUES(?,?,?,?,?)
                  ON CONFLICT(build_id,name) DO UPDATE SET status=excluded.status,started_at=excluded.started_at,duration_seconds=excluded.duration_seconds""",
                  (build_id, s["name"], s.get("status"), s.get("started_at"), s.get("duration_seconds")))

    def save_failure(self, build_id: int, failure: dict) -> None:
        with self.connect() as c:
            c.execute("""INSERT INTO build_failures(build_id,failed_stage,error_category,error_code,root_cause,suggestion,
              responsibility_type,error_fingerprint,confidence,normalized_error,error_excerpt,analysis_source,analysis_model,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(build_id) DO UPDATE SET failed_stage=excluded.failed_stage,error_category=excluded.error_category,
              error_code=excluded.error_code,root_cause=excluded.root_cause,suggestion=excluded.suggestion,
              responsibility_type=excluded.responsibility_type,error_fingerprint=excluded.error_fingerprint,
              confidence=excluded.confidence,normalized_error=excluded.normalized_error,error_excerpt=excluded.error_excerpt,
              analysis_source=excluded.analysis_source,analysis_model=excluded.analysis_model,created_at=excluded.created_at""",
              (build_id, failure.get("failed_stage"), failure["error_category"], failure.get("error_code"),
               failure["root_cause"], failure["suggestion"], failure["responsibility_type"],
               failure["error_fingerprint"], failure["confidence"], failure.get("normalized_error"), failure.get("error_excerpt"),
               failure.get("analysis_source") or "RULE", failure.get("analysis_model"), now()))

    def has_failure(self, build_id: int) -> bool:
        return bool(self.query("SELECT id FROM build_failures WHERE build_id=? LIMIT 1", (build_id,)))

    def recent_llm_failure(self, fingerprint: str, since: str, exclude_build_id: int) -> dict | None:
        """A model diagnosis of the same error fingerprint, reused to avoid paying for identical failures."""
        rows=self.query("""SELECT error_category,root_cause,suggestion,responsibility_type,confidence,error_excerpt,analysis_model
          FROM build_failures WHERE error_fingerprint=? AND analysis_source='LLM' AND created_at>=? AND build_id!=?
          ORDER BY created_at DESC LIMIT 1""",(fingerprint,since,exclude_build_id))
        return rows[0] if rows else None

    def query(self, sql: str, params=()) -> list[dict]:
        with self.connect() as c:
            return [{k: _value(v) for k, v in dict(r).items()} for r in c.execute(sql, params).fetchall()]

    def report_was_sent(self, report_type: str, environment: str, period_key: str, chat_id: str) -> bool:
        if not chat_id: return True
        rows=self.query("""SELECT id FROM report_deliveries WHERE report_type=? AND environment=?
          AND period_key=? AND chat_id=? LIMIT 1""",(report_type,environment,period_key,chat_id))
        return bool(rows)

    def mark_report_sent(self, report_type: str, environment: str, period_key: str, chat_id: str) -> None:
        with self.connect() as c:
            c.execute("""INSERT INTO report_deliveries(report_type,environment,period_key,chat_id,sent_at)
              VALUES(?,?,?,?,?) ON CONFLICT(report_type,environment,period_key,chat_id) DO NOTHING""",
              (report_type,environment,period_key,chat_id,now()))


class _CompatConnection:
    def __init__(self, raw, postgres: bool):
        self.raw,self.postgres=raw,postgres

    def execute(self, sql: str, params=()):
        if self.postgres: sql=sql.replace("?","%s")
        return self.raw.execute(sql,params)

    def executescript(self, sql: str):
        if self.postgres:
            for statement in sql.split(";"):
                if statement.strip(): self.raw.execute(statement)
        else:
            self.raw.executescript(sql)


def _value(value):
    if isinstance(value,Decimal): return float(value)
    return value
