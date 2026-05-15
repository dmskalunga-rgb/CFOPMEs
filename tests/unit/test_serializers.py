# =====================================================================
# FILE: app/schemas/serializers.py
# =====================================================================

"""
Enterprise Serializer Schemas
=============================

Pydantic schemas for:

- API request validation
- API response serialization
- Error standardization
- Strong typing
- OpenAPI schema generation
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------
# Base Schema
# ---------------------------------------------------------------------

class BaseSchema(BaseModel):
    """
    Shared base schema config.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        frozen=False,
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------

class TextRequest(BaseSchema):
    """
    NLP / anomaly text request.
    """

    text: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Input text for analysis",
        examples=["Suspicious login detected from unknown device"],
    )


class FraudRequest(BaseSchema):
    """
    Fraud detection batch request.
    """

    data: List[Dict[str, Any]] = Field(
        ...,
        description="Batch transaction records",
        examples=[
            [
                {
                    "transaction_id": "TXN001",
                    "amount": 1000.50,
                    "country": "US",
                }
            ]
        ],
    )


# ---------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------

class PredictionResponse(BaseSchema):
    """
    Generic prediction output.
    """

    prediction: str = Field(
        ...,
        description="Predicted class label",
        examples=["fraud"],
    )

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Prediction confidence score",
        examples=[0.982],
    )


class ErrorResponse(BaseSchema):
    """
    Standardized API error.
    """

    error: str = Field(
        ...,
        description="Error code",
        examples=["ValidationError"],
    )

    detail: Optional[str] = Field(
        default=None,
        description="Detailed explanation",
        examples=["Missing required field: text"],
    )


# =====================================================================
# FILE: tests/unit/test_serializers.py
# =====================================================================

"""
Enterprise Unit Tests for Serializer Schemas
===========================================

Coverage:

- Validation success
- Validation failure
- Type enforcement
- Serialization
- JSON serialization
- Round-trip consistency
- OpenAPI schema generation
- Edge cases
"""

import pytest
from pydantic import ValidationError

from app.schemas.serializers import (
    TextRequest,
    FraudRequest,
    PredictionResponse,
    ErrorResponse,
)


# =====================================================================
# TEXT REQUEST TESTS
# =====================================================================

class TestTextRequest:
    """Tests for TextRequest."""

    def test_valid_text_request(self):
        obj = TextRequest(
            text="Unauthorized access attempt"
        )

        assert obj.text == "Unauthorized access attempt"

    def test_empty_text_should_fail(self):
        with pytest.raises(ValidationError):
            TextRequest(text="")

    def test_missing_text_should_fail(self):
        with pytest.raises(ValidationError):
            TextRequest()

    def test_invalid_type_should_fail(self):
        with pytest.raises(ValidationError):
            TextRequest(text=123)

    def test_text_max_length_exceeded(self):
        with pytest.raises(ValidationError):
            TextRequest(text="A" * 10001)

    def test_serialization(self):
        obj = TextRequest(text="hello")
        dumped = obj.model_dump()

        assert dumped == {"text": "hello"}

    def test_json_serialization(self):
        obj = TextRequest(text="hello")
        json_data = obj.model_dump_json()

        assert '"text":"hello"' in json_data

    @pytest.mark.parametrize(
        "value",
        [
            "simple",
            "Unicode テスト",
            "🔥 alert",
            "Long text " * 100,
        ],
    )
    def test_various_valid_inputs(self, value):
        obj = TextRequest(text=value)

        assert obj.text == value


# =====================================================================
# FRAUD REQUEST TESTS
# =====================================================================

class TestFraudRequest:
    """Tests for FraudRequest."""

    def test_valid_request(self):
        obj = FraudRequest(
            data=[
                {"amount": 100},
                {"amount": 200},
            ]
        )

        assert len(obj.data) == 2

    def test_empty_list_allowed(self):
        obj = FraudRequest(data=[])

        assert obj.data == []

    def test_missing_data_should_fail(self):
        with pytest.raises(ValidationError):
            FraudRequest()

    def test_invalid_type_should_fail(self):
        with pytest.raises(ValidationError):
            FraudRequest(data="invalid")

    def test_non_dict_items_should_fail(self):
        with pytest.raises(ValidationError):
            FraudRequest(data=["bad"])

    def test_serialization(self):
        obj = FraudRequest(
            data=[{"id": 1}]
        )

        dumped = obj.model_dump()

        assert dumped["data"][0]["id"] == 1


# =====================================================================
# PREDICTION RESPONSE TESTS
# =====================================================================

class TestPredictionResponse:
    """Tests for PredictionResponse."""

    def test_valid_prediction(self):
        obj = PredictionResponse(
            prediction="fraud",
            confidence=0.95,
        )

        assert obj.prediction == "fraud"
        assert obj.confidence == 0.95

    def test_confidence_above_one_should_fail(self):
        with pytest.raises(ValidationError):
            PredictionResponse(
                prediction="fraud",
                confidence=1.1,
            )

    def test_confidence_below_zero_should_fail(self):
        with pytest.raises(ValidationError):
            PredictionResponse(
                prediction="fraud",
                confidence=-0.1,
            )

    def test_invalid_confidence_type(self):
        with pytest.raises(ValidationError):
            PredictionResponse(
                prediction="fraud",
                confidence="high",
            )

    def test_serialization(self):
        obj = PredictionResponse(
            prediction="normal",
            confidence=0.77,
        )

        dumped = obj.model_dump()

        assert dumped == {
            "prediction": "normal",
            "confidence": 0.77,
        }

    @pytest.mark.parametrize(
        "score",
        [0.0, 0.2, 0.5, 0.99, 1.0]
    )
    def test_valid_confidence_values(self, score):
        obj = PredictionResponse(
            prediction="fraud",
            confidence=score,
        )

        assert obj.confidence == score


# =====================================================================
# ERROR RESPONSE TESTS
# =====================================================================

class TestErrorResponse:
    """Tests for ErrorResponse."""

    def test_with_detail(self):
        obj = ErrorResponse(
            error="ValidationError",
            detail="Missing field",
        )

        assert obj.error == "ValidationError"
        assert obj.detail == "Missing field"

    def test_without_detail(self):
        obj = ErrorResponse(
            error="InternalError"
        )

        assert obj.detail is None

    def test_serialization(self):
        obj = ErrorResponse(
            error="Unauthorized",
            detail="Token expired",
        )

        dumped = obj.model_dump()

        assert dumped == {
            "error": "Unauthorized",
            "detail": "Token expired",
        }


# =====================================================================
# SCHEMA TESTS
# =====================================================================

class TestSchemaMetadata:
    """Ensure OpenAPI schema correctness."""

    def test_text_schema(self):
        schema = TextRequest.model_json_schema()

        assert "text" in schema["properties"]

    def test_fraud_schema(self):
        schema = FraudRequest.model_json_schema()

        assert "data" in schema["properties"]

    def test_prediction_schema(self):
        schema = PredictionResponse.model_json_schema()

        assert "prediction" in schema["properties"]
        assert "confidence" in schema["properties"]

    def test_error_schema(self):
        schema = ErrorResponse.model_json_schema()

        assert "error" in schema["properties"]


# =====================================================================
# ROUND TRIP TESTS
# =====================================================================

class TestRoundTrip:
    """Serialization → deserialization consistency."""

    def test_text_round_trip(self):
        original = TextRequest(
            text="test"
        )

        recreated = TextRequest(
            **original.model_dump()
        )

        assert recreated == original

    def test_fraud_round_trip(self):
        original = FraudRequest(
            data=[{"amount": 999}]
        )

        recreated = FraudRequest(
            **original.model_dump()
        )

        assert recreated == original

    def test_prediction_round_trip(self):
        original = PredictionResponse(
            prediction="fraud",
            confidence=0.91,
        )

        recreated = PredictionResponse(
            **original.model_dump()
        )

        assert recreated == original


# =====================================================================
# END
# =====================================================================