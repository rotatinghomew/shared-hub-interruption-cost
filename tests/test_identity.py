"""身份分区与信息时点规则测试。"""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.data import Dataset
from planner.identity import (
    build_partitions, unresolved_links, followup_decisions,
)


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = Dataset.build()

    def test_initial_cutoff_keeps_all_five_links_unresolved(self):
        links = unresolved_links(self.ds, self.ds.policy["initial_cutoff"])
        self.assertEqual(len(links), 5)
        split, merged = build_partitions(self.ds, 2)
        self.assertEqual(len(merged.merges), 5)
        self.assertEqual(split.merges, ())
        self.assertEqual(len(merged.dropped), 5)

    def test_revised_cutoff_fixes_followup_conclusions(self):
        split, merged = build_partitions(self.ds, 4)
        # 访001=访000 已确证同一行程：两个分区都必须合并
        self.assertIn(("访000", "访001"), split.merges)
        self.assertIn(("访000", "访001"), merged.merges)
        # 访044 vs 访045 已确证不同家庭：任何分区都不得合并
        self.assertNotIn(("访045", "访044"), split.merges)
        self.assertNotIn(("访044", "访045"), split.merges)
        keeper_pairs = {m for m in merged.merges}
        self.assertFalse(any("访044" in m and "访045" in m for m in keeper_pairs))
        # 仍有 3 条未决链
        self.assertEqual(len(unresolved_links(self.ds, 4)), 3)

    def test_keeper_prefers_registered_authority(self):
        # 访000 已登记、访001 问卷：合并时保留访000
        _, merged = build_partitions(self.ds, 2)
        self.assertEqual(merged.survivors["访001"], "访000")

    def test_no_dedup_beyond_evidence(self):
        # 没有 possible_same_as / group_link / followup 证据的记录永不合并
        split, merged = build_partitions(self.ds, 2)
        evidenced = {"访000", "访001", "访011", "访012", "访022", "访023",
                     "访033", "访034", "访044", "访045"}
        for tid in self.ds.trips:
            if tid not in evidenced:
                self.assertNotIn(tid, merged.dropped)
                self.assertNotIn(tid, split.dropped)

    def test_partition_demand_sets_are_nested_and_complete(self):
        split, merged = build_partitions(self.ds, 2)
        all_ids = set(self.ds.trips)
        self.assertEqual(set(split.demand_ids(list(all_ids))), all_ids)
        self.assertTrue(
            set(merged.demand_ids(list(all_ids))) < all_ids)


if __name__ == "__main__":
    unittest.main()
