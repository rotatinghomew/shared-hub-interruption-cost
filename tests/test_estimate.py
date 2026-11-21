"""分队列估计与未覆盖框规则测试。"""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.data import Dataset
from planner.scheduler import schedule
from planner.identity import build_partitions
from planner.estimate import (
    covered_estimates, uncovered_bounds, aggregate_covered, Interval,
)


class EstimateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = Dataset.build()

    def test_uncovered_frame_never_claims_zero_demand(self):
        bounds = uncovered_bounds(self.ds)
        self.assertEqual(set(bounds), {"午后"})
        band = bounds["午后"]["household_bounds"]
        # 25 次经过，每户 1~2 次 -> 13~25 户，且无点估计
        self.assertEqual((band.low, band.high), (13, 25))
        self.assertIsNone(band.point)

    def test_ht_extrapolation_and_floor(self):
        split, _ = build_partitions(self.ds, 2)
        result = schedule(self.ds, list(self.ds.trips), ())
        estimates = covered_estimates(self.ds, result, split)
        early = estimates["早班"]
        # 早班 p=0.3：需求上界 = 观测数 / 0.3
        self.assertAlmostEqual(early.demand.high, early.observed_trips / 0.3)
        self.assertEqual(early.demand.low, early.observed_trips)
        # 损失率地板不高于点估计，点估计不高于 1
        self.assertLessEqual(early.loss_rate.low, early.loss_rate.point + 1e-9)
        self.assertLessEqual(early.loss_rate.point, 1.0)

    def test_aggregate_rates_stay_within_unit_interval(self):
        split, _ = build_partitions(self.ds, 2)
        for shutdown in [(), ("工段甲",), ("工段丙", "工段乙", "工段甲")]:
            result = schedule(self.ds, list(self.ds.trips), shutdown)
            agg = aggregate_covered(covered_estimates(self.ds, result, split))
            rate = agg["loss_rate"]
            self.assertGreaterEqual(rate.low, 0.0)
            self.assertLessEqual(rate.high, 1.0 + 1e-9)
            self.assertTrue(rate.low <= rate.point <= rate.high)

    def test_registered_cohort_is_deterministic_not_extrapolated(self):
        split, _ = build_partitions(self.ds, 2)
        result = schedule(self.ds, list(self.ds.trips), ())
        reg = covered_estimates(self.ds, result, split)["已登记"]
        self.assertEqual(reg.demand.low, reg.demand.high)
        self.assertEqual(reg.demand.point, reg.demand.low)


if __name__ == "__main__":
    unittest.main()
