# =========================================================
# TESTS / UNIT / test_nlp_model.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise NLP Model Test Suite
# =========================================================

from __future__ import annotations

import os
import json
import time
import shutil
import unittest

from dataclasses import (
    dataclass,
    asdict
)

from typing import (
    Dict,
    List,
    Any
)

import numpy as np
import pandas as pd

from sklearn.pipeline import (
    Pipeline
)

from sklearn.feature_extraction.text import (
    TfidfVectorizer
)

from sklearn.linear_model import (
    LogisticRegression
)

from sklearn.metrics import (
    accuracy_score
)

# =========================================================
# NLP RESULT
# =========================================================

@dataclass
class NLPResult:

    tenant_id: str

    text: str

    prediction: str

    confidence: float

    processing_time: float

    model_version: str

    created_at: float

# =========================================================
# ENTERPRISE NLP MODEL
# =========================================================

class EnterpriseNLPModel:

    def __init__(self):

        self.model_version = (
            "nlp-enterprise-v1"
        )

        self.storage_dir = (
            "nlp_test_storage"
        )

        os.makedirs(
            self.storage_dir,
            exist_ok=True
        )

        self.pipeline = Pipeline([

            (
                "tfidf",

                TfidfVectorizer()
            ),

            (
                "classifier",

                LogisticRegression(
                    max_iter=1000
                )
            )
        ])

        self.is_trained = False

    # =====================================================
    # TRAIN
    # =====================================================

    def train(

        self,

        texts: List[str],

        labels: List[str]

    ):

        self.pipeline.fit(
            texts,
            labels
        )

        self.is_trained = True

    # =====================================================
    # PREDICT
    # =====================================================

    def predict(

        self,

        tenant_id: str,

        text: str

    ) -> NLPResult:

        if not self.is_trained:

            raise RuntimeError(
                "Model not trained."
            )

        start = time.time()

        prediction = (
            self.pipeline.predict(
                [text]
            )[0]
        )

        probabilities = (
            self.pipeline.predict_proba(
                [text]
            )[0]
        )

        confidence = float(
            np.max(probabilities)
        )

        result = NLPResult(

            tenant_id=
                tenant_id,

            text=
                text,

            prediction=
                prediction,

            confidence=
                confidence,

            processing_time=
                time.time() - start,

            model_version=
                self.model_version,

            created_at=
                time.time()
        )

        self._persist(result)

        return result

    # =====================================================
    # EVALUATE
    # =====================================================

    def evaluate(

        self,

        texts: List[str],

        labels: List[str]

    ) -> float:

        predictions = (
            self.pipeline.predict(
                texts
            )
        )

        return float(

            accuracy_score(
                labels,
                predictions
            )
        )

    # =====================================================
    # SAVE RESULTS
    # =====================================================

    def _persist(

        self,

        result: NLPResult

    ):

        filename = (

            f"nlp_{int(result.created_at)}.json"
        )

        path = os.path.join(

            self.storage_dir,

            filename
        )

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(

                asdict(result),

                file,

                indent=2
            )

    # =====================================================
    # HEALTH
    # =====================================================

    def health(self):

        return {

            "service":
                "enterprise_nlp_model",

            "status":
                "healthy",

            "trained":
                self.is_trained,

            "model_version":
                self.model_version
        }

# =========================================================
# TEST SUITE
# =========================================================

class TestEnterpriseNLPModel(
    unittest.TestCase
):

    # =====================================================
    # SETUP
    # =====================================================

    def setUp(self):

        self.model = (
            EnterpriseNLPModel()
        )

        self.texts = [

            "generate payroll report",
            "cashflow forecast for company",
            "detect fraud transaction",
            "financial audit report",
            "invoice processing workflow",
            "employee salary optimization",
            "government tax document",
            "bank transaction anomaly"
        ]

        self.labels = [

            "payroll",
            "forecast",
            "fraud",
            "audit",
            "invoice",
            "payroll",
            "tax",
            "fraud"
        ]

        self.model.train(

            self.texts,

            self.labels
        )

    # =====================================================
    # CLEANUP
    # =====================================================

    def tearDown(self):

        if os.path.exists(
            "nlp_test_storage"
        ):

            shutil.rmtree(
                "nlp_test_storage"
            )

    # =====================================================
    # TEST TRAINING
    # =====================================================

    def test_training(self):

        self.assertTrue(
            self.model.is_trained
        )

    # =====================================================
    # TEST PREDICTION
    # =====================================================

    def test_prediction(self):

        result = self.model.predict(

            tenant_id=
                "tenant-001",

            text=
                "create payroll salary report"
        )

        self.assertIsNotNone(
            result.prediction
        )

    # =====================================================
    # TEST CONFIDENCE
    # =====================================================

    def test_confidence(self):

        result = self.model.predict(

            tenant_id=
                "tenant-002",

            text=
                "detect suspicious bank transfer"
        )

        self.assertTrue(

            0 <= result.confidence <= 1
        )

    # =====================================================
    # TEST STORAGE
    # =====================================================

    def test_storage(self):

        self.model.predict(

            tenant_id=
                "tenant-storage",

            text=
                "generate accounting report"
        )

        files = os.listdir(
            "nlp_test_storage"
        )

        self.assertTrue(
            len(files) > 0
        )

    # =====================================================
    # TEST EVALUATION
    # =====================================================

    def test_evaluation(self):

        accuracy = (
            self.model.evaluate(

                self.texts,

                self.labels
            )
        )

        self.assertGreaterEqual(
            accuracy,
            0.7
        )

    # =====================================================
    # TEST INVALID MODEL
    # =====================================================

    def test_invalid_model(self):

        untrained = (
            EnterpriseNLPModel()
        )

        with self.assertRaises(
            RuntimeError
        ):

            untrained.predict(

                tenant_id=
                    "tenant-invalid",

                text=
                    "some text"
            )

    # =====================================================
    # TEST PROCESSING TIME
    # =====================================================

    def test_processing_time(self):

        result = self.model.predict(

            tenant_id=
                "tenant-speed",

            text=
                "predict company cashflow"
        )

        self.assertLess(

            result.processing_time,

            5
        )

    # =====================================================
    # TEST MULTI TENANT
    # =====================================================

    def test_multi_tenant(self):

        result_a = self.model.predict(

            tenant_id=
                "tenant-A",

            text=
                "salary optimization"
        )

        result_b = self.model.predict(

            tenant_id=
                "tenant-B",

            text=
                "tax government invoice"
        )

        self.assertNotEqual(

            result_a.tenant_id,

            result_b.tenant_id
        )

    # =====================================================
    # TEST HEALTH
    # =====================================================

    def test_health(self):

        health = (
            self.model.health()
        )

        self.assertEqual(

            health["status"],

            "healthy"
        )

    # =====================================================
    # TEST BULK PREDICTIONS
    # =====================================================

    def test_bulk_predictions(self):

        predictions = []

        for text in self.texts:

            result = self.model.predict(

                tenant_id=
                    "tenant-bulk",

                text=
                    text
            )

            predictions.append(
                result.prediction
            )

        self.assertEqual(

            len(predictions),

            len(self.texts)
        )

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print("""

=========================================================
KWANZACONTROL CFO AI
ENTERPRISE NLP MODEL TEST SUITE
=========================================================

Running enterprise NLP tests...

=========================================================

""")

    unittest.main(
        verbosity=2
    )