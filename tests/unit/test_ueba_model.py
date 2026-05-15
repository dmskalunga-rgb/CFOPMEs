# =====================================================================
# FILE: ml/ueba/model.py
# =====================================================================

"""
Enterprise UEBA Model
=====================

User and Entity Behavior Analytics model abstraction.

Features:
- anomaly detection
- train/predict lifecycle
- model persistence
- strong validation
- structured logging
"""

from pathlib import Path
import logging
import joblib
import numpy as np

from sklearn.ensemble import IsolationForest


logger = logging.getLogger(__name__)


# =====================================================================
# MODEL
# =====================================================================

class UEBAModel:
    """
    Enterprise UEBA anomaly detection model.
    """

    def __init__(self):
        self.model = IsolationForest(
            n_estimators=200,
            contamination=0.05,
            random_state=42,
        )

        self.is_trained = False
        self.feature_names = []

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def _validate_input(self, X):
        """
        Validate inference/training input.
        """
        X = np.asarray(X)

        if X.size == 0:
            raise ValueError("Input cannot be empty.")

        if np.isnan(X).any():
            raise ValueError("Input contains NaN values.")

        return X

    # -----------------------------------------------------------------
    # Training
    # -----------------------------------------------------------------

    def train(self, X):
        """
        Train anomaly detector.
        """
        logger.info("Training UEBA model")

        X = self._validate_input(X)

        self.model.fit(X)

        self.is_trained = True

        logger.info("UEBA model training complete")

    # -----------------------------------------------------------------
    # Prediction
    # -----------------------------------------------------------------

    def predict(self, X):
        """
        Predict anomalies.
        """
        if not self.is_trained:
            raise RuntimeError(
                "Model must be trained first."
            )

        X = self._validate_input(X)

        return self.model.predict(X)

    # -----------------------------------------------------------------
    # Probability
    # -----------------------------------------------------------------

    def predict_proba(self, X):
        """
        Generate anomaly confidence score.
        """

        if not self.is_trained:
            raise RuntimeError(
                "Model must be trained first."
            )

        X = self._validate_input(X)

        scores = self.model.decision_function(X)

        normalized = (
            scores - scores.min()
        ) / (
            scores.max() - scores.min()
            + 1e-12
        )

        return normalized

    # -----------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------

    def save(self, path):
        """
        Persist trained model.
        """
        path = Path(path)

        joblib.dump(
            {
                "model": self.model,
                "is_trained": self.is_trained,
                "feature_names": self.feature_names,
            },
            path,
        )

        logger.info(
            "Model saved to %s",
            path,
        )

    def load(self, path):
        """
        Restore persisted model.
        """
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(path)

        payload = joblib.load(path)

        self.model = payload["model"]
        self.is_trained = payload[
            "is_trained"
        ]
        self.feature_names = payload[
            "feature_names"
        ]

        logger.info(
            "Model loaded from %s",
            path,
        )


# =====================================================================
# FILE: tests/unit/test_ueba_model.py
# =====================================================================

"""
Enterprise unit tests for UEBA model.
"""

from pathlib import Path
from unittest.mock import Mock
import tempfile

import numpy as np
import pytest

from ml.ueba.model import UEBAModel


# =====================================================================
# FIXTURES
# =====================================================================

@pytest.fixture
def sample_features():
    return np.array(
        [
            [0.1, 0.2, 0.5],
            [0.3, 0.1, 0.8],
            [0.9, 0.7, 0.6],
            [0.2, 0.4, 0.1],
        ]
    )


@pytest.fixture
def trained_model(sample_features):
    model = UEBAModel()
    model.train(sample_features)
    return model


# =====================================================================
# INITIALIZATION
# =====================================================================

class TestInitialization:

    def test_model_initializes(self):
        model = UEBAModel()

        assert model is not None
        assert model.is_trained is False
        assert hasattr(model, "model")

    def test_feature_names_exists(self):
        model = UEBAModel()

        assert hasattr(model, "feature_names")


# =====================================================================
# TRAINING
# =====================================================================

class TestTraining:

    def test_training_success(
        self,
        sample_features,
    ):
        model = UEBAModel()

        model.train(sample_features)

        assert model.is_trained is True

    def test_empty_input_fails(self):
        model = UEBAModel()

        with pytest.raises(ValueError):
            model.train(np.array([]))

    def test_invalid_type_fails(self):
        model = UEBAModel()

        with pytest.raises(Exception):
            model.train("bad")

    def test_double_training_allowed(
        self,
        sample_features,
    ):
        model = UEBAModel()

        model.train(sample_features)
        model.train(sample_features)

        assert model.is_trained


# =====================================================================
# PREDICTION
# =====================================================================

class TestPrediction:

    def test_predict_returns_array(
        self,
        trained_model,
        sample_features,
    ):
        result = trained_model.predict(
            sample_features
        )

        assert isinstance(
            result,
            np.ndarray,
        )

    def test_predict_shape_matches(
        self,
        trained_model,
        sample_features,
    ):
        preds = trained_model.predict(
            sample_features
        )

        assert len(preds) == len(
            sample_features
        )

    def test_predict_before_training_fails(
        self,
        sample_features,
    ):
        model = UEBAModel()

        with pytest.raises(
            RuntimeError
        ):
            model.predict(
                sample_features
            )


# =====================================================================
# PROBABILITY
# =====================================================================

class TestProbability:

    def test_predict_proba_returns_array(
        self,
        trained_model,
        sample_features,
    ):
        probs = trained_model.predict_proba(
            sample_features
        )

        assert isinstance(
            probs,
            np.ndarray,
        )

    def test_probability_range(
        self,
        trained_model,
        sample_features,
    ):
        probs = trained_model.predict_proba(
            sample_features
        )

        assert np.all(probs >= 0)
        assert np.all(probs <= 1)


# =====================================================================
# PERSISTENCE
# =====================================================================

class TestPersistence:

    def test_save_creates_file(
        self,
        trained_model,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ueba.pkl"

            trained_model.save(path)

            assert path.exists()

    def test_load_restores_model(
        self,
        trained_model,
        sample_features,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ueba.pkl"

            trained_model.save(path)

            model = UEBAModel()
            model.load(path)

            assert model.is_trained

            preds = model.predict(
                sample_features
            )

            assert len(preds) == len(
                sample_features
            )

    def test_load_missing_file_fails(
        self,
    ):
        model = UEBAModel()

        with pytest.raises(
            FileNotFoundError
        ):
            model.load(
                "missing.pkl"
            )


# =====================================================================
# INTERNAL MOCK TESTS
# =====================================================================

class TestInternalCalls:

    def test_internal_fit_called(
        self,
        sample_features,
    ):
        model = UEBAModel()
        model.model = Mock()

        model.train(
            sample_features
        )

        model.model.fit.assert_called_once()

    def test_internal_predict_called(
        self,
        trained_model,
        sample_features,
    ):
        trained_model.model = Mock()
        trained_model.model.predict.return_value = (
            np.array(
                [0, 1, 0, 0]
            )
        )

        trained_model.predict(
            sample_features
        )

        trained_model.model.predict.assert_called_once()


# =====================================================================
# EDGE CASES
# =====================================================================

class TestEdgeCases:

    def test_single_row_prediction(
        self,
        trained_model,
    ):
        x = np.array(
            [[0.1, 0.2, 0.3]]
        )

        pred = trained_model.predict(x)

        assert len(pred) == 1

    def test_large_batch_prediction(
        self,
        trained_model,
    ):
        x = np.random.rand(
            1000,
            3,
        )

        pred = trained_model.predict(x)

        assert len(pred) == 1000

    def test_nan_input_fails(
        self,
        trained_model,
    ):
        x = np.array(
            [[np.nan, 0.1, 0.2]]
        )

        with pytest.raises(
            ValueError
        ):
            trained_model.predict(x)


# =====================================================================
# END
# =====================================================================