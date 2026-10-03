from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .db import Database


class Telegram:
    def __init__(self, token: str, db: Database, chat_resolver):
        self.token, self.db, self.chat_resolver = token, db, chat_resolver

    def send(self, chat_id: str, text: str, reply_to: int | None = None) -> bool:
        if not self.token or not chat_id: return False
        fields = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if reply_to:
            fields["reply_parameters"] = json.dumps({"message_id": int(reply_to), "allow_sending_without_reply": True})
        body = urlencode(fields).encode()
        req = Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=body, method="POST")
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read()).get("ok", False)

    def _call(self, method: str, fields: dict | None = None, timeout: int = 20) -> dict:
        body = urlencode(fields or {}).encode()
        req = Request(f"https://api.telegram.org/bot{self.token}/{method}", data=body, method="POST")
        with urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
        if not payload.get("ok"): raise RuntimeError(payload.get("description", f"Telegram {method} failed"))
        return payload.get("result")

    def username(self) -> str:
        """This bot's @username (cached), so commands addressed to other bots are ignored."""
        if getattr(self, "_username", None) is None and self.token:
            try: self._username = (self._call("getMe") or {}).get("username") or ""
            except Exception: return ""
        return getattr(self, "_username", "") or ""

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Show the commands in the group "/" menu."""
        payload = json.dumps([{"command": c, "description": d[:256]} for c, d in commands], ensure_ascii=False)
        for scope in ("all_group_chats", "all_private_chats"):
            self._call("setMyCommands", {"commands": payload, "scope": json.dumps({"type": scope})})

    def updates(self, offset: int = 0, timeout: int = 30) -> list[dict]:
        """Long-poll Telegram updates for group trigger-result messages."""
        if not self.token: return []
        query=urlencode({"offset":offset,"timeout":max(1,min(timeout,50)),
                         "allowed_updates":json.dumps(["message","edited_message","channel_post","edited_channel_post"])})
        req=Request(f"https://api.telegram.org/bot{self.token}/getUpdates?{query}")
        with urlopen(req,timeout=max(10,timeout+10)) as response:
            payload=json.loads(response.read())
        if not payload.get("ok"): raise RuntimeError(payload.get("description","Telegram getUpdates failed"))
        return payload.get("result",[])

    def failure_row(self, build_id: int) -> dict | None:
        rows = self.db.query("""SELECT b.build_number,b.build_url,b.branch,b.commit_sha,b.result,b.duration_seconds,
          j.id job_id,j.job_name,j.environment,u.display_name,u.telegram_username,
          f.failed_stage,f.error_category,f.error_code,f.root_cause,f.suggestion,f.responsibility_type,f.confidence,f.error_excerpt,f.analysis_source,f.analysis_model,
          (SELECT COUNT(*) FROM build_failures f2 WHERE f2.error_fingerprint=f.error_fingerprint) repeats
          FROM builds b JOIN jobs j ON j.id=b.job_id LEFT JOIN users u ON u.id=b.trigger_user_id
          JOIN build_failures f ON f.build_id=b.id WHERE b.id=?""", (build_id,))
        return rows[0] if rows else None

    def failure_target(self, r: dict) -> tuple[str, int | None]:
        """The release group of the build, replying to its "Jenkins 发布通知" message when known."""
        announced = self.db.release_result_for(r["job_id"], r["build_number"]) or {}
        return announced.get("telegram_chat_id") or self.chat_resolver(r["environment"]), announced.get("telegram_message_id")

    def failure(self, build_id: int) -> bool:
        """Post a diagnosis for a failed build to the release group it belongs to.

        When the group already announced the failure ("Jenkins 发布通知"), the
        report is sent as a reply to that message. Each build is reported once.
        """
        r = self.failure_row(build_id)
        if not r: return False
        chat_id, reply_to = self.failure_target(r)
        if not chat_id or not self.db.claim_failure_notification(build_id, chat_id):
            return False
        try:
            ok = self.send(chat_id, failure_text(r), reply_to)
        except Exception:
            ok = False
        if not ok:
            self.db.release_failure_notification(build_id)
        return ok

    def send_failure(self, build_id: int, chat_id: str = "", dry_run: bool = False) -> dict:
        """Manual (re)send of one report (CLI ``analyze --send``); ignores the once-per-build guard.

        Without ``chat_id`` it goes where the automatic report would go. Sending to
        the release group also marks the build as reported, so it is not sent twice.
        """
        r = self.failure_row(build_id)
        if not r: raise ValueError("no diagnosis stored for this build")
        group, reply_to = self.failure_target(r)
        target = chat_id or group
        if chat_id and str(chat_id) != str(group): reply_to = None
        out = {"chat_id": target, "reply_to": reply_to, "text": failure_text(r), "sent": False}
        if not target: raise ValueError(f"no Telegram chat configured for environment {r.get('environment')!r}")
        if dry_run: return out
        if not self.token: raise ValueError("TELEGRAM_BOT_TOKEN is empty")
        out["sent"] = self.send(target, out["text"], reply_to)
        if out["sent"] and str(target) == str(group):
            self.db.claim_failure_notification(build_id, target, "MANUAL")
        return out


def failure_text(r: dict) -> str:
    if r.get("telegram_username"): who = f"{r.get('display_name') or r['telegram_username']} (@{r['telegram_username']})"
    else: who = r.get("display_name") or "未识别（自动/未关联）"
    status = {"FAILURE": "❌ 构建失败", "UNSTABLE": "⚠️ 构建不稳定", "ABORTED": "⚪ 构建被取消"}.get(r.get("result") or "", "❌ 构建失败")
    confidence = int(round(float(r.get("confidence") or 0) * 100))
    lines = [f"🔎 <b>Jenkins 发布失败分析</b>  {status}", "",
             f"任务：<b>{html.escape(r['job_name'])}</b> #{r['build_number']}",
             f"环境：{html.escape((r.get('environment') or '-').upper())}",
             f"触发人：{html.escape(who)}",
             f"分支：{html.escape(r.get('branch') or '-')}　提交：<code>{html.escape((r.get('commit_sha') or '-')[:12])}</code>",
             "", f"失败阶段：<b>{html.escape(r.get('failed_stage') or '未知')}</b>",
             f"错误分类：<b>{html.escape(r['error_category'])}</b> / {html.escape(r.get('error_code') or '-')}（置信度 {confidence}%）",
             f"根因判断：{html.escape(r['root_cause'])}",
             f"处理建议：{html.escape(r['suggestion'])}",
             f"责任类型：{html.escape(r.get('responsibility_type') or '-')}",
             f"同类错误累计：{r['repeats']} 次",
             f"分析方式：{'大模型 ' + html.escape(r.get('analysis_model') or '') if r.get('analysis_source') == 'LLM' else '规则匹配'}"]
    excerpt = (r.get("error_excerpt") or "").strip()
    if excerpt:
        lines += ["", "<b>关键日志</b>", f"<pre>{html.escape(excerpt[-1500:])}</pre>"]
    if r.get("build_url"):
        lines += ["", f"<a href=\"{html.escape(r['build_url'])}\">查看 Jenkins 构建</a> · <a href=\"{html.escape(r['build_url'].rstrip('/'))}/console\">完整日志</a>"]
    return "\n".join(lines)


TRIGGER_TITLES = ("Jenkins 发布触发结果", "Jenkins 发布触发通知")
# Single and batch variants: "Jenkins 发布触发通知", "Jenkins 批量发布触发通知", "Jenkins 批量发布触发结果" ...
TRIGGER_TITLE = re.compile(r"Jenkins\s*(批量)?\s*发布触发(结果|通知)")
RESULT_TITLE = "Jenkins 发布通知"
RESULTS = ("SUCCESS", "FAILURE", "FAILED", "UNSTABLE", "ABORTED")


def _message(update: dict) -> dict | None:
    return next((update.get(k) for k in ("message","edited_message","channel_post","edited_channel_post") if update.get(k)),None)


def _field(text: str, label: str) -> str:
    match=re.search(rf"(?m)^\s*{re.escape(label)}\s*[:：=]\s*(.*?)\s*$",text)
    value=match.group(1).strip() if match else ""
    return "" if value in {"-","—"} else value


def _jobs_from_url(url: str) -> str:
    parts=[x for x in urlparse(url).path.split("/") if x]
    return "/".join(parts[i+1] for i,x in enumerate(parts[:-1]) if x=="job")


def _chat(message: dict, allowed_chats) -> str | None:
    chat_id=str((message.get("chat") or {}).get("id", ""))
    if allowed_chats is not None and chat_id not in allowed_chats: return None
    return chat_id


def _chat_environment(chat_id: str, allowed_chats) -> str:
    return allowed_chats.get(chat_id, "") if isinstance(allowed_chats, dict) else ""


def _local_iso(value: str, timezone_name: str, fallback_ts=None) -> str:
    if value:
        try: return datetime.strptime(value,"%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(timezone_name)).isoformat()
        except ValueError: return value
    if fallback_ts: return datetime.fromtimestamp(int(fallback_ts),ZoneInfo(timezone_name)).isoformat()
    return ""


_BULLET = re.compile(r"^\s*(?:[•·●▪◦\-\*]|\d+[.、)])\s*(.+?)\s*$")
_JOB_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/]*")
_URL = re.compile(r"https?://\S+")


def _batch_items(text: str) -> list[dict]:
    """Service lines of a batch notice (\"• uat-report-rpc-prod\"), before the 触发状态 line / trailing panel text.

    A line may also carry its own queue or build URL (\"• job: https://.../job/job/142/\").
    """
    items, seen = [], set()
    for line in text.splitlines():
        if re.match(r"^\s*(触发状态|📌|构建面板)", line): break
        bullet = _BULLET.match(line)
        if not bullet: continue
        body = bullet.group(1)
        urls = [u.rstrip(")）,，") for u in _URL.findall(body)]
        build_url = next((u for u in urls if "/job/" in u and re.search(r"/\d+/?$", u)), "")
        queue_url = next((u for u in urls if "/queue/item/" in u), "")
        job = _jobs_from_url(build_url) if build_url else ""
        if not job:
            token = _JOB_TOKEN.search(_URL.sub("", body))
            job = token.group(0).rstrip("/.:") if token else ""
        if not job or job in seen or "-" not in job: continue
        seen.add(job)
        items.append({"job_name": job, "build_url": build_url, "queue_url": queue_url})
    return items


def parse_trigger_messages(update: dict, timezone_name: str, allowed_chats=None) -> list[dict]:
    """Parse the human-readable trigger receipt posted in a release group into one event per job.

    Handles the variants the release bots post:
    "📣 Jenkins 发布触发结果" (with queue/build URL), "🚀 Jenkins 发布触发通知"
    (job + time only; linked to the build later) and "🚀 Jenkins 批量发布触发通知"
    (one message for several services listed as "• <job>").
    ``allowed_chats`` may be a set of chat ids or a {chat_id: environment} map.
    """
    message=_message(update)
    if not message: return []
    chat_id=_chat(message, allowed_chats)
    if chat_id is None: return []
    text=str(message.get("text") or message.get("caption") or "")
    title=TRIGGER_TITLE.search(text)
    if not title: return []
    field=lambda label: _field(text,label)
    person=field("触发人")
    identity=re.match(r"(.+?)\s*\(@([^\)]+)\)\s*$",person)
    trigger_name=(identity.group(1).strip() if identity else person).strip()
    telegram_username=(identity.group(2).strip().lstrip("@") if identity else "")
    if not (trigger_name or telegram_username): return []
    triggered_at=_local_iso(field("触发时间"),timezone_name,message.get("date"))
    namespace=field("命名空间") or field("环境")
    http_text=field("HTTP状态")
    message_key=f"{chat_id}:{message.get('message_id',update.get('update_id'))}"
    common={"trigger_name":trigger_name,"telegram_username":telegram_username,"telegram_chat_id":chat_id,
            "branch":field("Git分支") or field("分支"),"triggered_at":triggered_at,
            "service_type":field("服务类型"),"service_name":field("服务名称"),"namespace":namespace,
            "environment":_chat_environment(chat_id, allowed_chats) or namespace,
            "status":field("触发状态"),"http_status":int(http_text) if http_text.isdigit() else None}
    build_url=field("构建地址")
    job_name=field("触发项目")
    if build_url and _jobs_from_url(build_url): job_name=_jobs_from_url(build_url)
    items=_batch_items(text)
    batch=bool(title.group(1)) or (not job_name and len(items)>0)
    if not batch:
        if not job_name: return []
        return [{**common,"event_id":f"telegram-message:{message_key}","job_name":job_name,"build_url":build_url,
                 "queue_url":field("队列地址"),"trigger_mode":"SINGLE","batch_id":"","batch_size":1}]
    if job_name and not any(i["job_name"]==job_name for i in items):
        items.insert(0,{"job_name":job_name,"build_url":build_url,"queue_url":field("队列地址")})
    batch_id=f"telegram-batch:{message_key}"
    return [{**common,"event_id":f"telegram-message:{message_key}:{item['job_name']}","job_name":item["job_name"],
             "build_url":item["build_url"],"queue_url":item["queue_url"],"service_name":"","service_type":"",
             "trigger_mode":"BATCH","batch_id":batch_id,"batch_size":len(items)} for item in items]


def parse_trigger_message(update: dict, timezone_name: str, allowed_chats=None) -> dict | None:
    """First trigger event of a message (kept for callers that expect a single event)."""
    events=parse_trigger_messages(update,timezone_name,allowed_chats)
    return events[0] if events else None


def parse_release_result(update: dict, timezone_name: str, allowed_chats=None) -> dict | None:
    """Parse the "✅/❌ Jenkins 发布通知" message posted when a pipeline finishes."""
    message=_message(update)
    if not message: return None
    chat_id=_chat(message, allowed_chats)
    if chat_id is None: return None
    text=str(message.get("text") or message.get("caption") or "")
    if RESULT_TITLE not in text or TRIGGER_TITLE.search(text): return None
    field=lambda label: _field(text,label)
    build_url=field("Jenkins")
    # 任务 is the Jenkins job; when a failed pipeline leaves it as "-", fall back to
    # the job in the Jenkins URL, then to the service name (resolved against Jenkins later).
    job_name=field("任务") or (_jobs_from_url(build_url) if build_url else "")
    service_name=field("发布服务名称")
    job_guessed=not job_name and bool(service_name)
    job_name=job_name or service_name
    number=field("构建号")
    if not number.isdigit() and build_url:
        match=re.search(r"/(\d+)/?$",build_url); number=match.group(1) if match else ""
    if not job_name or not number.isdigit(): return None
    status_text=field("状态").upper()
    result=next((x for x in RESULTS if x in status_text),"")
    if result=="FAILED": result="FAILURE"
    if not result:
        head=text.strip().splitlines()[0] if text.strip() else ""
        result="FAILURE" if "❌" in head else "SUCCESS" if "✅" in head else "UNKNOWN"
    finished_at=""
    raw_finished=field("完成时间")
    for fmt in ("%Y-%m-%d %H:%M:%S %z","%Y-%m-%d %H:%M:%S UTC","%Y-%m-%d %H:%M:%S"):
        try:
            parsed=datetime.strptime(raw_finished,fmt)
            if parsed.tzinfo is None:
                parsed=parsed.replace(tzinfo=timezone.utc if fmt.endswith("UTC") else ZoneInfo(timezone_name))
            finished_at=parsed.isoformat();break
        except ValueError: continue
    if not finished_at and message.get("date"):
        finished_at=datetime.fromtimestamp(int(message["date"]),ZoneInfo(timezone_name)).isoformat()
    return {"event_id":f"telegram-result:{chat_id}:{message.get('message_id',update.get('update_id'))}",
            "job_name":job_name,"build_number":int(number),"build_url":build_url,"result":result,
            "telegram_chat_id":chat_id,"telegram_message_id":None if message.get("reply_unsafe") else message.get("message_id"),
            "environment":_chat_environment(chat_id, allowed_chats),"branch":field("分支名称"),
            "commit_sha":field("提交"),"finished_at":finished_at,"job_name_guessed":job_guessed}
