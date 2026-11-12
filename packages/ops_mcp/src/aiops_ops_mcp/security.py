import re
from pathlib import Path
from typing import Final, cast

SENSITIVE_KEY: Final = re.compile(
    r"(?:authorization|api[_-]?key|password|secret|token)",
    re.IGNORECASE,
)
BEARER_TOKEN: Final = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
ASSIGNMENT_SECRET: Final = re.compile(r"(?i)\b(password|secret|token|api[_-]?key)=([^\s,;]+)")
REVISION: Final = re.compile(r"[A-Za-z0-9._/@{}~^:+-]{1,200}")


def redact_text(value: str, *, max_length: int = 16_384) -> str:
    redacted = BEARER_TOKEN.sub("Bearer [REDACTED]", value)
    redacted = ASSIGNMENT_SECRET.sub(r"\1=[REDACTED]", redacted)
    if len(redacted) > max_length:
        return f"{redacted[:max_length]}\n[TRUNCATED]"
    return redacted


def redact_json(value: object) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else redact_json(item)
            for key, item in mapping.items()
        }
    if isinstance(value, list):
        return [redact_json(item) for item in cast(list[object], value)]
    if isinstance(value, str):
        return redact_text(value)
    return value


def validate_revision(value: str) -> str:
    if value.startswith("-") or REVISION.fullmatch(value) is None:
        raise ValueError("invalid Git revision")
    return value


def resolve_repository_path(repository: Path, value: str) -> Path:
    if not value or "\x00" in value:
        raise ValueError("path must be non-empty")
    candidate = (repository / value).resolve()
    try:
        candidate.relative_to(repository)
    except ValueError as error:
        raise ValueError("path escapes the configured repository") from error
    return candidate
