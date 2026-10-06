"""Bot commands used inside the release groups.

/analyze                 today's failed builds of this group's environment, most recent first
/analyze <job> [build]   full report of one build (latest failed build when no number is given)

Stored diagnoses are reused; only builds that have none yet are analyzed
(Jenkins log + model), at most COMMAND_LIMIT per command.
"""
from __future__ import annotations

import html
import logging
import re
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .telegram import failure_text

log = logging.getLogger(__name__)

COMMAND = re.compile(r"^/(analyze)(?:@(\w+))?(?:[ \t]+(.*))?$", re.I)
COMMAND_LIMIT = 5
FAILED = ("FAILURE", "UNSTABLE")
BOT_COMMANDS = [("analyze", "分析本群环境今日失败的构建（最近优先）；/analyze <job> [构建号] 查看完整报告")]


def parse_command(update: dict, bot_username: str = "") -> dict | None:
    message = next((update.get(k) for k in ("message", "edited_message") if update.get(k)), None)
    if not message or update.get("edited_message"): return None
    text = str(message.get("text") or "").strip().splitlines()
    match = COMMAND.match(text[0].strip()) if text else None
    if not match: return None
    mention = (match.group(2) or "").lower()
    if mention and bot_username and mention != bot_username.lower().lstrip("@"):
        return None  # addressed to another bot
    return {"command": match.group(1).lower(), "args": (match.group(3) or "").split(),
            "chat_id": str((message.get("chat") or {}).get("id", "")), "message_id": message.get("message_id"),
            "from": (message.get("from") or {}).get("username") or ""}


class GroupCommands:
    def __init__(self, app):
        self.app, self.db, self.config = app, app.db, app.config
        self.busy: set[str] = set()
        self.lock = threading.Lock()

    def chats(self) -> dict[str, str]:
        chats = dict(self.config.chat_environments())
        if self.config.management_chat_id: chats.setdefault(str(self.config.management_chat_id), "")
        return chats

    def handle(self, update: dict, background: bool = True) -> bool:
        """True when the update was a command for this bot (handled or refused)."""
        cmd = parse_command(update, self.app.telegram.username())
        if not cmd: return False
        if cmd["chat_id"] not in self.chats():
            log.info("ignoring /%s from chat %s (not a release/management group)", cmd["command"], cmd["chat_id"])
            return True
        with self.lock:
            if cmd["chat_id"] in self.busy:
                self.reply(cmd, "上一次 /analyze 还在分析中，请稍候。")
                return True
            self.busy.add(cmd["chat_id"])
        log.info("Telegram command /%s %s from @%s in %s", cmd["command"], " ".join(cmd["args"]), cmd["from"], cmd["chat_id"])
        if background:
            threading.Thread(target=self._run, args=(cmd,), daemon=True, name="telegram-command").start()
        else:
            self._run(cmd)
        return True

    def _run(self, cmd: dict) -> None:
        try:
            env = self.chats().get(cmd["chat_id"], "")
            if cmd["args"]: self.analyze_one(cmd, env)
            else: self.analyze_today(cmd, env)
        except Exception as exc:
            log.exception("command /%s failed", cmd["command"])
            self.reply(cmd, f"分析失败：{html.escape(str(exc))[:300]}")
        finally:
            with self.lock: self.busy.discard(cmd["chat_id"])

    def reply(self, cmd: dict, text: str) -> None:
        try: self.app.telegram.send(cmd["chat_id"], text, cmd.get("message_id"))
        except Exception: log.exception("cannot reply to command in %s", cmd["chat_id"])

    # -- data ---------------------------------------------------------------------------
    def _today_start(self) -> str:
        tz = ZoneInfo(self.config.timezone)
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(timezone.utc).isoformat()

    def failed_today(self, env: str) -> list[dict]:
        """Latest failed build per job today, most recent first."""
        sql = """SELECT b.id,b.job_id,j.job_name,j.environment,b.build_number,b.result,
                   COALESCE(b.finished_at,b.started_at) AS happened_at,
                   (SELECT COUNT(*) FROM build_failures f WHERE f.build_id=b.id) analyzed
                 FROM builds b JOIN jobs j ON j.id=b.job_id
                 WHERE b.result IN (?,?) AND b.building=0 AND COALESCE(b.finished_at,b.started_at) >= ?"""
        params = [*FAILED, self._today_start()]
        if env: sql += " AND j.environment=?"; params.append(env)
        rows = self.db.query(sql + " ORDER BY COALESCE(b.finished_at,b.started_at) DESC, b.id DESC", tuple(params))
        seen, out = set(), []
        for r in rows:
            if r["job_id"] in seen: continue
            seen.add(r["job_id"]); out.append(r)
        return out

    def later_success(self, job_id: int, number: int) -> int | None:
        rows = self.db.query("SELECT MAX(build_number) n FROM builds WHERE job_id=? AND build_number>? AND result='SUCCESS'", (job_id, number))
        return rows[0]["n"] if rows and rows[0]["n"] else None

    def ensure_analyzed(self, job_name: str, number: int) -> None:
        self.app.collector.reanalyze(job_name, number)

    def report(self, build_id: int) -> dict | None:
        return self.app.telegram.failure_row(build_id)

    # -- /analyze ---------------------------------------------------------------------------
    def analyze_today(self, cmd: dict, env: str) -> None:
        label = env.upper() if env else "全部环境"
        builds = self.failed_today(env)
        if not builds:
            self.reply(cmd, f"✅ 今日 {label} 暂无失败的构建。")
            return
        shown = builds[:COMMAND_LIMIT]
        pending = [b for b in shown if not b["analyzed"]]
        if pending:
            self.reply(cmd, f"🔎 今日 {label} 有 {len(builds)} 个 Job 构建失败，正在分析其中 {len(pending)} 个尚未分析的构建…")
            for b in pending:
                try: self.ensure_analyzed(b["job_name"], b["build_number"])
                except Exception as exc: log.warning("cannot analyze %s #%s: %s", b["job_name"], b["build_number"], exc)
        tz = ZoneInfo(self.config.timezone)
        lines = [f"🔎 <b>今日 {html.escape(label)} 发布失败</b>（最近优先，共 {len(builds)} 个 Job"
                 + (f"，显示最近 {len(shown)} 个" if len(builds) > len(shown) else "") + "）", ""]
        first = None
        for i, b in enumerate(shown, 1):
            r = self.report(b["id"])
            at = _local_time(b["happened_at"], tz)
            head = f"{i}. <b>{html.escape(b['job_name'])}</b> #{b['build_number']} · {at}"
            if r and (r.get("display_name") or r.get("telegram_username")):
                head += f" · {html.escape(r.get('display_name') or '@' + r['telegram_username'])}"
            fixed = self.later_success(b["job_id"], b["build_number"])
            if fixed: head += f" · ✅ 已恢复（#{fixed} 成功）"
            lines.append(head)
            if r:
                first = first or r
                cause = (r.get("root_cause") or "").strip()
                lines.append(f"　{html.escape(r.get('error_category') or '-')} · {html.escape(cause[:160] + ('…' if len(cause) > 160 else ''))}")
            else:
                lines.append("　暂无分析结果（Jenkins 日志读取失败）")
        lines += ["", "发送 /analyze &lt;job&gt; [构建号] 查看完整报告"]
        self.reply(cmd, "\n".join(lines))
        if first:  # the most recent failure in full
            self.reply(cmd, failure_text(first))
            self.mark_reported(first, cmd)

    def analyze_one(self, cmd: dict, env: str) -> None:
        name = cmd["args"][0]
        number = int(cmd["args"][1]) if len(cmd["args"]) > 1 and cmd["args"][1].lstrip("#").isdigit() else None
        if number is None and len(cmd["args"]) > 1:
            self.reply(cmd, "用法：/analyze &lt;job&gt; [构建号]"); return
        name = self._job_name(name, env)
        if not name:
            self.reply(cmd, f"找不到 Job：{html.escape(cmd['args'][0])}"); return
        if number is None:
            rows = self.db.query("""SELECT b.build_number FROM builds b JOIN jobs j ON j.id=b.job_id
                WHERE j.job_name=? AND b.result IN (?,?) AND b.building=0 ORDER BY b.build_number DESC LIMIT 1""", (name, *FAILED))
            if not rows:
                self.reply(cmd, f"{html.escape(name)} 最近没有失败的构建。"); return
            number = rows[0]["build_number"]
        rows = self.db.query("""SELECT b.id,(SELECT COUNT(*) FROM build_failures f WHERE f.build_id=b.id) analyzed
            FROM builds b JOIN jobs j ON j.id=b.job_id WHERE j.job_name=? AND b.build_number=?""", (name, number))
        if not rows or not rows[0]["analyzed"]:
            self.reply(cmd, f"🔎 正在分析 {html.escape(name)} #{number}…")
            self.ensure_analyzed(name, number)
            rows = self.db.query("SELECT b.id FROM builds b JOIN jobs j ON j.id=b.job_id WHERE j.job_name=? AND b.build_number=?", (name, number))
        r = self.report(rows[0]["id"]) if rows else None
        self.reply(cmd, failure_text(r) if r else f"{html.escape(name)} #{number} 没有失败分析（可能构建成功或仍在运行）。")
        if r: self.mark_reported(r, cmd, rows[0]["id"])

    def mark_reported(self, r: dict, cmd: dict, build_id: int | None = None) -> None:
        """A full report posted by /analyze in the build's own group counts as its one report."""
        try:
            build_id = build_id or self.db.query("SELECT id FROM builds WHERE job_id=? AND build_number=?",
                                                 (r["job_id"], r["build_number"]))[0]["id"]
            group, _ = self.app.telegram.failure_target(r)
            if group and str(group) == str(cmd["chat_id"]):
                self.db.claim_failure_notification(build_id, cmd["chat_id"], "COMMAND")
        except Exception:
            log.debug("cannot mark /analyze report", exc_info=True)

    def _job_name(self, name: str, env: str) -> str:
        """A job known to this service; a bare service name is resolved within the group's environment."""
        def known(n): return bool(self.db.query("SELECT 1 FROM jobs WHERE job_name=?", (n,)))
        if known(name): return name
        resolved = self.app.resolve_job_name(name, env)
        if resolved != name or known(resolved): return resolved
        rows = self.db.query("SELECT job_name FROM jobs WHERE job_name LIKE ?" + (" AND environment=?" if env else ""),
                             (f"%{name}%", env) if env else (f"%{name}%",))
        return rows[0]["job_name"] if len(rows) == 1 else ""


def _local_time(value, tz) -> str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(tz).strftime("%H:%M")
    except (TypeError, ValueError):
        return "-"
