"""分队列的需求与损失估计。

队列口径（review_policy.cohorts）：
1. 已登记：全量记录，逐条确定性判断，不做外推。
2. 抽样估计：问卷行程按抽样框分层。框有抽样概率时用 Horvitz–Thompson
   外推（1/p），同时以实际观测条数作保守下界，形成区间；概率为空的
   框不产生点估计。
3. 未覆盖：只有门口经过次数、没有抽样概率也没有行程名单（午后框）。
   经过次数不宣称需求为零；按每户 1~2 次经过给出户数边界，且补点
   观察只校验实际覆盖人群，不会自动填平该时段。

禁止给每个场所选最有利的混合：区间端点必须在同一分区、同一供给
假设下整体计算。
"""

from dataclasses import dataclass

from .data import Dataset
from .scheduler import ScheduleResult


@dataclass(frozen=True)
class Interval:
    low: float
    high: float
    point: float | None = None
    note: str = ""

    def __post_init__(self):
        if self.point is not None and not (self.low <= self.point <= self.high + 1e-9):
            raise ValueError(f"点估计 {self.point} 超出区间 [{self.low}, {self.high}]")


@dataclass
class StratumEstimate:
    frame_id: str
    observed_trips: int
    sampling_probability: float | None
    demand: Interval
    unfinished: Interval          # 同一供给下的整次未完成户数
    loss_rate: Interval


def questionnaire_strata(ds: Dataset):
    """问卷行程按抽样框分层（午后框无名单，单独作为未覆盖处理）。"""
    strata = {fid: [] for fid in ds.frames}
    for tid, trip in ds.trips.items():
        if trip["group"] != "问卷抽样":
            continue
        fid = ds.trip_frame.get(tid)
        strata[fid].append(tid)
    return strata


def _stratum_estimate(frame_id: str, trip_ids: list, frame: dict,
                      unfinished_ids: frozenset) -> StratumEstimate:
    n = len(trip_ids)
    p = frame["probability"]
    unfinished_n = sum(1 for tid in trip_ids if tid in unfinished_ids)
    observed_rate = unfinished_n / n if n else 0.0
    if p is not None:
        # 有抽样概率：HT 外推户数；层内比率用观测样本比率作点估计，
        # 不完整框给保守地板（已确证未完成户数 / 外推总户数）。
        demand = Interval(n, n / p, point=n / p,
                          note=f"观测{n}户，p={p}，HT外推 {n/p:g}")
        unfinished = Interval(
            unfinished_n, unfinished_n / p, point=unfinished_n / p,
            note=f"观测未完成{unfinished_n}户，HT外推 {unfinished_n/p:g}")
        floor = unfinished_n / (n / p) if n else 0.0   # = p * 观测比率
        rate = Interval(floor, observed_rate, point=observed_rate,
                        note=f"点估计=样本比率{observed_rate:.3f}；地板假设未观测部分零损失")
    else:
        demand = Interval(n, n, point=None, note="无抽样概率，仅计已登记到本层的记录")
        unfinished = Interval(unfinished_n, unfinished_n, point=None)
        rate = Interval(observed_rate, observed_rate, point=None)
    return StratumEstimate(frame_id, n, p, demand, unfinished, rate)


def covered_estimates(ds: Dataset, result: ScheduleResult, partition) -> dict:
    """已登记 + 抽样估计两个有覆盖队列的分层估计。"""
    unfinished = frozenset(result.unfinished)
    registered = [tid for tid in partition.demand_ids(list(ds.trips))
                  if ds.trips[tid]["group"] == "已登记"]
    reg_unfinished = sum(1 for tid in registered if tid in unfinished)
    out = {
        "已登记": StratumEstimate(
            "全量", len(registered), None,
            Interval(len(registered), len(registered), point=len(registered)),
            Interval(reg_unfinished, reg_unfinished, point=reg_unfinished),
            Interval(reg_unfinished / len(registered) if registered else 0,
                     reg_unfinished / len(registered) if registered else 0,
                     point=reg_unfinished / len(registered) if registered else 0)),
    }
    strata = questionnaire_strata(ds)
    for fid, ids in strata.items():
        ids = partition.demand_ids(ids)
        if not ids and ds.frames[fid]["probability"] is None:
            continue  # 未覆盖框在 uncovered_bounds 单独处理
        out[fid] = _stratum_estimate(fid, ids, ds.frames[fid], unfinished)
    return out


def uncovered_bounds(ds: Dataset) -> dict:
    """无抽样覆盖框（午后）：仅给经过次数支撑的户数边界，不产生需求点估计。"""
    out = {}
    for fid, frame in ds.frames.items():
        if frame["probability"] is not None or frame["trip_ids"]:
            continue
        passes = frame["observed_passes"]
        low = -(-passes // max(ds.policy["allowed_households_per_passes"]))
        high = passes // min(ds.policy["allowed_households_per_passes"])
        out[fid] = {
            "window": frame["window"],
            "observed_passes": passes,
            "households_per_pass": tuple(ds.policy["allowed_households_per_passes"]),
            "household_bounds": Interval(low, high, point=None,
                                         note="仅有经过次数，需求与损失均不可点估计"),
        }
    return out


def aggregate_covered(estimates: dict) -> dict:
    """把已登记与可外推抽样层合成可估计总体的损失率区间。

    点估计：各层外推户数加权的样本比率（HT 自加权口径）。
    区间下界：未观测部分按零损失的保守地板；上界：样本观测比率
    （不把未观测损失凭空放大）。未覆盖框不进入本汇总。
    """
    low_d = high_d = low_u = high_u = 0.0
    point_d = point_u = floor_num = 0.0
    has_point = True
    for est in estimates.values():
        low_d += est.demand.low
        high_d += est.demand.high
        low_u += est.unfinished.low
        high_u += est.unfinished.high
        if est.demand.point is None:
            has_point = False
        else:
            point_d += est.demand.point
            point_u += est.unfinished.point
            floor_num += est.unfinished.low          # 已确证未完成户数
    point = (point_u / point_d) if has_point and point_d else None
    rate_floor = (floor_num / point_d) if point_d else 0.0
    rate_high = point if point is not None else rate_floor
    return {
        "demand": Interval(low_d, high_d, point=point_d if has_point else None),
        "unfinished": Interval(low_u, high_u, point=point_u if has_point else None),
        "loss_rate": Interval(rate_floor, rate_high, point=point),
    }
