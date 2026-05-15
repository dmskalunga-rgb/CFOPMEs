# =========================================================
# TESTS / UNIT / test_batch_pipeline.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise Batch Pipeline Unit Tests
# =========================================================

from __future__ import annotations

import os
import time
import json
import shutil
import unittest

from typing import (
    Dict,
    Any,
    List
)

from concurrent.futures import (
    ThreadPoolExecutor
)

# =========================================================
# MOCK ENTERPRISE BATCH PIPELINE
# =========================================================

class EnterpriseBatchPipeline:

    def __init__(self):

        self.jobs = []

        self.storage_path = (
            "batch_pipeline_storage"
        )

        os.makedirs(
            self.storage_path,
            exist_ok=True
        )

    # =====================================================
    # SUBMIT JOB
    # =====================================================

    def submit_job(

        self,

        tenant_id: str,

        pipeline_name: str,

        payload: Dict[str, Any]

    ) -> Dict[str, Any]:

        job = {

            "job_id":
                f"job-{time.time_ns()}",

            "tenant_id":
                tenant_id,

            "pipeline_name":
                pipeline_name,

            "payload":
                payload,

            "status":
                "QUEUED",

            "created_at":
                time.time()
        }

        self.jobs.append(job)

        self._persist_job(job)

        return job

    # =====================================================
    # EXECUTE JOB
    # =====================================================

    def execute_job(

        self,

        job_id: str

    ) -> Dict[str, Any]:

        for job in self.jobs:

            if job["job_id"] == job_id:

                job["status"] = (
                    "RUNNING"
                )

                time.sleep(0.1)

                job["result"] = {

                    "success":
                        True,

                    "records_processed":
                        1000
                }

                job["status"] = (
                    "COMPLETED"
                )

                self._persist_job(job)

                return job

        raise Exception(
            "Job not found."
        )

    # =====================================================
    # GET JOB
    # =====================================================

    def get_job(

        self,

        job_id: str

    ) -> Dict[str, Any]:

        for job in self.jobs:

            if job["job_id"] == job_id:

                return job

        raise Exception(
            "Job not found."
        )

    # =====================================================
    # LIST JOBS
    # =====================================================

    def list_jobs(

        self,

        tenant_id: str | None = None

    ) -> List[Dict[str, Any]]:

        if not tenant_id:

            return self.jobs

        return [

            job for job in self.jobs

            if job["tenant_id"]
            == tenant_id
        ]

    # =====================================================
    # DELETE JOB
    # =====================================================

    def delete_job(

        self,

        job_id: str

    ) -> bool:

        for job in self.jobs:

            if job["job_id"] == job_id:

                self.jobs.remove(job)

                return True

        return False

    # =====================================================
    # PERSISTENCE
    # =====================================================

    def _persist_job(

        self,

        job: Dict[str, Any]

    ):

        path = os.path.join(

            self.storage_path,

            f"{job['job_id']}.json"
        )

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                job,
                file,
                indent=2
            )

    # =====================================================
    # HEALTH CHECK
    # =====================================================

    def health(self):

        return {

            "service":
                "enterprise_batch_pipeline",

            "status":
                "healthy",

            "queued_jobs":
                len(self.jobs)
        }

# =========================================================
# TEST CLASS
# =========================================================

class TestEnterpriseBatchPipeline(
    unittest.TestCase
):

    # =====================================================
    # SETUP
    # =====================================================

    def setUp(self):

        self.pipeline = (
            EnterpriseBatchPipeline()
        )

    # =====================================================
    # CLEANUP
    # =====================================================

    def tearDown(self):

        if os.path.exists(
            "batch_pipeline_storage"
        ):

            shutil.rmtree(
                "batch_pipeline_storage"
            )

    # =====================================================
    # TEST JOB SUBMISSION
    # =====================================================

    def test_submit_job(self):

        job = self.pipeline.submit_job(

            tenant_id=
                "tenant-001",

            pipeline_name=
                "cashflow_pipeline",

            payload={

                "month":
                    "2026-05"
            }
        )

        self.assertEqual(

            job["status"],

            "QUEUED"
        )

        self.assertEqual(

            job["pipeline_name"],

            "cashflow_pipeline"
        )

    # =====================================================
    # TEST EXECUTION
    # =====================================================

    def test_execute_job(self):

        job = self.pipeline.submit_job(

            tenant_id=
                "tenant-002",

            pipeline_name=
                "fraud_pipeline",

            payload={}
        )

        result = (
            self.pipeline.execute_job(
                job["job_id"]
            )
        )

        self.assertEqual(

            result["status"],

            "COMPLETED"
        )

        self.assertTrue(

            result["result"]["success"]
        )

    # =====================================================
    # TEST GET JOB
    # =====================================================

    def test_get_job(self):

        job = self.pipeline.submit_job(

            tenant_id=
                "tenant-003",

            pipeline_name=
                "ueba_pipeline",

            payload={}
        )

        loaded = (
            self.pipeline.get_job(
                job["job_id"]
            )
        )

        self.assertEqual(

            loaded["job_id"],

            job["job_id"]
        )

    # =====================================================
    # TEST LIST JOBS
    # =====================================================

    def test_list_jobs(self):

        self.pipeline.submit_job(

            tenant_id=
                "tenant-A",

            pipeline_name=
                "pipeline-A",

            payload={}
        )

        self.pipeline.submit_job(

            tenant_id=
                "tenant-B",

            pipeline_name=
                "pipeline-B",

            payload={}
        )

        jobs_a = (
            self.pipeline.list_jobs(
                tenant_id="tenant-A"
            )
        )

        jobs_b = (
            self.pipeline.list_jobs(
                tenant_id="tenant-B"
            )
        )

        self.assertEqual(
            len(jobs_a),
            1
        )

        self.assertEqual(
            len(jobs_b),
            1
        )

    # =====================================================
    # TEST DELETE JOB
    # =====================================================

    def test_delete_job(self):

        job = self.pipeline.submit_job(

            tenant_id=
                "tenant-delete",

            pipeline_name=
                "delete_pipeline",

            payload={}
        )

        deleted = (
            self.pipeline.delete_job(
                job["job_id"]
            )
        )

        self.assertTrue(deleted)

    # =====================================================
    # TEST INVALID JOB
    # =====================================================

    def test_invalid_job(self):

        with self.assertRaises(
            Exception
        ):

            self.pipeline.get_job(
                "invalid-job-id"
            )

    # =====================================================
    # TEST PARALLEL EXECUTION
    # =====================================================

    def test_parallel_execution(self):

        jobs = []

        for i in range(5):

            job = self.pipeline.submit_job(

                tenant_id=
                    "tenant-parallel",

                pipeline_name=
                    f"pipeline-{i}",

                payload={}
            )

            jobs.append(job)

        with ThreadPoolExecutor(
            max_workers=5
        ) as executor:

            results = list(

                executor.map(

                    lambda j:
                    self.pipeline.execute_job(
                        j["job_id"]
                    ),

                    jobs
                )
            )

        self.assertEqual(
            len(results),
            5
        )

        for result in results:

            self.assertEqual(

                result["status"],

                "COMPLETED"
            )

    # =====================================================
    # TEST STORAGE FILE
    # =====================================================

    def test_storage_file_created(self):

        job = self.pipeline.submit_job(

            tenant_id=
                "tenant-storage",

            pipeline_name=
                "storage_pipeline",

            payload={}
        )

        filepath = os.path.join(

            "batch_pipeline_storage",

            f"{job['job_id']}.json"
        )

        self.assertTrue(
            os.path.exists(filepath)
        )

    # =====================================================
    # TEST HEALTH
    # =====================================================

    def test_health(self):

        health = (
            self.pipeline.health()
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
ENTERPRISE BATCH PIPELINE TEST SUITE
=========================================================

Running enterprise pipeline tests...

=========================================================

""")

    unittest.main(
        verbosity=2
    )