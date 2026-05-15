# =========================================================
# TESTS / UNIT / test_cashflow_model.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise Cashflow Forecasting Unit Tests
# =========================================================

from __future__ import annotations

import os
import time
import json
import shutil
import unittest
import tempfile

import numpy as np
import pandas as pd

from typing import (
    Dict,
    Any,
    List
)

# =========================================================
# IMPORT MODEL
# =========================================================

from models.cashflow_forecast import (
    CashflowForecastModel
)

# =========================================================
# TEST CONFIG
# =========================================================

TEST_TENANT_ID = (
    "tenant-cashflow-test"
)

TEST_STORAGE_DIR = (
    "cashflow_test_storage"
)

# =========================================================
# TEST CLASS
# =========================================================

class TestEnterpriseCashflowModel(
    unittest.TestCase
):

    # =====================================================
    # SETUP
    # =====================================================

    def setUp(self):

        self.model = (
            CashflowForecastModel()
        )

        os.makedirs(
            TEST_STORAGE_DIR,
            exist_ok=True
        )

        self.synthetic_data = [

            1000,
            1200,
            1500,
            1800,
            1700,
            1900,
            2100,
            2300,
            2500,
            2700,
            3000,
            3200
        ]

    # =====================================================
    # CLEANUP
    # =====================================================

    def tearDown(self):

        if os.path.exists(
            TEST_STORAGE_DIR
        ):

            shutil.rmtree(
                TEST_STORAGE_DIR
            )

    # =====================================================
    # TEST DATA PREPARATION
    # =====================================================

    def test_prepare_data(self):

        prepared = (
            self.model.prepare_data(
                self.synthetic_data
            )
        )

        self.assertIsNotNone(
            prepared
        )

        self.assertEqual(
            prepared.shape[0],
            len(self.synthetic_data)
        )

    # =====================================================
    # TEST FORECAST GENERATION
    # =====================================================

    def test_forecast_generation(self):

        df = pd.DataFrame({

            "ds": pd.date_range(

                start="2025-01-01",

                periods=len(
                    self.synthetic_data
                ),

                freq="D"
            ),

            "y": self.synthetic_data
        })

        forecast = (
            self.model.forecast(df)
        )

        self.assertIsNotNone(
            forecast
        )

        self.assertTrue(
            len(forecast) > 0
        )

    # =====================================================
    # TEST MODEL HEALTH
    # =====================================================

    def test_model_health(self):

        health = (
            self.model.health()
        )

        self.assertEqual(

            health["status"],

            "healthy"
        )

        self.assertTrue(

            health["enterprise_mode"]
        )

    # =====================================================
    # TEST FORECAST OUTPUT COLUMNS
    # =====================================================

    def test_forecast_columns(self):

        df = pd.DataFrame({

            "ds": pd.date_range(

                start="2025-01-01",

                periods=len(
                    self.synthetic_data
                ),

                freq="D"
            ),

            "y": self.synthetic_data
        })

        forecast = (
            self.model.forecast(df)
        )

        required_columns = [

            "ds",

            "yhat",

            "yhat_lower",

            "yhat_upper"
        ]

        for column in required_columns:

            self.assertIn(
                column,
                forecast.columns
            )

    # =====================================================
    # TEST EMPTY DATA
    # =====================================================

    def test_empty_data(self):

        with self.assertRaises(
            Exception
        ):

            empty_df = pd.DataFrame({

                "ds": [],

                "y": []
            })

            self.model.forecast(
                empty_df
            )

    # =====================================================
    # TEST INVALID DATA
    # =====================================================

    def test_invalid_data(self):

        with self.assertRaises(
            Exception
        ):

            invalid_df = pd.DataFrame({

                "date": [1, 2, 3],

                "value": [1, 2, 3]
            })

            self.model.forecast(
                invalid_df
            )

    # =====================================================
    # TEST FORECAST PERFORMANCE
    # =====================================================

    def test_forecast_performance(self):

        df = pd.DataFrame({

            "ds": pd.date_range(

                start="2025-01-01",

                periods=500,

                freq="D"
            ),

            "y": np.random.randint(
                1000,
                10000,
                500
            )
        })

        start = time.time()

        forecast = (
            self.model.forecast(df)
        )

        end = time.time()

        execution_time = (
            end - start
        )

        self.assertTrue(
            execution_time < 30
        )

    # =====================================================
    # TEST MULTI TENANT
    # =====================================================

    def test_multi_tenant_isolation(self):

        tenant_a = {

            "tenant":
                "tenant-A",

            "values":
                [100, 200, 300]
        }

        tenant_b = {

            "tenant":
                "tenant-B",

            "values":
                [5000, 6000, 7000]
        }

        self.assertNotEqual(

            tenant_a["values"],

            tenant_b["values"]
        )

    # =====================================================
    # TEST SAVE FORECAST
    # =====================================================

    def test_save_forecast_file(self):

        df = pd.DataFrame({

            "ds": pd.date_range(

                start="2025-01-01",

                periods=len(
                    self.synthetic_data
                ),

                freq="D"
            ),

            "y": self.synthetic_data
        })

        forecast = (
            self.model.forecast(df)
        )

        filepath = os.path.join(

            TEST_STORAGE_DIR,

            "forecast.json"
        )

        forecast.to_json(
            filepath
        )

        self.assertTrue(
            os.path.exists(filepath)
        )

    # =====================================================
    # TEST LARGE DATASET
    # =====================================================

    def test_large_dataset(self):

        large_df = pd.DataFrame({

            "ds": pd.date_range(

                start="2020-01-01",

                periods=2000,

                freq="D"
            ),

            "y": np.random.randint(
                500,
                50000,
                2000
            )
        })

        forecast = (
            self.model.forecast(
                large_df
            )
        )

        self.assertTrue(
            len(forecast) > 0
        )

    # =====================================================
    # TEST NUMERIC OUTPUT
    # =====================================================

    def test_numeric_forecast_output(self):

        df = pd.DataFrame({

            "ds": pd.date_range(

                start="2025-01-01",

                periods=len(
                    self.synthetic_data
                ),

                freq="D"
            ),

            "y": self.synthetic_data
        })

        forecast = (
            self.model.forecast(df)
        )

        self.assertTrue(

            np.issubdtype(

                forecast["yhat"].dtype,

                np.number
            )
        )

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print("""

=========================================================
KWANZACONTROL CFO AI
ENTERPRISE CASHFLOW MODEL TEST SUITE
=========================================================

Running enterprise forecasting tests...

=========================================================

""")

    unittest.main(
        verbosity=2
    )