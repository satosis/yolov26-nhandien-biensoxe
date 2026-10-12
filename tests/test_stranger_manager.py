import unittest
from unittest.mock import MagicMock
import numpy as np
import backend.ai.stranger_manager as sm


class TestStrangerManager(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_db._execute_query.return_value = []
        sm.db = self.mock_db
        self.manager = sm.StrangerManager()
        self.manager._initialized = True  # Avoid querying real DB on init

    def test_empty_emb(self):
        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        stranger_id, sim, is_new = self.manager.process_stranger(
            emb=None, frame=dummy_frame, cam_id="cam_01", bbox=[10, 10, 50, 50]
        )
        self.assertEqual(stranger_id, -1)
        self.assertEqual(sim, 0.0)
        self.assertFalse(is_new)

    def test_new_stranger_registration(self):
        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        emb1 = np.random.randn(512).astype(np.float32)
        emb1 = emb1 / np.linalg.norm(emb1)

        self.mock_db._execute_query.return_value = 101

        stranger_id, sim, is_new = self.manager.process_stranger(
            emb=emb1, frame=dummy_frame, cam_id="cam_01", bbox=[10, 10, 50, 50]
        )
        self.assertEqual(stranger_id, 101)
        self.assertTrue(is_new)
        self.assertEqual(len(self.manager._records), 1)

    def test_matching_existing_stranger(self):
        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        emb1 = np.random.randn(512).astype(np.float32)
        emb1 = emb1 / np.linalg.norm(emb1)

        self.mock_db._execute_query.return_value = 102
        s_id1, _, is_new1 = self.manager.process_stranger(
            emb=emb1, frame=dummy_frame, cam_id="cam_01", bbox=[10, 10, 50, 50]
        )
        self.assertEqual(s_id1, 102)
        self.assertTrue(is_new1)

        # Same or very close embedding (> 0.95 cosine similarity)
        emb2 = emb1.copy()
        s_id2, sim2, is_new2 = self.manager.process_stranger(
            emb=emb2, frame=dummy_frame, cam_id="cam_01", bbox=[10, 10, 50, 50]
        )
        self.assertEqual(s_id2, 102)
        self.assertFalse(is_new2)
        self.assertGreater(sim2, 0.95)

    def test_remove_stranger(self):
        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        emb1 = np.random.randn(512).astype(np.float32)
        emb1 = emb1 / np.linalg.norm(emb1)

        self.mock_db._execute_query.return_value = 103
        self.manager.process_stranger(
            emb=emb1, frame=dummy_frame, cam_id="cam_01", bbox=[10, 10, 50, 50]
        )
        self.assertEqual(len(self.manager._records), 1)

        self.manager.remove(103)
        self.assertEqual(len(self.manager._records), 0)
        self.assertIsNone(self.manager._matrix)


if __name__ == "__main__":
    unittest.main()

