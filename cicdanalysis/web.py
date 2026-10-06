from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .db import Database
from .reports import USER_LABEL
from .openapi import VERSION, openapi_json, redoc_html, swagger_ui_html

log = logging.getLogger(__name__)

DASHBOARD = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cicdanalysis</title><style>:root{color-scheme:dark}body{font:14px system-ui;margin:0;background:#07111f;color:#dbeafe}.wrap{max-width:1200px;margin:auto;padding:28px}
h1{font-size:26px}h2{font-size:18px;margin:0 0 12px}.filters{display:flex;gap:18px;align-items:end;margin-bottom:16px}.notice{display:none;margin:0 0 16px;padding:12px 16px;border-radius:10px;background:#422006;border:1px solid #b45309;color:#fde68a}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}.card,.panel{background:#101e31;border:1px solid #24364d;border-radius:12px;padding:18px}.n{font-size:30px;font-weight:700;color:#5eead4}.sub{color:#8ba3c0;font-size:12px;margin-top:4px}.card.warn .n{color:#fbbf24}.card.muted .n{color:#94a3b8}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}.wide{grid-column:1/-1}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #24364d;vertical-align:top}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid #38506d;color:#cbd5e1;white-space:nowrap}.tag.ok{border-color:#0f766e;color:#5eead4}.tag.warn{border-color:#b45309;color:#fbbf24}.tag.bad{border-color:#b91c1c;color:#fca5a5}.tag.batch{border-color:#6d28d9;color:#c4b5fd}
.auto{color:#94a3b8}.unknown{color:#fbbf24}.cause{color:#8ba3c0;font-size:12px;max-width:420px}a{color:#7dd3fc}
select,input{padding:8px;background:#101e31;color:white;border:1px solid #38506d;border-radius:8px}@media(max-width:760px){.panels{grid-template-columns:1fr}.filters{align-items:stretch;flex-direction:column}}</style></head>
<body><div class="wrap"><h1>Jenkins Build Intelligence</h1><div class="filters"><label>报告日期 <input id="date" type="date"></label><label>环境 <select id="env"><option value="">全部</option><option>testa</option><option>uat</option><option>prod</option></select></label></div>
<div class="notice" id="triggerNotice"></div><div class="cards" id="cards"></div>
<div class="panels"><div class="panel wide"><h2>失败自动分析</h2><div class="sub" id="analysisSummary"></div><div class="scroll"><table id="analysis"></table></div></div>
<div class="panel"><h2>构建最多 Job</h2><table id="jobs"></table></div><div class="panel"><h2>触发人构建情况</h2><table id="users"></table></div><div class="panel"><h2>最近触发记录</h2><div class="scroll"><table id="triggers"></table></div></div><div class="panel"><h2>失败分类</h2><table id="failures"></table></div><div class="panel"><h2>重复故障</h2><table id="repeated"></table></div></div></div>
<script>const esc=x=>String(x??'').replace(/[&<>\\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\\"':'&quot;',"'":'&#39;'}[c]));const di=document.querySelector('#date'),pad=x=>String(x).padStart(2,'0');let y=new Date();y.setDate(y.getDate()-1);di.value=y.getFullYear()+'-'+pad(y.getMonth()+1)+'-'+pad(y.getDate());
const who=(n,u)=>esc(n)+(u?' (@'+esc(u)+')':''),hm=v=>{if(!v)return '-';const d=new Date(v);return isNaN(d)?'-':pad(d.getHours())+':'+pad(d.getMinutes())},secs=v=>v==null?'':(v<60?v+'s':Math.floor(v/60)+'m'+pad(v%60)+'s');
const tab=(id,heads,rows)=>document.querySelector(id).innerHTML='<tr>'+heads.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr>'+(rows.length?rows.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join(''):'<tr><td colspan="'+heads.length+'" class="auto">暂无数据</td></tr>');
function report(x){if(!x.reported)return '<span class="tag warn">未推送</span>';if(x.report_source==='AUTO')return '<span class="tag ok">自动推送</span>'+(x.latency_seconds!=null?' <span class="auto">'+secs(x.latency_seconds)+'</span>':'');return '<span class="tag">'+(x.report_source==='COMMAND'?'/analyze':'手动补发')+'</span>';}
async function load(){let e=document.querySelector('#env').value,d=di.value,range='date='+encodeURIComponent(d),[s,t]=await Promise.all([fetch('/api/v1/overview?'+range+'&environment='+encodeURIComponent(e)).then(x=>x.json()),fetch('/api/v1/triggers?'+range+'&limit=12&environment='+encodeURIComponent(e)).then(x=>x.json())]),c=s.counts||{},a=s.analysis||{};
const cards=[['当日构建',s.total],['成功率',s.success_rate+'%'],['成功',c.SUCCESS||0],['失败',(c.FAILURE||0)+(c.UNSTABLE||0),c.UNSTABLE?'含不稳定 '+c.UNSTABLE:''],['实名触发',s.panel_triggered||0,'参与开发 '+(s.developer_count||0)+' 人'],['批量发布',s.batch_builds||0,(s.batch_messages||0)+' 条批量指令'],['自动触发',s.automatic||0,'定时 / SCM / 上游','muted'],['未识别触发人',s.unattributed||0,'',(s.unattributed?'warn':'')],['待关联',s.unmatched_triggers||0,'',(s.unmatched_triggers?'warn':'')],['失败自动推送',(a.auto_reported||0)+' / '+(a.failed||0),a.avg_latency_seconds!=null?'平均延迟 '+secs(a.avg_latency_seconds):'',(a.unreported?'warn':'')],['平均耗时',Math.round(s.avg_duration/60)+' min']];
document.querySelector('#cards').innerHTML=cards.map(x=>`<div class="card ${x[3]||''}">${esc(x[0])}<div class=n>${esc(x[1])}</div>${x[2]?'<div class=sub>'+esc(x[2])+'</div>':''}</div>`).join('');
const notice=document.querySelector('#triggerNotice');if(s.trigger_identity_status==='NOT_CONNECTED'){notice.style.display='block';notice.textContent='⚠️ 本日期存在构建，但未收到任何真实触发人。请检查 Telegram 群消息监听、Bot 隐私模式及 getUpdates/webhook 冲突。';}else if(s.trigger_identity_status==='PARTIAL'){notice.style.display='block';notice.textContent='⚠️ 有 '+(s.unattributed||0)+' 个构建未识别触发人（不含定时/SCM/上游等自动构建），请检查“最近触发记录”与构建地址。';}else{notice.style.display='none';notice.textContent='';}
document.querySelector('#analysisSummary').textContent=a.failed?`失败 ${a.failed} 个 · 已分析 ${a.analyzed}（大模型 ${a.llm}）· 自动推送 ${a.auto_reported} · 手动 ${a.manual_reported} · 未推送 ${a.unreported}`:'';
tab('#analysis',['时间','Job','触发人','分类 / 根因','推送'],(a.recent||[]).map(x=>[hm(x.finished_at),(x.build_url?'<a href="'+esc(x.build_url)+'" target=_blank rel=noopener>':'')+esc(x.job_name)+' #'+esc(x.build_number)+(x.build_url?'</a>':''),who(x.trigger_name,x.telegram_username),x.error_category?esc(x.error_category)+'<div class=cause>'+esc((x.root_cause||'').slice(0,140))+'</div>':'<span class="tag warn">分析中</span>',report(x)]));
tab('#jobs',['Job','构建','失败'],s.jobs.map(x=>[esc(x.job_name),esc(x.total),esc(x.failures)]));
tab('#users',['触发人','构建','批量','成功','失败','成功率'],s.users.map(x=>[x.kind==='user'?who(x.name,x.telegram_username):'<span class="'+(x.kind==='auto'?'auto':'unknown')+'">'+esc(x.name)+'</span>',esc(x.total),esc(x.batch||0),esc(x.success),esc(x.failures),esc((x.success_rate||0)+'%')]));
tab('#triggers',['时间','触发人','Job','方式','构建号','关联'],t.map(x=>[hm(x.triggered_at),who(x.trigger_name,x.telegram_username),esc(x.job_name),x.trigger_mode==='BATCH'?'<span class="tag batch">批量 '+esc(x.batch_size||'')+'</span>':'单个',esc(x.build_number||'-'),x.matched?'<span class="tag ok">已关联</span>':'<span class="tag warn">待关联</span>']));
tab('#failures',['分类','次数'],s.failures.map(x=>[esc(x.error_category),esc(x.count)]));tab('#repeated',['错误','次数'],s.repeated.map(x=>[esc(x.error_code),esc(x.count)]));}
document.querySelector('#env').onchange=load;di.onchange=load;load();setInterval(()=>{if(!document.hidden)load()},60000);</script></body></html>"""


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
                return self.send_json(app.db.query(f"""SELECT {USER_LABEL} name,u.telegram_username,u.telegram_user_id,COUNT(*) total,
                  SUM(CASE WHEN b.result='SUCCESS' THEN 1 ELSE 0 END) success,SUM(CASE WHEN b.result='FAILURE' THEN 1 ELSE 0 END) failures,
                  SUM(CASE WHEN b.result='ABORTED' THEN 1 ELSE 0 END) aborted,SUM(CASE WHEN b.result='UNSTABLE' THEN 1 ELSE 0 END) unstable,
                  ROUND(100.0*SUM(CASE WHEN b.result='SUCCESS' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN b.result IN ('SUCCESS','FAILURE','UNSTABLE') THEN 1 ELSE 0 END),0),2) success_rate,ROUND(AVG(b.duration_seconds)) avg_duration,
                  COUNT(DISTINCT j.project_id) projects,COUNT(DISTINCT b.job_id) jobs FROM builds b JOIN jobs j ON j.id=b.job_id
                  LEFT JOIN users u ON u.id=b.trigger_user_id WHERE b.started_at>=? GROUP BY b.trigger_user_id,{USER_LABEL},u.telegram_username,u.telegram_user_id ORDER BY total DESC""",((datetime.now(timezone.utc)-timedelta(days=d)).isoformat(),)))
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
                  u.telegram_username,u.telegram_user_id,COALESCE(t.trigger_mode,'SINGLE') trigger_mode,t.batch_id,t.batch_size,
                  t.matched_build_id,CASE WHEN t.matched_build_id IS NULL THEN 0 ELSE 1 END matched
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
