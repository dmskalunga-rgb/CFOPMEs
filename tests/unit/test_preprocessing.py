# =========================================================
# TESTS / UNIT / test_preprocessing.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise Data Preprocessing Test Suite
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

from sklearn.preprocessing import (
    StandardScaler,
    MinMaxScaler,
    LabelEncoder
)

from sklearn.impute import (
    SimpleImputer
)

# =========================================================
# PREPROCESSING RESULT
# =========================================================

@dataclass
class PreprocessingResult:

    tenant_id: str

    rows_processed: int

    columns_processed: int

    missing_values_fixed: int

    normalized_columns: List[str]

    encoded_columns: List[str]

    execution_time: float

    created_at: float

# =========================================================
# ENTERPRISE PREPROCESSOR
# =========================================================

class EnterprisePreprocessor:

    def __init__(self):

        self.storage_dir = (
            "preprocessing_storage"
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

        self.label_encoder = (
            LabelEncoder()
        )

    # =====================================================
    # MAIN PROCESS
    # =====================================================

    def process(

        self,

        tenant_id: str,

        dataframe: pd.DataFrame

    ) -> tuple[pd.DataFrame,
               PreprocessingResult]:

        start = time.time()

        df = dataframe.copy()

        # =================================================
        # FIX MISSING VALUES
        # =================================================

        missing_before = (
            df.isnull()
            .sum()
            .sum()
        )

        numeric_columns = (

            df.select_dtypes(
                include=[np.number]
            ).columns
        )

        if len(numeric_columns) > 0:

            df[numeric_columns] = (

                self.imputer.fit_transform(

                    df[numeric_columns]
                )
            )

        # =================================================
        # ENCODE CATEGORICAL
        # =================================================

        encoded_columns = []

        categorical_columns = (

            df.select_dtypes(
                include=["object"]
            ).columns
        )

        for column in categorical_columns:

            df[column] = (

                self.label_encoder
                .fit_transform(

                    df[column]
                )
            )

            encoded_columns.append(
                column
            )

        # =================================================
        # NORMALIZE NUMERICAL
        # =================================================

        normalized_columns = []

        for column in numeric_columns:

            df[column] = (

                self.minmax_scaler
                .fit_transform(

                    df[[column]]
                )
            )

            normalized_columns.append(
                column
            )

        # =================================================
        # RESULT
        # =================================================

        result = PreprocessingResult(

            tenant_id=
                tenant_id,

            rows_processed=
                len(df),

            columns_processed=
                len(df.columns),

            missing_values_fixed=
                int(missing_before),

            normalized_columns=
                normalized_columns,

            encoded_columns=
                encoded_columns,

            execution_time=
                time.time() - start,

            created_at=
                time.time()
        )

        self._persist(result)

        return df, result

    # =====================================================
    # SAVE RESULT
    # =====================================================

    def _persist(

        self,

        result: PreprocessingResult

    ):

        filename = (

            f"preprocessing_"
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
                "enterprise_preprocessor",

            "status":
                "healthy",

            "enterprise_mode":
                True
        }

# =========================================================
# TEST SUITE
# =========================================================

class TestEnterprisePreprocessing(
    unittest.TestCase
):

    # =====================================================
    # SETUP
    # =====================================================

    def setUp(self):

        self.preprocessor = (
            EnterprisePreprocessor()
        )

        self.df = pd.DataFrame({

            "revenue": [
                1000,
                2000,
                np.nan,
                5000
            ],

            "expenses": [
                500,
                np.nan,
                1200,
                2000
            ],

            "department": [
                "finance",
                "hr",
                "finance",
                "operations"
            ],

            "region": [
                "luanda",
                "benguela",
                "huambo",
                "luanda"
            ]
        })

    # =====================================================
    # CLEANUP
    # =====================================================

    def tearDown(self):

        if os.path.exists(
            "preprocessing_storage"
        ):

            shutil.rmtree(
                "preprocessing_storage"
            )

    # =====================================================
    # TEST PROCESS
    # =====================================================

    def test_process(self):

        processed, result = (

            self.preprocessor.process(

                tenant_id=
                    "tenant-001",

                dataframe=
                    self.df
            )
        )

        self.assertEqual(

            result.rows_processed,

            4
        )

    # =====================================================
    # TEST MISSING VALUES
    # =====================================================

    def test_missing_values(self):

        processed, result = (

            self.preprocessor.process(

                tenant_id=
                    "tenant-002",

                dataframe=
                    self.df
            )
        )

        self.assertFalse(

            processed.isnull()
            .values.any()
        )

    # =====================================================
    # TEST ENCODING
    # =====================================================

    def test_encoding(self):

        processed, result = (

            self.preprocessor.process(

                tenant_id=
                    "tenant-003",

                dataframe=
                    self.df
            )
        )

        self.assertTrue(

            np.issubdtype(

                processed["department"]
                .dtype,

                np.number
            )
        )

    # =====================================================
    # TEST NORMALIZATION
    # =====================================================

    def test_normalization(self):

        processed, result = (

            self.preprocessor.process(

                tenant_id=
                    "tenant-004",

                dataframe=
                    self.df
            )
        )

        self.assertTrue(

            processed["revenue"]
            .max() <= 1
        )

    # =====================================================
    # TEST STORAGE
    # =====================================================

    def test_storage(self):

        self.preprocessor.process(

            tenant_id=
                "tenant-storage",

            dataframe=
                self.df
        )

        files = os.listdir(
            "preprocessing_storage"
        )

        self.assertTrue(
            len(files) > 0
        )

    # =====================================================
    # TEST HEALTH
    # =====================================================

    def test_health(self):

        health = (
            self.preprocessor.health()
        )

        self.assertEqual(

            health["status"],

            "healthy"
        )

    # =====================================================
    # TEST EXECUTION TIME
    # =====================================================

    def test_execution_time(self):

        processed, result = (

            self.preprocessor.process(

                tenant_id=
                    "tenant-speed",

                dataframe=
                    self.df
            )
        )

        self.assertLess(

            result.execution_time,

            5
        )

    # =====================================================
    # TEST MULTI TENANT
    # =====================================================

    def test_multi_tenant(self):

        _, result_a = (

            self.preprocessor.process(

                tenant_id=
                    "tenant-A",

                dataframe=
                    self.df
            )
        )

        _, result_b = (

            self.preprocessor.process(

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
    # TEST LARGE DATASET
    # =====================================================

    def test_large_dataset(self):

        large_df = pd.DataFrame({

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

            "department":
                np.random.choice(

                    [
                        "finance",
                        "hr",
                        "ops"
                    ],

                    10000
                )
        })

        processed, result = (

            self.preprocessor.process(

                tenant_id=
                    "tenant-large",

                dataframe=
                    large_df
            )
        )

        self.assertEqual(

            len(processed),

            10000
        )

    # =====================================================
    # TEST EMPTY DATAFRAME
    # =====================================================

    def test_empty_dataframe(self):

        empty_df = pd.DataFrame()

        processed, result = (

            self.preprocessor.process(

                tenant_id=
                    "tenant-empty",

                dataframe=
                    empty_df
            )
        )

        self.assertEqual(

            len(processed.columns),

            0
        )

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print("""

=========================================================
KWANZACONTROL CFO AI
ENTERPRISE PREPROCESSING TEST SUITE
=========================================================

Running enterprise preprocessing tests...

=========================================================

""")

    unittest.main(
        verbosity=2
    )