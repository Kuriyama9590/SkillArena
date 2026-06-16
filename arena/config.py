"""统一配置:从环境变量读取 API key、模型名、超时与重试参数。

严禁硬编码密钥,所有敏感信息必须通过环境变量传入。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录:arena/ 的上一级
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
REPORTS_DIR: Path = PROJECT_ROOT / "reports"
SKILLS_DIR: Path = PROJECT_ROOT / "skills"
TASKS_DIR: Path = PROJECT_ROOT / "tasks" / "fixed"  # 指向 fixed 子目录,与 TASKS_AUTO_DIR 对应
TASKS_AUTO_DIR: Path = PROJECT_ROOT / "tasks" / "auto"
ELO_STATE_FILE: Path = REPORTS_DIR / "elo_state.json"


@dataclass(frozen=True)
class Settings:
    """运行时配置。frozen=True 保证不可变,便于在不同模块间安全共享。"""

    api_key: str
    base_url: str
    execute_model: str
    judge_model: str
    timeout_seconds: float
    max_retries: int
    enable_thinking: bool = False
    context_length: int = 128000
    elo_state_path: Path = ELO_STATE_FILE

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构造配置。

        行为约定:
        - 若未设置 DEEPSEEK_API_KEY,直接抛出明确错误(不静默使用占位符)。
        - .env 文件会被自动加载,但 .env.example 只是模板,不会读密钥。
        - 可选参数均有合理默认值。
        """
        load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)

        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY 未设置。请在环境变量或 .env 文件中配置,"
                "参考 .env.example。"
            )

        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            execute_model=os.getenv("DEEPSEEK_EXECUTE_MODEL", "deepseek-v4-flash"),
            judge_model=os.getenv("DEEPSEEK_JUDGE_MODEL", "deepseek-v4-pro"),
            timeout_seconds=float(os.getenv("ARENA_TIMEOUT_SECONDS", "120")),
            max_retries=int(os.getenv("ARENA_MAX_RETRIES", "3")),
            enable_thinking=os.getenv("ARENA_ENABLE_THINKING", "false").lower()
            in ("1", "true", "yes", "on"),
            context_length=int(os.getenv("ARENA_CONTEXT_LENGTH", "128000")),
        )


def get_settings() -> Settings:
    """便捷获取配置的工厂方法(每次调用都重新读环境变量,便于测试时 monkeypatch)。"""
    return Settings.from_env()


# 路径辅助函数
def ensure_reports_dir() -> Path:
    """确保 reports 目录存在,返回路径。"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR


__all__ = [
    "Settings",
    "PROJECT_ROOT",
    "REPORTS_DIR",
    "SKILLS_DIR",
    "TASKS_DIR",
    "TASKS_AUTO_DIR",
    "ELO_STATE_FILE",
    "get_settings",
    "ensure_reports_dir",
]