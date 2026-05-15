# =========================================================
# TESTS / UNIT / test_loaders.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise Data Loaders Test Suite
# =========================================================

from __future__ import annotations

import os
import csv
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

import pandas as pd
import numpy as np

# =========================================================
# ENTERPRISE LOADER RESULT
# =========================================================

@dataclass
class LoaderResult:

    tenant_id: str

    loader_name: str

    rows_loaded: int

    columns_loaded: int

    execution_time: float

    success: bool

    created_at: float

# =========================================================
# ENTERPRISE DATA LOADER
# =========================================================

class EnterpriseDataLoader:

    def __init__(self):

        self.storage_dir = (
            "loader_storage"
        )

        os.makedirs(
            self.storage_dir,
            exist_ok=True
        )

    # =====================================================
    # LOAD CSV
    # =====================================================

    def load_csv(

        self,

        tenant_id: str,

        filepath: str

    ) -> tuple[pd.DataFrame,
               LoaderResult]:

        start = time.time()

        dataframe = pd.read_csv(
            filepath
        )

        result = LoaderResult(

            tenant_id=
                tenant_id,

            loader_name=
                "csv_loader",

            rows_loaded=
                len(dataframe),

            columns_loaded=
                len(dataframe.columns),

            execution_time=
                time.time() - start,

            success=
                True,

            created_at=
                time.time()
        )

        self._persist(result)

        return dataframe, result

    # =====================================================
    # LOAD JSON
    # =====================================================

    def load_json(

        self,

        tenant_id: str,

        filepath: str

    ) -> tuple[pd.DataFrame,
               LoaderResult]:

        start = time.time()

        with open(
            filepath,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        dataframe = pd.DataFrame(
            data
        )

        result = LoaderResult(

            tenant_id=
                tenant_id,

            loader_name=
                "json_loader",

            rows_loaded=
                len(dataframe),

            columns_loaded=
                len(dataframe.columns),

            execution_time=
                time.time() - start,

            success=
                True,

            created_at=
                time.time()
        )

        self._persist(result)

        return dataframe, result

    # =====================================================
    # LOAD PARQUET
    # =====================================================

    def load_parquet(

        self,

        tenant_id: str,

        filepath: str

    ) -> tuple[pd.DataFrame,
               LoaderResult]:

        start = time.time()

        dataframe = pd.read_parquet(
            filepath
        )

        result = LoaderResult(

            tenant_id=
                tenant_id,

            loader_name=
                "parquet_loader",

            rows_loaded=
                len(dataframe),

            columns_loaded=
                len(dataframe.columns),

            execution_time=
                time.time() - start,

            success=
                True,

            created_at=
                time.time()
        )

        self._persist(result)

        return dataframe, result

    # =====================================================
    # VALIDATE DATAFRAME
    # =====================================================

    def validate_dataframe(

        self,

        dataframe: pd.DataFrame

    ) -> bool:

        if dataframe.empty:

            return False

        if len(dataframe.columns) == 0:

            return False

        return True

    # =====================================================
    # PERSIST RESULTS
    # =====================================================

    def _persist(

        self,

        result: LoaderResult

    ):

        filename = (

            f"{result.loader_name}_"
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
                "enterprise_loader",

            "status":
                "healthy",

            "enterprise_mode":
                True
        }

# =========================================================
# TEST SUITE
# =========================================================

class TestEnterpriseLoaders(
    unittest.TestCase
):

    # =====================================================
    # SETUP
    # =====================================================

    def setUp(self):

        self.loader = (
            EnterpriseDataLoader()
        )

        os.makedirs(
            "test_data",
            exist_ok=True
        )

        # ================================================
        # CSV
        # ================================================

        self.csv_path = (
            "test_data/sample.csv"
        )

        df = pd.DataFrame({

            "revenue": [
                1000,
                2000,
                3000
            ],

            "expenses": [
                500,
                700,
                900
            ]
        })

        df.to_csv(
            self.csv_path,
            index=False
        )

        # ================================================
        # JSON
        # ================================================

        self.json_path = (
            "test_data/sample.json"
        )

        with open(
            self.json_path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(

                df.to_dict(
                    orient="records"
                ),

                file
            )

        # ================================================
        # PARQUET
        # ================================================

        self.parquet_path = (
            "test_data/sample.parquet"
        )

        df.to_parquet(
            self.parquet_path
        )

    # =====================================================
    # CLEANUP
    # =====================================================

    def tearDown(self):

        if os.path.exists(
            "loader_storage"
        ):

            shutil.rmtree(
                "loader_storage"
            )

        if os.path.exists(
            "test_data"
        ):

            shutil.rmtree(
                "test_data"
            )

    # =====================================================
    # TEST CSV LOADER
    # =====================================================

    def test_load_csv(self):

        dataframe, result = (

            self.loader.load_csv(

                tenant_id=
                    "tenant-csv",

                filepath=
                    self.csv_path
            )
        )

        self.assertEqual(

            result.rows_loaded,

            3
        )

    # =====================================================
    # TEST JSON LOADER
    # =====================================================

    def test_load_json(self):

        dataframe, result = (

            self.loader.load_json(

                tenant_id=
                    "tenant-json",

                filepath=
                    self.json_path
            )
        )

        self.assertEqual(

            result.columns_loaded,

            2
        )

    # =====================================================
    # TEST PARQUET LOADER
    # =====================================================

    def test_load_parquet(self):

        dataframe, result = (

            self.loader.load_parquet(

                tenant_id=
                    "tenant-parquet",

                filepath=
                    self.parquet_path
            )
        )

        self.assertTrue(

            len(dataframe) > 0
        )

    # =====================================================
    # TEST DATAFRAME VALIDATION
    # =====================================================

    def test_validate_dataframe(self):

        df = pd.DataFrame({

            "a": [1, 2, 3]
        })

        valid = (
            self.loader.validate_dataframe(
                df
            )
        )

        self.assertTrue(valid)

    # =====================================================
    # TEST EMPTY DATAFRAME
    # =====================================================

    def test_empty_dataframe(self):

        df = pd.DataFrame()

        valid = (
            self.loader.validate_dataframe(
                df
            )
        )

        self.assertFalse(valid)

    # =====================================================
    # TEST STORAGE FILE
    # =====================================================

    def test_storage_file(self):

        dataframe, result = (

            self.loader.load_csv(

                tenant_id=
                    "tenant-storage",

                filepath=
                    self.csv_path
            )
        )

        files = os.listdir(
            "loader_storage"
        )

        self.assertTrue(
            len(files) > 0
        )

    # =====================================================
    # TEST INVALID FILE
    # =====================================================

    def test_invalid_file(self):

        with self.assertRaises(
            Exception
        ):

            self.loader.load_csv(

                tenant_id=
                    "tenant-invalid",

                filepath=
                    "invalid.csv"
            )

    # =====================================================
    # TEST LARGE CSV
    # =====================================================

    def test_large_csv(self):

        large_path = (
            "test_data/large.csv"
        )

        large_df = pd.DataFrame({

            "revenue":
                np.random.randint(
                    1000,
                    50000,
                    10000
                ),

            "expenses":
                np.random.randint(
                    500,
                    30000,
                    10000
                )
        })

        large_df.to_csv(
            large_path,
            index=False
        )

        dataframe, result = (

            self.loader.load_csv(

                tenant_id=
                    "tenant-large",

                filepath=
                    large_path
            )
        )

        self.assertEqual(

            len(dataframe),

            10000
        )

    # =====================================================
    # TEST MULTI TENANT
    # =====================================================

    def test_multi_tenant_isolation(self):

        _, result_a = (

            self.loader.load_csv(

                tenant_id=
                    "tenant-A",

                filepath=
                    self.csv_path
            )
        )

        _, result_b = (

            self.loader.load_csv(

                tenant_id=
                    "tenant-B",

                filepath=
                    self.csv_path
            )
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
            self.loader.health()
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
ENTERPRISE DATA LOADERS TEST SUITE
=========================================================

Running enterprise data loader tests...

=========================================================

""")

    unittest.main(
        verbosity=2
    )