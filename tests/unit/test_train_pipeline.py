"""
Unit tests for training pipeline.

Enterprise-grade coverage for:
- pipeline orchestration
- dependency interaction
- failure handling
- logging verification
- retry/abort behavior
- output contract validation
"""

from unittest.mock import Mock, patch, call
import pytest

# Ajuste para seu path real
from ml.training.train_pipeline import run_training_pipeline


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def sample_raw_data():
    return [
        {"feature1": 10, "feature2": 20, "label": 1},
        {"feature1": 5, "feature2": 15, "label": 0},
    ]


@pytest.fixture
def sample_processed_data():
    return {
        "X_train": [[0.5, 0.8], [0.2, 0.6]],
        "y_train": [1, 0],
    }


@pytest.fixture
def sample_model():
    model = Mock()
    model.name = "fraud_model_v1"
    return model


@pytest.fixture
def sample_metrics():
    return {
        "accuracy": 0.97,
        "precision": 0.95,
        "recall": 0.96,
        "f1_score": 0.955,
    }


# ============================================================
# SUCCESS PATH
# ============================================================

@patch("ml.training.train_pipeline.register_model")
@patch("ml.training.train_pipeline.save_model")
@patch("ml.training.train_pipeline.evaluate_model")
@patch("ml.training.train_pipeline.train_model")
@patch("ml.training.train_pipeline.preprocess_training_data")
@patch("ml.training.train_pipeline.load_training_data")
def test_run_training_pipeline_success(
    mock_load,
    mock_preprocess,
    mock_train,
    mock_evaluate,
    mock_save,
    mock_register,
    sample_raw_data,
    sample_processed_data,
    sample_model,
    sample_metrics,
):
    """
    Ensure complete pipeline executes successfully.
    """

    mock_load.return_value = sample_raw_data
    mock_preprocess.return_value = sample_processed_data
    mock_train.return_value = sample_model
    mock_evaluate.return_value = sample_metrics

    result = run_training_pipeline()

    mock_load.assert_called_once()
    mock_preprocess.assert_called_once_with(sample_raw_data)
    mock_train.assert_called_once_with(sample_processed_data)
    mock_evaluate.assert_called_once_with(sample_model)

    mock_save.assert_called_once_with(
        sample_model,
        metrics=sample_metrics,
    )

    mock_register.assert_called_once_with(
        sample_model,
        sample_metrics,
    )

    assert result["status"] == "success"
    assert result["metrics"] == sample_metrics


# ============================================================
# DATA LOADING FAILURES
# ============================================================

@patch("ml.training.train_pipeline.load_training_data")
def test_pipeline_fails_when_data_loading_fails(mock_load):
    """
    Pipeline should fail immediately if data loading fails.
    """

    mock_load.side_effect = RuntimeError(
        "Unable to load training data"
    )

    with pytest.raises(RuntimeError):
        run_training_pipeline()


# ============================================================
# PREPROCESS FAILURES
# ============================================================

@patch("ml.training.train_pipeline.preprocess_training_data")
@patch("ml.training.train_pipeline.load_training_data")
def test_pipeline_fails_when_preprocessing_fails(
    mock_load,
    mock_preprocess,
    sample_raw_data,
):
    mock_load.return_value = sample_raw_data

    mock_preprocess.side_effect = ValueError(
        "Invalid schema"
    )

    with pytest.raises(ValueError):
        run_training_pipeline()


# ============================================================
# TRAINING FAILURES
# ============================================================

@patch("ml.training.train_pipeline.train_model")
@patch("ml.training.train_pipeline.preprocess_training_data")
@patch("ml.training.train_pipeline.load_training_data")
def test_pipeline_fails_when_training_fails(
    mock_load,
    mock_preprocess,
    mock_train,
    sample_raw_data,
    sample_processed_data,
):
    mock_load.return_value = sample_raw_data
    mock_preprocess.return_value = sample_processed_data

    mock_train.side_effect = RuntimeError(
        "Model convergence failed"
    )

    with pytest.raises(RuntimeError):
        run_training_pipeline()


# ============================================================
# EVALUATION FAILURES
# ============================================================

@patch("ml.training.train_pipeline.evaluate_model")
@patch("ml.training.train_pipeline.train_model")
@patch("ml.training.train_pipeline.preprocess_training_data")
@patch("ml.training.train_pipeline.load_training_data")
def test_pipeline_fails_when_evaluation_fails(
    mock_load,
    mock_preprocess,
    mock_train,
    mock_evaluate,
    sample_raw_data,
    sample_processed_data,
    sample_model,
):
    mock_load.return_value = sample_raw_data
    mock_preprocess.return_value = sample_processed_data
    mock_train.return_value = sample_model

    mock_evaluate.side_effect = RuntimeError(
        "Metric computation failed"
    )

    with pytest.raises(RuntimeError):
        run_training_pipeline()


# ============================================================
# MODEL SAVE FAILURES
# ============================================================

@patch("ml.training.train_pipeline.save_model")
@patch("ml.training.train_pipeline.evaluate_model")
@patch("ml.training.train_pipeline.train_model")
@patch("ml.training.train_pipeline.preprocess_training_data")
@patch("ml.training.train_pipeline.load_training_data")
def test_pipeline_fails_when_model_save_fails(
    mock_load,
    mock_preprocess,
    mock_train,
    mock_evaluate,
    mock_save,
    sample_raw_data,
    sample_processed_data,
    sample_model,
    sample_metrics,
):
    mock_load.return_value = sample_raw_data
    mock_preprocess.return_value = sample_processed_data
    mock_train.return_value = sample_model
    mock_evaluate.return_value = sample_metrics

    mock_save.side_effect = IOError(
        "Disk write failed"
    )

    with pytest.raises(IOError):
        run_training_pipeline()


# ============================================================
# MODEL REGISTRATION FAILURES
# ============================================================

@patch("ml.training.train_pipeline.register_model")
@patch("ml.training.train_pipeline.save_model")
@patch("ml.training.train_pipeline.evaluate_model")
@patch("ml.training.train_pipeline.train_model")
@patch("ml.training.train_pipeline.preprocess_training_data")
@patch("ml.training.train_pipeline.load_training_data")
def test_pipeline_fails_when_registry_fails(
    mock_load,
    mock_preprocess,
    mock_train,
    mock_evaluate,
    mock_save,
    mock_register,
    sample_raw_data,
    sample_processed_data,
    sample_model,
    sample_metrics,
):
    mock_load.return_value = sample_raw_data
    mock_preprocess.return_value = sample_processed_data
    mock_train.return_value = sample_model
    mock_evaluate.return_value = sample_metrics

    mock_register.side_effect = RuntimeError(
        "Registry unavailable"
    )

    with pytest.raises(RuntimeError):
        run_training_pipeline()


# ============================================================
# LOGGING TESTS
# ============================================================

@patch("ml.training.train_pipeline.logger")
@patch("ml.training.train_pipeline.register_model")
@patch("ml.training.train_pipeline.save_model")
@patch("ml.training.train_pipeline.evaluate_model")
@patch("ml.training.train_pipeline.train_model")
@patch("ml.training.train_pipeline.preprocess_training_data")
@patch("ml.training.train_pipeline.load_training_data")
def test_pipeline_logs_expected_messages(
    mock_load,
    mock_preprocess,
    mock_train,
    mock_evaluate,
    mock_save,
    mock_register,
    mock_logger,
    sample_raw_data,
    sample_processed_data,
    sample_model,
    sample_metrics,
):
    mock_load.return_value = sample_raw_data
    mock_preprocess.return_value = sample_processed_data
    mock_train.return_value = sample_model
    mock_evaluate.return_value = sample_metrics

    run_training_pipeline()

    expected_calls = [
        call.info("Starting training pipeline"),
        call.info("Loading training data"),
        call.info("Preprocessing training data"),
        call.info("Training model"),
        call.info("Evaluating model"),
        call.info("Saving model"),
        call.info("Registering model"),
        call.info("Training pipeline completed"),
    ]

    mock_logger.assert_has_calls(
        expected_calls,
        any_order=False,
    )


# ============================================================
# OUTPUT CONTRACT
# ============================================================

@patch("ml.training.train_pipeline.register_model")
@patch("ml.training.train_pipeline.save_model")
@patch("ml.training.train_pipeline.evaluate_model")
@patch("ml.training.train_pipeline.train_model")
@patch("ml.training.train_pipeline.preprocess_training_data")
@patch("ml.training.train_pipeline.load_training_data")
def test_pipeline_returns_expected_contract(
    mock_load,
    mock_preprocess,
    mock_train,
    mock_evaluate,
    mock_save,
    mock_register,
    sample_raw_data,
    sample_processed_data,
    sample_model,
    sample_metrics,
):
    mock_load.return_value = sample_raw_data
    mock_preprocess.return_value = sample_processed_data
    mock_train.return_value = sample_model
    mock_evaluate.return_value = sample_metrics

    result = run_training_pipeline()

    assert isinstance(result, dict)
    assert "status" in result
    assert "metrics" in result
    assert result["status"] == "success"