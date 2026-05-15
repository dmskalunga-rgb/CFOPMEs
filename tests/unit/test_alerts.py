# =========================================================
# TESTS / UNIT / test_alerts.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise Governance Alerts Unit Tests
# =========================================================

from __future__ import annotations

import os
import json
import shutil
import unittest

from typing import Dict, Any

# =========================================================
# IMPORT ALERT ENGINE
# =========================================================

from ml.governance.alerts import (

    EnterpriseAlertEngine,

    AlertCategory,

    AlertPriority,

    AlertStatus
)

# =========================================================
# TEST CONFIG
# =========================================================

TEST_ALERT_DIR = (
    "alerts_storage"
)

TEST_TENANT = (
    "tenant-test-enterprise"
)

# =========================================================
# TEST CLASS
# =========================================================

class TestEnterpriseAlerts(
    unittest.TestCase
):

    # =====================================================
    # SETUP
    # =====================================================

    def setUp(self):

        self.engine = (
            EnterpriseAlertEngine()
        )

        os.makedirs(
            TEST_ALERT_DIR,
            exist_ok=True
        )

    # =====================================================
    # CLEANUP
    # =====================================================

    def tearDown(self):

        if os.path.exists(
            TEST_ALERT_DIR
        ):

            shutil.rmtree(
                TEST_ALERT_DIR
            )

    # =====================================================
    # CREATE ALERT
    # =====================================================

    def test_create_alert(self):

        alert = self.engine.create_alert(

            tenant_id=
                TEST_TENANT,

            category=
                AlertCategory.FRAUD,

            priority=
                AlertPriority.CRITICAL,

            title=
                "Fraud Detection Triggered",

            description=
                "High-value suspicious transaction detected.",

            source=
                "fraud_model_v2",

            metadata={

                "transaction_id":
                    "txn-001",

                "score":
                    0.97
            },

            tags=[
                "fraud",
                "critical"
            ]
        )

        self.assertIsNotNone(
            alert.alert_id
        )

        self.assertEqual(
            alert.category,
            AlertCategory.FRAUD
        )

        self.assertEqual(
            alert.priority,
            AlertPriority.CRITICAL
        )

        self.assertEqual(
            alert.status,
            AlertStatus.OPEN
        )

    # =====================================================
    # LOAD ALERT
    # =====================================================

    def test_load_alert(self):

        alert = self.engine.create_alert(

            tenant_id=
                TEST_TENANT,

            category=
                AlertCategory.SECURITY,

            priority=
                AlertPriority.HIGH,

            title=
                "Unauthorized Access",

            description=
                "Multiple failed login attempts.",

            source=
                "auth_service"
        )

        loaded = (
            self.engine.load_alert(
                alert.alert_id
            )
        )

        self.assertEqual(

            loaded["title"],

            "Unauthorized Access"
        )

    # =====================================================
    # LIST ALERTS
    # =====================================================

    def test_list_alerts(self):

        self.engine.create_alert(

            tenant_id=
                TEST_TENANT,

            category=
                AlertCategory.CASHFLOW,

            priority=
                AlertPriority.MEDIUM,

            title=
                "Cashflow Risk",

            description=
                "Negative trend forecast.",

            source=
                "cashflow_model"
        )

        alerts = (
            self.engine.list_alerts(
                tenant_id=TEST_TENANT
            )
        )

        self.assertTrue(
            len(alerts) > 0
        )

    # =====================================================
    # UPDATE STATUS
    # =====================================================

    def test_update_status(self):

        alert = self.engine.create_alert(

            tenant_id=
                TEST_TENANT,

            category=
                AlertCategory.MODEL_DRIFT,

            priority=
                AlertPriority.HIGH,

            title=
                "Model Drift Detected",

            description=
                "Prediction drift above threshold.",

            source=
                "monitoring_engine"
        )

        self.engine.update_status(

            alert_id=
                alert.alert_id,

            status=
                AlertStatus.ACKNOWLEDGED,

            assigned_to=
                "ml-engineer",

            resolution_notes=
                "Retraining scheduled."
        )

        updated = (
            self.engine.load_alert(
                alert.alert_id
            )
        )

        self.assertEqual(

            updated["status"],

            AlertStatus.ACKNOWLEDGED
        )

        self.assertEqual(

            updated["assigned_to"],

            "ml-engineer"
        )

    # =====================================================
    # FILTER CATEGORY
    # =====================================================

    def test_filter_by_category(self):

        self.engine.create_alert(

            tenant_id=
                TEST_TENANT,

            category=
                AlertCategory.UEBA,

            priority=
                AlertPriority.LOW,

            title=
                "Suspicious Behavior",

            description=
                "Unusual user activity pattern.",

            source=
                "ueba_engine"
        )

        results = (
            self.engine.list_alerts(

                tenant_id=
                    TEST_TENANT,

                category=
                    AlertCategory.UEBA
            )
        )

        self.assertEqual(
            len(results),
            1
        )

    # =====================================================
    # FILTER PRIORITY
    # =====================================================

    def test_filter_by_priority(self):

        self.engine.create_alert(

            tenant_id=
                TEST_TENANT,

            category=
                AlertCategory.API,

            priority=
                AlertPriority.CRITICAL,

            title=
                "API Failure",

            description=
                "Critical API endpoint down.",

            source=
                "api_gateway"
        )

        critical_alerts = (
            self.engine.list_alerts(

                tenant_id=
                    TEST_TENANT,

                priority=
                    AlertPriority.CRITICAL
            )
        )

        self.assertEqual(
            len(critical_alerts),
            1
        )

    # =====================================================
    # INVALID ALERT LOAD
    # =====================================================

    def test_invalid_alert_load(self):

        with self.assertRaises(
            Exception
        ):

            self.engine.load_alert(
                "invalid-alert-id"
            )

    # =====================================================
    # MULTI TENANT ISOLATION
    # =====================================================

    def test_multi_tenant_isolation(self):

        self.engine.create_alert(

            tenant_id=
                "tenant-A",

            category=
                AlertCategory.FRAUD,

            priority=
                AlertPriority.HIGH,

            title=
                "Tenant A Fraud",

            description=
                "Fraud alert A",

            source=
                "fraud_engine"
        )

        self.engine.create_alert(

            tenant_id=
                "tenant-B",

            category=
                AlertCategory.FRAUD,

            priority=
                AlertPriority.HIGH,

            title=
                "Tenant B Fraud",

            description=
                "Fraud alert B",

            source=
                "fraud_engine"
        )

        tenant_a_alerts = (
            self.engine.list_alerts(
                tenant_id="tenant-A"
            )
        )

        tenant_b_alerts = (
            self.engine.list_alerts(
                tenant_id="tenant-B"
            )
        )

        self.assertEqual(
            len(tenant_a_alerts),
            1
        )

        self.assertEqual(
            len(tenant_b_alerts),
            1
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

        self.assertTrue(
            health["enterprise_mode"]
        )

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print("""

=========================================================
KWANZACONTROL CFO AI
ENTERPRISE ALERT TEST SUITE
=========================================================

Running enterprise governance tests...

=========================================================

""")

    unittest.main(
        verbosity=2
    )