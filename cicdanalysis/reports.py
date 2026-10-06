from __future__ import annotations

import html
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import Database, parse_time
from .jenkins import AUTOMATIC_SOURCES

# Label of builds without a known person: really automatic vs. a release whose trigger was not captured.
AUTO_SQL = "(" + ",".join(f"'{x}'" for x in AUTOMATIC_SOURCES) + ")"
USER_LABEL = f"""COALESCE(u.display_name,CASE WHEN b.trigger_source IN {AUTO_SQL} THEN '自动触发' ELSE '未识别触发人' END)"""


class Reports:
    def __init__(self, db: Database, timezone_name: str):
        self.db, self.tz = db, ZoneInfo(timezone_name)

    def bounds(self, kind: str, now: datetime | None = None):
        local = (now or datetime.now(timezone.utc)).astimezone(self.tz)
        today = local.replace(hour=0, minute=0, second=0, microsecond=0)
        if kind == "daily": start, end = today - timedelta(days=1), today
        elif kind == "weekly":
            end = today - timedelta(days=today.weekday())
            start = end - timedelta(days=7)
        elif kind == "monthly":
            end = today.replace(day=1)
            prev = end - timedelta(days=1)
            start = prev.replace(day=1)
        else: raise ValueError("kind must be daily, weekly or monthly")
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    def date_bounds(self, value: str):
        try: selected=date.fromisoformat(value)
        except (TypeError,ValueError): raise ValueError("date must use YYYY-MM-DD")
        start=datetime(selected.year,selected.month,selected.day,tzinfo=self.tz)
        return start.astimezone(timezone.utc),(start+timedelta(days=1)).astimezone(timezone.utc)

    def period_key(self, kind: str, now: datetime | None = None) -> str:
        local=(now or datetime.now(timezone.utc)).astimezone(self.tz)
        if kind=="daily": return local.date().isoformat()
        if kind=="weekly": return f"{local:%G-W%V}"
        if kind=="monthly": return f"{local:%Y-%m}"
        raise ValueError("kind must be daily, weekly or monthly")

    def summary(self, start: datetime, end: datetime, environment: str = "") -> dict:
        where = "b.started_at>=? AND b.started_at<?"
        params: list = [start.isoformat(), end.isoformat()]
        if environment:
            where += " AND j.environment=?"; params.append(environment)
        rows = self.db.query(f"""SELECT b.result,COUNT(*) count FROM builds b JOIN jobs j ON j.id=b.job_id
          WHERE {where} GROUP BY b.result""", params)
        counts = {r["result"]: r["count"] for r in rows}
        total = sum(counts.values()); decided = counts.get("SUCCESS", 0)+counts.get("FAILURE", 0)+counts.get("UNSTABLE", 0)
        base_params = list(params)
        jobs = self.db.query(f"""SELECT j.job_name,COUNT(*) total,SUM(CASE WHEN b.result='FAILURE' THEN 1 ELSE 0 END) failures
          FROM builds b JOIN jobs j ON j.id=b.job_id WHERE {where} GROUP BY j.id,j.job_name ORDER BY total DESC LIMIT 5""", base_params)
        users = self.db.query(f"""SELECT name,telegram_username,COUNT(*) total,
          SUM(CASE WHEN result='SUCCESS' THEN 1 ELSE 0 END) success,
          SUM(CASE WHEN result='FAILURE' THEN 1 ELSE 0 END) failures,
          SUM(CASE WHEN result='ABORTED' THEN 1 ELSE 0 END) aborted,
          SUM(batch) batch,
          ROUND(100.0*SUM(CASE WHEN result='SUCCESS' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN result IN ('SUCCESS','FAILURE','UNSTABLE') THEN 1 ELSE 0 END),0),2) success_rate,
          MAX(kind) kind
          FROM (SELECT {USER_LABEL} name,u.telegram_username,b.trigger_user_id,b.result,
                  CASE WHEN EXISTS(SELECT 1 FROM trigger_events t WHERE t.matched_build_id=b.id AND t.trigger_mode='BATCH') THEN 1 ELSE 0 END batch,
                  CASE WHEN b.trigger_user_id IS NOT NULL THEN 'user' WHEN b.trigger_source IN {AUTO_SQL} THEN 'auto' ELSE 'unknown' END kind
                FROM builds b JOIN jobs j ON j.id=b.job_id LEFT JOIN users u ON u.id=b.trigger_user_id WHERE {where}) x
          GROUP BY trigger_user_id,name,telegram_username ORDER BY total DESC LIMIT 12""", base_params)
        failures = self.db.query(f"""SELECT f.error_category,COUNT(*) count FROM build_failures f
          JOIN builds b ON b.id=f.build_id JOIN jobs j ON j.id=b.job_id WHERE {where}
          GROUP BY f.error_category ORDER BY count DESC LIMIT 8""", base_params)
        repeated = self.db.query(f"""SELECT f.error_code,f.error_fingerprint,COUNT(*) count FROM build_failures f
          JOIN builds b ON b.id=f.build_id JOIN jobs j ON j.id=b.job_id WHERE {where}
          GROUP BY f.error_code,f.error_fingerprint HAVING COUNT(*)>1 ORDER BY count DESC LIMIT 5""", base_params)
        avg = self.db.query(f"SELECT AVG(b.duration_seconds) avg FROM builds b JOIN jobs j ON j.id=b.job_id WHERE {where}", base_params)[0]["avg"] or 0
        source = self.db.query(f"""SELECT SUM(CASE WHEN b.trigger_source='TELEGRAM_PANEL' THEN 1 ELSE 0 END) panel_triggered,
          SUM(CASE WHEN b.trigger_source!='TELEGRAM_PANEL' AND b.trigger_user_id IS NOT NULL THEN 1 ELSE 0 END) jenkins_identified,
          SUM(CASE WHEN b.trigger_user_id IS NULL AND b.trigger_source IN {AUTO_SQL} THEN 1 ELSE 0 END) automatic,
          SUM(CASE WHEN b.trigger_user_id IS NULL AND COALESCE(b.trigger_source,'') NOT IN {AUTO_SQL} THEN 1 ELSE 0 END) unattributed,
          COUNT(DISTINCT CASE WHEN b.trigger_source='TELEGRAM_PANEL' THEN b.trigger_user_id END) developer_count
          FROM builds b JOIN jobs j ON j.id=b.job_id WHERE {where}""", base_params)[0]
        batch = self.db.query(f"""SELECT COUNT(DISTINCT t.batch_id) messages,COUNT(DISTINCT t.matched_build_id) builds
          FROM trigger_events t JOIN builds b ON b.id=t.matched_build_id JOIN jobs j ON j.id=b.job_id
          WHERE t.trigger_mode='BATCH' AND {where}""", base_params)[0]
        analysis = self.failure_analysis(where, base_params)
        trigger_where="t.triggered_at>=? AND t.triggered_at<? AND t.matched_build_id IS NULL"
        trigger_params:list=[start.isoformat(),end.isoformat()]
        if environment:
            trigger_where+=" AND COALESCE(NULLIF(t.environment,''),j.environment)=?";trigger_params.append(environment)
        unmatched=self.db.query(f"""SELECT COUNT(*) count FROM trigger_events t JOIN jobs j ON j.id=t.job_id
          WHERE {trigger_where}""",trigger_params)[0]["count"]
        panel_triggered=source["panel_triggered"] or 0
        unattributed=source["unattributed"] or 0
        if not total: identity_status="NO_DATA"
        elif unattributed and panel_triggered: identity_status="PARTIAL"
        elif unattributed and not panel_triggered: identity_status="NOT_CONNECTED"
        else: identity_status="OK"
        return {"start": start.isoformat(), "end": end.isoformat(), "environment": environment or "all", "total": total,
                "counts": counts, "success_rate": round(counts.get("SUCCESS", 0)*100/decided, 2) if decided else 0,
                "avg_duration": round(avg), "panel_triggered": panel_triggered,
                "jenkins_identified": source["jenkins_identified"] or 0,"unattributed": unattributed,
                "automatic": source["automatic"] or 0,
                "batch_messages": batch["messages"] or 0,"batch_builds": batch["builds"] or 0,
                "analysis": analysis,
                "developer_count": source["developer_count"] or 0,"unmatched_triggers": unmatched,
                "trigger_identity_status":identity_status,
                "jobs": jobs, "users": users, "failures": failures, "repeated": repeated}

    def failure_analysis(self, where: str, params: list) -> dict:
        """How failed builds were analyzed and reported (automatic / manual) and how quickly."""
        rows = self.db.query(f"""SELECT b.id,j.job_name,j.environment,b.build_number,b.result,b.build_url,
            COALESCE(b.finished_at,b.started_at) finished_at,{USER_LABEL} trigger_name,u.telegram_username,
            f.error_category,f.root_cause,f.analysis_source,n.sent_at,n.source report_source
          FROM builds b JOIN jobs j ON j.id=b.job_id LEFT JOIN users u ON u.id=b.trigger_user_id
          LEFT JOIN build_failures f ON f.build_id=b.id LEFT JOIN failure_notifications n ON n.build_id=b.id
          WHERE b.result IN ('FAILURE','UNSTABLE') AND b.building=0 AND {where}
          ORDER BY COALESCE(b.finished_at,b.started_at) DESC""", params)
        latencies = []
        for r in rows:
            r["reported"] = bool(r["sent_at"])
            r["report_source"] = (r.get("report_source") or ("AUTO" if r["sent_at"] else "")) or ""
            r["latency_seconds"] = None
            finished, sent = parse_time(r.get("finished_at")), parse_time(r.get("sent_at"))
            if finished and sent and r["report_source"] == "AUTO":
                r["latency_seconds"] = max(0, round((sent - finished).total_seconds()))
                latencies.append(r["latency_seconds"])
        by = lambda k: sum(1 for r in rows if r["report_source"] == k)
        return {"failed": len(rows), "analyzed": sum(1 for r in rows if r.get("error_category")),
                "llm": sum(1 for r in rows if r.get("analysis_source") == "LLM"),
                "auto_reported": by("AUTO"), "manual_reported": by("MANUAL") + by("COMMAND"),
                "unreported": sum(1 for r in rows if not r["reported"]),
                "auto_rate": round(by("AUTO") * 100 / len(rows), 2) if rows else 0,
                "avg_latency_seconds": round(sum(latencies) / len(latencies)) if latencies else None,
                "recent": [{k: r.get(k) for k in ("job_name", "environment", "build_number", "result", "build_url", "finished_at",
                                                    "trigger_name", "telegram_username", "error_category", "root_cause",
                                                    "analysis_source", "reported", "report_source", "latency_seconds")}
                           for r in rows[:10]]}

    def text(self, kind: str, environment: str = "", now: datetime | None = None) -> str:
        start, end = self.bounds(kind, now)
        title = {"daily": "每日", "weekly": "每周", "monthly": "每月"}[kind]
        return self._text(title,start,end,environment)

    def text_for_date(self, value: str, environment: str = "") -> str:
        start,end=self.date_bounds(value)
        return self._text("每日",start,end,environment)

    def _text(self,title: str,start: datetime,end: datetime,environment: str) -> str:
        s = self.summary(start, end, environment)
        label = start.astimezone(self.tz).strftime("%Y-%m-%d") + " ～ " + (end.astimezone(self.tz)-timedelta(seconds=1)).strftime("%Y-%m-%d")
        period_label="日期" if title=="每日" else "周期"
        c = s["counts"]
        lines = [f"📊 <b>Jenkins {title}构建报告</b>", f"环境：<b>{html.escape(s['environment'].upper())}</b>", f"{period_label}：{label.split(' ～ ')[0] if title=='每日' else label}", "",
                 f"总构建：<b>{s['total']}</b>", f"✅ 成功：{c.get('SUCCESS',0)}", f"❌ 失败：{c.get('FAILURE',0)}",
                 f"⚠️ 不稳定：{c.get('UNSTABLE',0)}", f"⚪ 取消：{c.get('ABORTED',0)}", f"成功率：<b>{s['success_rate']}%</b>",
                 f"平均耗时：{s['avg_duration']//60}m{s['avg_duration']%60}s", "", "<b>触发来源</b>",
                 f"👤 群聊/面板实名触发：{s['panel_triggered']} 次 / {s['developer_count']} 人",
                 f"📦 其中批量发布：{s['batch_builds']} 次（{s['batch_messages']} 条批量指令）",
                 f"🔗 Jenkins 识别：{s['jenkins_identified']} 次",f"🤖 自动触发（定时/SCM/上游）：{s['automatic']} 次",
                 f"❔ 未识别触发人：{s['unattributed']} 次",
                 f"⏳ 待关联触发事件：{s['unmatched_triggers']} 次", "", "<b>构建最多 Job</b>"]
        lines += [f"{i}. {html.escape(r['job_name'])} — {r['total']} 次 / 失败 {r['failures']}" for i,r in enumerate(s["jobs"],1)] or ["暂无数据"]
        lines += ["", "<b>触发情况</b>"]
        lines += [f"{html.escape(r['name'])}{' (@'+html.escape(r['telegram_username'])+')' if r.get('telegram_username') else ''}：{r['total']} 次{'（批量 '+str(r['batch'])+'）' if r.get('batch') else ''}，成功 {r['success']}，失败 {r['failures']}，取消 {r['aborted']}，成功率 {r.get('success_rate') or 0}%" for r in s["users"]] or ["暂无数据"]
        a = s["analysis"]
        if a["failed"]:
            lines += ["", "<b>失败自动分析</b>",
                      f"失败构建：{a['failed']} 个，已分析 {a['analyzed']}（大模型 {a['llm']}）",
                      f"自动推送：{a['auto_reported']}　手动补发：{a['manual_reported']}　未推送：{a['unreported']}"
                      + (f"　平均推送延迟：{a['avg_latency_seconds']}s" if a["avg_latency_seconds"] is not None else "")]
        lines += ["", "<b>失败原因</b>"]
        lines += [f"{html.escape(r['error_category'])}：{r['count']}" for r in s["failures"]] or ["暂无失败"]
        if s["repeated"]:
            lines += ["", "<b>高频重复故障</b>"] + [f"{html.escape(r['error_code'])}：{r['count']} 次" for r in s["repeated"]]
        return "\n".join(lines)

    def overview_days(self, days: int, environment="") -> dict:
        end = datetime.now(timezone.utc); return self.summary(end-timedelta(days=max(1,min(days,365))), end, environment)

    def overview_date(self, value: str, environment="") -> dict:
        start,end=self.date_bounds(value);return self.summary(start,end,environment)
