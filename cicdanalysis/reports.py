from __future__ import annotations

import html
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import Database


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
        users = self.db.query(f"""SELECT COALESCE(u.display_name,'自动触发') name,u.telegram_username,COUNT(*) total,
          SUM(CASE WHEN b.result='SUCCESS' THEN 1 ELSE 0 END) success,
          SUM(CASE WHEN b.result='FAILURE' THEN 1 ELSE 0 END) failures,
          SUM(CASE WHEN b.result='ABORTED' THEN 1 ELSE 0 END) aborted,
          ROUND(100.0*SUM(CASE WHEN b.result='SUCCESS' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN b.result IN ('SUCCESS','FAILURE','UNSTABLE') THEN 1 ELSE 0 END),0),2) success_rate
          FROM builds b JOIN jobs j ON j.id=b.job_id LEFT JOIN users u ON u.id=b.trigger_user_id
          WHERE {where} GROUP BY b.trigger_user_id,u.display_name,u.telegram_username ORDER BY total DESC LIMIT 10""", base_params)
        failures = self.db.query(f"""SELECT f.error_category,COUNT(*) count FROM build_failures f
          JOIN builds b ON b.id=f.build_id JOIN jobs j ON j.id=b.job_id WHERE {where}
          GROUP BY f.error_category ORDER BY count DESC LIMIT 8""", base_params)
        repeated = self.db.query(f"""SELECT f.error_code,f.error_fingerprint,COUNT(*) count FROM build_failures f
          JOIN builds b ON b.id=f.build_id JOIN jobs j ON j.id=b.job_id WHERE {where}
          GROUP BY f.error_code,f.error_fingerprint HAVING COUNT(*)>1 ORDER BY count DESC LIMIT 5""", base_params)
        avg = self.db.query(f"SELECT AVG(b.duration_seconds) avg FROM builds b JOIN jobs j ON j.id=b.job_id WHERE {where}", base_params)[0]["avg"] or 0
        source = self.db.query(f"""SELECT SUM(CASE WHEN b.trigger_source='TELEGRAM_PANEL' THEN 1 ELSE 0 END) panel_triggered,
          SUM(CASE WHEN b.trigger_source!='TELEGRAM_PANEL' AND b.trigger_user_id IS NOT NULL THEN 1 ELSE 0 END) jenkins_identified,
          SUM(CASE WHEN b.trigger_user_id IS NULL THEN 1 ELSE 0 END) unattributed,
          COUNT(DISTINCT CASE WHEN b.trigger_source='TELEGRAM_PANEL' THEN b.trigger_user_id END) developer_count
          FROM builds b JOIN jobs j ON j.id=b.job_id WHERE {where}""", base_params)[0]
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
                "developer_count": source["developer_count"] or 0,"unmatched_triggers": unmatched,
                "trigger_identity_status":identity_status,
                "jobs": jobs, "users": users, "failures": failures, "repeated": repeated}

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
                 f"🔗 Jenkins 识别：{s['jenkins_identified']} 次",f"❔ 未识别触发人：{s['unattributed']} 次",
                 f"⏳ 待关联触发事件：{s['unmatched_triggers']} 次", "", "<b>构建最多 Job</b>"]
        lines += [f"{i}. {html.escape(r['job_name'])} — {r['total']} 次 / 失败 {r['failures']}" for i,r in enumerate(s["jobs"],1)] or ["暂无数据"]
        lines += ["", "<b>触发情况</b>"]
        lines += [f"{html.escape(r['name'])}{' (@'+html.escape(r['telegram_username'])+')' if r.get('telegram_username') else ''}：{r['total']} 次，成功 {r['success']}，失败 {r['failures']}，取消 {r['aborted']}，成功率 {r.get('success_rate') or 0}%" for r in s["users"]] or ["暂无数据"]
        lines += ["", "<b>失败原因</b>"]
        lines += [f"{html.escape(r['error_category'])}：{r['count']}" for r in s["failures"]] or ["暂无失败"]
        if s["repeated"]:
            lines += ["", "<b>高频重复故障</b>"] + [f"{html.escape(r['error_code'])}：{r['count']} 次" for r in s["repeated"]]
        return "\n".join(lines)

    def overview_days(self, days: int, environment="") -> dict:
        end = datetime.now(timezone.utc); return self.summary(end-timedelta(days=max(1,min(days,365))), end, environment)

    def overview_date(self, value: str, environment="") -> dict:
        start,end=self.date_bounds(value);return self.summary(start,end,environment)
