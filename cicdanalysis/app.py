from __future__ import annotations

import logging
import hashlib
import json
import queue
import re
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .collector import Collector
from .commands import BOT_COMMANDS, GroupCommands
from .config import Config
from .db import Database, parse_time
from .jenkins import JenkinsClient
from .llm import LLMAnalyzer
from .jenkins import infer_job
from .reports import Reports
from .telegram import Telegram
from .telegram import parse_release_result, parse_trigger_messages
from .userbot import UserAccountListener


class App:
    def __init__(self, config: Config):
        self.config=config; self.db=Database(config.database_url or config.database_path); self.db.init()
        self.reports=Reports(self.db,config.timezone)
        self.telegram=Telegram(config.telegram_token,self.db,config.chat_for)
        self.jenkins=JenkinsClient(config.jenkins_url,config.jenkins_user,config.jenkins_token,config.jenkins_verify_tls)
        self.llm=LLMAnalyzer(config.llm_base_url,config.llm_api_key,config.llm_model,config.llm_timeout,config.llm_max_input_chars,config.llm_max_tokens)
        logging.info("LLM root-cause analysis: %s",f"enabled ({self.llm.model} @ {self.llm.base_url})" if self.llm.enabled else "disabled (LLM_API_KEY/AI_API_KEY empty), rule engine only")
        self.collector=Collector(self.db,self.jenkins,self.telegram if config.notify_failures else None,config.initial_build_limit,
                                 config.console_max_bytes,config.console_head_bytes,config.jenkins_service_users,
                                 self.llm,config.llm_reuse_hours,config.failure_notify_max_age_minutes,config.notify_aborted)
        self.work=queue.Queue(); self.stop=threading.Event(); self.last_report={}
        self.follow_ups: dict[tuple[str,int],int]={}
        self.user_listener=UserAccountListener(self)
        self.commands=GroupCommands(self)

    def enqueue(self,name,number): self.work.put((name,number))

    def _follow_up(self, name: str, number: int) -> None:
        """Re-collect a build that was still running when it was announced, until Jenkins finalizes it.

        The pipeline posts "❌ Jenkins 发布通知" from its post step, i.e. before the build is
        final ("耗时: 49 sec and counting"); the analysis must wait for the final result.
        """
        rows=self.db.query("""SELECT b.building FROM builds b JOIN jobs j ON j.id=b.job_id
          WHERE j.job_name=? AND b.build_number=?""",(name,number))
        key=(name,number)
        if not rows or not rows[0]["building"]:
            self.follow_ups.pop(key,None); return
        interval=max(5,self.config.follow_up_seconds)
        attempt=self.follow_ups.get(key,0)+1
        if attempt*interval > self.config.follow_up_max_minutes*60:
            self.follow_ups.pop(key,None)
            logging.warning("%s #%s still running after %s min; leaving it to the poller",name,number,self.config.follow_up_max_minutes)
            return
        self.follow_ups[key]=attempt
        timer=threading.Timer(interval,self.enqueue,args=(name,number)); timer.daemon=True; timer.start()

    def record_trigger(self, data: dict, allow_pending: bool = False) -> dict:
        """Store who triggered a release.

        ``allow_pending`` accepts a trigger without build number or queue id
        (the "Jenkins 发布触发通知" message); it is linked to the Jenkins build
        later by job + start time, or by the "Jenkins 发布通知" result message.
        """
        job_name=str(data.get("job_name","")).strip()
        if not job_name: raise ValueError("job_name is required")
        telegram_username=str(data.get("telegram_username","")).strip().lstrip("@")
        telegram_user_id=str(data.get("telegram_user_id","")).strip()
        trigger_name=str(data.get("trigger_name","")).strip()
        if not (telegram_user_id or telegram_username or trigger_name):
            raise ValueError("one of telegram_user_id, telegram_username or trigger_name is required")
        build_number=data.get("build_number")
        queue_id=data.get("queue_id")
        if build_number in (None,""):
            match=re.search(r"/(\d+)/?$",str(data.get("build_url", "")))
            build_number=int(match.group(1)) if match else None
        else: build_number=int(build_number)
        if queue_id in (None,""):
            match=re.search(r"/queue/item/(\d+)/?",str(data.get("queue_url", "")))
            queue_id=int(match.group(1)) if match else None
        else: queue_id=int(queue_id)
        if build_number is not None and build_number < 1: raise ValueError("build_number must be positive")
        if queue_id is not None and queue_id < 1: raise ValueError("queue_id must be positive")
        if build_number is None and queue_id is None and not allow_pending:
            raise ValueError("build_number or queue_id is required")
        triggered_at=str(data.get("triggered_at") or datetime.now(ZoneInfo(self.config.timezone)).isoformat())
        # Store UTC so text comparisons against UTC period bounds hold on SQLite too.
        parsed=parse_time(triggered_at)
        if parsed: triggered_at=parsed.astimezone(timezone.utc).isoformat()
        event_key={"job_name":job_name,"build_number":build_number,"queue_id":queue_id,
                   "telegram_user_id":telegram_user_id,"telegram_username":telegram_username,"triggered_at":triggered_at}
        event_id=str(data.get("event_id") or hashlib.sha256(json.dumps(event_key,sort_keys=True).encode()).hexdigest()[:32])
        meta=infer_job(job_name,str(data.get("build_url", "")))
        for key in ("service_type","service_name","namespace","environment"):
            if data.get(key): meta[key]=str(data[key])
        job_id=self.db.upsert_job(meta)
        identity=f"telegram:{telegram_user_id or telegram_username.lower() or trigger_name.lower()}"
        user_id=self.db.upsert_user(identity,trigger_name or telegram_username,telegram_username,telegram_user_id)
        event={**data,"event_id":event_id,"build_number":build_number,"queue_id":queue_id,"triggered_at":triggered_at,
               "raw_payload":json.dumps(data,ensure_ascii=False)}
        trigger_id,matched_build_id=self.db.save_trigger_event(job_id,user_id,event)
        if build_number is not None and matched_build_id is None:
            self.enqueue(job_name,build_number)
        elif build_number is None and queue_id is None:
            matched_build_id=self._match_recent_build(job_id,triggered_at)
        return {"accepted":True,"event_id":event_id,"trigger_id":trigger_id,"matched":matched_build_id is not None,
                "build_id":matched_build_id,"job_name":job_name,"build_number":build_number,"queue_id":queue_id,
                "trigger_name":trigger_name or telegram_username,"telegram_username":telegram_username}

    def _match_recent_build(self, job_id: int, triggered_at: str) -> int | None:
        """A pending trigger that arrives after its build was already collected (late message or history replay)."""
        when=parse_time(triggered_at)
        if when is None: return None
        rows=self.db.query("""SELECT id,build_number,queue_id,started_at FROM builds WHERE job_id=?
          AND COALESCE(trigger_source,'')!='TELEGRAM_PANEL'""",(job_id,))
        window=[r for r in rows for t in [parse_time(r["started_at"])]
                if t and when-self.db.PENDING_BEFORE <= t <= when+self.db.PENDING_AFTER]
        for row in sorted(window,key=lambda r: parse_time(r["started_at"])):
            if self.db.apply_trigger_event(job_id,row["id"],row["build_number"],row["queue_id"],row["started_at"]):
                return row["id"]
        return None

    def record_release_result(self, data: dict) -> dict:
        """Handle a "Jenkins 发布通知": link the pending trigger and, on failure, collect + diagnose the build."""
        if data.get("job_name_guessed"):
            data={**data,"job_name":self.resolve_job_name(data["job_name"],data.get("environment",""))}
        meta=infer_job(data["job_name"],str(data.get("build_url","")))
        if data.get("environment"): meta["environment"]=data["environment"]
        job_id=self.db.upsert_job(meta)
        self.db.save_release_result(job_id,data)
        linked=self.db.link_pending_trigger(job_id,data["build_number"],data.get("finished_at"))
        failed=data["result"] in {"FAILURE","UNSTABLE"}
        if failed or linked:
            self.enqueue(data["job_name"],data["build_number"])
        return {"job_name":data["job_name"],"build_number":data["build_number"],"result":data["result"],
                "trigger_linked":linked,"analysis_queued":failed or linked}

    def resolve_job_name(self, service: str, environment: str = "") -> str:
        """Map a service name from a release message to its Jenkins job (e.g. xgcash-next -> xgcash-next-testa)."""
        if not (self.config.jenkins_url and self.config.jenkins_token): return service
        now=time.time()
        if now-getattr(self,"_jobs_cached_at",0)>600:
            try: self._jobs_cache=[j["name"] for j in self.jenkins.jobs()]; self._jobs_cached_at=now
            except Exception:
                logging.warning("cannot list Jenkins jobs to resolve %s",service); return service
        names=set(getattr(self,"_jobs_cache",[]))
        aliases={"testa":("testa","test"),"uat":("uat",),"prod":("prod",)}.get(environment,(environment,) if environment else ())
        candidates=[service]+[f"{service}-{a}" for a in aliases]+[f"{a}-{service}" for a in aliases]
        exact=next((c for c in candidates if c in names),None)
        if exact: return exact
        # e.g. service "activity-rpc" in the UAT group -> job "uat-activity-rpc-prod"
        matches=[n for n in names if infer_job(n)["service_name"]==service and (not environment or infer_job(n)["environment"]==environment)]
        return matches[0] if len(matches)==1 else service

    def handle_update(self, update: dict) -> dict | None:
        chats=self.config.chat_environments()
        if self.config.management_chat_id: chats.setdefault(str(self.config.management_chat_id),"")
        events=parse_trigger_messages(update,self.config.timezone,chats)
        if events:
            results=[]
            for event in events:
                result=self.record_trigger(event,allow_pending=True)
                results.append(result)
                logging.info("Telegram trigger captured: %s #%s by @%s matched=%s%s",
                             result["job_name"],result.get("build_number") or "-",result.get("telegram_username"),result["matched"],
                             f" (batch {len(events)})" if event.get("trigger_mode")=="BATCH" else "")
            if len(results)==1 and events[0].get("trigger_mode")!="BATCH": return results[0]
            return {"accepted":True,"batch":True,"count":len(results),"matched":sum(1 for r in results if r["matched"]),
                    "trigger_name":results[0]["trigger_name"],"telegram_username":results[0]["telegram_username"],
                    "triggers":results}
        event=parse_release_result(update,self.config.timezone,chats)
        if event:
            result=self.record_release_result(event)
            logging.info("Telegram release result captured: %s #%s %s trigger_linked=%s",
                         result["job_name"],result["build_number"],result["result"],result["trigger_linked"])
            return result
        return None

    def telegram_backfill(self, data: dict) -> dict:
        if not self.user_listener.configured: raise ValueError("Telegram user account is not configured")
        if not self.user_listener.running: raise ValueError("Telegram user-account listener is not connected; check telegram-login and logs")
        days=int(data.get("days",14))
        if not 1 <= days <= 365: raise ValueError("days must be between 1 and 365")
        return self.user_listener.backfill(days)

    def record_release_message(self, data: dict) -> dict:
        """Same handling as a group message, for a release bot that forwards what it posts.

        Telegram does not deliver one bot's group messages to another bot, so
        when the release panel is a separate bot it should call this endpoint.
        """
        chat_id=str(data.get("chat_id","")).strip(); text=str(data.get("text","")).strip()
        if not chat_id or not text: raise ValueError("chat_id and text are required")
        message_id=data.get("message_id")
        if message_id in (None,""): raise ValueError("message_id is required")
        update={"update_id":0,"message":{"message_id":int(message_id),"chat":{"id":chat_id},"text":text,
                                         **({"date":int(data["date"])} if data.get("date") else {})}}
        result=self.handle_update(update)
        return {"accepted":True,"handled":result is not None,"result":result}

    def start_background(self):
        threading.Thread(target=self._worker,daemon=True,name="collector-worker").start()
        if self.config.jenkins_url and self.config.jenkins_user and self.config.jenkins_token:
            threading.Thread(target=self._poller,daemon=True,name="jenkins-poller").start()
        if self.config.telegram_token and (self.config.telegram_listen_group_messages or self.config.telegram_commands):
            threading.Thread(target=self._telegram_poller,daemon=True,name="telegram-group-poller").start()
        if self.user_listener.configured:
            threading.Thread(target=self.user_listener.run_forever,args=(self.stop,),daemon=True,name="telegram-user-listener").start()
        threading.Thread(target=self._scheduler,daemon=True,name="report-scheduler").start()

    def _telegram_poller(self):
        offset=0
        if self.config.telegram_commands:
            try: self.telegram.set_commands(BOT_COMMANDS); logging.info("Telegram commands enabled for @%s: /analyze",self.telegram.username() or "?")
            except Exception as exc: logging.warning("cannot register Telegram bot commands: %s",exc)
        while not self.stop.is_set():
            try:
                updates=self.telegram.updates(offset,self.config.telegram_poll_timeout)
                for update in updates:
                    offset=max(offset,int(update.get("update_id",0))+1)
                    try:
                        if self.config.telegram_commands and self.commands.handle(update): continue
                        if self.config.telegram_listen_group_messages: self.handle_update(update)
                    except Exception: logging.exception("cannot handle Telegram update %s",update.get("update_id"))
            except Exception:
                logging.exception("Telegram group polling failed; ensure this Bot token is not consumed by another getUpdates client")
                self.stop.wait(10)

    def _worker(self):
        while not self.stop.is_set():
            try: name,number=self.work.get(timeout=1)
            except queue.Empty: continue
            try:
                self.collector.collect_one(name,number)
                self._follow_up(name,number)
            except Exception:logging.exception("webhook collection failed")
            finally:self.work.task_done()

    def _poller(self):
        while not self.stop.is_set():
            try:self.collector.collect_all()
            except Exception:logging.exception("Jenkins polling failed")
            self.stop.wait(max(15,self.config.poll_seconds))

    def _scheduler(self):
        tz=ZoneInfo(self.config.timezone)
        while not self.stop.is_set():
            now=datetime.now(tz)
            schedules=[]
            scheduled=now.replace(hour=self.config.daily_hour,minute=self.config.daily_minute,second=0,microsecond=0)
            due=now>=scheduled
            if due: schedules.append(("daily",now.date().isoformat()))
            if due and now.weekday()==self.config.weekly_weekday: schedules.append(("weekly",f"{now:%G-W%V}"))
            if due and now.day==self.config.monthly_day: schedules.append(("monthly",f"{now:%Y-%m}"))
            for kind,key in schedules:
                if self.last_report.get(kind)==key: continue
                if self.send_reports(kind,key): self.last_report[kind]=key
            self.stop.wait(60)

    def send_reports(self,kind,period_key=""):
        all_ok=True
        for env in ("testa","uat","prod"):
            chat_id=self.config.chat_for(env)
            key=period_key or self.reports.period_key(kind)
            if self.db.report_was_sent(kind,env,key,chat_id): continue
            try:
                if self.telegram.send(chat_id,self.reports.text(kind,env)):
                    self.db.mark_report_sent(kind,env,key,chat_id)
                else: all_ok=False
            except Exception:
                all_ok=False;logging.exception("cannot send %s report to %s",kind,env)
        if self.config.management_chat_id:
            key=period_key or self.reports.period_key(kind)
            if not self.db.report_was_sent(kind,"all",key,self.config.management_chat_id):
                try:
                    if self.telegram.send(self.config.management_chat_id,self.reports.text(kind,"")):
                        self.db.mark_report_sent(kind,"all",key,self.config.management_chat_id)
                    else: all_ok=False
                except Exception:
                    all_ok=False;logging.exception("cannot send %s report to management",kind)
        return all_ok
