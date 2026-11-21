"""维护合同条件、信息时点与窗口确认规则。

- 停用窗口必须落在候选区间内且满足 min_duration 的连续作业条件；
  网格按 5 分钟步进枚举，缩短/变更后的停运也必须能在同一引擎下比较。
- 事件按 received_order 与 initial_cutoff / revised_cutoff 划分信息时点：
  已接受换约与已取消保养在相应时点生效，且换约不可回退。
- 窗口确认需同时守住受保护预约（protected_max_unfinished=0）与维护
  连续性；两个身份解释下排序不稳健的组合保持待议。
- 服务台输出三分类：原预约无需改 / 仅替换建议 / 确实无法安排。
"""

from itertools import combinations
from dataclasses import dataclass

from .data import Dataset
from .identity import Partition
from .scheduler import ScheduleResult, REASON_LOCK

STEP = 5


@dataclass(frozen=True)
class GridWindow:
    candidate: str
    start: int
    end: int

    @property
    def key(self):
        return (self.candidate, self.start, self.end)


def candidate_grid(ds: Dataset, candidate_id: str,
                   overrides: dict | None = None) -> list[GridWindow]:
    """枚举满足连续作业时长的全部窗口；override 可模拟缩短/变更。"""
    cand = ds.candidates[candidate_id]
    span0 = overrides.get("start", cand["start"]) if overrides else cand["start"]
    span1 = overrides.get("end", cand["end"]) if overrides else cand["end"]
    min_dur = overrides.get("min_duration", cand["min_duration"]) if overrides else cand["min_duration"]
    out = []
    for start in range(span0, span1 - min_dur + 1, STEP):
        for end in range(max(start + min_dur, start + STEP), span1 + 1, STEP):
            if end - start >= min_dur:
                out.append(GridWindow(candidate_id, start, end))
    return out


def active_candidates(ds: Dataset, cutoff: int) -> set:
    """cutoff 时点仍有效的候选工段（被取消的退出）。"""
    cancelled = {
        d["candidate"] for d in ds.decisions
        if d["kind"] == "取消保养" and d["received_order"] <= cutoff
    }
    return set(ds.candidates) - cancelled


def all_combos(candidate_ids) -> list[tuple]:
    items = tuple(sorted(candidate_ids))
    combos = [()]
    for r in range(1, len(items) + 1):
        combos.extend(combinations(items, r))
    return combos


@dataclass
class WindowVerdict:
    combo: tuple
    confirmable: bool
    reasons: tuple
    desk_categories: dict     # trip -> 无需改/替换建议/无法安排
    protected_gap: int
    lock_status: dict


def desk_triage(ds: Dataset, result: ScheduleResult) -> dict:
    """服务台三分类。已接受换约保持换后场地，不能退回原预约。"""
    out = {}
    for (tid, act), lock in ds.locks.items():
        option = result.placed.get(tid)
        if option is None:
            out[tid] = "确实无法安排"
            continue
        placement = next(p for p in option.placements if p.activity == act)
        same_room = placement.room == lock["room"]
        same_start = placement.start == lock["window"][0]
        if lock["source"] == "已接受换约":
            # 换约已被居民接受：保持即"无需改"，不允许退回原位
            out[tid] = "原预约无需改" if (same_room and same_start) else "只有替换建议"
        elif same_room and same_start:
            out[tid] = "原预约无需改"
        else:
            out[tid] = "只有替换建议"
    for tid in result.unfinished:
        if tid in {t for (t, _a) in ds.locks}:
            out[tid] = "确实无法安排"
    return out


def evaluate_combo(ds: Dataset, combo: tuple, results: dict,
                   partitions: tuple[Partition, ...],
                   protected_limit: int,
                   baseline: dict | None = None) -> WindowVerdict:
    """对一个停用组合做确认审查；results 为 {分区名: ScheduleResult}。

    基线（无停用）下已存在的结构性/陪同性缺口是既有问题，不计入停用
    账上；只有停用新增的受保护缺口才违反 protected_max_unfinished。
    """
    reasons = []
    protected_gap = 0
    lock_status = {}
    triage = {}

    for part in partitions:
        result = results[part.name]
        base = baseline[part.name] if baseline else None
        base_protected = (set(base.unfinished) if base else set())
        base_protected = {t for t in base_protected if ds.trips[t]["protected"]}
        new_protected = {t for t in result.unfinished
                         if ds.trips[t]["protected"]} - base_protected
        protected_gap = max(protected_gap, len(new_protected))
        if len(new_protected) > protected_limit:
            reasons.append(
                f"{part.name}下停用新增受保护未完成 {len(new_protected)} 户"
                f"（{sorted(new_protected)}，底线 {protected_limit}）")
        if result.locked_unplaceable:
            reasons.append(
                f"{part.name}下固定/换约义务无法安排：{sorted(result.locked_unplaceable)}")
        for tid in result.locked_unplaceable:
            lock_status[(part.name, tid)] = "义务冲突"
        triage.update(desk_triage(ds, result))

    confirmable = not reasons
    return WindowVerdict(
        combo=combo,
        confirmable=confirmable,
        reasons=tuple(reasons),
        desk_categories=triage,
        protected_gap=protected_gap,
        lock_status=lock_status,
    )
