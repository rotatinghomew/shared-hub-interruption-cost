"""把排程、身份分区、队列估计与确认规则串成一次完整评审。

产出（每个信息时点、每个停用组合、每个身份解释各一份同一供给下的结果）：
- 整次出行未完成的数量与原因（按队列拆开，未覆盖单列）
- 替代出行代价（总行走分钟、换场数、服务台三分类）
- 底线人群（受保护）服务缺口
- 组合排名及其对身份解释与抽样区间的敏感性
- 最可能改变排序的时段/行程组与针对性补点建议
"""

from dataclasses import dataclass, field

from .data import Dataset
from .identity import build_partitions, Partition
from .scheduler import schedule
from .estimate import (covered_estimates, uncovered_bounds, aggregate_covered)
from .policy import active_candidates, all_combos, evaluate_combo


@dataclass
class ComboEvaluation:
    combo: tuple
    partitions: tuple
    results: dict                       # partition name -> ScheduleResult
    estimates: dict                     # partition name -> 分层估计
    aggregate: dict                     # partition name -> 可估计总体汇总
    uncovered: dict
    verdict: object
    incremental: dict                   # partition name -> 相对基线的新增未完成
    reasons: dict                       # partition name -> Counter 原因


@dataclass
class Review:
    cutoff: int
    partitions: tuple
    active: set
    combos: list
    baseline: dict                      # partition -> ScheduleResult
    evaluations: dict                   # combo -> ComboEvaluation
    uncovered: dict
    rankings: dict                      # partition -> [combo...]
    swaps: list                         # 解释间点估计排序互换的组合对
    indistinguishable: list             # 损失率区间跨解释重叠、无法稳健区分的组合对
    sensitive: list                     # 两类信号的并集
    key_groups: list                    # 最可能改排序的行程组/时段
    followup_targets: list              # 针对性补点建议


def _run(ds: Dataset, demand_ids, combo):
    return schedule(ds, demand_ids, combo)


def evaluate(ds: Dataset, cutoff: int,
             combos: list[tuple] | None = None) -> Review:
    partitions = build_partitions(ds, cutoff)
    active = active_candidates(ds, cutoff)
    if combos is None:
        combos = all_combos(active)

    # 基线：不做任何停用时仍存在的结构性/容量性损失（不能记在保养账上）
    baseline = {}
    for part in partitions:
        ids = part.demand_ids(list(ds.trips))
        baseline[part.name] = _run(ds, ids, ())

    uncovered = uncovered_bounds(ds)
    evaluations = {}
    for combo in combos:
        results, estimates, aggregate, incremental, reasons = {}, {}, {}, {}, {}
        for part in partitions:
            ids = part.demand_ids(list(ds.trips))
            # 锚定基线放置，保证停用损失相对基线单调、不出现重排"救活"
            result = _run(ds, ids, combo) if not combo else schedule(
                ds, ids, combo,
                anchors=dict(baseline[part.name].placed))
            results[part.name] = result
            estimates[part.name] = covered_estimates(ds, result, part)
            aggregate[part.name] = aggregate_covered(estimates[part.name])
            base_unfinished = set(baseline[part.name].unfinished)
            incremental[part.name] = sorted(set(result.unfinished) - base_unfinished)
            from collections import Counter
            reasons[part.name] = Counter(result.unfinished.values())
        verdict = evaluate_combo(
            ds, combo, results, partitions,
            ds.policy["protected_max_unfinished"], baseline=baseline)
        evaluations[combo] = ComboEvaluation(
            combo, partitions, results, estimates, aggregate,
            uncovered, verdict, incremental, reasons)

    rankings, swaps, indistinguishable, key_groups = _rankings(
        ds, partitions, evaluations)
    sensitive = swaps + [pair for pair in indistinguishable
                         if pair not in swaps]
    followup_targets = _followup_targets(ds, partitions, evaluations,
                                         rankings, key_groups, cutoff)
    return Review(cutoff, partitions, active, combos, baseline,
                  evaluations, uncovered, rankings, swaps,
                  indistinguishable, sensitive, key_groups, followup_targets)


def _rank_key(ds, agg, result):
    # 政策：优先完整出行损失率（点估计；无点估计时退回到区间上界），
    # 同率偏好替代出行时长（行走分钟），再看换场数
    rate = agg["loss_rate"].point
    if rate is None:
        rate = agg["loss_rate"].high
    return (rate, result.walking, result.substitutions)


def _rankings(ds, partitions, evaluations):
    rankings = {}
    for part in partitions:
        ordered = sorted(
            evaluations,
            key=lambda combo: _rank_key(
                ds, evaluations[combo].aggregate[part.name],
                evaluations[combo].results[part.name]))
        rankings[part.name] = ordered

    a, b = rankings[partitions[0].name], rankings[partitions[1].name]
    pos_b = {c: i for i, c in enumerate(b)}
    swaps, indistinguishable = [], []
    # 只审查拆户口径下相邻的组合对：非相邻对之间隔有可区分的中间档，
    # 不构成"优先方案可能互换"的决策风险，避免宽区间制造大量噪声。
    for i in range(len(a) - 1):
        x, y = a[i], a[i + 1]
        rx = _rate_band(evaluations, x, partitions)
        ry = _rate_band(evaluations, y, partitions)
        overlaps = rx[0] <= ry[1] and ry[0] <= rx[1]
        if pos_b[x] > pos_b[y] and overlaps:
            swaps.append((x, y))
        elif overlaps:
            indistinguishable.append((x, y))
    key_groups = _key_groups(ds, partitions, evaluations,
                             swaps + indistinguishable)
    return rankings, swaps, indistinguishable, key_groups


def _rate_band(evaluations, combo, partitions):
    lows = [evaluations[combo].aggregate[p.name]["loss_rate"].low for p in partitions]
    highs = [evaluations[combo].aggregate[p.name]["loss_rate"].high for p in partitions]
    return min(lows), max(highs)


def _key_groups(ds, partitions, evaluations, sensitive):
    """定位最可能改变排序的时段与行程组。

    对敏感组合对，找出在两个身份解释下去留不同（即受未决合并链影响）、
    且落在停用时段内的行程；再按抽样框时段汇总。
    """
    frame_of = ds.trip_frame
    groups = []
    merged_trips = set()
    for part in partitions:
        merged_trips |= part.dropped
    for x, y in sensitive:
        ex, ey = evaluations[x], evaluations[y]
        touched = set()
        for part in partitions:
            touched |= set(ex.incremental[part.name])
            touched |= set(ey.incremental[part.name])
        pivotal = sorted(t for t in touched if t in merged_trips)
        by_frame = {}
        for t in pivotal:
            by_frame.setdefault(frame_of.get(t, "未覆盖"), []).append(t)
        groups.append({"pair": (x, y), "pivotal_trips": pivotal,
                       "by_frame": by_frame})
    return groups


def _followup_targets(ds, partitions, evaluations, rankings, key_groups, cutoff):
    """补点只针对实际能覆盖的框；午后框无概率，不能用补点填平。"""
    targets = []
    coverable = {fid for fid, f in ds.frames.items() if f["probability"] is not None}
    for grp in key_groups:
        for fid, tids in grp["by_frame"].items():
            if fid in coverable:
                targets.append({
                    "frame": fid,
                    "window": ds.frames[fid]["window"],
                    "trips": tids,
                    "question": "核对这些经过是否同一户的重复经过（每次经过对应几户）",
                    "scope_note": "补点仅校验该框实际覆盖人群，不外推到午后未覆盖框",
                })
    # 未决身份链本身也是补点对象（若其所在框可覆盖且尚未确证）
    from .identity import unresolved_links
    for left, right in unresolved_links(ds, cutoff):
        fid = ds.trip_frame.get(left)
        if fid in coverable:
            targets.append({
                "frame": fid, "window": ds.frames[fid]["window"],
                "trips": [left, right],
                "question": "确认两条记录是否同一行程",
                "scope_note": "补点仅校验该框实际覆盖人群，不外推到午后未覆盖框",
            })
    # 按框合并去重：同一框的所有待核行程汇总成一条补点任务
    merged = {}
    for t in targets:
        key = (t["frame"], t["question"])
        if key not in merged:
            merged[key] = dict(t, trips=set(t["trips"]))
        else:
            merged[key]["trips"].update(t["trips"])
    uniq = []
    for (fid, question), row in sorted(merged.items()):
        row["trips"] = sorted(row["trips"])
        uniq.append(row)
    return uniq
