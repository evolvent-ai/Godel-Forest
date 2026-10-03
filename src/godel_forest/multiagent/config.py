"""Environment configuration for multi-agent orchestration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_DEFAULT_AGENT_IDS = ("agent001", "agent002", "agent003", "agent004")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class MultiAgentConfig:
    """Resolved multi-agent runtime settings."""

    enabled: bool
    agent_ids: tuple[str, ...] = ()
    model: str = ""
    final_buffer_sec: int = 3600
    restart_agents: bool = True
    agent_error_grace_sec: int = 300
    monitor_interval_sec: int = 60
    max_manager_iterations: int = 0

    @property
    def num_agents(self) -> int:
        return len(self.agent_ids)


def load_multiagent_config(
    env: Mapping[str, str] | None = None,
    *,
    default_model: str = "",
) -> MultiAgentConfig:
    """Parse multi-agent settings from environment variables.

    Multi-agent mode is opt-in. It is enabled when ``GODEL_FOREST_MODE=multi``,
    or when explicit multi-agent IDs / a count greater than one are provided.
    """
    import os

    env = os.environ if env is None else env
    mode = env.get("GODEL_FOREST_MODE", "").strip().lower()
    raw_ids = env.get("GODEL_FOREST_AGENT_IDS", "")
    raw_num = env.get("GODEL_FOREST_NUM_AGENTS", "")

    explicit_multi = mode == "multi"
    ids_requested = bool(raw_ids.strip())
    num_requested = _parse_int(raw_num, 0, minimum=0) > 1 if raw_num.strip() else False
    enabled = explicit_multi or ids_requested or num_requested
    if mode in {"single", "off", "disabled"}:
        enabled = False

    if not enabled:
        return MultiAgentConfig(enabled=False, model=env.get("GODEL_FOREST_AGENT_MODEL", default_model))

    if ids_requested:
        agent_ids = _parse_agent_ids(raw_ids)
    else:
        n = _parse_int(raw_num, 3, minimum=1)
        agent_ids = _default_agent_ids(n)

    return MultiAgentConfig(
        enabled=True,
        agent_ids=tuple(agent_ids),
        model=env.get("GODEL_FOREST_AGENT_MODEL") or default_model,
        final_buffer_sec=_parse_int(env.get("GODEL_FOREST_FINAL_BUFFER_SEC", "3600"), 3600, minimum=0),
        restart_agents=_parse_bool(env.get("GODEL_FOREST_AGENT_RESTART", "1"), True),
        agent_error_grace_sec=_parse_int(
            env.get("GODEL_FOREST_AGENT_ERROR_GRACE_SEC", "300"),
            300,
            minimum=0,
        ),
        monitor_interval_sec=_parse_int(env.get("GODEL_FOREST_MONITOR_INTERVAL_SEC", "60"), 60, minimum=1),
        max_manager_iterations=_parse_int(env.get("GODEL_FOREST_MANAGER_MAX_ITERATIONS", "0"), 0, minimum=0),
    )


def _parse_agent_ids(raw: str) -> list[str]:
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not ids:
        raise ValueError("GODEL_FOREST_AGENT_IDS did not contain any agent ids")
    seen: set[str] = set()
    out: list[str] = []
    for agent_id in ids:
        if not _AGENT_ID_RE.fullmatch(agent_id):
            raise ValueError(
                f"invalid agent id {agent_id!r}: use only letters, numbers, underscore, dash, and dot"
            )
        if agent_id in seen:
            raise ValueError(f"duplicate agent id: {agent_id}")
        seen.add(agent_id)
        out.append(agent_id)
    return out


def _default_agent_ids(n: int) -> list[str]:
    if n <= len(_DEFAULT_AGENT_IDS):
        return list(_DEFAULT_AGENT_IDS[:n])
    ids = list(_DEFAULT_AGENT_IDS)
    for i in range(len(_DEFAULT_AGENT_IDS) + 1, n + 1):
        ids.append(f"agent{i:03d}")
    return ids


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _parse_int(raw: str | None, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value
