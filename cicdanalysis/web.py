from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .db import Database
from .openapi import VERSION, openapi_json, redoc_html, swagger_ui_html

log = logging.getLogger(__name__)

DASHBOARD = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cicdanalysis</title><style>:root{color-scheme:dark}body{font:14px system-ui;margin:0;background:#07111f;color:#dbeafe}.wrap{max-width:1200px;margin:auto;padding:28px}
h1{font-size:26px}.filters{display:flex;gap:18px;align-items:end;margin-bottom:16px}.notice{display:none;margin:0 0 16px;padding:12px 16px;border-radius:10px;background:#422006;border:1px solid #b45309;color:#fde68a}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}.card,.panel{background:#101e31;border:1px solid #24364d;border-radius:12px;padding:18px}.n{font-size:30px;font-weight:700;color:#5eead4}.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #24364d}select,input{padding:8px;background:#101e31;color:white;border:1px solid #38506d;border-radius:8px}@media(max-width:760px){.panels{grid-template-columns:1fr}.filters{align-items:stretch;flex-direction:column}}</style></head>
<body><div class="wrap"><h1>Jenkins Build Intelligence</h1><div class="filters"><label>报告日期 <input id="date" type="date"></label><label>环境 <select id="env"><option value="">全部</option><option>testa</option><option>uat</option><option>prod</option></select></label></div>
<div class="notice" id="triggerNotice"></div><div class="cards" id="cards"></div><div class="panels"><div class="panel"><h2>构建最多 Job</h2><table id="jobs"></table></div><div class="panel"><h2>触发人构建情况</h2><table id="users"></table></div><div class="panel"><h2>最近触发记录</h2><table id="triggers"></table></div><div class="panel"><h2>失败分类</h2><table id="failures"></table></div><div class="panel"><h2>重复故障</h2><table id="repeated"></table></div></div></div>
<script>const esc=x=>String(x??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));const di=document.querySelector('#date'),pad=x=>String(x).padStart(2,'0');let y=new Date();y.setDate(y.getDate()-1);di.value=y.getFullYear()+'-'+pad(y.getMonth()+1)+'-'+pad(y.getDate());async function load(){let e=document.querySelector('#env').value,d=di.value,range='date='+encodeURIComponent(d),[s,t]=await Promise.all([fetch('/api/v1/overview?'+range+'&environment='+encodeURIComponent(e)).then(x=>x.json()),fetch('/api/v1/triggers?'+range+'&limit=10&environment='+encodeURIComponent(e)).then(x=>x.json())]),c=s.counts||{};
document.querySelector('#cards').innerHTML=[['当日构建',s.total],['成功率',s.success_rate+'%'],['成功',c.SUCCESS||0],['失败',c.FAILURE||0],['实名触发',s.panel_triggered||0],['参与开发',s.developer_count||0],['待关联',s.unmatched_triggers||0],['平均耗时',Math.round(s.avg_duration/60)+' min']].map(x=>`<div class=card>${esc(x[0])}<div class=n>${esc(x[1])}</div></div>`).join('');
const notice=document.querySelector('#triggerNotice');if(s.trigger_identity_status==='NOT_CONNECTED'){notice.style.display='block';notice.textContent='⚠️ 本日期存在构建，但未收到任何真实触发人。请检查 Telegram 群消息监听、Bot 隐私模式及 getUpdates/webhook 冲突。';}else if(s.trigger_identity_status==='PARTIAL'){notice.style.display='block';notice.textContent='⚠️ 部分构建尚未关联触发人，请检查“最近触发记录”与构建地址。';}else{notice.style.display='none';notice.textContent='';}
const tab=(id,heads,rows)=>document.querySelector(id).innerHTML='<tr>'+heads.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+r.map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('');
tab('#jobs',['Job','构建','失败'],s.jobs.map(x=>[x.job_name,x.total,x.failures]));tab('#users',['触发人','构建','成功','失败','成功率'],s.users.map(x=>[x.name+(x.telegram_username?' (@'+x.telegram_username+')':''),x.total,x.success,x.failures,(x.success_rate||0)+'%']));tab('#triggers',['触发人','Job','构建号','关联'],t.map(x=>[x.trigger_name+(x.telegram_username?' (@'+x.telegram_username+')':''),x.job_name,x.build_number||'-',x.matched?'已关联':'待关联']));tab('#failures',['分类','次数'],s.failures.map(x=>[x.error_category,x.count]));tab('#repeated',['错误','次数'],s.repeated.map(x=>[x.error_code,x.count]));}document.querySelector('#env').onchange=load;di.onchange=load;load();</script></body></html>"""


def handler_factory(app):
    class Handler(BaseHTTPRequestHandler):
        def send_html(self, content, status=200):
            raw=content.encode("utf-8"); self.send_response(status)
            self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def send_json(self, data, status=200):
            raw=json.dumps(data,ensure_ascii=False,default=str).encode(); self.send_response(status)
            self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def query_int(self, q, name, default, minimum=1, maximum=365):
            try: value=int(q.get(name,[str(default)])[0])
            except (TypeError,ValueError): raise ValueError(f"invalid parameter: {name}")
            if value < minimum or value > maximum: raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return value
        def do_GET(self):
            p=urlparse(self.path); q=parse_qs(p.query)
            if p.path=="/healthz": return self.send_json({"status":"ok","version":VERSION})
            if p.path=="/readyz":
                ready=app.db.ping();return self.send_json({"status":"ok" if ready else "not_ready","version":VERSION},200 if ready else 503)
            if p.path=="/openapi.json":
                raw=openapi_json(); self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            if p.path=="/docs": return self.send_html(swagger_ui_html())
            if p.path=="/redoc": return self.send_html(redoc_html())
            if p.path=="/":
                return self.send_html(DASHBOARD)
            try:
                return self.route_api_get(p.path,q)
            except ValueError as exc:
                return self.send_json({"error":str(exc)},400)
        def route_api_get(self,path,q):
            if path=="/api/v1/overview":
                environment=q.get("environment",[""])[0]
                if q.get("date"): return self.send_json(app.reports.overview_date(q["date"][0],environment))
                return self.send_json(app.reports.overview_days(self.query_int(q,"days",7),environment))
            if path=="/api/v1/builds":
                where=["1=1"]; params=[]
                for key,col in (("result","b.result"),("environment","j.environment"),("trigger_user","u.jenkins_username")):
                    if q.get(key): where.append(col+"=?"); params.append(q[key][0])
                limit=self.query_int(q,"limit",100,1,500); params.append(limit)
                return self.send_json(app.db.query(f"""SELECT b.id,j.job_name,j.environment,b.build_number,b.result,b.branch,b.commit_sha,
                  b.duration_seconds,b.started_at,b.build_url,b.trigger_source,u.display_name trigger_user,u.telegram_username FROM builds b JOIN jobs j ON j.id=b.job_id
                  LEFT JOIN users u ON u.id=b.trigger_user_id WHERE {' AND '.join(where)} ORDER BY b.started_at DESC LIMIT ?""",params))
            if path=="/api/v1/users":
                d=self.query_int(q,"days",30)
                return self.send_json(app.db.query("""SELECT COALESCE(u.display_name,'自动触发') name,u.telegram_username,u.telegram_user_id,COUNT(*) total,
                  SUM(CASE WHEN b.result='SUCCESS' THEN 1 ELSE 0 END) success,SUM(CASE WHEN b.result='FAILURE' THEN 1 ELSE 0 END) failures,
                  SUM(CASE WHEN b.result='ABORTED' THEN 1 ELSE 0 END) aborted,SUM(CASE WHEN b.result='UNSTABLE' THEN 1 ELSE 0 END) unstable,
                  ROUND(100.0*SUM(CASE WHEN b.result='SUCCESS' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN b.result IN ('SUCCESS','FAILURE','UNSTABLE') THEN 1 ELSE 0 END),0),2) success_rate,ROUND(AVG(b.duration_seconds)) avg_duration,
                  COUNT(DISTINCT j.project_id) projects,COUNT(DISTINCT b.job_id) jobs FROM builds b JOIN jobs j ON j.id=b.job_id
                  LEFT JOIN users u ON u.id=b.trigger_user_id WHERE b.started_at>=? GROUP BY b.trigger_user_id,u.display_name,u.telegram_username,u.telegram_user_id ORDER BY total DESC""",((datetime.now(timezone.utc)-timedelta(days=d)).isoformat(),)))
            if path=="/api/v1/triggers":
                limit=self.query_int(q,"limit",100,1,500)
                if q.get("date"):
                    start,end=app.reports.date_bounds(q["date"][0]);where=["t.triggered_at>=?","t.triggered_at<?"];params=[start.isoformat(),end.isoformat()]
                else:
                    d=self.query_int(q,"days",7);where=["t.triggered_at>=?"];params=[(datetime.now(timezone.utc)-timedelta(days=d)).isoformat()]
                if q.get("environment"): where.append("COALESCE(NULLIF(t.environment,''),j.environment)=?"); params.append(q["environment"][0])
                params.append(limit)
                rows=app.db.query(f"""SELECT t.event_id,j.job_name,t.build_number,t.queue_id,t.branch,t.environment,t.namespace,
                  t.trigger_status,t.http_status,t.queue_url,t.build_url,t.triggered_at,u.display_name trigger_name,
                  u.telegram_username,u.telegram_user_id,t.matched_build_id,CASE WHEN t.matched_build_id IS NULL THEN 0 ELSE 1 END matched
                  FROM trigger_events t JOIN jobs j ON j.id=t.job_id JOIN users u ON u.id=t.trigger_user_id
                  WHERE {' AND '.join(where)} ORDER BY t.triggered_at DESC LIMIT ?""",params)
                return self.send_json(rows)
            if path=="/api/v1/failures":
                d=self.query_int(q,"days",30)
                return self.send_json(app.db.query("""SELECT f.error_category,f.error_code,f.failed_stage,f.responsibility_type,
                  f.error_fingerprint,COUNT(*) count FROM build_failures f JOIN builds b ON b.id=f.build_id
                  WHERE b.started_at>=? GROUP BY f.error_category,f.error_code,f.failed_stage,f.responsibility_type,f.error_fingerprint ORDER BY count DESC""",((datetime.now(timezone.utc)-timedelta(days=d)).isoformat(),)))
            if path=="/metrics":
                rows=app.db.query("SELECT result,COUNT(*) count FROM builds GROUP BY result")
                body=("# HELP cicdanalysis_builds_total Collected Jenkins builds\n# TYPE cicdanalysis_builds_total gauge\n"+"".join(f'cicdanalysis_builds_total{{result="{r["result"]}"}} {r["count"]}\n' for r in rows)).encode()
                self.send_response(200);self.send_header("Content-Type","text/plain; version=0.0.4");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body);return
            self.send_json({"error":"not found"},404)
        def do_POST(self):
            path=urlparse(self.path).path
            if path not in {"/api/v1/webhooks/jenkins","/api/v1/webhooks/trigger","/api/v1/webhooks/release-message","/api/v1/admin/telegram-backfill"}: return self.send_json({"error":"not found"},404)
            secret=app.config.webhook_secret if path.endswith(("/jenkins","/telegram-backfill")) else app.config.trigger_webhook_secret
            expected=f"Bearer {secret}"; supplied=self.headers.get("Authorization","")
            if not secret or not hmac.compare_digest(supplied,expected): return self.send_json({"error":"unauthorized"},401)
            try:
                size=int(self.headers.get("Content-Length","0"))
                if size > 65536: return self.send_json({"error":"request body too large"},413)
                data=json.loads(self.rfile.read(size))
                if path.endswith("/trigger"):
                    return self.send_json(app.record_trigger(data),202)
                if path.endswith("/telegram-backfill"):
                    return self.send_json(app.telegram_backfill(data),200)
                if path.endswith("/release-message"):
                    return self.send_json(app.record_release_message(data),202)
                name=str(data["job_name"]); number=int(data["build_number"])
                if not name.strip() or number < 1: raise ValueError("job_name must not be empty and build_number must be positive")
                app.enqueue(name,number); return self.send_json({"accepted":True,"job_name":name,"build_number":number},202)
            except (ValueError,KeyError,json.JSONDecodeError) as exc: return self.send_json({"error":str(exc)},400)
        def log_message(self, fmt, *args): log.info("http %s",fmt%args)
    return Handler


def serve(app):
    server=ThreadingHTTPServer((app.config.host,app.config.port),handler_factory(app)); log.info("listening on %s:%s",app.config.host,app.config.port); server.serve_forever()
