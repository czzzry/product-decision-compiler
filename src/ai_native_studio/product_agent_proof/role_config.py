"""Load and validate the immutable ProductAgent role configuration."""

import json
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProductAgentRoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    role: Literal["ProductAgent"]
    role_version: str
    prompt_version: str
    oauth_client_id: str
    app_user_id: str
    founder_actor_id: str
    authority_owner: Literal["Founder and Product Lead"]
    live_approval_channel_enabled: Literal[False]
    synthetic_approval_enabled: Literal[True]
    untrusted_sources: tuple[str, ...]
    implementation_terms: tuple[str, ...]
    injection_terms: tuple[str, ...]


def load_product_agent_role() -> ProductAgentRoleConfig:
    """Load the packaged, versioned role contract."""

    path = files(__package__).joinpath("config/product_agent.v1.json")
    return ProductAgentRoleConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_product_agent_prompt() -> str:
    """Load the versioned ProductAgent advisory prompt."""

    path = files(__package__).joinpath("config/product_agent_prompt.v1.md")
    return path.read_text(encoding="utf-8")
