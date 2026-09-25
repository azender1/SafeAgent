"""Behavioral checks for source discrepancies and refusal to infer unknown rates."""

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from reconcile import run


SCHEMAS = {
    "payroll": ("employee_id", "work_date", "local", "classification", "covered_hours"),
    "rates": ("local", "classification", "fund", "effective_from", "effective_to", "rate_per_hour"),
    "remittance": ("employee_id", "local", "classification", "fund", "reported_hours", "reported_amount"),
    "fund_ack": ("local", "fund", "amount_received"),
}


class FringeProofTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = {name: Path(self.temp.name) / (name + ".csv") for name in SCHEMAS}
        self.rows = {
            "payroll": [
                ("WORKER-001", "2026-09-01", "L24", "journeyworker", "8"),
                ("WORKER-001", "2026-09-02", "L24", "journeyworker", "8"),
            ],
            "rates": [
                ("L24", "journeyworker", "HEALTH", "2026-01-01", "2026-09-01", "10"),
                ("L24", "journeyworker", "HEALTH", "2026-09-02", "", "11"),
            ],
            "remittance": [("WORKER-001", "L24", "journeyworker", "HEALTH", "16", "160.00")],
            "fund_ack": [("L24", "HEALTH", "160.00")],
        }

    def report(self):
        for name, rows in self.rows.items():
            with self.paths[name].open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(SCHEMAS[name])
                writer.writerows(rows)
        return run(self.paths, date(2026, 9, 1), date(2026, 9, 7))

    def test_fund_receipt_can_match_an_incorrect_worker_amount(self):
        report = self.report()
        self.assertEqual(report["status"], "EXCEPTIONS")
        amount = [finding for finding in report["findings"] if finding["type"] == "AMOUNT_DELTA"]
        self.assertEqual(len(amount), 1)
        self.assertEqual(amount[0]["expected_amount"], "168.00")
        self.assertEqual(amount[0]["reported_amount"], "160.00")
        self.assertFalse(any(f["type"] == "FUND_AMOUNT_DELTA" for f in report["findings"]))
        self.assertEqual(report, self.report())

    def test_missing_fund_receipt_is_unverified_not_a_match(self):
        self.rows["remittance"] = [("WORKER-001", "L24", "journeyworker", "HEALTH", "16", "168.00")]
        self.rows["fund_ack"] = []
        findings = self.report()["findings"]
        self.assertEqual({f["type"] for f in findings}, {"EMPTY_SOURCE", "MISSING_FUND_ACK"})

    def test_rate_overlap_requires_human_resolution(self):
        self.rows["rates"].append(("L24", "journeyworker", "HEALTH", "2026-09-02", "", "12"))
        self.assertIn("AMBIGUOUS_RATE", {f["type"] for f in self.report()["findings"]})

    def test_out_of_period_records_are_rejected(self):
        self.rows["payroll"].append(("WORKER-002", "2026-08-31", "L24", "journeyworker", "8"))
        with self.assertRaisesRegex(ValueError, "outside selected period"):
            self.report()

    def test_all_empty_files_cannot_return_a_clean_report(self):
        self.rows = {name: [] for name in SCHEMAS}
        report = self.report()
        self.assertEqual(report["status"], "EXCEPTIONS")
        self.assertEqual(len(report["findings"]), 4)


if __name__ == "__main__":
    unittest.main()
