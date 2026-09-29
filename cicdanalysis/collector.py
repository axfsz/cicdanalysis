from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from .analyzer import analyze
from .db import Database
from .jenkins import JenkinsClient, failed_downstream, infer_job, parse_build, parse_console_trigger

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, db: Database, client: JenkinsClient, notifier=None, initial_limit: int = 20, console_max: int = 524288,
                 console_head_bytes: int = 65536, service_users: str = "", llm=None, llm_reuse_hours: int = 24):
        self.llm, self.llm_reuse_hours = llm, llm_reuse_hours
        self.db, self.client, self.notifier = db, client, notifier
        self.initial_limit, self.console_max = initial_limit, console_max
        self.console_head_bytes=console_head_bytes
        self.service_users={x.strip().lower() for x in service_users.split(",") if x.strip()}

    def collect_all(self) -> dict:
        stats = {"jobs": 0, "builds": 0, "errors": 0}
        for job in self.client.jobs():
            stats["jobs"] += 1
            try:
                stats["builds"] += self.collect_job(job["name"], job["url"])
            except Exception:
                stats["errors"] += 1
                log.exception("job collection failed: %s", job.get("name"))
        return stats

    def collect_job(self, name: str, url: str, only_number: int | None = None) -> int:
        meta = infer_job(name, url)
        job_id = self.db.upsert_job(meta)
        existing = self.db.query("SELECT last_build_number FROM jobs WHERE id=?", (job_id,))
        previous_last = existing[0]["last_build_number"] if existing else 0
        numbers = [only_number] if only_number else self.client.job_builds(url, self.initial_limit)
        count = 0
        for number in sorted(numbers):
            try:
                raw = self.client.build(url, number)
                build = parse_build(raw)
                current_user=str(build.get("trigger_username") or "").strip()
                service_account=current_user.lower() in self.service_users
                if build.get("trigger_source")!="TELEGRAM_PANEL" and (not current_user or service_account):
                    try:
                        discovered=parse_console_trigger(self.client.console_head(url,number,self.console_head_bytes))
                        if discovered.get("trigger_username") or discovered.get("telegram_username") or discovered.get("telegram_user_id"):
                            build.update(discovered)
                        elif service_account:
                            build.update({"trigger_username":"","trigger_display":"","telegram_username":"","telegram_user_id":"","trigger_source":"SERVICE_ACCOUNT"})
                    except (HTTPError,URLError) as exc:
                        log.debug("cannot inspect console trigger for %s #%s: %s",name,number,exc)
                username=build.pop("trigger_username","");display=build.pop("trigger_display","")
                telegram=build.pop("telegram_username","");telegram_user_id=build.pop("telegram_user_id","")
                identity=f"telegram:{telegram_user_id}" if telegram_user_id else username or (f"telegram:{telegram.lower()}" if telegram else "")
                build["trigger_user_id"] = self.db.upsert_user(identity,display or username or telegram,telegram,telegram_user_id)
                build_id, became_final = self.db.save_build(job_id, build)
                # The Telegram trigger panel is the authoritative source for the
                # human initiator. Jenkins API causes are only a fallback.
                self.db.apply_trigger_event(job_id, build_id, build["build_number"], build.get("queue_id"), build.get("started_at"))
                stages = self.client.stages(url, number)
                self.db.save_stages(build_id, stages)
                if build["result"] in {"FAILURE", "UNSTABLE", "ABORTED"} and not build["building"]:
                    # Diagnose once per final result: polling revisits the last builds every
                    # minute, and each diagnosis downloads logs (and may call the model).
                    if became_final or not self.db.has_failure(build_id):
                        self.diagnose(build_id, name, url, number, build, stages, meta)
                    # 首次历史回灌不批量发送旧失败；后续新构建、webhook 构建及群内“发布通知”触发的采集正常通知。
                    # notifier.failure 自身按 build 去重，重复采集不会重复发群。
                    if self.notifier and (only_number is not None or became_final and previous_last > 0 and number > previous_last):
                        self.notifier.failure(build_id)
                count += 1
            except (HTTPError, URLError) as exc:
                log.warning("cannot collect %s #%s: %s", name, number, exc)
        return count

    def diagnose(self, build_id: int, name: str, url: str, number: int, build: dict, stages: list[dict], meta: dict) -> dict:
        console = self.client.console(url, number, self.console_max)
        focus = self.focus_log(url, number, stages, console)
        failure = analyze(console, stages, build["result"], focus)
        if build["result"] != "ABORTED" and self.llm is not None and self.llm.enabled:
            reused = self.db.recent_llm_failure(failure["error_fingerprint"],
                (datetime.now(timezone.utc)-timedelta(hours=self.llm_reuse_hours)).isoformat(), build_id) if failure["error_code"] != "UNKNOWN" else None
            if reused:
                failure.update({k: v for k, v in reused.items() if v not in (None, "") and k != "analysis_model"},
                               analysis_source="LLM", analysis_model=reused.get("analysis_model"))
            else:
                context = {"Job": name, "构建号": number, "环境": meta.get("environment"), "结果": build["result"],
                           "分支": build.get("branch"), "提交信息": (build.get("commit_message") or "")[:200],
                           "Pipeline 阶段": ", ".join(f"{s.get('name')}={s.get('status')}" for s in stages)[:600]}
                result = self.llm.analyze(context, failure, focus, console)
                if result:
                    failure.update(result, analysis_source="LLM", analysis_model=self.llm.label())
        self.db.save_failure(build_id, failure)
        return failure

    def focus_log(self, url: str, number: int, stages: list[dict], console: str) -> str:
        """The failed Pipeline steps' own logs plus the consoles of failed downstream builds."""
        parts = []
        try:
            parts.append(self.client.failed_step_logs(url, number, stages))
        except (HTTPError, URLError) as exc:
            log.debug("cannot read failed step logs for %s #%s: %s", url, number, exc)
        for job, downstream in failed_downstream(console)[:3]:
            try:
                text = self.client.console(self.client.job_url_for(job), downstream, self.console_max // 4)
                parts.append(f"===== downstream: {job} #{downstream}\n{text}")
            except (HTTPError, URLError) as exc:
                log.debug("cannot read downstream %s #%s: %s", job, downstream, exc)
        return "\n".join(p for p in parts if p)

    def reanalyze(self, name: str, number: int) -> dict:
        """Re-run the diagnosis of one build (CLI `analyze`)."""
        match = self._find_job(name)
        meta = infer_job(name, match["url"])
        job_id = self.db.upsert_job(meta)
        build = parse_build(self.client.build(match["url"], number))
        stages = self.client.stages(match["url"], number)
        rows = self.db.query("SELECT id FROM builds WHERE job_id=? AND build_number=?", (job_id, number))
        build_id = rows[0]["id"] if rows else None
        if build_id is None:
            self.collect_job(name, match["url"], number)
            build_id = self.db.query("SELECT id FROM builds WHERE job_id=? AND build_number=?", (job_id, number))[0]["id"]
        if build["result"] not in {"FAILURE", "UNSTABLE", "ABORTED"}:
            raise ValueError(f"{name} #{number} result is {build['result']}, nothing to analyze")
        return self.diagnose(build_id, name, match["url"], number, build, stages, meta)

    def _find_job(self, name: str) -> dict:
        match = next((j for j in self.client.jobs() if j["name"] == name or j["url"].rstrip("/").endswith("/job/" + name)), None)
        if not match:
            raise ValueError(f"Jenkins job not found: {name}")
        return match

    def collect_one(self, name: str, number: int) -> int:
        return self.collect_job(name, self._find_job(name)["url"], number)
