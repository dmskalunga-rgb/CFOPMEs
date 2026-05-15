# =====================================================================
# FILE: app/schemas/serializers.py
# =====================================================================

# =====================================================================
# FILE: app/schemas/serializers.py
# =====================================================================

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictStr,
    field_validator,
)


class BaseSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        frozen=False,
        str_strip_whitespace=False,
    )


class TextRequest(BaseSchema):
    text: StrictStr = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="Input text for analysis",
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text cannot be empty")

        return value


class FraudRequest(BaseSchema):
    data: list[dict[str, Any]] = Field(
        ...,
        description="Batch transaction records",
    )

    @field_validator("data")
    @classmethod
    def validate_data(
        cls,
        value: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:

        for item in value:
            if not isinstance(item, dict):
                raise ValueError(
                    "all items in data must be dictionaries"
                )

        return value


class PredictionResponse(BaseSchema):
    prediction: StrictStr = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Prediction label",
    )

    confidence: StrictFloat = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Prediction confidence score between 0 and 1",
    )


class ErrorResponse(BaseSchema):
    error: StrictStr = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Error code or name",
    )

    detail: str | None = Field(
        default=None,
        max_length=10_000,
        description="Optional error detail",
    )


class HealthResponse(BaseSchema):
    status: StrictStr = Field(..., min_length=1)
    service: StrictStr = Field(..., min_length=1)
    version: str | None = None


class MetricsResponse(BaseSchema):
    metrics: dict[str, Any] = Field(default_factory=dict)