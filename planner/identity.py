"""疑似同一行程的身份分区。

规则（来自业务约束）：
- 只有 possible_same_as、观测 group_link 或补点 followup 涉及的记录对才可能合并；
  没有确证绝不凭同一天/同姓等特征去重。
- 补点 followup 到达前，每条未决链同时保留"两户/一户"两种解释；
  到达后按结论固定："同一行程"必须合并，"不同家庭"必须拆开。
- 合并后保留权威记录：已登记优先于问卷；同为问卷时保留经过观测更早者；
  另一记录视为同户重复经过，不再单独计需求。
- 同一供给下的所有比较使用同一个完整分区，禁止按场所挑有利混合。
"""

from dataclasses import dataclass

from .data import Dataset


@dataclass(frozen=True)
class Partition:
    name: str
    # 保留记录 -> 被吸收记录（空元组表示无合并）
    survivors: dict
    dropped: frozenset          # 被吸收的记录全集
    merges: tuple               # 本分区实际生效的 (保留, 吸收) 对

    def demand_ids(self, trip_ids):
        return [tid for tid in trip_ids if tid not in self.dropped]

    def household_of(self, trip_id):
        return self.survivors.get(trip_id, trip_id)


def unresolved_links(ds: Dataset, cutoff: int) -> list:
    """返回截止某信息时点仍无确证的疑似链。"""
    decided = set()
    for note in ds.followup:
        if note["received_order"] <= cutoff:
            decided.add(frozenset((note["left"], note["right"])))
    links = []
    for trip in ds.raw["trips"]:
        other = trip["possible_same_as"]
        if other is None:
            continue
        pair = frozenset((trip["id"], other))
        if pair not in decided:
            links.append((trip["id"], other))
    return sorted(set(links))


def followup_decisions(ds: Dataset, cutoff: int) -> dict:
    """cutoff 内已确证的链 -> True 同一行程 / False 不同家庭。"""
    out = {}
    for note in ds.followup:
        if note["received_order"] <= cutoff:
            out[frozenset((note["left"], note["right"]))] = (
                note["observation"] == "同一行程")
    return out


def _authoritative_keeper(ds: Dataset, left: str, right: str) -> str:
    tl, tr = ds.trips[left], ds.trips[right]
    if tl["group"] != tr["group"]:
        return left if tl["group"] == "已登记" else right
    # 同为问卷：有门口观测的优先；都有则取观测时刻更早者
    obs = {o["trip"]: o["at"] for o in ds.observations}
    if left in obs and right not in obs:
        return left
    if right in obs and left not in obs:
        return right
    if left in obs and right in obs:
        return left if obs[left] <= obs[right] else right
    return min(left, right)


def build_partitions(ds: Dataset, cutoff: int) -> tuple[Partition, Partition]:
    """返回 (保守拆分区, 合并区) 两个极端解释，涵盖全部未决不确定性。"""
    decisions = followup_decisions(ds, cutoff)
    pending = unresolved_links(ds, cutoff)

    # 已确证的合并在两个分区中都必须生效
    forced_merges = []
    for pair, is_same in decisions.items():
        if is_same:
            left, right = sorted(pair)
            keeper = _authoritative_keeper(ds, left, right)
            other = right if keeper == left else left
            forced_merges.append((keeper, other))

    def make(name, merge_pending: bool):
        merges = list(forced_merges)
        if merge_pending:
            for left, right in pending:
                keeper = _authoritative_keeper(ds, left, right)
                other = right if keeper == left else left
                merges.append((keeper, other))
        survivors = {}
        dropped = set()
        for keeper, other in merges:
            survivors[other] = keeper
            dropped.add(other)
        return Partition(name, survivors, frozenset(dropped), tuple(sorted(merges)))

    return (make("拆户解释（两次经过按两户）", False),
            make("并户解释（确证/未决链按同一户）", True))


def merge_status(ds: Dataset, cutoff: int) -> list:
    """供报告使用的逐条链状态。"""
    decisions = followup_decisions(ds, cutoff)
    rows = []
    seen = set()
    for trip in ds.raw["trips"]:
        other = trip["possible_same_as"]
        if other is None:
            continue
        pair = frozenset((trip["id"], other))
        if pair in seen:
            continue
        seen.add(pair)
        if pair in decisions:
            status = "已确证同一行程" if decisions[pair] else "已确证不同家庭"
        else:
            status = "未决（保留区间）"
        rows.append({"pair": sorted(pair), "status": status})
    return rows
