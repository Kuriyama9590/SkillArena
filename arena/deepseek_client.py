"""DeepSeek API 客户端封装。

基于 OpenAI 兼容协议,提供 execute 和 judge 两个高层方法。
所有调用都内置指数退避重试 + 超时控制。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionResult:
    """一次模型调用的结构化结果。

    Attributes:
        content: 模型回复的文本内容(已 strip)。
        prompt_tokens: 输入 token 数(若模型未返回则为 0)。
        completion_tokens: 输出 token 数(若模型未返回则为 0)。
        total_tokens: 总 token 数。
        model: 实际使用的模型名。
    """

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


class DeepSeekClient:
    """DeepSeek API 客户端封装。

    该客户端对外提供两个方法:
    - execute:用于"执行"任务(通常是普通 skill + 任务的产物生成)。
    - judge:用于"评判"两段产物优劣(可以是更高级的 reasoning 模型)。

    所有方法都自动注入超时、重试与日志,业务侧无需关心。
    """

    # 指数退避基础秒数;实际等待 = BASE_DELAY * (2 ** attempt)
    BASE_DELAY: float = 1.0

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = OpenAI(
            api_key=self._settings.api_key,
            base_url=self._settings.base_url,
            timeout=self._settings.timeout_seconds,
        )

    @property
    def settings(self) -> Settings:
        """暴露只读 settings,便于测试和报告输出。"""
        return self._settings

    # -------- 高层 API --------

    def execute(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> CompletionResult:
        """调用"执行"模型生成产物。

        Args:
            messages: OpenAI 格式的对话列表。
            model: 可选模型覆盖,默认用 settings.execute_model。
            temperature: 采样温度,执行任务通常用 0.7。
        """
        return self._chat(
            messages=messages,
            model=model or self._settings.execute_model,
            temperature=temperature,
        )

    def judge(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> CompletionResult:
        """调用"评判"模型评估两段产物。

        评判模型通常希望更稳定、更低随机性,所以默认 temperature=0.2。
        """
        return self._chat(
            messages=messages,
            model=model or self._settings.judge_model,
            temperature=temperature,
        )

    # -------- 内部:统一 chat 入口 --------

    def _chat(
        self,
        *,
        messages: Sequence[dict[str, str]],
        model: str,
        temperature: float,
    ) -> CompletionResult:
        """统一的 chat 调用入口:带超时 + 指数退避重试。"""
        last_exc: Exception | None = None

        # 构建请求参数:开启最大思考模式时附带 thinking 字段 + 充足 max_tokens
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "timeout": self._settings.timeout_seconds,
        }
        if self._settings.enable_thinking:
            request_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            # 思考模式需要更大的输出预算:取 context_length 的一半给 max_tokens,
            # 但封顶 32K,避免单次请求过大
            request_kwargs["max_tokens"] = min(32768, self._settings.context_length // 2)

        for attempt in range(self._settings.max_retries):
            try:
                response = self._client.chat.completions.create(**request_kwargs)
                return self._parse_response(response, model)

            except APITimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "DeepSeek API timeout (attempt %d/%d): %s",
                    attempt + 1,
                    self._settings.max_retries,
                    exc,
                )
            except RateLimitError as exc:
                last_exc = exc
                logger.warning(
                    "DeepSeek rate limit (attempt %d/%d): %s",
                    attempt + 1,
                    self._settings.max_retries,
                    exc,
                )
            except APIError as exc:
                last_exc = exc
                logger.warning(
                    "DeepSeek API error (attempt %d/%d): %s",
                    attempt + 1,
                    self._settings.max_retries,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                # 网络层异常(如连接错误)也走重试
                last_exc = exc
                logger.warning(
                    "DeepSeek unexpected error (attempt %d/%d): %s",
                    attempt + 1,
                    self._settings.max_retries,
                    exc,
                )

            # 退避
            if attempt < self._settings.max_retries - 1:
                self._sleep_backoff(attempt)

        # 重试耗尽,抛出最后一次的异常,带上可读的上下文
        assert last_exc is not None
        raise RuntimeError(
            f"DeepSeek 调用失败(模型={model},重试 {self._settings.max_retries} 次后放弃):"
            f" {last_exc!r}"
        ) from last_exc

    def _sleep_backoff(self, attempt: int) -> None:
        """指数退避:1s, 2s, 4s, ... 可被测试 patch。"""
        delay = self.BASE_DELAY * (2 ** attempt)
        time.sleep(delay)

    @staticmethod
    def _parse_response(response: Any, model: str) -> CompletionResult:
        """解析 OpenAI 兼容响应为结构化结果。

        不同模型/不同 SDK 版本的 usage 字段可能缺失,做防御性处理。
        """
        # 响应对象的标准字段
        try:
            message = response.choices[0].message
            content = (message.content or "").strip()
        except (AttributeError, IndexError, KeyError) as exc:
            raise RuntimeError(
                f"DeepSeek 响应格式异常,无法解析 message.content: {exc!r}"
            ) from exc

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        completion_tokens = (
            int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        )
        total_tokens = (
            int(getattr(usage, "total_tokens", 0) or 0)
            if usage
            else prompt_tokens + completion_tokens
        )

        return CompletionResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model,
        )


__all__ = ["DeepSeekClient", "CompletionResult"]