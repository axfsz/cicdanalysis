from __future__ import annotations

import argparse
import json
import re
import sqlite3
import logging

from .app import App
from .config import Config
from .db import Database
from .web import serve


def backfill(cfg, listener, days):
    """Ask the running service to replay history on its own Telegram connection.

    The session file can only be open in one process ("database is locked"), so
    `docker compose exec ... telegram-backfill` goes through the local API; a
    direct connection is used only when the service is not running.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
    req=Request(f"http://127.0.0.1:{cfg.port}/api/v1/admin/telegram-backfill",data=json.dumps({"days":days}).encode(),
                headers={"Authorization":f"Bearer {cfg.webhook_secret}","Content-Type":"application/json"},method="POST")
    try:
        with urlopen(req,timeout=900) as response: return json.loads(response.read())
    except HTTPError as exc:
        raise SystemExit(f"backfill failed: HTTP {exc.code} {exc.read().decode(errors='replace')}")
    except URLError:
        pass  # service not running here: use the session directly
    try:
        return listener.backfill(days)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc): raise
        raise SystemExit("Telegram session is in use by another process; run this inside the running service container "
                         "(docker compose exec cicdanalysis ...) or stop the service first")


def send_report(app, cfg, job, number, target, dry_run):
    """analyze --send/--dry-run: post (or preview) the stored diagnosis of one build."""
    rows=app.db.query("SELECT b.id FROM builds b JOIN jobs j ON j.id=b.job_id WHERE j.job_name=? AND b.build_number=?",(job,number))
    if not rows: raise SystemExit(f"{job} #{number} is not in the database")
    if target=="management":
        if not cfg.management_chat_id: raise SystemExit("TELEGRAM_MANAGEMENT_CHAT_ID is empty")
        target=cfg.management_chat_id
    elif target=="group": target=""
    elif not re.fullmatch(r"-?\d+",target): raise SystemExit("--send expects group, management or a numeric chat id")
    try: out=app.telegram.send_failure(rows[0]["id"],target,dry_run)
    except ValueError as exc: raise SystemExit(str(exc))
    head=f"chat_id={out['chat_id']} reply_to={out['reply_to'] or '-'}"
    if dry_run: return f"\n----- 预览（未发送）{head} -----\n{out['text']}"
    return f"\n{'已发送' if out['sent'] else '发送失败'}：{head}"


def main():
    parser=argparse.ArgumentParser(prog="cicdanalysis"); parser.add_argument("command",choices=["serve","init-db","collect-once","report","telegram-login","telegram-backfill","analyze"]);parser.add_argument("--kind",choices=["daily","weekly","monthly"],default="daily");parser.add_argument("--environment",default="");parser.add_argument("--date",help="daily report date, YYYY-MM-DD");parser.add_argument("--days",type=int,default=14,help="telegram-backfill: days of group history to replay");parser.add_argument("--job",help="analyze: Jenkins job name");parser.add_argument("--build",type=int,help="analyze: build number");parser.add_argument("--send",metavar="TARGET",help="analyze: post the report to 'group' (the build's release group), 'management', or a chat id");parser.add_argument("--dry-run",action="store_true",help="analyze --send: print the Telegram message instead of sending it")
    args=parser.parse_args(); cfg=Config(); logging.basicConfig(level=getattr(logging,cfg.log_level.upper(),logging.INFO),format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command=="init-db": Database(cfg.database_url or cfg.database_path).init(); print("database initialized");return
    app=App(cfg)
    if args.command in {"telegram-login","telegram-backfill"}:
        from .userbot import UserAccountListener
        listener=UserAccountListener(app)
        if not listener.configured: raise SystemExit("set TELEGRAM_USER_API_ID and TELEGRAM_USER_API_HASH first")
        if args.command=="telegram-login": listener.login()
        else: print(backfill(cfg,listener,args.days))
        return
    if args.command=="analyze":
        if not args.job or not args.build: raise SystemExit("usage: analyze --job <job name> --build <number>")
        result=app.collector.reanalyze(args.job,args.build)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.send or args.dry_run: print(send_report(app,cfg,args.job,args.build,args.send or "group",args.dry_run))
        return
    if args.command=="collect-once": print(app.collector.collect_all());return
    if args.command=="report": print(app.reports.text_for_date(args.date,args.environment) if args.date else app.reports.text(args.kind,args.environment));return
    if not cfg.jenkins_url or not cfg.jenkins_user or not cfg.jenkins_token: logging.warning("Jenkins configuration is incomplete; API remains available")
    app.start_background(); serve(app)

if __name__=="__main__": main()
