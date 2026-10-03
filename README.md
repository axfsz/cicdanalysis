# cicdanalysis

Jenkins 构建智能分析与 Telegram 报告服务。生产部署默认使用 Docker Compose 内置的 PostgreSQL 16，保存 Jenkins 构建、真实触发人、故障分析及报告发送记录。

## 已实现

- Jenkins Job/Build 定时采集，支持 Folder/嵌套 Job
- Pipeline `wfapi` Stage 采集，并在不可用时从日志识别失败阶段
- 触发人、Telegram 用户名、项目、服务、环境、分支、Commit、Agent、耗时维度
- 失败日志脱敏、规则分类、错误指纹、根因与建议
- 同 Commit 重试与重复错误识别
- TESTA/UAT/PROD 分群日报、周报、月报
- 每日定时向三个环境对应的 Telegram 群发送前一自然日的独立报告
- Dashboard 与 API 支持选择 `YYYY-MM-DD` 日期
- PostgreSQL 持久化发送状态，服务重启不会重复发送报告
- Telegram 实时失败通知并 @ 已映射触发人
- Web Dashboard、JSON API、Prometheus 指标、健康检查
- Jenkins Webhook（推荐实时调用）与轮询兜底
- Telegram 研发触发面板事件接入，按 `telegram_user_id` 支持群内多名开发人员
- `@cicd_analysis_bot` 自动监听三个发布群的“Jenkins 发布触发结果”，从消息正文关联真实触发人
- 解析 UAT/PROD 的“🚀 Jenkins 发布触发通知”（无构建地址），按 Job + 触发时间或“发布通知”里的构建号关联到 Jenkins Build
- 群内出现“❌ Jenkins 发布通知”时自动拉取 Jenkins 日志分析失败原因，并回复到该群原消息（构建仍在运行时自动等待结束）
- 识别“🚀 Jenkins 批量发布触发通知”，批量发布的每个服务关联到真实触发人
- `POST /api/v1/webhooks/release-message`：发布面板 Bot 直接转发群消息原文
- Jenkins 参数、Cause、控制台日志三层触发人回退识别
- 通过 Job + build_number/queue_id 精确关联真实触发人与 Jenkins Build
- Docker Compose 内置 PostgreSQL 16、健康检查、持久卷和启动依赖
- Kubernetes manifests、Jenkinsfile 调用示例

## 快速启动

```bash
cp .env.example .env
# 编辑 .env；不要把真实 Token 提交到 Git
docker compose up -d --build
curl http://127.0.0.1:8080/healthz
```

启动后包含 `cicdanalysis` 和 `postgres` 两个容器。查看状态：

```bash
docker compose ps
docker compose logs -f cicdanalysis
```

访问：

- `http://127.0.0.1:8080/`：统计面板
- `http://127.0.0.1:8080/docs`：Swagger UI 交互式接口文档
- `http://127.0.0.1:8080/redoc`：ReDoc 阅读版接口文档
- `http://127.0.0.1:8080/openapi.json`：OpenAPI 3.0.3 原始定义

本地运行：

```bash
python3 -m cicdanalysis init-db
python3 -m cicdanalysis collect-once
python3 -m cicdanalysis serve
```

测试：

```bash
python3 -m unittest discover -s tests -v
```

## 必需环境变量

| 变量 | 说明 |
|---|---|
| `JENKINS_URL` | Jenkins 地址 |
| `JENKINS_USER` | 只读账号 |
| `JENKINS_API_TOKEN` | Jenkins API Token |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `TELEGRAM_LISTEN_GROUP_MESSAGES` | 是否监听发布群触发结果，默认 `true` |
| `TELEGRAM_COMMANDS` | 是否响应群内 `/analyze` 命令，默认 `true`（与上一项独立，关闭监听也能用命令） |
| `WEBHOOK_SECRET` | Jenkins webhook Bearer Token |

三个群默认值已按需求写入 `.env.example` 和 K8s ConfigMap，可通过环境变量覆盖。

## Jenkins 权限

至少需要 `Overall/Read`、`Job/Read`、`View/Read`，并能读取：

- `/api/json`
- `/job/.../<build>/api/json`
- `/job/.../<build>/consoleText`
- `/job/.../<build>/wfapi/describe`（可选）
- `/job/.../<build>/execution/node/<id>/wfapi/describe` 与 `.../wfapi/log`（失败步骤日志，推荐）

## 实时 webhook

轮询能独立工作。为了在构建结束后立即分析，在 Jenkinsfile `post` 中调用：

```groovy
post {
  always {
    withCredentials([string(credentialsId: 'cicdanalysis-webhook-secret', variable: 'CICD_SECRET')]) {
      sh '''curl -fsS -X POST "$CICD_ANALYSIS_URL/api/v1/webhooks/jenkins" \
        -H "Authorization: Bearer $CICD_SECRET" \
        -H "Content-Type: application/json" \
        --data "{\\"job_name\\":\\"${JOB_NAME}\\",\\"build_number\\":${BUILD_NUMBER}}"'''
    }
  }
}
```

## API

- `GET /healthz`
- `GET /readyz`
- `GET /metrics`
- `GET /api/v1/overview?days=7&environment=prod`
- `GET /api/v1/overview?date=2026-09-07&environment=prod`
- `GET /api/v1/builds?limit=100&result=FAILURE&trigger_user=mew`
- `GET /api/v1/users?days=30`
- `GET /api/v1/failures?days=30`
- `GET /api/v1/triggers?days=7&environment=testa&limit=100`
- `POST /api/v1/webhooks/jenkins`
- `POST /api/v1/webhooks/trigger`
- `POST /api/v1/webhooks/release-message`

详细的请求参数、响应字段、状态码、示例和在线调试入口见 `/docs`。Swagger UI 和 ReDoc 的静态资源默认从 jsDelivr 加载；API 服务本身及 `/openapi.json` 不依赖外部网络。

仓库内也提供可离线阅读的中文文档：[`docs/API.md`](docs/API.md)。

系统默认由 `@cicd_analysis_bot` 监听三个发布群中的“📣 Jenkins 发布触发结果”，解析消息中的触发人并按构建地址关联。Trigger Webhook 仍是数据最完整、可携带稳定 `telegram_user_id` 的推荐方式，详见 [`docs/TRIGGER-PANEL-INTEGRATION.md`](docs/TRIGGER-PANEL-INTEGRATION.md)。

群消息监听要求关闭 BotFather 的 Privacy Mode（或将 Bot 设为群管理员）。同一个 Bot Token 只能有一个 `getUpdates` 消费者；若已有程序正在消费，请关闭本服务的监听并由现有程序调用 Trigger Webhook。

部署后检查监听日志：

```bash
docker compose logs -f cicdanalysis
```

收到发布消息后应出现 `Telegram trigger captured`。如果出现 `Conflict`，请确认该 Token 没有配置 Telegram webhook，且没有第二个长轮询进程。旧群消息不会由 Telegram 自动提供历史回放；升级前已经保存为“自动触发”的记录，需要重发原消息或通过 Trigger Webhook 回放才能修复。

## 发布群消息与失败分析（0.6.0）

服务识别三种发布群消息，环境一律按消息所在群判定（TESTA/UAT/PROD 群 ID 见下文）：

| 消息 | 用途 |
|---|---|
| 📣 Jenkins 发布触发结果 | 触发人 + 构建地址，直接关联构建 |
| 🚀 Jenkins 发布触发通知 | 触发人 + 触发项目 + 触发时间，关联该 Job 在触发后 30 分钟内启动的第一个构建 |
| ✅/❌ Jenkins 发布通知 | 任务 + 构建号 + 状态；为待关联的触发通知补上构建号；失败时触发日志分析 |

失败时的流程：收到“❌ Jenkins 发布通知” → 采集该构建并读取 `consoleText` → 规则分类、定位失败阶段、截取关键日志（已脱敏）
→ 以回复原消息的方式发到同一个群。轮询先发现失败构建时同样会发到该环境的群；每个构建只发一次。

### 重要：Bot 之间看不到对方的群消息

Telegram 不会把一个 Bot 发的群消息推送给另一个 Bot（与 Privacy Mode 无关）。如果发布消息是由另一个 Bot（如 `ugopsbot`）发出的，
`@cicd_analysis_bot` 的 getUpdates 收不到它们，触发人统计和失败分析都不会生效。验证方法：

```bash
docker compose logs cicdanalysis | grep -E "Telegram (trigger|release result) captured"
```

若从未出现，请在发布面板 Bot 每次 `sendMessage` 成功后，把同一条消息转发给本服务（`TRIGGER_WEBHOOK_SECRET` 鉴权）：

```bash
curl -fsS -X POST "$CICD_ANALYSIS_URL/api/v1/webhooks/release-message" \
  -H "Authorization: Bearer $TRIGGER_WEBHOOK_SECRET" -H "Content-Type: application/json" \
  --data '{"chat_id":"-5592801576","message_id":4521,"text":"<消息纯文本>"}'
```

`message_id` 使用 sendMessage 返回值，失败分析会回复这条消息。

## 用真人 Telegram 账号读取发布群（0.6.1，推荐）

三个发布群的消息分别由 @ugopsbot（TESTA）、@uguatdeploybot（UAT）、@ugprodopsbot（PROD）发出，`@cicd_analysis_bot` 收不到。
配置一个已在三个群里的真人账号后，服务用该账号实时读取群消息，发布机器人无需改动。失败分析报告仍由 `@cicd_analysis_bot` 发出。

1. 用该账号登录 <https://my.telegram.org> → API development tools，创建应用，得到 `api_id` 与 `api_hash`。
2. 写入服务器 `.env`（不要提交到 Git，也不要发到聊天里）：

   ```env
   TELEGRAM_USER_API_ID=<api_id>
   TELEGRAM_USER_API_HASH=<api_hash>
   TELEGRAM_USER_SESSION=/data/telegram-user
   TELEGRAM_USER_TRUSTED_SENDERS=ugopsbot,uguatdeploybot,ugprodopsbot
   ```

3. 构建新镜像并一次性登录（按提示输入手机号、Telegram 发来的验证码、两步验证密码；输出应显示三个群均为 OK）：

   ```bash
   docker compose build cicdanalysis
   docker compose stop cicdanalysis
   docker compose run --rm -it cicdanalysis telegram-login
   ```

4. 启动并补历史（默认回放 14 天，只补已采集到的构建）：

   ```bash
   docker compose up -d cicdanalysis
   docker compose exec cicdanalysis python3 -m cicdanalysis telegram-backfill --days 14   # 由运行中的服务执行，无需停服
   docker compose logs -f cicdanalysis | grep -E "user-account listener|captured"
   ```

会话文件保存在 `cicd-data` 卷的 `/data/telegram-user.session`，等同于该账号的登录凭据，请限制服务器访问权限；
在 Telegram“设置 → 设备”中可随时踢下线使其失效。UAT 群是普通群，账号看到的消息 ID 与 Bot 不同，因此 UAT 的失败分析不会以“回复原消息”形式出现。

## 批量发布与失败自动推送（0.8.0）

**批量发布**：“🚀 Jenkins 批量发布触发通知”里 `• ` 列出的每个服务都会记为一条触发记录，按 Job + 触发时间关联到各自的构建，
触发人统计中计入对应开发人员（面板“批量”列）。升级前已被统计为“自动触发/未识别”的批量构建，执行一次回放即可修复：

```bash
docker compose exec cicdanalysis python3 -m cicdanalysis telegram-backfill --days 14
```

**失败自动推送**：失败构建结束后自动拉日志分析并推送到所属环境的发布群（有“❌ Jenkins 发布通知”时作为回复），每个构建只推一次，
无需再手动 `/analyze`。触发路径有三条，任一先到即可：群内“❌ Jenkins 发布通知”、Jenkins webhook、定时轮询。
发布通知到达时构建多半仍在运行，服务会每 15 秒重查直到结束。

| 变量 | 默认 | 说明 |
|---|---|---|
| `FAILURE_NOTIFY_MAX_AGE_MINUTES` | `180` | 结束于该时间内的失败构建才自动推送，避免首次采集时推送历史失败 |
| `TELEGRAM_NOTIFY_ABORTED` | `false` | 取消的构建是否推送 |
| `FOLLOW_UP_SECONDS` / `FOLLOW_UP_MAX_MINUTES` | `15` / `60` | 运行中构建的重查间隔与上限 |

统计面板“失败自动分析”列出每个失败构建的推送方式与延迟，“失败自动推送”卡片出现“未推送”时请检查日志中的 `cannot diagnose` 与 Telegram 发送错误。

## 失败根因分析（0.7.0）

分析顺序：Jenkins 失败步骤日志（Pipeline 节点）+ 失败的下游构建日志 → 规则匹配 → 大模型分析（已配置时）。

启用 DeepSeek（或任意 OpenAI 兼容服务，如通义千问、Moonshot、自建 vLLM，只需改 `LLM_BASE_URL` 和 `LLM_MODEL`）：

```env
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=<在 DeepSeek 控制台创建>
LLM_MODEL=deepseek-chat
```

已有 `.env` 里用的是 `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` / `AI_MAX_TOKENS` 也可以直接生效（同时配置时以 `LLM_*` 为准）。启动日志会打印 `LLM root-cause analysis: enabled (...)` 或 `disabled`。

核对某次失败的分析结果（会重新拉日志、重新分析并覆盖该构建的结论，不发群）：

```bash
docker compose exec cicdanalysis python3 -m cicdanalysis analyze --job xgcash-admin-prod --build 312
```

把分析报告发到 Telegram（手动补发/测试，不受“每个构建只发一次”限制）：

```bash
# 先预览：打印将要发送的目标群和消息内容，不发送
docker compose exec cicdanalysis python3 -m cicdanalysis analyze --job testa-ug-app-ios --build 111 --dry-run
# 发到该构建所属环境的发布群（群里有对应“❌ Jenkins 发布通知”时作为回复）
docker compose exec cicdanalysis python3 -m cicdanalysis analyze --job testa-ug-app-ios --build 111 --send group
# 发到管理群（TELEGRAM_MANAGEMENT_CHAT_ID）或任意群/个人 chat id
docker compose exec cicdanalysis python3 -m cicdanalysis analyze --job testa-ug-app-ios --build 111 --send management
docker compose exec cicdanalysis python3 -m cicdanalysis analyze --job testa-ug-app-ios --build 111 --send -100xxxxxxxxxx
```

### 群内命令 /analyze

在 TESTA/UAT/PROD 发布群（或管理群，范围为全部环境）发送：

- `/analyze`：列出本群环境**今天**失败的构建，按时间从近到远，每个 Job 只列最近一次，最多 5 个；
  标出触发人、错误分类和根因摘要，之后又成功过的标“已恢复”；随后附上最近一次失败的完整报告。
- `/analyze <job 或服务名> [构建号]`：某个构建的完整报告；不写构建号时取该 Job 最近一次失败。服务名按本群环境匹配 Job
  （如 UAT 群里 `activity-rpc` → `uat-activity-rpc-prod`）。

已分析过的构建直接复用结论，只有还没分析的才会拉 Jenkins 日志并调用大模型。Bot 开着隐私模式时，
群里有多个 Bot 需发送 `/analyze@<本 Bot 用户名>`（从输入框 “/” 菜单点选会自动带上）；想直接发 `/analyze`，
在 BotFather 执行 `/setprivacy` → Disable，然后把 Bot 移出群再拉回。

读取 Pipeline 节点日志需要 Jenkins 账号能访问 `/<build>/execution/node/<id>/wfapi/*`（与 `wfapi/describe` 同权限）。

## 统计口径

成功率为 `SUCCESS / (SUCCESS + FAILURE + UNSTABLE)`；取消中的构建与运行中的构建不进入成功率分母。所有自然日、周、月边界使用 `APP_TIMEZONE`（默认 `Asia/Kuala_Lumpur`）。触发失败不等于责任归属；故障表另存 `responsibility_type`。

## 每日报告定时发送

默认使用马来西亚时区，每天 `09:00` 分别发送昨天的 TESTA、UAT、PROD 报告：

```env
APP_TIMEZONE=Asia/Kuala_Lumpur
REPORT_DAILY_HOUR=9
REPORT_DAILY_MINUTE=0
```

| 环境 | Telegram 群 ID |
|---|---:|
| TESTA | `-1003919548725` |
| UAT | `-5592801576` |
| PROD | `-1003412281586` |

发送成功后会写入 PostgreSQL `report_deliveries`。容器重启不会重复发送，发送失败的群会自动重试。

手动查看指定日期报告：

```bash
docker compose exec cicdanalysis python3 -m cicdanalysis report \
  --kind daily --date 2026-09-07 --environment testa
```

## PostgreSQL

复制 `.env.example` 后必须修改数据库密码：

```env
POSTGRES_DB=cicdanalysis
POSTGRES_USER=cicdanalysis
POSTGRES_PASSWORD=replace-with-a-strong-password
```

应用的 `DATABASE_URL` 由 Docker Compose 自动生成。数据库文件保存在命名卷 `postgres-data`。

备份：

```bash
docker compose exec -T postgres pg_dump -U cicdanalysis -d cicdanalysis -Fc > cicdanalysis.dump
```

恢复：

```bash
docker compose exec -T postgres pg_restore -U cicdanalysis -d cicdanalysis --clean --if-exists < cicdanalysis.dump
```

## 上线建议

先用实际历史构建校准规则库。生产环境请使用新生成的 Jenkins/Telegram Token，通过 Secret 注入，不要写入镜像或代码仓库。
