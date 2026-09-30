# cicdanalysis API 接口文档

版本：`0.5.0`  
协议：HTTP/HTTPS  
数据格式：除 `/metrics` 外均为 `application/json; charset=utf-8`

## 文档入口

| 地址 | 用途 |
|---|---|
| `/docs` | Swagger UI，可填写参数并在线发起请求 |
| `/redoc` | ReDoc 阅读版 |
| `/openapi.json` | OpenAPI 3.0.3 原始定义，可导入 Postman、Apifox、YApi 等工具 |

## 鉴权与访问控制

统计面板（`/`）、`/api/v1/*` 查询接口和接口文档（`/docs`、`/redoc`、`/openapi.json`）必须携带只读 Token：

```http
Authorization: Bearer <READ_API_TOKEN>
```

浏览器访问统计面板时会弹出 Basic 认证框：用户名任意，密码填 `READ_API_TOKEN`，之后面板里的查询请求自动带上凭据。
服务端未设置 `READ_API_TOKEN` 时，上述地址一律返回 `401`。

`/healthz`、`/readyz`、`/metrics` 默认无需鉴权。设置 `PUBLIC_HEALTH=false` 或 `PUBLIC_METRICS=false` 后，
对应地址同样要求 `READ_API_TOKEN`（注意 Compose 健康检查和 K8s 探针默认不带 Token）。

只读 Token 不能调用 Webhook，Webhook 仍使用各自的密钥。仍建议只通过内网、VPN、Ingress 白名单或 API Gateway 暴露服务。

```bash
curl -H "Authorization: Bearer $READ_API_TOKEN" 'http://127.0.0.1:8080/api/v1/builds?limit=20'
```

Jenkins Webhook 必须携带：

```http
Authorization: Bearer <WEBHOOK_SECRET>
Content-Type: application/json
```

`WEBHOOK_SECRET` 来自服务端同名环境变量。不要使用 Jenkins Token 或 Telegram Bot Token 作为 Webhook Secret。

## 通用错误

```json
{
  "error": "invalid parameter: days"
}
```

| HTTP 状态码 | 含义 |
|---:|---|
| `200` | 查询成功 |
| `202` | Webhook 已进入后台采集队列 |
| `400` | 参数或 JSON 格式错误 |
| `401` | Webhook 密钥或 `READ_API_TOKEN` 缺失或不正确 |
| `404` | 路径不存在 |
| `413` | Webhook 请求体超过 64 KiB |

## 接口总览

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/healthz` | 存活检查 | 默认无（`PUBLIC_HEALTH=false` 时 READ_API_TOKEN） |
| GET | `/readyz` | 就绪检查 | 默认无（`PUBLIC_HEALTH=false` 时 READ_API_TOKEN） |
| GET | `/metrics` | Prometheus 指标 | 默认无（`PUBLIC_METRICS=false` 时 READ_API_TOKEN） |
| GET | `/api/v1/overview` | 构建综合概览 | READ_API_TOKEN |
| GET | `/api/v1/builds` | 查询构建记录 | READ_API_TOKEN |
| GET | `/api/v1/users` | 触发人构建统计 | READ_API_TOKEN |
| GET | `/api/v1/failures` | 故障指纹统计 | READ_API_TOKEN |
| GET | `/api/v1/triggers` | 查询触发面板事件与关联状态 | READ_API_TOKEN |
| POST | `/api/v1/webhooks/jenkins` | 接收 Jenkins 构建结束事件 | Bearer Token |
| POST | `/api/v1/webhooks/trigger` | 接收真实 Telegram 点击者与队列/构建信息 | Bearer Token |
| POST | `/api/v1/webhooks/release-message` | 转发发布群消息原文（触发通知/触发结果/发布通知），失败时自动分析并回复原群 | Bearer Token（TRIGGER_WEBHOOK_SECRET） |

## GET /healthz

用于 Kubernetes `livenessProbe`，确认 HTTP 进程存活。

响应示例：

```json
{
  "status": "ok",
  "version": "0.5.0"
}
```

## GET /readyz

用于 Kubernetes `readinessProbe`，确认服务可以接收请求。响应格式与 `/healthz` 相同。

## GET /metrics

导出 Prometheus text exposition format 指标。

```text
# HELP cicdanalysis_builds_total Collected Jenkins builds
# TYPE cicdanalysis_builds_total gauge
cicdanalysis_builds_total{result="SUCCESS"} 301
cicdanalysis_builds_total{result="FAILURE"} 19
```

目前指标 `cicdanalysis_builds_total` 按 `result` 标签区分 Jenkins 构建结果。

## GET /api/v1/overview

返回最近滚动时间窗口内的综合统计，包括构建数量、成功率、平均耗时、构建最多 Job、触发人、故障类型及重复故障。

查询参数：

| 参数 | 类型 | 必填 | 默认值 | 范围/可选值 |
|---|---|---|---|---|
| `days` | integer | 否 | `7` | `1`～`365`；未传 `date` 时使用 |
| `date` | string | 否 | - | `YYYY-MM-DD`；传入后优先于 `days` |
| `environment` | string | 否 | 空 | 空、`testa`、`uat`、`prod` |

请求示例：

```bash
curl -H "Authorization: Bearer $READ_API_TOKEN" 'http://127.0.0.1:8080/api/v1/overview?date=2026-09-07&environment=prod'
```

响应示例：

```json
{
  "start": "2026-08-31T08:00:00+00:00",
  "end": "2026-09-07T08:00:00+00:00",
  "environment": "prod",
  "total": 328,
  "counts": {
    "SUCCESS": 301,
    "FAILURE": 19,
    "ABORTED": 6,
    "UNSTABLE": 2
  },
  "success_rate": 93.48,
  "avg_duration": 222,
  "jobs": [
    {"job_name": "xgcash-admin-prod", "total": 39, "failures": 5}
  ],
  "users": [
    {"name": "mew", "total": 46, "success": 41, "failures": 4}
  ],
  "failures": [
    {"error_category": "K8S_ERROR", "count": 3}
  ],
  "repeated": [
    {"error_code": "K8S_ROLLOUT_TIMEOUT", "error_fingerprint": "4f2e8a143b876611", "count": 3}
  ]
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `start` / `end` | UTC 格式的统计窗口起止时间 |
| `environment` | 请求的环境；未筛选时为 `all` |
| `total` | 窗口内全部构建数 |
| `counts` | 按 Jenkins result 分组的数量；未出现的状态可能不返回 |
| `success_rate` | `SUCCESS / (SUCCESS + FAILURE + UNSTABLE) × 100` |
| `avg_duration` | 平均构建耗时，单位秒 |
| `jobs` | 构建次数最多的 5 个 Job |
| `users` | 触发构建最多的 10 人；无用户的任务显示“自动触发” |
| `failures` | 失败分类 Top 8 |
| `repeated` | 同一错误指纹出现超过 1 次的 Top 5 |

## GET /api/v1/builds

查询构建明细，结果按 `started_at` 倒序返回。多个过滤条件使用 AND。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | `100` | `1`～`500` |
| `result` | string | 否 | - | `SUCCESS`、`FAILURE`、`UNSTABLE`、`ABORTED`、`NOT_BUILT`、`UNKNOWN` |
| `environment` | string | 否 | - | `testa`、`uat`、`prod` |
| `trigger_user` | string | 否 | - | Jenkins 用户名精确匹配，例如 `mew` |

请求示例：

```bash
curl -H "Authorization: Bearer $READ_API_TOKEN" 'http://127.0.0.1:8080/api/v1/builds?limit=20&result=FAILURE&environment=prod&trigger_user=mew'
```

响应示例：

```json
[
  {
    "id": 1288,
    "job_name": "xgcash-admin-prod",
    "environment": "prod",
    "build_number": 1922,
    "result": "FAILURE",
    "branch": "main",
    "commit_sha": "e65242a7efae",
    "duration_seconds": 142.0,
    "started_at": "2026-09-07T06:12:04+00:00",
    "build_url": "https://jenkins.example/job/xgcash-admin-prod/1922/",
    "trigger_user": "mew"
  }
]
```

`branch`、`commit_sha`、`duration_seconds`、`started_at`、`build_url`、`trigger_user` 在 Jenkins 未提供相应信息时可能为 `null`。

## GET /api/v1/users

按触发人汇总最近若干天的构建情况。

| 参数 | 类型 | 必填 | 默认值 | 范围 |
|---|---|---|---|---|
| `days` | integer | 否 | `30` | `1`～`365` |

```bash
curl -H "Authorization: Bearer $READ_API_TOKEN" 'http://127.0.0.1:8080/api/v1/users?days=30'
```

```json
[
  {
    "name": "mew",
    "telegram_username": "slo_dream_03",
    "total": 218,
    "success": 203,
    "failures": 12,
    "avg_duration": 208.0,
    "projects": 9,
    "jobs": 17
  }
]
```

`projects` 和 `jobs` 分别是该触发人在统计窗口内涉及的去重项目数及 Job 数。触发失败次数不等于个人责任故障次数。

## GET /api/v1/failures

按 `error_fingerprint` 聚合最近若干天的构建故障。

| 参数 | 类型 | 必填 | 默认值 | 范围 |
|---|---|---|---|---|
| `days` | integer | 否 | `30` | `1`～`365` |

```bash
curl -H "Authorization: Bearer $READ_API_TOKEN" 'http://127.0.0.1:8080/api/v1/failures?days=30'
```

```json
[
  {
    "error_category": "DEPENDENCY_ERROR",
    "error_code": "ERR_PNPM_OUTDATED_LOCKFILE",
    "failed_stage": "Install Dependencies",
    "responsibility_type": "CODE",
    "error_fingerprint": "4f2e8a143b876611",
    "count": 83
  }
]
```

字段说明：

| 字段 | 说明 |
|---|---|
| `error_category` | 故障大类，如 `CODE_ERROR`、`DEPENDENCY_ERROR`、`K8S_ERROR` |
| `error_code` | 稳定的机器可读错误码；无法细分时可能为 `null` |
| `failed_stage` | Pipeline 失败阶段；无法识别时可能为 `null` |
| `responsibility_type` | `CODE`、`INFRASTRUCTURE`、`NETWORK`、`CONFIGURATION` 或 `UNKNOWN` |
| `error_fingerprint` | `stage + category + normalized_error` 生成的稳定指纹 |
| `count` | 统计窗口内该错误指纹出现次数 |

## POST /api/v1/webhooks/jenkins

Jenkins 构建结束后调用此接口。接口不会同步分析日志；成功校验请求后将任务放入内存队列并返回 `202 Accepted`。

请求：

```bash
curl -X POST 'http://cicdanalysis:8080/api/v1/webhooks/jenkins' \
  -H 'Authorization: Bearer <WEBHOOK_SECRET>' \
  -H 'Content-Type: application/json' \
  --data '{"job_name":"xgcash-admin-testa","build_number":1922}'
```

请求字段：

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| `job_name` | string | 是 | 非空；完整 Jenkins Job 名称 |
| `build_number` | integer | 是 | 大于或等于 `1` |

响应：

```json
{
  "accepted": true,
  "job_name": "xgcash-admin-testa",
  "build_number": 1922
}
```

Jenkinsfile 调用示例：

```groovy
post {
  always {
    withCredentials([string(credentialsId: 'cicdanalysis-webhook-secret', variable: 'CICD_SECRET')]) {
      sh '''curl -fsS -X POST "$CICD_ANALYSIS_URL/api/v1/webhooks/jenkins" \\
        -H "Authorization: Bearer $CICD_SECRET" \\
        -H "Content-Type: application/json" \\
        --data "{\\"job_name\\":\\"${JOB_NAME}\\",\\"build_number\\":${BUILD_NUMBER}}"'''
    }
  }
}
```

## 部署后验证

```bash
curl -fsS https://your-cicdanalysis-domain/healthz
curl -fsS -H "Authorization: Bearer $READ_API_TOKEN" https://your-cicdanalysis-domain/openapi.json
```

浏览器打开：

```text
https://your-cicdanalysis-domain/docs
```

在 Swagger UI 中展开接口，点击 **Try it out**，填写参数后点击 **Execute**。测试 Webhook 时先点击页面右上角 **Authorize**，填写 `WEBHOOK_SECRET`，Swagger UI 会自动添加 `Bearer` 前缀。

## Telegram 触发面板接口

`POST /api/v1/webhooks/trigger` 是真实触发人的权威数据入口。支持同一个群内任意数量的开发人员，并以 `telegram_user_id` 区分身份。完整接入顺序、请求字段、幂等设计和历史修复方法见 [`TRIGGER-PANEL-INTEGRATION.md`](TRIGGER-PANEL-INTEGRATION.md)。
