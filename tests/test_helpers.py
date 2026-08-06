import unittest
from pathlib import Path

from automation_pipeline import (
    _normalise_columns,
    extract_date_from_filename,
    parse_column_name,
)
import pandas as pd


class ReportingHelperTests(unittest.TestCase):
    def test_extract_date_from_filename(self):
        self.assertEqual(extract_date_from_filename(Path("TPRD_20250731.csv")), "2025-07")
        self.assertIsNone(extract_date_from_filename(Path("missing-date.csv")))

    def test_identity_column_alias_is_normalized(self):
        result = _normalise_columns(pd.DataFrame({"Fund": ["Example"]}))
        self.assertIn("Group/Investment", result.columns)

    def test_exposure_parser_returns_three_fields(self):
        parsed = parse_column_name("United States Equity Exposure")
        self.assertEqual(len(parsed), 3)


if __name__ == "__main__":
    unittest.main()
