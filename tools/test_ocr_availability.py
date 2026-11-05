import unittest
from unittest.mock import patch
from mediaviewer import ocr

class OptionalOcrTests(unittest.TestCase):
    def test_missing_parent(self):
        with patch.object(ocr.sys, 'platform', 'win32'):
            for parent in ('winrt', 'winrt.windows', 'winrt.windows.media'):
                with self.subTest(parent=parent), patch.object(ocr.importlib.util, 'find_spec', side_effect=ModuleNotFoundError(parent)):
                    self.assertFalse(ocr.OcrManager.packages_ok(None))

    def test_present_or_absent_leaf(self):
        with patch.object(ocr.sys, 'platform', 'win32'):
            for spec, expected in ((None, False), (object(), True)):
                with patch.object(ocr.importlib.util, 'find_spec', return_value=spec):
                    self.assertEqual(ocr.OcrManager.packages_ok(None), expected)

    def test_other_platform(self):
        with patch.object(ocr.sys, 'platform', 'darwin'), patch.object(ocr.importlib.util, 'find_spec') as lookup:
            self.assertFalse(ocr.OcrManager.packages_ok(None))
            lookup.assert_not_called()

if __name__ == '__main__':
    unittest.main()
