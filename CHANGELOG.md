# Changelog

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
