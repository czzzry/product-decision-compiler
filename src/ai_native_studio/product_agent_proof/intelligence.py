"""Provider-neutral, schema-validated ProductAgent intelligence layer."""

import hashlib
import json
from typing import Protocol

from pydantic import ValidationError

from .models import AdvisoryResult, ModelGeneration, ModelRequest, ProductAdvisory
from .role_config import ProductAgentRoleConfig, load_product_agent_prompt


class ProductAdvisoryModel(Protocol):
    def generate(self, request: ModelRequest) -> ModelGeneration:
        """Generate one structured ProductAgent advisory response."""


class IntelligenceError(RuntimeError):
    """Base error for model or structured-output failures."""


class ModelOutputValidationError(IntelligenceError):
    """The provider returned output that cannot influence workflow state."""


class ProductAgentIntelligence:
    def __init__(self, role: ProductAgentRoleConfig, model: ProductAdvisoryModel) -> None:
        self._role = role
        self._model = model
        self._system_prompt = load_product_agent_prompt()

    def advise(self, untrusted_product_input: str) -> AdvisoryResult:
        request = ModelRequest(
            prompt_version=self._role.prompt_version,
            system_prompt=self._system_prompt,
            untrusted_product_input=untrusted_product_input,
        )
        generation = self._model.generate(request)
        try:
            advisory = ProductAdvisory.model_validate_json(generation.raw_output)
        except ValidationError as error:
            raise ModelOutputValidationError(self._validation_reason(error)) from error

        specification_version = self._specification_version(advisory)
        return AdvisoryResult(
            specification_version=specification_version,
            prompt_version=self._role.prompt_version,
            advisory=advisory,
            model_usage=generation.usage,
        )

    @staticmethod
    def _specification_version(advisory: ProductAdvisory) -> str:
        canonical = json.dumps(advisory.model_dump(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return f"product-spec-{digest}"

    @staticmethod
    def _validation_reason(error: ValidationError) -> str:
        first = error.errors(include_url=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        return f"Model output rejected at {location}: {first['msg']}"
