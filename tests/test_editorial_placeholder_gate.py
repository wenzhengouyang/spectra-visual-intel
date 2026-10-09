import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate-editorial-issue.py"
SPEC = importlib.util.spec_from_file_location("validate_editorial_issue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class EditorialPlaceholderGateTest(unittest.TestCase):
    def test_internal_verification_placeholder_is_blocked(self):
        self.assertTrue(MODULE.contains_internal_placeholder(
            "来源标题提及上述动态，具体口径与背景尚待原文核验。"
        ))

    def test_normal_reader_copy_is_allowed(self):
        self.assertFalse(MODULE.contains_internal_placeholder(
            "公司表示，新工具将于本周向测试用户开放。"
        ))


if __name__ == "__main__":
    unittest.main()
