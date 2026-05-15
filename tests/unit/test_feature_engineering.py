# =========================================================
# TESTS / UNIT / test_feature_engineering.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise Feature Engineering Test Suite
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
    List,
    Dict,
    Any
)

import numpy as np
import pandas as pd

from sklearn.preprocessing import (
    StandardScaler,
    MinMaxScaler
)

from sklearn.impute import (
    SimpleImputer
)

# =========================================================
# ENTERPRISE FEATURE ENGINEERING
# =========================================================

@dataclass
class FeatureEngineeringResult:

    tenant_id: str

    total_features: int

    transformed_rows: int

    created_features: List[str]

    processing_time: float

    created_at: float

# =========================================================
# FEATURE ENGINEERING ENGINE
# =========================================================

class EnterpriseFeatureEngineering:

    def __init__(self):

        self.storage_dir = (
            "feature_engineering_storage"
        )

        os.makedirs(
            self.storage_dir,
            exist_ok=True
        )

        self.standard_scaler = (
            StandardScaler()
        )

        self.minmax_scaler = (
            MinMaxScaler()
        )

        self.imputer = (
            SimpleImputer(
                strategy="mean"
            )
        )

    # =====================================================
    # CREATE FEATURES
    # =====================================================

    def transform(

        self,

        tenant_id: str,

        dataframe: pd.DataFrame

    ) -> tuple[pd.DataFrame,
               FeatureEngineeringResult]:

        start_time = time.time()

        df = dataframe.copy()

        # ================================================
        # HANDLE MISSING VALUES
        # ================================================

        numeric_columns = (

            df.select_dtypes(
                include=[np.number]
            ).columns
        )

        df[numeric_columns] = (
            self.imputer.fit_transform(
                df[numeric_columns]
            )
        )

        # ================================================
        # FEATURE CREATION
        # ================================================

        created_features = []

        if "revenue" in df.columns:

            df["revenue_growth"] = (
                df["revenue"].pct_change()
                .fillna(0)
            )

            created_features.append(
                "revenue_growth"
            )

        if "expenses" in df.columns:

            df["expense_ratio"] = (

                df["expenses"] /

                (df["revenue"] + 1)
            )

            created_features.append(
                "expense_ratio"
            )

        if "cashflow" in df.columns:

            df["cashflow_scaled"] = (
                self.standard_scaler
                .fit_transform(

                    df[["cashflow"]]
                )
            )

            created_features.append(
                "cashflow_scaled"
            )

        # ================================================
        # NORMALIZATION
        # ================================================

        if "revenue" in df.columns:

            df["revenue_normalized"] = (
                self.minmax_scaler
                .fit_transform(

                    df[["revenue"]]
                )
            )

            created_features.append(
                "revenue_normalized"
            )

        # ================================================
        # RESULT
        # ================================================

        processing_time = (
            time.time() - start_time
        )

        result = (
            FeatureEngineeringResult(

                tenant_id=
                    tenant_id,

                total_features=
                    len(df.columns),

                transformed_rows=
                    len(df),

                created_features=
                    created_features,

                processing_time=
                    processing_time,

                created_at=
                    time.time()
            )
        )

        self._persist_result(
            result
        )

        return df, result

    # =====================================================
    # SAVE RESULT
    # =====================================================

    def _persist_result(

        self,

        result:
        FeatureEngineeringResult

    ):

        filename = (

            f"{result.tenant_id}_"
            f"{int(result.created_at)}.json"
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
                "feature_engineering",

            "status":
                "healthy",

            "enterprise_mode":
                True
        }

# =========================================================
# TEST CLASS
# =========================================================

class TestEnterpriseFeatureEngineering(
    unittest.TestCase
):

    # =====================================================
    # SETUP
    # =====================================================

    def setUp(self):

        self.engine = (
            EnterpriseFeatureEngineering()
        )

        self.df = pd.DataFrame({

            "revenue": [

                1000,
                1200,
                1500,
                1800
            ],

            "expenses": [

                500,
                600,
                700,
                850
            ],

            "cashflow": [

                300,
                450,
                600,
                720
            ]
        })

    # =====================================================
    # CLEANUP
    # =====================================================

    def tearDown(self):

        if os.path.exists(
            "feature_engineering_storage"
        ):

            shutil.rmtree(
                "feature_engineering_storage"
            )

    # =====================================================
    # FEATURE CREATION
    # =====================================================

    def test_feature_creation(self):

        transformed_df, result = (

            self.engine.transform(

                tenant_id=
                    "tenant-001",

                dataframe=
                    self.df
            )
        )

        self.assertIn(

            "revenue_growth",

            transformed_df.columns
        )

        self.assertIn(

            "expense_ratio",

            transformed_df.columns
        )

    # =====================================================
    # FEATURE COUNT
    # =====================================================

    def test_feature_count(self):

        transformed_df, result = (

            self.engine.transform(

                tenant_id=
                    "tenant-002",

                dataframe=
                    self.df
            )
        )

        self.assertGreater(

            result.total_features,

            3
        )

    # =====================================================
    # MISSING VALUE HANDLING
    # =====================================================

    def test_missing_values(self):

        df = self.df.copy()

        df.loc[1, "revenue"] = np.nan

        transformed_df, result = (

            self.engine.transform(

                tenant_id=
                    "tenant-003",

                dataframe=
                    df
            )
        )

        self.assertFalse(

            transformed_df.isnull()
            .values.any()
        )

    # =====================================================
    # SCALING
    # =====================================================

    def test_scaling(self):

        transformed_df, result = (

            self.engine.transform(

                tenant_id=
                    "tenant-004",

                dataframe=
                    self.df
            )
        )

        self.assertIn(

            "cashflow_scaled",

            transformed_df.columns
        )

    # =====================================================
    # NORMALIZATION
    # =====================================================

    def test_normalization(self):

        transformed_df, result = (

            self.engine.transform(

                tenant_id=
                    "tenant-005",

                dataframe=
                    self.df
            )
        )

        normalized = (
            transformed_df[
                "revenue_normalized"
            ]
        )

        self.assertTrue(

            normalized.max() <= 1
        )

    # =====================================================
    # PROCESSING TIME
    # =====================================================

    def test_processing_time(self):

        transformed_df, result = (

            self.engine.transform(

                tenant_id=
                    "tenant-006",

                dataframe=
                    self.df
            )
        )

        self.assertLess(

            result.processing_time,

            10
        )

    # =====================================================
    # STORAGE FILE
    # =====================================================

    def test_storage_file(self):

        transformed_df, result = (

            self.engine.transform(

                tenant_id=
                    "tenant-storage",

                dataframe=
                    self.df
            )
        )

        files = os.listdir(
            "feature_engineering_storage"
        )

        self.assertTrue(
            len(files) > 0
        )

    # =====================================================
    # LARGE DATASET
    # =====================================================

    def test_large_dataset(self):

        df = pd.DataFrame({

            "revenue":
                np.random.randint(
                    1000,
                    100000,
                    10000
                ),

            "expenses":
                np.random.randint(
                    500,
                    50000,
                    10000
                ),

            "cashflow":
                np.random.randint(
                    100,
                    30000,
                    10000
                )
        })

        transformed_df, result = (

            self.engine.transform(

                tenant_id=
                    "tenant-large",

                dataframe=
                    df
            )
        )

        self.assertEqual(

            len(transformed_df),

            10000
        )

    # =====================================================
    # MULTI TENANT
    # =====================================================

    def test_multi_tenant_isolation(self):

        df_a, result_a = (

            self.engine.transform(

                tenant_id=
                    "tenant-A",

                dataframe=
                    self.df
            )
        )

        df_b, result_b = (

            self.engine.transform(

                tenant_id=
                    "tenant-B",

                dataframe=
                    self.df
            )
        )

        self.assertNotEqual(

            result_a.tenant_id,

            result_b.tenant_id
        )

    # =====================================================
    # HEALTH CHECK
    # =====================================================

    def test_health(self):

        health = (
            self.engine.health()
        )

        self.assertEqual(

            health["status"],

            "healthy"
        )

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print("""

=========================================================
KWANZACONTROL CFO AI
ENTERPRISE FEATURE ENGINEERING TEST SUITE
=========================================================

Running enterprise feature engineering tests...

=========================================================

""")

    unittest.main(
        verbosity=2
    )