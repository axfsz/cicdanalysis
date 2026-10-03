# Changelog

## 0.8.1

- 修复：同一条群消息里先后发出的多次批量发布，只有第一次被识别，之后的构建显示为“未识别触发人”。
  发布机器人会把构建面板消息编辑成“🚀 Jenkins 批量发布触发通知”再回到面板，同一个 message_id 承载多次发布；
  触发事件 ID 只用“群 + 消息 ID (+ Job)”，后一次发布覆盖了前一次已关联的记录（触发时间不更新），
  它的构建再也匹配不到触发人，“待关联”也保持 0，“批量指令”只算 1 条。
  现在事件 ID 与批量 ID 带上“触发时间”，同一条通知的状态编辑仍是同一事件；
  0.8.0 已入库的记录在回放历史时按“消息 + 触发时间”沿用，不会重复。

## 0.8.0

### 批量发布触发人识别
- 识别“🚀 Jenkins 批量发布触发通知”（以及“批量发布触发结果”）：标题中多了“批量”，且服务以 `• <job>` 列表给出、没有“触发项目”，
  0.7.x 无法解析，批量发布的构建全部落入“自动触发”。现在每个服务生成一条触发记录（同一 `batch_id`），按 Job + 触发时间
  或行内的构建/队列地址关联到各自的 Jenkins 构建。列表之后的“构建面板”文本会被忽略。
- `trigger_events` 新增 `trigger_mode`（SINGLE/BATCH）、`batch_id`、`batch_size`（自动迁移）。
- 触发时间统一按 UTC 存储（SQLite 下带 `+08:00` 的时间与 UTC 日期边界做文本比较会漏掉晚间记录）。
- Jenkins 触发原因细分：`TIMER` / `SCM` / `UPSTREAM` 才算自动构建；`REMOTE`（发布机器人调用 buildWithParameters）、
  服务账号等没有关联到人的构建统计为“未识别触发人”，不再混进“自动触发”。

### 失败自动分析与推送
- 修复：失败构建几乎不会自动推送分析、只能手动 `/analyze`。原因是 Jenkinsfile 的 post 步骤先发“❌ Jenkins 发布通知”
  （“耗时: 49 sec and counting”），此时 Jenkins 仍报告构建运行中，服务不分析；等构建结束后轮询再看到它时，
  旧的推送条件“本次变为最终态且构建号大于 last_build_number”已不成立（运行中那次采集已更新了 last_build_number）。
- 新规则：失败（FAILURE/UNSTABLE）构建只要已分析、尚未推送、且结束于 `FAILURE_NOTIFY_MAX_AGE_MINUTES`（默认 180）分钟内，
  就自动推送一次；webhook / 群内发布通知指定的构建不受时间限制。历史回灌的旧失败仍只入库不推送。
- 发布通知或 webhook 到达时构建仍在运行：每 `FOLLOW_UP_SECONDS`（默认 15）秒重查，直到结束后立刻分析推送（最多 `FOLLOW_UP_MAX_MINUTES`）。
- 日志读取失败时不再跳过推送：下次轮询重新分析后照常推送。
- `failure_notifications` 新增 `source`：AUTO（自动）/ MANUAL（CLI `analyze --send`）/ COMMAND（群内 `/analyze`）。
  `/analyze` 在构建所属群发出完整报告后记为已推送，自动流程不再重复发送。
- ABORTED 默认不推送（`TELEGRAM_NOTIFY_ABORTED=true` 可开启）。

### 统计面板与报告
- 新增“失败自动分析”面板：每个失败构建的触发人、分类与根因、推送方式（自动 / `/analyze` / 手动 / 未推送）及自动推送延迟。
- 新增卡片：批量发布（构建数 / 批量指令数）、自动触发、未识别触发人、失败自动推送（自动推送数 / 失败数，平均延迟）。
- “触发人构建情况”新增“批量”列，“自动触发”与“未识别触发人”分开显示；“最近触发记录”新增时间与方式（单个 / 批量 N）。
- 未关联提示只统计未识别触发人的构建，不含定时/SCM/上游构建；面板每 60 秒自动刷新。
- `/api/v1/overview` 新增 `automatic`、`batch_builds`、`batch_messages`、`analysis`；`/api/v1/triggers` 新增 `trigger_mode`、`batch_id`、`batch_size`。
- Telegram 日/周/月报增加批量发布、自动触发与“失败自动分析”统计。

## 0.7.6

- 发布群内新增 `/analyze` 命令：列出本群环境今天失败的构建（最近优先、每个 Job 取最近一次、最多 5 个，标注“已恢复”），
  并附最近一次失败的完整报告；`/analyze <job|服务名> [构建号]` 查看单个构建。只对尚未分析的构建调用 Jenkins 与大模型。
- Bot 轮询在 `TELEGRAM_LISTEN_GROUP_MESSAGES=false` 时也会启动以接收命令（`TELEGRAM_COMMANDS=false` 可关闭），
  启动时向 Telegram 注册命令菜单。命令只在三个发布群与管理群内生效，同一群同时只跑一个 /analyze。

## 0.7.5

- `analyze` 新增 `--send group|management|<chat_id>` 与 `--dry-run`：手动发送或预览某个构建的分析报告；
  发到发布群时回复该群的“❌ Jenkins 发布通知”，并记为已发送，自动流程不会再发第二次。
- `analyze` 遇到库里还没有的构建时，采集过程不再自动发群，是否发送只由 `--send` 决定。
- 错误指纹彻底去掉时间戳：Pipeline 步骤日志里带缩进或 BOM 的 `[2026-..T..Z]` 前缀、以及行内的 ISO 时间都会先去掉
  （0.7.4 中 #111 的 normalized_error 仍带时间戳，导致同一错误在不同构建上指纹不同、大模型结论无法复用）。

## 0.7.4

- 大模型配置兼容 `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` / `AI_MAX_TOKENS`（同时存在时 `LLM_*` 优先，空值会跳过）；
  之前只认 `LLM_*`，用 `AI_*` 配置时会静默退回规则分析。
- 新增 `LLM_MAX_TOKENS`（默认 1200）；`LLM_BASE_URL` 写成完整的 `.../chat/completions` 地址也能用。
- 启动日志打印大模型是否启用及模型名、地址（不打印密钥）。

## 0.7.3

- 用 activity-rpc-testa #717 真实日志校准：docker build 里的 `go build` 失败，根因是
  `lucky_value_wager_watermark.go:13:2: package UltraGaming/services/game/provider/accountmeta is not in std`
  （import 的包目录不在本次构建的代码里）。0.7.2 被末尾的 `ERROR: failed to solve` 带偏，报成“Docker 镜像构建失败 / 基础设施”。
- 新增规则 GO_PACKAGE_NOT_FOUND（CODE_ERROR，归属代码）：`is not in std/GOROOT`、`no required module provides package`、
  `cannot find package`、`missing go.sum entry`。
- 分析前去掉 BuildKit 行首的步骤号和耗时（`#12 4.154 `、`4.154 `）：Go/编译规则的 `^` 锚点生效，指纹不随耗时变化。
- 大模型提示词的“报错片段”也会带上 `xxx.go:行:列:` 这类不含 error 字样的编译报错行。

## 0.7.2

- 用 testa-ug-app-ios #111 真实日志校准：根因是 Dart 编译错误（app_router.dart 传了 GameViewView 不接受的 `platformName` 参数），
  0.7.1 只报出表层的“Xcode ARCHIVE FAILED”。
- 新增编译错误格式：Dart/Flutter（`.dart:行:列: Error:`）、Swift/ObjC/C（`: error:`）、Kotlin（`e: *.kt`）。
- 分析前去掉 Jenkins Timestamper 行首时间戳：规则 `^` 锚点恢复生效，同一错误在不同构建的指纹一致（大模型结论可复用）。
- 报错行识别排除 warning/note/deprecated（Xcode 大量告警中含 “error” 字样），也不再把 `# timeout=10` 当报错。
- 关键日志片段只取报错前 3 行（跳过告警）+ 报错后 5 行。

## 0.7.1

- “❌ Jenkins 发布通知”里 `任务` 为 `-` 时（流水线早期失败），依次从 `Jenkins=` 地址（含 `/view/testa/job/...` 视图路径）、
  `发布服务名称` 取 Job；只有服务名时按所在群的环境在 Jenkins Job 列表中匹配（如 `activity-rpc` → `uat-activity-rpc-prod`）。
- 新增规则：iOS 签名/描述文件、Xcode 构建/导出失败、CocoaPods、Gradle 任务失败。
- 端到端测试覆盖：TESTA 群 iOS 失败通知 → 读取该 Job 该构建日志 → 大模型分析 → 回复到群内原消息。

## 0.7.0

- 根因定位改为读取**失败步骤自己的日志**：通过 Jenkins Pipeline REST API（`execution/node/<id>/wfapi/log`）取失败节点日志，
  并读取失败的下游 Job（`build job:` / Parameterized Trigger）的控制台日志。规则只在这些日志里匹配，
  不再被其他并行分支或无关阶段里的警告干扰（例如可选镜像源的 `Could not resolve host` 会把 Go 编译失败误判为网络故障）。
- 未命中规则时，定位到**第一条**报错行（原来取最后一条，通常是连锁报错）。
- 新增大模型根因分析：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（OpenAI 兼容 Chat Completions，默认 DeepSeek `deepseek-chat`）。
  规则结果作为提示一并提供；模型不可用、超时或返回异常时自动回退规则结论。模型给出的“关键日志”必须逐字存在于日志中，否则丢弃。
- 发送前脱敏增强：URL 内凭据、Bearer、私钥、AWS Key、GitHub/GitLab/Slack/Telegram Token、`--password`。
- 成本控制：每个构建只分析一次（原来每次轮询都会重新下载失败构建的完整日志）；相同错误指纹 24 小时内复用上次模型结论。
- 失败报告显示“分析方式：大模型 deepseek-chat / 规则匹配”。
- 新增命令 `python3 -m cicdanalysis analyze --job <Job> --build <号>`：对指定构建重新分析并打印结果，便于核对。
- 规则补充：Go / Java 编译错误格式。

## 0.6.2

- 修复：服务运行时执行 `docker compose exec cicdanalysis python3 -m cicdanalysis telegram-backfill` 报
  `sqlite3.OperationalError: database is locked`。Telethon 会话文件同一时间只能被一个进程打开；
  现在该命令会请求运行中的服务，用它已有的连接回放历史（新接口 `POST /api/v1/admin/telegram-backfill`，
  `WEBHOOK_SECRET` 鉴权），服务未运行时才直接连接。
- 真人账号监听启动时自动回放最近 `TELEGRAM_USER_BACKFILL_DAYS`（默认 2）天的群消息，补上停机/重启期间漏掉的消息。

## 0.6.1

- 新增真人 Telegram 账号读取发布群（Telethon/MTProto）：三个发布群分别由 @ugopsbot、@uguatdeploybot、@ugprodopsbot 发消息，
  Bot 之间收不到彼此的群消息；用一个群成员账号监听，发布机器人无需改代码。
- 新增命令 `telegram-login`（一次性登录）和 `telegram-backfill --days N`（回放群历史，补齐已采集构建的触发人）。
- `TELEGRAM_USER_TRUSTED_SENDERS` 限定只接受发布机器人发出的消息，防止群成员手工伪造。
- 迟到/回放的触发通知按触发时间匹配已采集的构建，不再只看最近 3 个构建。
- Docker Compose 新增 `cicd-data` 卷保存登录会话；镜像内置 telethon。

## 0.6.0

- 发布群失败分析：解析群内“❌ Jenkins 发布通知”，自动拉取该构建的 Jenkins 日志、定位失败阶段与关键日志，
  生成分析报告并**回复到对应群的原消息**；每个构建只报告一次（新表 `failure_notifications`）。
- 失败报告新增：触发人、错误码与置信度、同类错误累计次数、脱敏后的关键日志片段、完整日志链接。
- 新增规则：单元测试失败、内存不足/OOM、构建脚本失败（ELIFECYCLE / Maven goal 等）、TypeScript 编译错误。
- 修复：`pnpm install --frozen-lockfile` 命令行本身会被误判为“lockfile 不一致”，导致前端失败几乎全部被错误归类。
- 触发人统计：支持 UAT/PROD 群的“🚀 Jenkins 发布触发通知”（无构建地址）。按 Job + 触发时间关联随后启动的构建，
  或由“Jenkins 发布通知”中的构建号精确关联。此前这类消息因缺少构建号被直接丢弃，UAT/PROD 实名统计为 0。
- 环境归属按消息所在群判定；`uat-xxx-prod` 这类 Job 不再被识别为 PROD（此前 UAT 构建计入 PROD 统计、失败通知发到 PROD 群）。
- 新增 `POST /api/v1/webhooks/release-message`：发布面板 Bot 可直接转发它发到群里的消息原文（见 README）。
- 单条群消息解析失败不再中断整批 getUpdates 处理。
- 数据库自动迁移：新增 `release_results`、`failure_notifications` 表与 `build_failures.error_excerpt` 列，启动时自动完成。

## 0.5.0

- 新增 `@cicd_analysis_bot` 发布群消息长轮询采集。
- 解析“Jenkins 发布触发结果”的触发人、项目、分支、队列及构建地址。
- 使用群 ID + 消息 ID 幂等写入，并通过 Job + Build/Queue 精确关联构建。
- 支持同一群内任意数量开发人员，按 Telegram 身份分别统计。
- 新增 Jenkins Build 参数、Cause 与控制台日志身份回退识别。
- Dashboard 新增触发身份未接入或部分缺失提示。
- 保留分环境定时日报、指定日期查询与 Docker Compose PostgreSQL 16。
