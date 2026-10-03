from __future__ import annotations

import base64
import html
import json
import re
import ssl
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class JenkinsClient:
    def __init__(self, url: str, user: str, token: str, verify_tls: bool = True, timeout: int = 30):
        self.url, self.timeout = url.rstrip("/"), timeout
        self.auth = base64.b64encode(f"{user}:{token}".encode()).decode()
        self.context = None if verify_tls else ssl._create_unverified_context()

    def _get(self, url: str, raw=False):
        req = Request(url, headers={"Authorization": f"Basic {self.auth}", "Accept": "application/json"})
        with urlopen(req, timeout=self.timeout, context=self.context) as response:
            data = response.read()
        return data if raw else json.loads(data)

    def jobs(self) -> list[dict]:
        data = self._get(f"{self.url}/api/json?tree=jobs[name,fullName,url,_class,jobs[name,fullName,url,_class,jobs[name,fullName,url,_class]]]")
        out = []
        def walk(items):
            for item in items or []:
                if item.get("jobs") is not None or "Folder" in item.get("_class", ""):
                    walk(item.get("jobs", []))
                else:
                    item["name"] = item.get("fullName") or item["name"]
                    out.append(item)
        walk(data.get("jobs", []))
        return out

    def job_builds(self, job_url: str, limit: int) -> list[int]:
        data = self._get(f"{job_url.rstrip('/')}/api/json?tree=builds[number]{{0,{limit}}}")
        return [b["number"] for b in data.get("builds", [])]

    def build(self, job_url: str, number: int) -> dict:
        tree = "number,url,queueId,timestamp,duration,result,building,builtOn,displayName,description,actions[*],changeSet[items[commitId,msg,author[fullName]]],changeSets[items[commitId,msg,author[fullName]]]"
        return self._get(f"{job_url.rstrip('/')}/{number}/api/json?tree={tree}")

    def console(self, job_url: str, number: int, max_bytes: int) -> str:
        data = self._get(f"{job_url.rstrip('/')}/{number}/consoleText", raw=True)
        return data[-max_bytes:].decode("utf-8", errors="replace")

    def console_head(self, job_url: str, number: int, max_bytes: int = 65536) -> str:
        url=f"{job_url.rstrip('/')}/{number}/consoleText"
        req=Request(url,headers={"Authorization":f"Basic {self.auth}","Accept":"text/plain"})
        with urlopen(req,timeout=self.timeout,context=self.context) as response:
            return response.read(max_bytes).decode("utf-8",errors="replace")

    def stages(self, job_url: str, number: int) -> list[dict]:
        try:
            data = self._get(f"{job_url.rstrip('/')}/{number}/wfapi/describe")
        except HTTPError as exc:
            if exc.code in {404, 405}:
                return []
            raise
        out = []
        for s in data.get("stages", []):
            started = datetime.fromtimestamp(s.get("startTimeMillis", 0) / 1000, timezone.utc).isoformat() if s.get("startTimeMillis") else None
            out.append({"name": s.get("name", "unknown"), "status": s.get("status"), "started_at": started,
                        "duration_seconds": s.get("durationMillis", 0) / 1000, "id": s.get("id"),
                        "error": (s.get("error") or {}).get("message", "")})
        return out

    def failed_step_logs(self, job_url: str, number: int, stages: list[dict], max_chars: int = 60000) -> str:
        """Logs of the failed steps only, via the Pipeline REST API.

        consoleText mixes every stage and parallel branch; the failed step's own
        log is where the real error is. Returns "" for freestyle jobs.
        """
        base = f"{job_url.rstrip('/')}/{number}/execution/node"
        parts = []
        for stage in stages:
            if stage.get("status") not in {"FAILED", "FAILURE", "UNSTABLE"} or not stage.get("id"):
                continue
            try:
                nodes = self._get(f"{base}/{stage['id']}/wfapi/describe").get("stageFlowNodes") or []
            except (HTTPError, ValueError):
                continue
            for node in nodes:
                if node.get("status") not in {"FAILED", "FAILURE", "UNSTABLE"}:
                    continue
                try:
                    text = self._get(f"{base}/{node['id']}/wfapi/log").get("text") or ""
                except (HTTPError, ValueError):
                    text = ""
                error = (node.get("error") or {}).get("message", "")
                header = f"===== stage: {stage.get('name')} / step: {node.get('name')} {node.get('parameterDescription') or ''}".rstrip()
                parts.append("\n".join(x for x in (header, strip_html(text), f"ERROR: {error}" if error else "") if x))
        return "\n".join(parts)[-max_chars:]

    def job_url_for(self, name: str) -> str:
        """URL of a job given the name Jenkins prints ("folder/job" or "folder » job")."""
        parts = [p.strip() for p in re.split(r"\s*(?:/|»)\s*", name) if p.strip()]
        return self.url + "".join(f"/job/{quote(p, safe='')}" for p in parts) + "/"


DOWNSTREAM = re.compile(r"(?m)^\s*(?:Build\s+)?(\S.*?) #(\d+) completed(?: with status|:)\s*(FAILURE|UNSTABLE|ABORTED)")


def failed_downstream(console: str) -> list[tuple[str, int]]:
    """Downstream builds that failed ("build job:" step or Parameterized Trigger)."""
    seen = []
    for match in DOWNSTREAM.finditer(console or ""):
        item = (match.group(1).strip(), int(match.group(2)))
        if item not in seen: seen.append(item)
    return seen


def strip_html(text: str) -> str:
    """wfapi/log returns console annotations as HTML."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def infer_job(job_name: str, job_url: str = "") -> dict:
    leaf = job_name.split("/")[-1]
    parts = leaf.split("-")
    env_aliases = {"testa", "testb", "testc", "test", "uat", "prod", "production"}
    # "uat-activity-rpc-prod" is the UAT copy of a prod job: a leading
    # environment prefix wins over the suffix.
    first = parts[0].lower() if parts else ""
    environment = first if first in env_aliases else next((p.lower() for p in reversed(parts) if p.lower() in env_aliases), "unknown")
    if environment == "production": environment = "prod"
    project = parts[0] if parts else leaf
    service_name = "-".join(p for p in parts if p.lower() not in env_aliases)
    service_type = "web" if any(x in leaf.lower() for x in ("admin", "web", "next", "frontend")) else "backend"
    return {"job_name": job_name, "job_url": job_url, "project": project, "service_name": service_name,
            "service_type": service_type, "environment": environment, "namespace": environment}


# Build causes that really start a build without a person behind it.
AUTOMATIC_SOURCES = ("TIMER", "SCM", "UPSTREAM")


def cause_source(causes: list[dict]) -> str:
    """Classify a build started without a user cause.

    Release bots usually call buildWithParameters (RemoteCause / API token), which is a
    person's release, not an automatic build; only timers, SCM/webhook pushes and
    upstream jobs count as automatic.
    """
    text = " ".join(f"{c.get('_class', '')} {c.get('shortDescription', '')}" for c in causes).lower()
    if "timer" in text: return "TIMER"
    if "remote" in text: return "REMOTE"
    if any(k in text for k in ("scm", "push", "branch indexing", "branchevent", "gitlab", "github", "webhook", "gerrit")): return "SCM"
    return "OTHER"


def parse_build(data: dict) -> dict:
    actions = data.get("actions") or []
    params, causes = {}, []
    for a in actions:
        for p in a.get("parameters") or []: params[p.get("name", "")] = p.get("value")
        causes.extend(a.get("causes") or [])
    user_cause = next((c for c in causes if c.get("userId") or c.get("userName") or "started by user" in str(c.get("shortDescription","")).lower()), {})
    upstream = next((c for c in causes if c.get("upstreamProject")), {})
    trigger_source = "USER" if user_cause else "UPSTREAM" if upstream else cause_source(causes) if causes else "UNKNOWN"
    description=str(user_cause.get("shortDescription", ""))
    described_user=re.sub(r"^Started by user\s+","",description,flags=re.I).strip() if description else ""
    user = user_cause.get("userId") or user_cause.get("userName") or described_user
    telegram = ""
    name_match = re.match(r"(.+?)\s*\(@([^\)]+)\)$", user_cause.get("userName", "") or described_user)
    if name_match:
        user, telegram = name_match.group(1).strip(), name_match.group(2)
    normalized={str(k).upper():v for k,v in params.items()}
    def param(*names):
        for name in names:
            value=normalized.get(name.upper())
            if value not in (None,""): return str(value).strip()
        return ""
    parameter_telegram=param("TELEGRAM_USERNAME","TG_USERNAME","TRIGGER_TELEGRAM_USERNAME","TELEGRAM_USER") .lstrip("@")
    parameter_telegram_id=param("TELEGRAM_USER_ID","TG_USER_ID","TRIGGER_TELEGRAM_USER_ID")
    parameter_display=param("TRIGGER_USER_NAME","TRIGGER_USER","BUILD_USER","RELEASE_USER","OPERATOR_NAME","OPERATOR")
    parameter_username=param("TRIGGER_USERNAME","BUILD_USER_ID","RELEASE_USERNAME","OPERATOR_USERNAME")
    if parameter_telegram or parameter_telegram_id or parameter_display or parameter_username:
        telegram=parameter_telegram or telegram
        user=parameter_username or parameter_display or user
        trigger_source="TELEGRAM_PANEL"
    change_sets = data.get("changeSets") or ([data.get("changeSet")] if data.get("changeSet") else [])
    items = [i for cs in change_sets for i in (cs or {}).get("items", [])]
    commit = items[-1] if items else {}
    timestamp = data.get("timestamp", 0) / 1000
    duration = data.get("duration", 0) / 1000
    start = datetime.fromtimestamp(timestamp, timezone.utc) if timestamp else None
    building = bool(data.get("building"))
    result = data.get("result") or ("RUNNING" if building else "UNKNOWN")
    return {"build_number": data["number"], "build_url": data.get("url"), "queue_id": data.get("queueId"),
            "trigger_username": user, "trigger_display": parameter_display or user_cause.get("userName", user) or described_user,
            "telegram_username": telegram,"telegram_user_id":parameter_telegram_id,
            "trigger_source": trigger_source, "branch": params.get("BRANCH_NAME") or params.get("GIT_BRANCH") or params.get("branch") or "",
            "commit_sha": commit.get("commitId") or params.get("GIT_COMMIT") or "", "commit_message": commit.get("msg", ""),
            "agent_name": data.get("builtOn") or "", "image_ref": params.get("IMAGE") or params.get("IMAGE_REF") or "",
            "started_at": start.isoformat() if start else None,
            "finished_at": (start + timedelta(seconds=duration)).isoformat() if start and not building else None,
            "duration_seconds": duration, "result": result, "building": building,
            "raw_summary": json.dumps({"displayName": data.get("displayName"), "description": data.get("description")}, ensure_ascii=False)}


def parse_console_trigger(text: str) -> dict:
    """Extract a human trigger identity from the beginning of a Jenkins log."""
    clean=re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]","",text or "")
    telegram="";telegram_id="";display="";username="";source="UNKNOWN"
    for line in clean.splitlines():
        line=line.strip()
        match=re.search(r"触发人\s*[:：]\s*(.+?)(?:\s*\(@([^\)]+)\))?$",line)
        if match:
            display=match.group(1).strip();telegram=(match.group(2) or "").strip().lstrip("@");source="TELEGRAM_PANEL";break
        match=re.search(r"Started by user\s+(.+?)(?:\s*\(@([^\)]+)\))?$",line,re.I)
        if match:
            display=match.group(1).strip();telegram=(match.group(2) or "").strip().lstrip("@");username=display;source="USER";break
    variables={}
    for match in re.finditer(r"(?m)^\s*(TELEGRAM_USERNAME|TG_USERNAME|TELEGRAM_USER_ID|TG_USER_ID|TRIGGER_USER_NAME|TRIGGER_USER|TRIGGER_USERNAME|BUILD_USER|BUILD_USER_ID)\s*[=:]\s*([^\r\n]+)",clean,re.I):
        variables[match.group(1).upper()]=match.group(2).strip().strip("'\"")
    telegram=variables.get("TELEGRAM_USERNAME") or variables.get("TG_USERNAME") or telegram
    telegram_id=variables.get("TELEGRAM_USER_ID") or variables.get("TG_USER_ID") or telegram_id
    display=variables.get("TRIGGER_USER_NAME") or variables.get("TRIGGER_USER") or variables.get("BUILD_USER") or display
    username=variables.get("TRIGGER_USERNAME") or variables.get("BUILD_USER_ID") or username or display
    if variables: source="TELEGRAM_PANEL" if telegram or telegram_id or "TRIGGER_USER" in variables else source
    return {"trigger_username":username,"trigger_display":display or username,"telegram_username":telegram.lstrip("@"),
            "telegram_user_id":telegram_id,"trigger_source":source}
