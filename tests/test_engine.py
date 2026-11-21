"""端到端评审：确认规则、排序敏感性、补点范围与事件时点。"""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.data import Dataset
from planner.engine import evaluate
from planner.policy import candidate_grid, active_candidates
from planner.scheduler import schedule


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = Dataset.build()
        cls.rv2 = evaluate(cls.ds, 2)
        cls.rv4 = evaluate(cls.ds, 4)

    def test_cancelled_segment_drops_out_after_decision(self):
        self.assertEqual(active_candidates(self.ds, 2),
                         {"工段甲", "工段乙", "工段丙"})
        self.assertEqual(active_candidates(self.ds, 4),
                         {"工段甲", "工段乙"})

    def test_baseline_and_segment_yi_confirmable_others_pending(self):
        for cutoff_review in (self.rv2, self.rv4):
            verdicts = {combo: ev.verdict
                        for combo, ev in cutoff_review.evaluations.items()}
            self.assertTrue(verdicts[()].confirmable)
            self.assertTrue(verdicts[("工段乙",)].confirmable)
            for combo, verdict in verdicts.items():
                if combo in ((), ("工段乙",)):
                    continue
                self.assertFalse(verdict.confirmable, combo)

    def test_ranking_robustness_signals(self):
        # cutoff=2：点估计排序不再因贪心抖动互换，但损失率区间跨解释重叠，
        # 甲 与 丙+乙 无法稳健区分，必须保持待议
        self.assertEqual(self.rv2.swaps, [])
        pair = (("工段甲",), ("工段丙", "工段乙"))
        self.assertIn(pair, self.rv2.indistinguishable)
        self.assertIn(pair, self.rv2.sensitive)

    def test_cutoff_four_still_flags_overlapping_bands(self):
        # 证据补全后未决链减少，但 甲 与 乙+甲 区间仍重叠
        self.assertEqual(self.rv4.swaps, [])
        self.assertIn((("工段甲",), ("工段乙", "工段甲")),
                      self.rv4.indistinguishable)
        # 可稳健区分的对子不得被标记
        self.assertNotIn(((), ("工段甲",)), self.rv4.sensitive)

    def test_followup_targets_stay_inside_covered_frames(self):
        for review in (self.rv2, self.rv4):
            for target in review.followup_targets:
                self.assertIsNotNone(self.ds.frames[target["frame"]]["probability"])
                self.assertNotEqual(target["frame"], "午后")

    def test_desk_triage_three_categories(self):
        ev_yi = self.rv4.evaluations[("工段乙",)]
        self.assertEqual(set(ev_yi.verdict.desk_categories.values()),
                         {"原预约无需改"})
        ev_jia = self.rv4.evaluations[("工段甲",)]
        self.assertEqual(ev_jia.verdict.desk_categories.get("访000"),
                         "确实无法安排")

    def test_any_shortening_of_segment_jia_still_breaches_protected_floor(self):
        ids = list(self.ds.trips)
        base = set(schedule(self.ds, ids, ()).unfinished)
        rooms = self.ds.candidates["工段甲"]["affects"]
        for grid in candidate_grid(self.ds, "工段甲"):
            custom = tuple((r, grid.start, grid.end) for r in rooms)
            result = schedule(self.ds, ids, (), custom_windows=custom)
            new_protected = {t for t in result.unfinished
                             if t not in base and self.ds.trips[t]["protected"]}
            self.assertTrue(new_protected, grid)

    def test_no_pending_window_comparison_uses_cherry_picked_mix(self):
        # 每个端点都必须来自同一个完整分区，不能按场所混着挑：
        # 两种解释各自独立给出全套结果，且并户数恒 <= 拆户数
        for combo, ev in self.rv2.evaluations.items():
            split_n = ev.results[self.rv2.partitions[0].name].unfinished_count
            merged_n = ev.results[self.rv2.partitions[1].name].unfinished_count
            self.assertLessEqual(merged_n, split_n)

    def test_structural_failure_is_monotonic_but_capacity_reallocation_is_not(self):
        # 硬边界：基线无可行整链的结构性失败行程，在任何停用下都必须仍失败
        for review in (self.rv2, self.rv4):
            base_structural = set()
            for part in review.partitions:
                base_structural |= set(
                    review.baseline[part.name].structurally_infeasible)
            for combo, ev in review.evaluations.items():
                if not combo:
                    continue
                for part in review.partitions:
                    now_structural = set(
                        ev.results[part.name].structurally_infeasible)
                    self.assertTrue(
                        base_structural <= now_structural,
                        (combo, part.name))
        # 容量/陪同性失败允许因求解器重排而变化（这些行程在基线下本就有
        # 可行方案，只是没抢到资源），但报告必须单列、不得计入结构性新增。
        ev = self.rv2.evaluations[("工段丙",)]
        rescued = (set(self.rv2.baseline[self.rv2.partitions[0].name].unfinished)
                   - set(ev.results[self.rv2.partitions[0].name].unfinished))
        for tid in rescued:
            self.assertNotIn(
                tid,
                self.rv2.baseline[self.rv2.partitions[0].name]
                .structurally_infeasible)

    def test_segment_yi_adds_travel_cost_not_unfinished_trips(self):
        # 工段乙不新增未完成户，只增加替代行走与换场，且不触底线
        ev = self.rv4.evaluations[("工段乙",)]
        self.assertTrue(ev.verdict.confirmable)
        for part in self.rv4.partitions:
            self.assertEqual(ev.incremental[part.name], [])
            self.assertGreater(
                ev.results[part.name].walking,
                self.rv4.baseline[part.name].walking)


if __name__ == "__main__":
    unittest.main()
