import unittest
from backend.ai.inference import PlateVoter


class TestPlateVoter(unittest.TestCase):
    def test_voter_empty(self):
        voter = PlateVoter(buffer_size=5)
        self.assertFalse(voter.is_ready())
        best_text, best_conf = voter.best()
        self.assertEqual(best_text, "")
        self.assertEqual(best_conf, 0.0)

    def test_voter_add_and_best(self):
        voter = PlateVoter(buffer_size=5)
        # Add high confidence readings
        voter.add("30A12345", 0.90)
        voter.add("30A12345", 0.92)
        voter.add("30A12345", 0.94)

        # 3 readings with conf > 0.88 should trigger is_ready
        self.assertTrue(voter.is_ready())
        best_text, best_conf = voter.best()
        self.assertEqual(best_text, "30A12345")
        self.assertAlmostEqual(best_conf, 0.92, places=2)

    def test_voter_majority_vote(self):
        voter = PlateVoter(buffer_size=5)
        voter.add("29A11111", 0.75)
        voter.add("30A22222", 0.85)
        voter.add("30A22222", 0.86)
        voter.add("30A22222", 0.84)
        voter.add("29A11111", 0.72)

        self.assertTrue(voter.is_ready())
        best_text, best_conf = voter.best()
        self.assertEqual(best_text, "30A22222")

    def test_voter_reset(self):
        voter = PlateVoter(buffer_size=5)
        voter.add("30A12345", 0.95)
        voter.reset()
        self.assertFalse(voter.is_ready())
        best_text, _ = voter.best()
        self.assertEqual(best_text, "")


if __name__ == "__main__":
    unittest.main()
