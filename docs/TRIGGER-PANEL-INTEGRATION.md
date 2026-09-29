# Telegram 研发触发面板接入

## 为什么 Jenkins API 识别不到真实触发人

实际链路是：

```text
开发人员点击 Telegram 触发面板
        ↓
触发面板服务调用 Jenkins API
        ↓
Jenkins 记录远程调用或服务账号
```

因此 Jenkins `actions.causes` 看到的不是点击按钮的开发人员，甚至没有用户信息。仅依靠 Jenkins API 无法还原 Telegram 点击者。

当前版本支持两种实名采集方式：

1. 默认由 `@cicd_analysis_bot` 监听发布群，解析“📣 Jenkins 发布触发结果”正文，并用构建地址精确关联。
2. 触发面板直接调用 Trigger Webhook，并传递稳定的 `telegram_user_id`（推荐）。

群消息方式无需预先维护开发人员名单，会按消息中的 `姓名 + @username` 自动建立用户。它要求 Bot 能收到对应群的消息，且该 Token 没有被另一个 `getUpdates` 客户端消费。

更完整的链路是由触发面板在取得 Jenkins 队列号/构建号后，同时调用：

```text
POST /api/v1/webhooks/trigger
```

## 多开发人员身份模型

每次点击都读取 Telegram Update/CallbackQuery 中本次点击者的信息：

| cicdanalysis 字段 | Telegram 字段 | 说明 |
|---|---|---|
| `telegram_user_id` | `callback_query.from.id` | 稳定唯一标识，必须优先传递 |
| `telegram_username` | `callback_query.from.username` | 可能被用户修改 |
| `trigger_name` | `first_name + last_name` | 报告显示名 |
| `telegram_chat_id` | `callback_query.message.chat.id` | TESTA/UAT/PROD 群 ID |

系统不会把群绑定成一个用户。群里 Mars、mew、antti、zero 或其他开发人员点击时，都会按各自 `telegram_user_id` 建立独立记录；无需预先维护人员名单。

## 直接监听群消息

```env
TELEGRAM_LISTEN_GROUP_MESSAGES=true
TELEGRAM_POLL_TIMEOUT=30
```

系统仅接受 `.env` 中 TESTA、UAT、PROD（以及可选管理群）的消息，忽略其他群和无关消息。解析字段包括触发人、项目、分支、时间、服务、命名空间、HTTP 状态、队列地址和构建地址。消息 `chat_id + message_id` 作为幂等键，编辑消息不会重复计数。

如果日志出现 `Conflict: terminated by other getUpdates request`，说明同一个 Bot Token 正被其他进程长轮询。只能保留一个消费者；此时设置 `TELEGRAM_LISTEN_GROUP_MESSAGES=false`，并使用下面的 Trigger Webhook。

## 接口调用时机

触发服务完成以下步骤后立即调用：

1. 收到开发人员的 Telegram CallbackQuery。
2. 调用 Jenkins Build API。
3. 从 Jenkins 响应获得 `queue_id`/`queue_url`。
4. 如已轮询到构建，则同时获得 `build_number`/`build_url`。
5. 调用 cicdanalysis Trigger Webhook。
6. 继续向原 Telegram 群发送“Jenkins 发布触发结果”。

只要 `queue_id` 或 `build_number` 至少有一个，cicdanalysis 就能在构建采集完成时关联；两者都有时最准确。

## 完整请求示例

```bash
curl --fail --silent --show-error \
  -X POST "${CICD_ANALYSIS_URL}/api/v1/webhooks/trigger" \
  -H "Authorization: Bearer ${TRIGGER_WEBHOOK_SECRET}" \
  -H "Content-Type: application/json" \
  --data '{
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
    "queue_url": "https://ugjekins.ugmid888.com/queue/item/32727/",
    "build_number": 101,
    "build_url": "https://ugjekins.ugmid888.com/job/statistics-testa/101/"
  }'
```

响应：

```json
{
  "accepted": true,
  "event_id": "tg--1003919548725-32727",
  "trigger_id": 1,
  "matched": false,
  "build_id": null,
  "job_name": "statistics-testa",
  "build_number": 101,
  "queue_id": 32727,
  "trigger_name": "Mars Stephen",
  "telegram_username": "marsstephen"
}
```

`matched=false` 不是失败，只表示 Jenkins Build 尚未被采集；后台采集后会自动关联。

## 触发面板伪代码

```python
def on_release_button(callback_query, release):
    developer = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    jenkins_result = trigger_jenkins(release)

    post_json(
        CICD_ANALYSIS_URL + "/api/v1/webhooks/trigger",
        headers={"Authorization": "Bearer " + TRIGGER_WEBHOOK_SECRET},
        json={
            "event_id": f"tg-{chat_id}-{jenkins_result.queue_id}",
            "trigger_name": " ".join(filter(None, [developer.get("first_name"), developer.get("last_name")])),
            "telegram_username": developer.get("username", ""),
            "telegram_user_id": str(developer["id"]),
            "telegram_chat_id": str(chat_id),
            "job_name": release.job_name,
            "branch": release.branch,
            "triggered_at": now_iso8601(),
            "service_type": release.service_type,
            "service_name": release.service_name,
            "namespace": release.namespace,
            "environment": release.environment,
            "http_status": jenkins_result.http_status,
            "queue_id": jenkins_result.queue_id,
            "queue_url": jenkins_result.queue_url,
            "build_number": jenkins_result.build_number,
            "build_url": jenkins_result.build_url,
        },
    )
```

## 幂等与重试

- `event_id` 由触发系统生成，并保证每次发布唯一。
- 推荐格式：`tg-<chat_id>-<queue_id>`。
- 网络失败可以用相同 `event_id` 重试，不会重复创建记录。
- 不要用 Telegram username 作为唯一身份；必须尽量传 `telegram_user_id`。

## 每个环境群

保持现有群映射：

| 环境 | Telegram Chat ID |
|---|---:|
| TESTA | `-1003919548725` |
| UAT | `-5592801576` |
| PROD | `-1003412281586` |

各环境报告仍发送至对应群。报告会显示：

```text
触发来源
👤 群聊/面板实名触发：415 次 / 18 人
🔗 Jenkins 识别：0 次
❔ 未识别触发人：0 次
⏳ 待关联触发事件：0 次

触发人构建情况
Mars Stephen (@marsstephen)：31 次，成功 30，失败 1，取消 0，成功率 96.77%
Mew (@slo_dream_03)：28 次，成功 27，失败 1，取消 0，成功率 96.43%
...
```

Web Dashboard 的“参与开发”统计是去重人数，“最近触发记录”会显示每次事件及关联状态。完整人员列表可通过：

```text
GET /api/v1/users?days=30
```

查询触发事件：

```text
GET /api/v1/triggers?days=7&environment=testa&limit=100
```

## 历史数据修复

新接口会准确处理接入后的构建。已经采集成“自动触发”的历史 Build，如果触发面板数据库或日志中仍保留触发人、Job、queue/build 信息，可以按原时间逐条重放同一接口。即使 Build 已存在，系统也会反向修正 `trigger_user_id` 和 `trigger_source`。

仅凭 Telegram Bot API 通常无法可靠读取完整历史群消息，因此历史修复应优先使用触发面板自身的数据库或日志。
