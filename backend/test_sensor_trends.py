import random
import unittest
from build_sensor_trends import record


class TrendTests(unittest.TestCase):
    def test_deepening_is_declining(self):
        xs = list(range(0, 140, 7))
        self.assertEqual(record(xs, [100 + 0.5 * x / 7 for x in xs])["c"], "declining")

    def test_shallowing_is_rising(self):
        xs = list(range(0, 140, 7))
        self.assertEqual(record(xs, [100 - 0.5 * x / 7 for x in xs])["c"], "rising")

    def test_noise_is_stable(self):
        random.seed(1)
        xs = list(range(0, 140, 7))
        self.assertEqual(record(xs, [100 + random.uniform(-1, 1) for _ in xs])["c"], "stable")

    def test_slope_magnitude(self):
        xs = list(range(0, 140, 7))
        self.assertAlmostEqual(record(xs, [100 + 0.5 * x / 7 for x in xs])["s"], 0.5, places=3)

    def test_too_few_points(self):
        self.assertEqual(record([0, 7, 14], [1, 2, 3])["c"], "none")


if __name__ == "__main__":
    unittest.main()
