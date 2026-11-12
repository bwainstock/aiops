from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class McpProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    tools: list[str] = Field(min_length=1)


class GitHubProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    allow_write: bool


class AgentToolProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops_mcp: McpProfile
    github_mcp: GitHubProfile


class ToolProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    triage: AgentToolProfile


def load_tool_profiles(path: Path) -> ToolProfiles:
    return ToolProfiles.model_validate_json(path.read_text())
