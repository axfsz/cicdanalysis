from __future__ import annotations

import hashlib
import re

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\b(?:ghp|gho|ghs|glpat|xox[abp])[-_][A-Za-z0-9_-]{16,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), "[REDACTED_TG_BOT_TOKEN]"),
    (re.compile(r"(?i)(--password[= ])(?!-)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:token|password|passwd|secret|api[_-]?key)\s*[=:]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"), "[REDACTED_JWT]"),
]

RULES = [
    ("DEPENDENCY_ERROR", "ERR_PNPM_OUTDATED_LOCKFILE", r"ERR_PNPM_OUTDATED_LOCKFILE|Cannot install with \"frozen-lockfile\"|lockfile needs to be updated|lockfile.*out.of.date", "依赖锁文件与项目清单不一致", "更新 lockfile 并提交，或核对 CI 的 frozen-lockfile 设置", "CODE"),
    ("DEPENDENCY_ERROR", "PACKAGE_INSTALL_FAILED", r"npm ERR!|pnpm.*ERR_|yarn error|Could not resolve dependencies", "依赖安装或解析失败", "检查包版本、镜像源、lockfile 与网络", "CODE"),
    ("SIGNING_ERROR", "IOS_SIGNING_FAILED", r"No profiles for '|requires a provisioning profile|[Pp]rovisioning profile .*(?:expired|doesn't include|doesn't match)|Code ?Sign(?:ing)? Error|No signing certificate|errSecInternalComponent|certificate has expired", "iOS 签名证书或描述文件问题", "检查证书/描述文件是否过期、Bundle ID 与 Team 是否匹配、构建机钥匙串是否已解锁", "CONFIG"),
    ("DEPENDENCY_ERROR", "COCOAPODS_FAILED", r"CocoaPods could not find compatible versions|\[!\] Unable to find a specification|pod install.*(?:failed|error)", "CocoaPods 依赖解析失败", "执行 pod repo update 后重试，核对 Podfile.lock 与 Podfile", "CODE"),
    ("TEST_ERROR", "TESTS_FAILED", r"Tests run:.*Failures: [1-9]|There (?:was|were) \d+ test failures?|^--- FAIL:|^FAIL\s+\S+|\d+ failing\b|Test Suites:.*\d+ failed", "单元测试未通过", "查看失败用例断言，本地复现后修复代码或测试", "CODE"),
    ("RESOURCE_ERROR", "OUT_OF_MEMORY", r"OutOfMemoryError|JavaScript heap out of memory|exit code 137|OOMKilled|Cannot allocate memory", "构建或容器内存不足被终止", "调大构建内存/Node max-old-space-size，或检查节点资源", "INFRASTRUCTURE"),
    ("CODE_ERROR", "GO_PACKAGE_NOT_FOUND", r"\.go:\d+:\d+: package \S+ is not in (?:std|GOROOT)|no required module provides package|cannot find package \"|cannot find module providing package|missing go\.sum entry", "Go 代码 import 的包在本次构建的代码中不存在（import 路径写错、包目录未提交或被 .dockerignore 排除，或 go.mod/go.sum 缺少该依赖）", "按报错行的文件和 import 路径核对：包目录是否已提交到当前分支/Commit、路径大小写是否一致；外部依赖执行 go mod tidy 后提交 go.mod/go.sum；本地 go build ./... 复现", "CODE"),
    ("CODE_ERROR", "COMPILE_FAILED", r"\.dart:\d+:\d+: Error: |\.(?:swift|mm?|c|cc|cpp|h):\d+:\d+: (?:fatal )?error: |^e: \S+\.kts?:|Compilation failed|BUILD FAILED|SyntaxError|TypeScript error|error TS\d+:|^\s*\S+\.go:\d+:\d+: |\.java:\[\d+,\d+\] |cannot find symbol|go build.*failed", "代码编译失败", "查看首个编译错误并在相同工具链下复现", "CODE"),
    ("GIT_ERROR", "GIT_CHECKOUT_FAILED", r"fatal:.*(?:repository|reference|revision)|Permission denied \(publickey\)|Couldn.t find any revision", "Git 拉取、凭据或分支解析失败", "检查仓库权限、凭据、分支及 Commit 是否存在", "CONFIG"),
    ("DOCKER_ERROR", "DOCKER_BUILD_FAILED", r"failed to solve|docker build.*(?:failed|error)|no space left on device", "Docker 镜像构建失败", "查看 Dockerfile 失败层；若磁盘已满先清理构建节点", "INFRASTRUCTURE"),
    ("CODE_ERROR", "XCODE_BUILD_FAILED", r"\*\* (?:ARCHIVE|BUILD|EXPORT) FAILED \*\*|xcodebuild: error|error: exportArchive", "Xcode 构建/导出失败", "查看 ** FAILED ** 之前的第一条 error，在同版本 Xcode 下复现", "CODE"),
    ("CODE_ERROR", "GRADLE_TASK_FAILED", r"Execution failed for task '", "Gradle 任务执行失败", "查看 What went wrong 段落，定位失败任务并本地复现", "CODE"),
    ("CODE_ERROR", "BUILD_SCRIPT_FAILED", r"ELIFECYCLE|Command failed with exit code|npm ERR! code [A-Z]|error during build|Build failed with \d+ errors?|\[ERROR\] Failed to execute goal", "构建脚本执行失败", "查看该行之前的首个报错（通常是编译/打包错误）并在本地复现", "CODE"),
    ("HARBOR_ERROR", "HARBOR_PUSH_FAILED", r"harbor.*(?:timeout|denied|unauthorized|connection)|denied: requested access|blob upload unknown", "Harbor 登录、网络或推送失败", "检查 Harbor 健康、凭据、项目权限和网络", "INFRASTRUCTURE"),
    ("K8S_ERROR", "ROLLOUT_TIMEOUT", r"timed out waiting for the condition|progress deadline exceeded|rollout.*timeout", "Kubernetes Rollout 超时", "检查 Pod 事件、探针、镜像拉取和资源限制", "INFRASTRUCTURE"),
    ("K8S_ERROR", "KUBECTL_FAILED", r"Error from server|kubectl.*(?:error|failed)|ImagePullBackOff|CrashLoopBackOff", "Kubernetes 发布或运行异常", "检查 kubectl 输出、Deployment、Pod 事件与日志", "INFRASTRUCTURE"),
    ("HELM_ERROR", "HELM_FAILED", r"UPGRADE FAILED|INSTALLATION FAILED|another operation.*in progress", "Helm 安装或升级失败", "检查 release 状态、values 和残留操作", "CONFIG"),
    ("JENKINS_AGENT_ERROR", "AGENT_LOST", r"agent.*(?:offline|disconnected)|channel is already closed|Cannot contact.*agent|script returned exit code 143", "Jenkins Agent 离线或连接中断", "检查 Agent 状态、Remoting、节点资源和网络", "INFRASTRUCTURE"),
    ("NETWORK_ERROR", "NETWORK_FAILURE", r"Connection timed out|Connection refused|Temporary failure in name resolution|Could not resolve host|TLS handshake timeout", "网络、DNS 或上游连接异常", "检查 DNS、路由、防火墙、代理及目标服务健康", "INFRASTRUCTURE"),
    ("SECRET_ERROR", "AUTH_FAILED", r"401 Unauthorized|403 Forbidden|authentication required|invalid credentials", "认证凭据无效或权限不足", "检查对应 Secret、Token 有效期与最小权限", "CONFIG"),
    ("TIMEOUT", "BUILD_TIMEOUT", r"Timeout has been exceeded|Cancelling nested steps due to timeout|Build timed out", "构建步骤超时", "定位耗时阶段并检查资源、网络和超时阈值", "INFRASTRUCTURE"),
]


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Jenkins Timestamper prefix, e.g. "[2026-09-28T08:39:17.434Z] "
TIMESTAMP = re.compile(r"(?m)^\[\d{4}-\d\d-\d\dT[\d:.]+Z?\] ?|^\d\d:\d\d:\d\d(?:\.\d+)? ")
# Docker BuildKit step output, e.g. "#12 4.154 main.go:13:2: ..." or "4.154 main.go:..." in the error summary.
BUILDKIT = re.compile(r"(?m)^#\d+ \d+\.\d{3} |^\d+\.\d{3} ")


def clean(text: str) -> str:
    """Strip ANSI colours, per-line timestamps and BuildKit step prefixes (they break ^-anchored rules and error fingerprints), then redact."""
    return redact(BUILDKIT.sub("", TIMESTAMP.sub("", ANSI.sub("", text or "")).replace("\r\n", "\n")))
NOISE = re.compile(r"^\s*\[Pipeline\]|^\s*$")
EXCERPT_BEFORE, EXCERPT_AFTER, EXCERPT_LINES = 3, 5, 14


def excerpt(log: str, position: int | None) -> str:
    """A short, redacted window of the log around the matched error (or the tail)."""
    lines = log.splitlines()
    if position is None:
        useful = [x for x in lines if not NOISE.search(x) and not x.startswith("Finished:")]
        picked = useful[-EXCERPT_LINES:]
    else:
        index = log.count("\n", 0, position)
        before = [x for x in lines[max(0, index-EXCERPT_BEFORE*4): index] if not NOISE.search(x) and not NOT_ERROR.search(x)]
        after = [x for x in lines[index: index+EXCERPT_AFTER+1] if not NOISE.search(x)]
        picked = (before[-EXCERPT_BEFORE:] + after)[:EXCERPT_LINES]
    return "\n".join(x.rstrip()[:240] for x in picked)


def redact(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def normalize(line: str) -> str:
    line = redact(line.strip())
    line = re.sub(r"https?://\S+", "<URL>", line)
    line = re.sub(r"\b[0-9a-f]{7,64}\b", "<HEX>", line, flags=re.I)
    line = re.sub(r"\b\d{2,}\b", "<N>", line)
    return re.sub(r"\s+", " ", line)[:1000]


def _match(text: str):
    for c, ec, pattern, rc, sug, resp in RULES:
        m = re.search(pattern, text, flags=re.I | re.M)
        if m: return (c, ec, rc, sug, resp), m
    return None, None


ERROR_LINE = re.compile(r"(?i)\berror\b|failed|failure|exception|fatal|timed out|timeout (?:has been )?exceeded|denied|not found|exit code [1-9]"
                        r"|^\S+\.go:\d+:\d+: |is not in (?:std|GOROOT)|no required module provides|cannot find package|undefined: ")
# Compiler warnings/notes often contain "error" (e.g. "statusOfValueForKey:error:"); they are not the cause.
NOT_ERROR = re.compile(r"(?i)\b(?:warning|note|deprecated)\b|will become an error|skipped due to earlier failure")


def is_error_line(line: str) -> bool:
    return bool(ERROR_LINE.search(line)) and not NOT_ERROR.search(line) and not NOISE.search(line) and not line.startswith("Finished:")


def _first_error_position(text: str) -> int | None:
    """Position of the first error-looking line (the first error is usually the cause, later ones are fallout)."""
    offset = 0
    for line in text.splitlines(keepends=True):
        if is_error_line(line):
            return offset
        offset += len(line)
    return None


def analyze(log: str, stages: list[dict] | None = None, result: str = "FAILURE", focus: str = "") -> dict:
    """Rule-based diagnosis.

    ``focus`` is the log of the failed Pipeline step(s) (and failed downstream
    builds); it is searched first because the whole console mixes all stages.
    """
    safe = clean(log)
    focus = clean(focus)
    failed_stage = next((s.get("name") for s in (stages or []) if s.get("status") in {"FAILED", "FAILURE"}), None)
    source, position = safe, None
    if result == "ABORTED":
        category, code, cause, suggestion, owner, confidence = "MANUAL_ABORT", "BUILD_ABORTED", "构建被人工或上游任务取消", "确认取消原因，无需归责给触发人", "NONE", .95
        matched = "Build aborted"
    else:
        category, code, cause, suggestion, owner, confidence = "UNKNOWN", "UNKNOWN", "暂未匹配已知故障规则", "查看完整日志首个异常，并补充故障规则", "UNKNOWN", .3
        # With a failed-step log available, rules only look there: the whole console
        # mixes every stage/branch and matches harmless warnings first.
        if focus.strip():
            source = focus
            rule, m = _match(focus)
        else:
            rule, m = _match(safe)
        if rule:
            (category, code, cause, suggestion, owner), confidence = rule, .9
            position = m.start()
        else:
            position = _first_error_position(source)
        if position is None: matched = "Unknown failure"
        else:
            line_start = source.rfind("\n", 0, position) + 1
            line_end = source.find("\n", position)
            matched = source[line_start: line_end if line_end >= 0 else len(source)]
    normalized = normalize(matched)
    fingerprint = hashlib.sha256(f"{failed_stage or '-'}|{code}|{normalized}".encode()).hexdigest()[:20]
    return {"failed_stage": failed_stage, "error_category": category, "error_code": code,
            "root_cause": cause, "suggestion": suggestion, "responsibility_type": owner,
            "error_fingerprint": fingerprint, "confidence": confidence, "normalized_error": normalized,
            "error_excerpt": excerpt(source, position) if result != "ABORTED" else "", "analysis_source": "RULE"}
