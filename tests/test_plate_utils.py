import unittest
import numpy as np
from backend.services import config as cfg
from backend.ai.inference import validate_plate_crop, enhance_plate


class TestPlateUtils(unittest.TestCase):
    def test_normalize_plate(self):
        self.assertEqual(cfg.normalize_plate("30A-123.45"), "30A12345")
        self.assertEqual(cfg.normalize_plate("51F 999.99"), "51F99999")
        self.assertEqual(cfg.normalize_plate("29-B1 012.34"), "29B101234")
        self.assertEqual(cfg.normalize_plate(""), "")
        self.assertEqual(cfg.normalize_plate(None), "")

    def test_validate_plate_crop_empty(self):
        self.assertFalse(validate_plate_crop(None))
        self.assertFalse(validate_plate_crop(np.zeros((0, 0, 3), dtype=np.uint8)))

    def test_validate_plate_crop_bad_aspect(self):
        # Too tall: h=100, w=50 (aspect 0.5 < 0.8)
        tall_crop = np.full((100, 50, 3), 128, dtype=np.uint8)
        self.assertFalse(validate_plate_crop(tall_crop))

        # Too small: h=10, w=20
        small_crop = np.full((10, 20, 3), 128, dtype=np.uint8)
        self.assertFalse(validate_plate_crop(small_crop))

    def test_enhance_plate_none(self):
        self.assertIsNone(enhance_plate(None))
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        self.assertEqual(enhance_plate(empty).size, 0)

    def test_enhance_plate_shape(self):
        dummy_plate = np.full((50, 150, 3), 120, dtype=np.uint8)
        enhanced = enhance_plate(dummy_plate)
        self.assertIsNotNone(enhanced)
        # Should be upscaled height >= 100
        self.assertGreaterEqual(enhanced.shape[0], 100)


if __name__ == "__main__":
    unittest.main()
