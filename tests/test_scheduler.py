"""排程器关键规则测试：整链判定、锁定、陪同、容量、停用语义。"""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.data import Dataset
from planner.scheduler import (
    schedule, enumerate_options, exact_feasible, REASON_LOCK,
)


class SchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = Dataset.build()
        cls.ids = list(cls.ds.trips)

    def test_baseline_structural_failures_are_independent_of_maintenance(self):
        # 这 4 户的出行窗口比自身链所需时长还短，无停用时也无法完成
        for tid in ("访006", "访012", "访013", "访048"):
            with self.subTest(tid=tid):
                self.assertEqual(enumerate_options(self.ds, tid, frozenset()), [])
        result = schedule(self.ds, self.ids, ())
        for tid in ("访006", "访012", "访013", "访048"):
            self.assertIn(tid, result.unfinished)

    def test_completion_is_all_or_nothing(self):
        # 不存在"完成一部分"的结果：每条行程要么在 placed，要么在 unfinished
        result = schedule(self.ds, self.ids, ("工段甲",))
        self.assertEqual(
            set(result.placed) | set(result.unfinished), set(self.ids))
        for option in result.placed.values():
            self.assertEqual(len(option.placements),
                             len(self.ds.trips[option.trip_id]["chain"]))

    def test_locked_booking_cannot_be_moved_or_double_promised(self):
        # 访000 的吃饭锁定南厅 575；工段丙整日关闭南厅 -> 锁定冲突
        result = schedule(self.ds, self.ids, ("工段丙",))
        self.assertIn("访000", result.unfinished)
        self.assertEqual(result.unfinished["访000"], REASON_LOCK)
        # 无停用时锁定事项必须落在原场地原时刻
        base = schedule(self.ds, self.ids, ())
        kept = {(row["trip"], row["new_room"], row["new_start"])
                for row in base.locked_kept}
        self.assertIn(("访000", "南厅", 575), kept)
        self.assertIn(("访010", "南厅", 695), kept)

    def test_accepted_change_is_never_reverted(self):
        # 已接受换约（访010 -> 南厅 695）必须保持，不能退回 660 的原预约
        base = schedule(self.ds, self.ids, ())
        option = base.placed["访010"]
        placement = next(p for p in option.placements if p.activity == "吃饭")
        self.assertEqual((placement.room, placement.start), ("南厅", 695))

    def test_escort_capacity_binds_protected_trips(self):
        # 680 前仅 1 个陪同工位：受保护行程在基线上就有冲突，且与精确回溯一致
        protected = [t for t in self.ids if self.ds.trips[t]["protected"]]
        greedy = schedule(self.ds, protected, ())
        exact, budget_hit = exact_feasible(self.ds, protected, ())
        self.assertEqual(len(greedy.placed), exact)
        self.assertGreaterEqual(greedy.unfinished_count, 1)
        # 逐分钟核对陪同容量（含跨 680 边界、按半开区间分段计数）
        cap = {}
        for block in self.ds.assistance:
            for minute in range(*block["window"]):
                cap[minute] = block["slots"]
        used = {}
        for option in greedy.placed.values():
            for minute in range(*option.span):
                used[minute] = used.get(minute, 0) + 1
        for minute, count in used.items():
            self.assertLessEqual(count, cap[minute])

    def test_greedy_matches_exact_on_small_sets(self):
        subset = ["访001", "访004", "访009", "访018", "访027", "访036"]
        for shutdown in [(), ("工段乙",)]:
            greedy = schedule(self.ds, subset, shutdown)
            exact, _ = exact_feasible(self.ds, subset, shutdown,
                                      node_budget=100_000)
            self.assertEqual(len(greedy.placed), exact, shutdown)

    def test_full_day_shutdown_equals_fully_covering_custom_window(self):
        custom = tuple((room, 0, 1440)
                       for room in self.ds.candidates["工段甲"]["affects"])
        via_id = schedule(self.ds, self.ids, ("工段甲",))
        via_window = schedule(self.ds, self.ids, (), custom_windows=custom)
        self.assertEqual(set(via_id.unfinished), set(via_window.unfinished))

    def test_partial_window_keeps_room_available_afterward(self):
        # 只在 560-620 关闭北厅/亲子室，620 之后应能恢复使用
        custom = tuple((room, 560, 620)
                       for room in self.ds.candidates["工段甲"]["affects"])
        partial = schedule(self.ds, self.ids, (), custom_windows=custom)
        full = schedule(self.ds, self.ids, ("工段甲",))
        self.assertLess(partial.unfinished_count, full.unfinished_count)
        later = [o for o in partial.placed.values()
                 if any(p.room in ("北厅", "亲子室") and p.start >= 620
                        for p in o.placements)]
        self.assertTrue(later)

    def test_combined_windows_are_not_additive(self):
        # 组合效应必须整体评估：甲+乙的新增损失严格大于各自新增之和的口径
        base = set(schedule(self.ds, self.ids, ()).unfinished)
        a = set(schedule(self.ds, self.ids, ("工段甲",)).unfinished) - base
        b = set(schedule(self.ds, self.ids, ("工段乙",)).unfinished) - base
        ab = set(schedule(self.ds, self.ids,
                          ("工段甲", "工段乙")).unfinished) - base
        self.assertGreater(len(ab), len(a | b))


if __name__ == "__main__":
    unittest.main()
