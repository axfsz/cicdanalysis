from __future__ import annotations

import json


VERSION = "0.7.4"


def openapi_spec() -> dict:
    """Return the public HTTP API contract.

    The document is intentionally maintained next to the standard-library HTTP
    router so deployments do not need a web-framework dependency just to serve
    API documentation.
    """
    error_response = {
        "description": "请求失败",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/Error"},
                "example": {"error": "invalid parameter: days"},
            }
        },
    }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "cicdanalysis API",
            "version": VERSION,
            "description": (
                "Jenkins Build Intelligence 服务接口。用于查询构建、触发人、故障与统计信息，"
                "并接收 Jenkins 构建完成事件。\n\n"
                "### 统计口径\n"
                "- 成功率 = `SUCCESS / (SUCCESS + FAILURE + UNSTABLE)`。\n"
                "- `ABORTED` 和运行中的构建不进入成功率分母。\n"
                "- 日、周、月边界使用服务的 `APP_TIMEZONE`，默认 `Asia/Kuala_Lumpur`。\n"
                "- 触发人只表示谁发起构建；故障责任由 `responsibility_type` 单独表示。\n\n"
                "### 鉴权\n"
                "查询接口当前仅建议在内网开放。Jenkins Webhook 必须使用 "
                "`Authorization: Bearer <WEBHOOK_SECRET>`。"
            ),
            "contact": {"name": "CI/CD Platform Team"},
        },
        "servers": [{"url": "/", "description": "当前 cicdanalysis 服务"}],
        "tags": [
            {"name": "System", "description": "服务健康与监控"},
            {"name": "Analytics", "description": "构建与故障统计查询"},
            {"name": "Jenkins", "description": "Jenkins 事件接入"},
            {"name": "Trigger Panel", "description": "Telegram 研发触发面板事件接入"},
        ],
        "paths": {
            "/healthz": {
                "get": {
                    "tags": ["System"],
                    "summary": "存活检查",
                    "description": "检查 HTTP 进程是否存活。适用于 Kubernetes livenessProbe。",
                    "operationId": "getHealth",
                    "responses": {"200": _json_response("服务正常", "Health")},
                }
            },
            "/readyz": {
                "get": {
                    "tags": ["System"],
                    "summary": "就绪检查",
                    "description": "检查服务是否可以接收请求。适用于 Kubernetes readinessProbe。",
                    "operationId": "getReadiness",
                    "responses": {"200": _json_response("服务就绪", "Health")},
                }
            },
            "/metrics": {
                "get": {
                    "tags": ["System"],
                    "summary": "Prometheus 指标",
                    "description": "按 Jenkins 构建结果导出累计构建数量。",
                    "operationId": "getMetrics",
                    "responses": {
                        "200": {
                            "description": "Prometheus text exposition format",
                            "content": {
                                "text/plain": {
                                    "schema": {"type": "string"},
                                    "example": '# HELP cicdanalysis_builds_total Collected Jenkins builds\n# TYPE cicdanalysis_builds_total gauge\ncicdanalysis_builds_total{result="SUCCESS"} 301\n',
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/overview": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "构建综合概览",
                    "description": "查询最近若干天的总量、成功率、耗时、热门 Job、触发人、故障分类和重复故障。",
                    "operationId": "getOverview",
                    "parameters": [
                        _query_parameter("days", "统计最近多少天，包含当前时间向前的滚动窗口。", "integer", 7, minimum=1, maximum=365),
                        _query_parameter("date", "选择单个自然日，格式 YYYY-MM-DD；传入后优先于 days，日期边界使用 APP_TIMEZONE。", "string", None, format="date"),
                        _query_parameter("environment", "环境筛选；空值表示全部环境。", "string", "", enum=["", "testa", "uat", "prod"]),
                    ],
                    "responses": {"200": _json_response("综合概览", "Overview"), "400": error_response},
                }
            },
            "/api/v1/builds": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "查询构建记录",
                    "description": "按结果、环境或 Jenkins 触发用户名筛选构建，按开始时间倒序返回。多个筛选条件同时传入时使用 AND。",
                    "operationId": "listBuilds",
                    "parameters": [
                        _query_parameter("limit", "最多返回的记录数。", "integer", 100, minimum=1, maximum=500),
                        _query_parameter("result", "Jenkins 构建结果。", "string", None, enum=["SUCCESS", "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT", "UNKNOWN"]),
                        _query_parameter("environment", "Job 所属环境。", "string", None, enum=["testa", "uat", "prod"]),
                        _query_parameter("trigger_user", "Jenkins 用户名，精确匹配，例如 `mew`。", "string", None),
                    ],
                    "responses": {
                        "200": _array_response("构建记录列表", "Build"),
                        "400": error_response,
                    },
                }
            },
            "/api/v1/users": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "触发人构建统计",
                    "description": "按触发人汇总构建数、成功/失败数、平均耗时，以及涉及的项目和 Job 数。自动任务归为“自动触发”。",
                    "operationId": "listUserStatistics",
                    "parameters": [_query_parameter("days", "统计最近多少天。", "integer", 30, minimum=1, maximum=365)],
                    "responses": {"200": _array_response("触发人统计列表", "UserStatistics"), "400": error_response},
                }
            },
            "/api/v1/failures": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "故障指纹统计",
                    "description": "按规范化错误指纹聚合失败构建，用于识别跨项目、跨 Job 的重复故障。",
                    "operationId": "listFailureStatistics",
                    "parameters": [_query_parameter("days", "统计最近多少天。", "integer", 30, minimum=1, maximum=365)],
                    "responses": {"200": _array_response("故障聚合列表", "FailureStatistics"), "400": error_response},
                }
            },
            "/api/v1/triggers": {
                "get": {
                    "tags": ["Trigger Panel"],
                    "summary": "查询发布触发记录",
                    "description": "查询研发人员从 Telegram 触发面板提交的发布事件，以及事件是否已与 Jenkins Build 精确关联。",
                    "operationId": "listTriggerEvents",
                    "parameters": [
                        _query_parameter("days", "统计最近多少天。", "integer", 7, minimum=1, maximum=365),
                        _query_parameter("date", "选择单个自然日，格式 YYYY-MM-DD；传入后优先于 days。", "string", None, format="date"),
                        _query_parameter("limit", "最多返回的记录数。", "integer", 100, minimum=1, maximum=500),
                        _query_parameter("environment", "环境筛选；空值表示全部环境。", "string", "", enum=["", "testa", "uat", "prod"]),
                    ],
                    "responses": {"200": _array_response("触发事件列表", "TriggerEvent"), "400": error_response},
                }
            },
            "/api/v1/webhooks/jenkins": {
                "post": {
                    "tags": ["Jenkins"],
                    "summary": "接收 Jenkins 构建事件",
                    "description": (
                        "由 Jenkinsfile 的 `post { always { ... } }` 调用。接口先返回 `202`，"
                        "再由后台队列读取 Jenkins Build API、Stage API 和 Console 日志。"
                    ),
                    "operationId": "receiveJenkinsWebhook",
                    "security": [{"WebhookBearer": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/JenkinsWebhookRequest"},
                                "example": {"job_name": "xgcash-admin-testa", "build_number": 1922},
                            }
                        },
                    },
                    "responses": {
                        "202": _json_response("事件已进入采集队列", "JenkinsWebhookAccepted"),
                        "400": error_response,
                        "401": {
                            "description": "未提供、格式错误或与 WEBHOOK_SECRET 不匹配",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}, "example": {"error": "unauthorized"}}},
                        },
                        "413": error_response,
                    },
                }
            },
            "/api/v1/webhooks/trigger": {
                "post": {
                    "tags": ["Trigger Panel"],
                    "summary": "接收 Telegram 触发面板事件",
                    "description": (
                        "触发面板成功调用 Jenkins 并取得 queue/build 地址后立即调用。"
                        "该接口保存 Telegram 用户身份，并通过 Job + build_number 或 queue_id 与 Jenkins Build 关联；"
                        "面板身份优先于 Jenkins Cause，后续轮询不会覆盖。"
                    ),
                    "operationId": "receiveTriggerPanelEvent",
                    "security": [{"TriggerBearer": []}],
                    "requestBody": {"required": True, "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/TriggerWebhookRequest"},
                        "example": {
                            "event_id": "tg--1003919548725-32727",
                            "trigger_name": "Mars Stephen",
                            "telegram_username": "marsstephen",
                            "telegram_user_id": "860100001",
                            "telegram_chat_id": "-1003919548725",
                            "job_name": "statistics-testa",
                            "branch": "main",
                            "triggered_at": "2026-09-07T15:57:17+08:00",
                            "status": "SUCCESS",
                            "service_type": "standalone",
                            "service_name": "statistics",
                            "namespace": "testa",
                            "environment": "testa",
                            "http_status": 201,
                            "queue_id": 32727,
                            "queue_url": "https://jenkins.example/queue/item/32727/",
                            "build_number": 101,
                            "build_url": "https://jenkins.example/job/statistics-testa/101/"
                        },
                    }}},
                    "responses": {
                        "202": _json_response("触发事件已保存", "TriggerWebhookAccepted"),
                        "400": error_response,
                        "401": {"description": "TRIGGER_WEBHOOK_SECRET 不匹配", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                        "413": error_response,
                    },
                }
            },
            "/api/v1/webhooks/release-message": {
                "post": {
                    "tags": ["Trigger Panel"],
                    "summary": "转发发布群消息（触发通知 / 触发结果 / 发布通知）",
                    "description": (
                        "发布面板 Bot 每次向发布群发送消息后，把同一条消息原文转发到这里，效果与 @cicd_analysis_bot 在群里收到该消息相同："
                        "“Jenkins 发布触发通知/触发结果”记录触发人；“Jenkins 发布通知”关联构建号，失败时拉取 Jenkins 日志分析并回复到该群的原消息。"
                        "Telegram 不会把一个 Bot 发的群消息推送给另一个 Bot，发布面板若是独立 Bot（如 ugopsbot），必须接入此接口。"
                        "chat_id 必须是 TESTA/UAT/PROD 发布群之一，否则消息被忽略。"
                    ),
                    "operationId": "receiveReleaseMessage",
                    "security": [{"TriggerBearer": []}],
                    "requestBody": {"required": True, "content": {"application/json": {
                        "schema": {"type": "object", "required": ["chat_id", "message_id", "text"], "properties": {
                            "chat_id": {"type": "string", "description": "发布群 chat id"},
                            "message_id": {"type": "integer", "description": "sendMessage 返回的 message_id，失败分析会回复这条消息"},
                            "text": {"type": "string", "description": "消息纯文本（不含 HTML 标签）"},
                            "date": {"type": "integer", "description": "可选，Unix 秒"},
                        }},
                        "example": {"chat_id": "-5592801576", "message_id": 4521,
                                    "text": "🚀 Jenkins 发布触发通知\n\n触发人: Infi (@infiwork6666)\n触发项目: uat-activity-rpc-prod\n触发时间: 2026-09-28 21:01:59"},
                    }}},
                    "responses": {
                        "202": {"description": "已处理；handled=false 表示不是可识别的发布消息", "content": {"application/json": {"schema": {"type": "object"}}}},
                        "400": error_response,
                        "401": {"description": "TRIGGER_WEBHOOK_SECRET 不匹配", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                        "413": error_response,
                    },
                }
            },
            "/api/v1/admin/telegram-backfill": {
                "post": {
                    "tags": ["Trigger Panel"],
                    "summary": "回放发布群历史消息",
                    "description": "由正在运行的真人账号监听连接读取最近 N 天三个发布群的消息并重新入库（幂等）。"
                                   "`python3 -m cicdanalysis telegram-backfill --days N` 在服务容器内执行时即调用此接口。",
                    "operationId": "telegramBackfill",
                    "security": [{"WebhookBearer": []}],
                    "requestBody": {"required": False, "content": {"application/json": {
                        "schema": {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 14}}},
                        "example": {"days": 14}}}},
                    "responses": {
                        "200": {"description": "回放完成", "content": {"application/json": {"schema": {"type": "object"},
                                "example": {"days": 14, "messages": 812, "handled": 164}}}},
                        "400": error_response,
                        "401": {"description": "WEBHOOK_SECRET 不匹配", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "WebhookBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "WEBHOOK_SECRET",
                    "description": "部署环境变量 WEBHOOK_SECRET 的值。",
                },
                "TriggerBearer": {
                    "type": "http", "scheme": "bearer", "bearerFormat": "TRIGGER_WEBHOOK_SECRET",
                    "description": "触发面板专用密钥；未配置时兼容使用 WEBHOOK_SECRET。",
                },
            },
            "schemas": _schemas(),
        },
    }


def _query_parameter(name, description, value_type, default, **constraints):
    schema = {"type": value_type, **constraints}
    if default is not None:
        schema["default"] = default
    return {"name": name, "in": "query", "required": False, "description": description, "schema": schema}


def _json_response(description: str, schema: str) -> dict:
    return {"description": description, "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}}}


def _array_response(description: str, schema: str) -> dict:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": f"#/components/schemas/{schema}"}}}},
    }


def _schemas() -> dict:
    nullable_string = {"type": "string", "nullable": True}
    nullable_number = {"type": "number", "nullable": True}
    return {
        "Error": {
            "type": "object",
            "required": ["error"],
            "properties": {"error": {"type": "string", "description": "可读错误信息"}},
        },
        "Health": {
            "type": "object",
            "required": ["status", "version"],
            "properties": {
                "status": {"type": "string", "enum": ["ok"], "example": "ok"},
                "version": {"type": "string", "example": VERSION},
            },
        },
        "BuildCounts": {
            "type": "object",
            "description": "键为 Jenkins result，值为对应构建数。没有发生的结果可能不返回。",
            "additionalProperties": {"type": "integer", "minimum": 0},
            "example": {"SUCCESS": 301, "FAILURE": 19, "ABORTED": 6, "UNSTABLE": 2},
        },
        "JobStatistics": {
            "type": "object",
            "required": ["job_name", "total", "failures"],
            "properties": {
                "job_name": {"type": "string", "example": "xgcash-admin-testa"},
                "total": {"type": "integer", "example": 39},
                "failures": {"type": "integer", "example": 5},
            },
        },
        "OverviewUserStatistics": {
            "type": "object",
            "required": ["name", "total", "success", "failures"],
            "properties": {
                "name": {"type": "string", "example": "mew"},
                "telegram_username": {**nullable_string, "example": "marsstephen"},
                "total": {"type": "integer", "example": 46},
                "success": {"type": "integer", "example": 41},
                "failures": {"type": "integer", "example": 4},
                "aborted": {"type": "integer", "example": 1},
                "success_rate": {"type": "number", "nullable": True, "example": 91.11},
            },
        },
        "FailureCategoryStatistics": {
            "type": "object",
            "required": ["error_category", "count"],
            "properties": {
                "error_category": {"type": "string", "example": "K8S_ERROR"},
                "count": {"type": "integer", "example": 21},
            },
        },
        "RepeatedFailure": {
            "type": "object",
            "required": ["error_fingerprint", "count"],
            "properties": {
                "error_code": {**nullable_string, "example": "ERR_PNPM_OUTDATED_LOCKFILE"},
                "error_fingerprint": {"type": "string", "example": "4f2e8a143b876611"},
                "count": {"type": "integer", "minimum": 2, "example": 4},
            },
        },
        "Overview": {
            "type": "object",
            "required": ["start", "end", "environment", "total", "counts", "success_rate", "avg_duration", "jobs", "users", "failures", "repeated"],
            "properties": {
                "start": {"type": "string", "format": "date-time", "description": "UTC 统计窗口起点"},
                "end": {"type": "string", "format": "date-time", "description": "UTC 统计窗口终点"},
                "environment": {"type": "string", "example": "prod"},
                "total": {"type": "integer", "example": 328},
                "counts": {"$ref": "#/components/schemas/BuildCounts"},
                "success_rate": {"type": "number", "format": "float", "example": 93.44},
                "avg_duration": {"type": "integer", "description": "平均构建耗时，单位秒", "example": 222},
                "panel_triggered": {"type":"integer","description":"已关联群聊触发消息或触发面板事件的构建数"},
                "jenkins_identified": {"type":"integer","description":"由 Jenkins Cause 识别的用户构建数"},
                "unattributed": {"type":"integer","description":"尚无用户身份的构建数"},
                "developer_count": {"type":"integer","description":"实名触发开发人员去重数"},
                "unmatched_triggers": {"type":"integer","description":"尚未关联 Jenkins Build 的触发事件数"},
                "trigger_identity_status": {"type":"string","enum":["OK","PARTIAL","NOT_CONNECTED","NO_DATA"],"description":"触发身份采集状态"},
                "jobs": {"type": "array", "items": {"$ref": "#/components/schemas/JobStatistics"}},
                "users": {"type": "array", "items": {"$ref": "#/components/schemas/OverviewUserStatistics"}},
                "failures": {"type": "array", "items": {"$ref": "#/components/schemas/FailureCategoryStatistics"}},
                "repeated": {"type": "array", "items": {"$ref": "#/components/schemas/RepeatedFailure"}},
            },
        },
        "Build": {
            "type": "object",
            "required": ["id", "job_name", "build_number", "result"],
            "properties": {
                "id": {"type": "integer", "example": 1288},
                "job_name": {"type": "string", "example": "xgcash-admin-testa"},
                "environment": {**nullable_string, "example": "testa"},
                "build_number": {"type": "integer", "example": 1922},
                "result": {"type": "string", "example": "FAILURE"},
                "branch": {**nullable_string, "example": "main"},
                "commit_sha": {**nullable_string, "example": "e65242a7efae"},
                "duration_seconds": {**nullable_number, "description": "构建耗时，单位秒", "example": 142},
                "started_at": {**nullable_string, "format": "date-time"},
                "build_url": {**nullable_string, "format": "uri", "example": "https://jenkins.example/job/xgcash-admin-testa/1922/"},
                "trigger_user": {**nullable_string, "description": "触发人显示名；自动任务可能为 null", "example": "mew"},
                "telegram_username": {**nullable_string, "example": "marsstephen"},
                "trigger_source": {**nullable_string, "description": "TELEGRAM_PANEL 表示来自研发触发面板，优先级高于 Jenkins Cause。", "example": "TELEGRAM_PANEL"},
            },
        },
        "UserStatistics": {
            "type": "object",
            "required": ["name", "total", "success", "failures", "avg_duration", "projects", "jobs"],
            "properties": {
                "name": {"type": "string", "example": "mew"},
                "telegram_username": {**nullable_string, "example": "slo_dream_03"},
                "total": {"type": "integer", "example": 218},
                "success": {"type": "integer", "example": 203},
                "failures": {"type": "integer", "example": 12},
                "aborted": {"type": "integer", "example": 1},
                "unstable": {"type": "integer", "example": 2},
                "success_rate": {"type": "number", "nullable": True, "example": 93.12},
                "avg_duration": {**nullable_number, "description": "平均构建耗时，单位秒", "example": 208},
                "projects": {"type": "integer", "example": 9},
                "jobs": {"type": "integer", "example": 17},
            },
        },
        "FailureStatistics": {
            "type": "object",
            "required": ["error_category", "error_fingerprint", "count"],
            "properties": {
                "error_category": {"type": "string", "example": "DEPENDENCY_ERROR"},
                "error_code": {**nullable_string, "example": "ERR_PNPM_OUTDATED_LOCKFILE"},
                "failed_stage": {**nullable_string, "example": "Install Dependencies"},
                "responsibility_type": {**nullable_string, "description": "CODE、INFRASTRUCTURE、NETWORK、CONFIGURATION 或 UNKNOWN", "example": "CODE"},
                "error_fingerprint": {"type": "string", "example": "4f2e8a143b876611"},
                "count": {"type": "integer", "example": 83},
            },
        },
        "JenkinsWebhookRequest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["job_name", "build_number"],
            "properties": {
                "job_name": {"type": "string", "minLength": 1, "maxLength": 512, "description": "完整 Jenkins Job 名；Folder Job 使用 Jenkins 返回的 fullName。", "example": "xgcash-admin-testa"},
                "build_number": {"type": "integer", "minimum": 1, "example": 1922},
            },
        },
        "JenkinsWebhookAccepted": {
            "type": "object",
            "required": ["accepted", "job_name", "build_number"],
            "properties": {
                "accepted": {"type": "boolean", "example": True},
                "job_name": {"type": "string", "example": "xgcash-admin-testa"},
                "build_number": {"type": "integer", "example": 1922},
            },
        },
        "TriggerWebhookRequest": {
            "type": "object",
            "required": ["job_name"],
            "description": "必须提供 build_number 或 queue_id；用户身份必须提供 telegram_user_id、telegram_username 或 trigger_name 之一。",
            "properties": {
                "event_id": {"type": "string", "description": "触发系统生成的幂等 ID，强烈建议提供。", "example": "tg--1003919548725-32727"},
                "trigger_name": {"type": "string", "example": "Mars Stephen"},
                "telegram_username": {"type": "string", "example": "marsstephen"},
                "telegram_user_id": {"type": "string", "description": "Telegram 数字用户 ID，身份变更时比 username 更稳定。", "example": "860100001"},
                "telegram_chat_id": {"type": "string", "example": "-1003919548725"},
                "job_name": {"type": "string", "minLength": 1, "example": "statistics-testa"},
                "branch": {"type": "string", "example": "main"},
                "triggered_at": {"type": "string", "format": "date-time", "example": "2026-09-07T15:57:17+08:00"},
                "status": {"type": "string", "example": "SUCCESS"},
                "service_type": {"type": "string", "example": "standalone"},
                "service_name": {"type": "string", "example": "statistics"},
                "namespace": {"type": "string", "example": "testa"},
                "environment": {"type": "string", "example": "testa"},
                "http_status": {"type": "integer", "example": 201},
                "queue_id": {"type": "integer", "minimum": 1, "example": 32727},
                "queue_url": {"type": "string", "format": "uri"},
                "build_number": {"type": "integer", "minimum": 1, "example": 101},
                "build_url": {"type": "string", "format": "uri"},
            },
        },
        "TriggerWebhookAccepted": {
            "type": "object",
            "properties": {
                "accepted": {"type": "boolean", "example": True},
                "event_id": {"type": "string"},
                "trigger_id": {"type": "integer"},
                "matched": {"type": "boolean", "description": "是否已经关联到已采集的 Build。"},
                "build_id": {"type": "integer", "nullable": True},
                "job_name": {"type": "string"},
                "build_number": {"type": "integer", "nullable": True},
                "queue_id": {"type": "integer", "nullable": True},
                "trigger_name": {"type": "string"},
                "telegram_username": {"type": "string"},
            },
        },
        "TriggerEvent": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"}, "job_name": {"type": "string"},
                "build_number": {"type": "integer", "nullable": True}, "queue_id": {"type": "integer", "nullable": True},
                "branch": nullable_string, "environment": nullable_string, "namespace": nullable_string,
                "trigger_status": nullable_string, "http_status": {"type": "integer", "nullable": True},
                "queue_url": nullable_string, "build_url": nullable_string,
                "triggered_at": {"type": "string", "format": "date-time"},
                "trigger_name": {"type": "string"}, "telegram_username": nullable_string,
                "telegram_user_id": nullable_string, "matched_build_id": {"type": "integer", "nullable": True},
                "matched": {"type": "integer", "enum": [0, 1]},
            },
        },
    }


def swagger_ui_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>cicdanalysis - Swagger UI</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>body{margin:0;background:#fafafa}.topbar{display:none}.swagger-ui .info{margin:32px 0 24px}.swagger-ui .scheme-container{box-shadow:none;border:1px solid #e5e7eb}</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
  <script>
  window.onload = () => SwaggerUIBundle({
    url: '/openapi.json', dom_id: '#swagger-ui', deepLinking: true,
    displayRequestDuration: true, filter: true, persistAuthorization: true,
    tryItOutEnabled: true, docExpansion: 'list', defaultModelsExpandDepth: 1,
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
    layout: 'BaseLayout'
  });
  </script>
</body>
</html>"""


def redoc_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>cicdanalysis - API Reference</title></head>
<body><redoc spec-url="/openapi.json" expand-responses="200,202" required-props-first></redoc>
<script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"></script></body>
</html>"""


def openapi_json() -> bytes:
    return json.dumps(openapi_spec(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
