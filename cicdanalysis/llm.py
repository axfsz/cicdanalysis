"""Root-cause analysis with an OpenAI-compatible chat model (DeepSeek by default).

The rule engine in analyzer.py always runs first; its result is passed to the
model as a hint and is kept as the answer whenever the model is not configured
or its call fails. Logs are redacted before they leave the service.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .analyzer import NOISE, clean, is_error_line, redact

log = logging.getLogger(__name__)

CATEGORIES = ["CODE_ERROR", "TEST_ERROR", "DEPENDENCY_ERROR", "GIT_ERROR", "DOCKER_ERROR", "HARBOR_ERROR",
              "K8S_ERROR", "HELM_ERROR", "JENKINS_AGENT_ERROR", "NETWORK_ERROR", "SECRET_ERROR",
              "RESOURCE_ERROR", "CONFIG_ERROR", "SIGNING_ERROR", "TIMEOUT", "UNKNOWN"]
RESPONSIBILITY = ["CODE", "CONFIG", "INFRASTRUCTURE", "NONE", "UNKNOWN"]

SYSTEM_PROMPT = f"""你是资深 DevOps 工程师，负责分析 Jenkins 发布流水线失败的根因。
只依据给出的日志下结论；日志里没有证据的内容不要猜测。首个真正的报错通常是根因，后续报错多为连锁反应。
只输出一个 JSON 对象，字段：
- error_category：取值之一 {", ".join(CATEGORIES)}
- root_cause：中文，1-3 句，指出具体出错的文件/命令/服务/依赖及原因
- suggestion：中文，给出可直接执行的修复步骤（最多 4 步，用分号分隔）
- responsibility_type：取值之一 {", ".join(RESPONSIBILITY)}（CODE=代码或测试问题，CONFIG=流水线/凭据/配置，INFRASTRUCTURE=节点/网络/镜像仓库/集群）
- key_lines：数组，最多 8 行，逐字摘自日志、最能说明根因的原始行
- confidence：0 到 1 的小数"""


class LLMAnalyzer:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60, max_input_chars: int = 16000,
                 max_tokens: int = 1200):
        # Accept a full endpoint URL too ("https://api.deepseek.com/v1/chat/completions").
        self.base_url = re.sub(r"/chat/completions/?$", "", (base_url or "").strip()).rstrip("/")
        self.api_key, self.model = (api_key or "").strip(), (model or "").strip()
        self.timeout, self.max_input_chars = timeout, max_input_chars
        # Reasoning models spend part of the budget before the JSON answer; keep a sane floor.
        self.max_tokens = max(400, max_tokens)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def label(self) -> str:
        return self.model

    # -- prompt -----------------------------------------------------------------------
    def build_prompt(self, context: dict, rule: dict, focus: str, console: str) -> str:
        budget = self.max_input_chars
        focus = clean(focus)
        console = clean(console)
        sections = [
            "## 构建信息",
            *(f"{k}: {v}" for k, v in context.items() if v not in (None, "")),
            f"失败阶段: {rule.get('failed_stage') or '未知'}",
            f"规则引擎初判: {rule.get('error_category')} / {rule.get('error_code')} - {rule.get('root_cause')}",
        ]
        if focus.strip():
            sections += ["", "## 失败步骤日志（Pipeline 失败节点及失败的下游构建）", _tail(focus, budget // 2)]
        sections += ["", "## 完整控制台日志中的报错片段", _error_windows(console, budget // 4)]
        sections += ["", "## 控制台日志末尾", _tail(console, budget // 4)]
        return "\n".join(sections)[: budget + 2000]

    # -- call -------------------------------------------------------------------------
    def analyze(self, context: dict, rule: dict, focus: str, console: str) -> dict | None:
        """Returns fields to merge over the rule result, or None to keep the rule result."""
        if not self.enabled: return None
        body = {"model": self.model, "temperature": 0.1, "max_tokens": self.max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": self.build_prompt(context, rule, focus, console)}]}
        req = Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(), method="POST",
                      headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read())
            content = payload["choices"][0]["message"]["content"]
        except HTTPError as exc:
            log.warning("LLM analysis failed: HTTP %s %s", exc.code, exc.read()[:300].decode(errors="replace"))
            return None
        except (URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
            log.warning("LLM analysis failed: %s", exc)
            return None
        return self.parse(content, focus + "\n" + console)

    @staticmethod
    def parse(content: str, source_log: str) -> dict | None:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip())
        try:
            data = json.loads(text)
        except ValueError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match: return None
            try: data = json.loads(match.group(0))
            except ValueError: return None
        cause = str(data.get("root_cause") or "").strip()
        if not cause: return None
        out = {"root_cause": cause[:600], "suggestion": str(data.get("suggestion") or "").strip()[:600]}
        if data.get("error_category") in CATEGORIES and data["error_category"] != "UNKNOWN":
            out["error_category"] = data["error_category"]
        if data.get("responsibility_type") in RESPONSIBILITY:
            out["responsibility_type"] = data["responsibility_type"]
        try: out["confidence"] = max(0.0, min(1.0, float(data.get("confidence"))))
        except (TypeError, ValueError): pass
        # Keep only key lines that really occur in the log: the model must not invent evidence.
        safe_log = clean(source_log)
        lines = [redact(str(x)).strip()[:240] for x in (data.get("key_lines") or []) if str(x).strip()]
        lines = [x for x in lines if x in safe_log][:8]
        if lines: out["error_excerpt"] = "\n".join(lines)
        if not out["suggestion"]: out.pop("suggestion")
        return out


def _tail(text: str, limit: int) -> str:
    lines = [x for x in text.splitlines() if not NOISE.search(x)]
    out = "\n".join(x[:400] for x in lines)
    return out[-limit:]


def _error_windows(text: str, limit: int, before: int = 3, after: int = 3) -> str:
    lines = [x for x in text.splitlines() if not NOISE.search(x)]
    picked, last = [], -1
    for i, line in enumerate(lines):
        if is_error_line(line):
            start = max(i - before, last + 1)
            if start > last + 1 and picked: picked.append("...")
            picked += [x[:400] for x in lines[start: i + after + 1]]
            last = i + after
            if sum(len(x) for x in picked) > limit: break
    return "\n".join(picked)[:limit] or "(未找到明显报错行)"
