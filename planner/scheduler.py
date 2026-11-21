"""整次出行链的可行性排程。

关键立场：一次出行要么整链完成，要么未完成，不存在"完成三分之二"。
判定同时受以下约束：
- 场地开放半开区间与座位容量（按分钟占用）
- 事项先后顺序（ordered）与场地间行走耗时（transfer）
- 固定预约/已接受换约的锁定场地与起始时刻
- 受保护行程整段占用陪同工位（shared_assistance）
- 停用窗口内相关场地完全不可用，替代场地在同一供给下竞争，不能一女二嫁
"""

from dataclasses import dataclass, field
from itertools import permutations

from .data import Dataset

# 排程终止原因代码（评审按这些代码汇总"为什么没完成"）
REASON_LOCK = "停用直撞固定预约或已接受换约"
REASON_OPEN = "开放时段内无法容纳整链"
REASON_ORDER = "顺序与行走约束无解"
REASON_SEAT = "场地座位容量不足"
REASON_ESCORT = "陪同工位不足"


@dataclass(frozen=True)
class Placement:
    activity: str
    room: str
    start: int
    end: int
    preferred_room: str
    locked: bool

    @property
    def substituted(self) -> bool:
        return self.room != self.preferred_room


@dataclass(frozen=True)
class TripOption:
    trip_id: str
    order: tuple  # 事项的执行顺序
    placements: tuple[Placement, ...]
    span: tuple[int, int]
    walking: int          # 方案总行走分钟
    substitutions: int    # 偏离首选/锁定场地的事项数

    @property
    def cost(self) -> tuple:
        # 同率时偏好预计替代出行时长较短者：行走分钟为主，其次换场数
        return (self.walking, self.substitutions)


@dataclass
class ScheduleResult:
    demand_id: str
    shutdowns: tuple
    placed: dict = field(default_factory=dict)        # trip_id -> TripOption
    unfinished: dict = field(default_factory=dict)    # trip_id -> reason
    walking: int = 0
    substitutions: int = 0
    locked_kept: list = field(default_factory=list)
    locked_substituted: list = field(default_factory=list)
    locked_unplaceable: list = field(default_factory=list)

    # 无任何停用/容量约束下就不存在可行整链方案的行程（结构性失败）。
    # 这类失败与保养无关，且在任何停用下都必须保持失败（可行性集合只缩不增）。
    structurally_infeasible: frozenset = frozenset()

    @property
    def unfinished_count(self) -> int:
        return len(self.unfinished)

    @property
    def protected_unfinished(self) -> int:
        return sum(1 for tid in self.unfinished if self._protected.get(tid))

    @property
    def _protected(self):
        return getattr(self, "_protected_map", {})


def _preferred_rooms(ds: Dataset) -> dict:
    """每个事项的首选场地：锁定场地优先，否则取资料中的第一个场地。"""
    preferred = {act: rooms[0] for act, rooms in ds.room_activities.items()}
    return preferred


def enumerate_options(ds: Dataset, trip_id: str, shutdown_rooms: frozenset,
                      shutdown_windows: tuple = ()) -> list[TripOption]:
    """枚举一次出行在给定停用集合下的全部可行整链方案（不含容量/陪同约束）。

    shutdown_windows：自定义 (room, start, end) 半开区间，用于评估缩短/
    变更后的停运；候选工段给的是整段关闭，网格变体只关其中一部分。
    """
    cache = _option_cache.setdefault(id(ds), {})
    key = (trip_id, shutdown_rooms, shutdown_windows)
    if key in cache:
        return cache[key]
    options = _enumerate_options_uncached(ds, trip_id, shutdown_rooms,
                                          shutdown_windows)
    cache[key] = options
    return options


_option_cache: dict = {}


def _enumerate_options_uncached(ds: Dataset, trip_id: str,
                                shutdown_rooms: frozenset,
                                shutdown_windows: tuple = ()) -> list[TripOption]:
    trip = ds.trips[trip_id]
    chain = trip["chain"]
    w0, w1 = trip["window"]
    preferred = _preferred_rooms(ds)
    for (_tid, act), lock in ds.locks.items():
        if _tid == trip_id:
            preferred[act] = lock["room"]

    orders = (chain,) if trip["ordered"] else tuple(permutations(chain))
    options = []
    for order in orders:
        _build(order, 0, w0, w1, [], None, 0, 0, ds, trip_id, shutdown_rooms,
               preferred, options, shutdown_windows)
    # 去重并按代价排序
    unique = {(_opt_key(o)): o for o in options}
    return sorted(unique.values(), key=lambda o: (o.cost, o.span))


def _opt_key(o: TripOption):
    return tuple((p.activity, p.room, p.start, p.end) for p in o.placements)


def _build(order, idx, earliest, w1, placements, prev_room, walking, subs,
           ds, trip_id, shutdown_rooms, preferred, out, shutdown_windows=()):
    if idx == len(order):
        span = (placements[0].start, placements[-1].end)
        out.append(TripOption(trip_id, order, tuple(placements), span, walking, subs))
        return

    activity = order[idx]
    dur = ds.duration[activity]
    lock = ds.locks.get((trip_id, activity))
    candidates = [lock["room"]] if lock else ds.room_activities[activity]

    for room in candidates:
        if room in shutdown_rooms:
            continue
        open0, open1 = ds.rooms[room]["open"]
        arrive = earliest if prev_room is None else earliest + ds.walk_time(prev_room, room)
        # 起始时刻下界：到达时刻（已不早于出行窗口起点）、场地开放、（锁定时）预约起点
        lo = max(arrive, open0)
        if lock:
            lo = max(lo, lock["window"][0])
        hi = w1 - dur  # 结束不得晚于出行窗口与场地关闭
        hi = min(hi, open1 - dur)
        if lock:
            # 锁定事项只能在预约起点执行（资料中预约窗长度恰等于事项时长）
            if not (lo <= lock["window"][0] <= hi):
                continue
            starts = [lock["window"][0]]
        else:
            if lo > hi:
                continue
            starts = range(lo, hi + 1)
        room_windows = tuple((s, e) for (r, s, e) in shutdown_windows if r == room)
        for start in starts:
            end = start + dur
            if any(start < e and end > s for (s, e) in room_windows):
                continue  # 与自定义停用窗重叠（半开区间）
            placement = Placement(
                activity=activity, room=room, start=start, end=end,
                preferred_room=preferred[activity],
                locked=lock is not None,
            )
            _build(
                order, idx + 1, end, w1,
                placements + [placement], room,
                walking + (0 if prev_room is None or prev_room == room
                           else ds.walk_time(prev_room, room)),
                subs + (1 if placement.substituted else 0),
                ds, trip_id, shutdown_rooms, preferred, out, shutdown_windows,
            )


class _Resources:
    """按分钟维护场地座位与陪同工位占用。"""

    def __init__(self, ds: Dataset):
        horizon = range(540, 851)
        self.horizon = (540, 850)
        self.room_seats = {rid: room["seats"] for rid, room in ds.rooms.items()}
        self.room_used = {rid: [0] * 311 for rid in ds.rooms}
        self.escort_used = [0] * 311
        self.escort_cap = [0] * 311
        for block in ds.assistance:
            for t in range(block["window"][0], block["window"][1]):
                self.escort_cap[t - 540] = block["slots"]

    def _idx(self, t):
        return t - 540

    def can_place(self, option: TripOption, protected: bool) -> bool:
        for p in option.placements:
            i0, i1 = self._idx(p.start), self._idx(p.end)
            seats = self.room_seats[p.room]
            if any(self.room_used[p.room][i] + 1 > seats for i in range(i0, i1)):
                return False
        if protected:
            i0, i1 = self._idx(option.span[0]), self._idx(option.span[1])
            if any(self.escort_used[i] + 1 > self.escort_cap[i] for i in range(i0, i1)):
                return False
        return True

    def add(self, option: TripOption, protected: bool):
        for p in option.placements:
            for i in range(self._idx(p.start), self._idx(p.end)):
                self.room_used[p.room][i] += 1
        if protected:
            for i in range(self._idx(option.span[0]), self._idx(option.span[1])):
                self.escort_used[i] += 1

    def remove(self, option: TripOption, protected: bool):
        for p in option.placements:
            for i in range(self._idx(p.start), self._idx(p.end)):
                self.room_used[p.room][i] -= 1
        if protected:
            for i in range(self._idx(option.span[0]), self._idx(option.span[1])):
                self.escort_used[i] -= 1


def _trip_priority(ds: Dataset, trip_id: str, option_count: int):
    trip = ds.trips[trip_id]
    locked = trip_id in {tid for (tid, _a) in ds.locks}
    # 义务（固定/锁定）最优先，受保护次之；同组按方案数少者优先（MRV）
    return (0 if locked else 1, 0 if trip["protected"] else 1, option_count,
            trip["window"][1] - trip["window"][0], trip_id)


def diagnose(ds: Dataset, trip_id: str, shutdown_rooms: frozenset,
             resources: _Resources | None = None,
             shutdown_windows: tuple = ()) -> str:
    """对排不进的行程给出最主要的结构性原因。"""
    trip = ds.trips[trip_id]
    lock_rooms = {lock["room"] for lock in ds.locks_for(trip_id)}
    blocked_by_window = {r for (r, s, e) in shutdown_windows}
    if lock_rooms & (shutdown_rooms | blocked_by_window):
        return REASON_LOCK
    options = enumerate_options(ds, trip_id, shutdown_rooms, shutdown_windows)
    if not options:
        # 放松先后顺序：若任意事项排列都无解，问题在开放时段/容量结构
        relaxed = [enumerate_order_relaxed(ds, trip_id, shutdown_rooms,
                                           shutdown_windows)]
        if not any(relaxed):
            return REASON_OPEN
        return REASON_ORDER
    if resources is not None:
        if trip["protected"] and not any(
            resources.can_place(o, True) for o in options
        ):
            seat_ok = any(resources.can_place(o, False) for o in options)
            if not seat_ok:
                return REASON_SEAT
            return REASON_ESCORT
        if not any(resources.can_place(o, False) for o in options):
            return REASON_SEAT
    return REASON_OPEN


def enumerate_order_relaxed(ds: Dataset, trip_id: str,
                            shutdown_rooms: frozenset,
                            shutdown_windows: tuple = ()) -> list[TripOption]:
    """忽略 ordered 标记，尝试链的全部排列，用于诊断顺序约束是否为唯一障碍。"""
    chain = ds.trips[trip_id]["chain"]
    preferred = _preferred_rooms(ds)
    for (_tid, act), lock in ds.locks.items():
        if _tid == trip_id:
            preferred[act] = lock["room"]
    found = []
    for order in set(permutations(chain)):
        _build(order, 0, ds.trips[trip_id]["window"][0],
               ds.trips[trip_id]["window"][1], [], None, 0, 0,
               ds, trip_id, shutdown_rooms, preferred, found,
               shutdown_windows)
    return found


def schedule(ds: Dataset, demand_ids, shutdown_ids=(),
             custom_windows: tuple = (), anchors: dict | None = None) -> ScheduleResult:
    """在同一供给下对需求集合做整链排程。

    策略：义务/受保护优先、MRV 排序的贪心构造 + 受阻重排；
    每一步都在同一组座位/陪同资源上竞争，替代场地不会被重复许诺。
    custom_windows：(room, start, end) 形式的部分时段停用（网格变体）。
    anchors：基线（无停用）排程结果。停用只可能收缩可行集，因此基线
    可行且在新条件下仍合法的放置一律锚定，避免贪心重排制造"停用反而
    救活行程"的非单调抖动；被停用挤出的行程再统一重排。
    """
    shutdown_rooms = frozenset(
        room for cid in shutdown_ids for room in ds.candidates[cid]["affects"]
    )
    demand_ids = tuple(demand_ids)
    all_options = {
        tid: enumerate_options(ds, tid, shutdown_rooms, custom_windows)
        for tid in demand_ids
    }
    protected_map = {tid: ds.trips[tid]["protected"] for tid in demand_ids}

    res = _Resources(ds)
    result = ScheduleResult(
        demand_id="",
        shutdowns=tuple(sorted(shutdown_ids)),
    )
    result._protected_map = protected_map

    # 结构性失败：在当前停用（不含容量/陪同竞争）下就没有任何可行整链。
    # 基线结构性失败在任何更强停用下仍失败，构成真正单调的硬边界。
    result.structurally_infeasible = frozenset(
        tid for tid in demand_ids if not all_options[tid])

    anchored_ids = set()
    if anchors:
        # 义务与受保护的基线放置先锚，其余按基线跨度从早到晚
        order = sorted(
            anchors,
            key=lambda tid: (0 if (tid, ds.trips[tid]["chain"][0]) in ds.locks
                             else 1,
                             0 if protected_map[tid] else 1,
                             anchors[tid].span[0], tid))
        for tid in order:
            if tid not in all_options:
                continue
            option = anchors[tid]
            if option not in all_options[tid]:
                continue  # 基线放置依赖的场地/时段已被停用
            if res.can_place(option, protected_map[tid]):
                res.add(option, protected_map[tid])
                result.placed[tid] = option
                anchored_ids.add(tid)

    pending = sorted((t for t in demand_ids if t not in anchored_ids),
                     key=lambda tid: _trip_priority(ds, tid, len(all_options[tid])))
    while pending:
        progressed = False
        still = []
        for tid in pending:
            options = all_options[tid]
            if not options:
                result.unfinished[tid] = diagnose(ds, tid, shutdown_rooms, res,
                                                  custom_windows)
                progressed = True
                continue
            chosen = next((o for o in options
                           if res.can_place(o, protected_map[tid])), None)
            if chosen is None:
                still.append(tid)
                continue
            res.add(chosen, protected_map[tid])
            result.placed[tid] = chosen
            progressed = True
        if not progressed:
            # 整圈无人落位：移除约束最强的受阻行程后继续
            victim = sorted(still,
                            key=lambda tid: (len(all_options[tid]),
                                             0 if protected_map[tid] else 1,
                                             tid))[0]
            result.unfinished[victim] = diagnose(
                ds, victim, shutdown_rooms, res, custom_windows)
            still.remove(victim)
        pending = still

    _improve(ds, res, result, protected_map, all_options)
    _finalize_totals(ds, result)
    return result


def _improve(ds, res, result, protected_map, all_options):
    """单行程换便宜方案的局部改进，直到没有改进空间。"""
    changed = True
    while changed:
        changed = False
        for tid in sorted(result.placed,
                          key=lambda t: result.placed[t].cost, reverse=True):
            current = result.placed[tid]
            res.remove(current, protected_map[tid])
            better = next((o for o in all_options[tid]
                           if o.cost < current.cost
                           and res.can_place(o, protected_map[tid])), None)
            if better is not None:
                res.add(better, protected_map[tid])
                result.placed[tid] = better
                changed = True
            else:
                res.add(current, protected_map[tid])


def _finalize_totals(ds: Dataset, result: ScheduleResult):
    result.walking = sum(o.walking for o in result.placed.values())
    result.substitutions = sum(o.substitutions for o in result.placed.values())
    for tid, option in result.placed.items():
        for (lock_trip, lock_act), lock in ds.locks.items():
            if lock_trip != tid:
                continue
            placement = next(p for p in option.placements if p.activity == lock_act)
            bucket = (result.locked_kept if placement.room == lock["room"]
                      and placement.start == lock["window"][0]
                      else result.locked_substituted)
            bucket.append({"trip": tid, "activity": lock_act,
                           "lock_room": lock["room"], "lock_source": lock["source"],
                           "new_room": placement.room, "new_start": placement.start})
    for tid in result.unfinished:
        if any(tid == lock_trip for (lock_trip, _a) in ds.locks):
            result.locked_unplaceable.append(tid)


def exact_feasible(ds: Dataset, demand_ids, shutdown_ids=(), node_budget=200_000,
                   custom_windows: tuple = ()):
    """小规模需求集上的精确回溯参考实现，返回可完成的最大行程数。"""
    shutdown_rooms = frozenset(
        room for cid in shutdown_ids for room in ds.candidates[cid]["affects"]
    )
    demand_ids = tuple(demand_ids)
    options = {tid: enumerate_options(ds, tid, shutdown_rooms, custom_windows)
               for tid in demand_ids}
    protected_map = {tid: ds.trips[tid]["protected"] for tid in demand_ids}
    res = _Resources(ds)
    best = [0]
    nodes = [0]

    order = sorted(demand_ids,
                   key=lambda tid: _trip_priority(ds, tid, len(options[tid])))

    def dfs(i, placed_count):
        nodes[0] += 1
        if nodes[0] > node_budget:
            return
        if placed_count + (len(order) - i) <= best[0]:
            return
        if i == len(order):
            best[0] = max(best[0], placed_count)
            return
        tid = order[i]
        # 尝试放置（代价低的方案先试）
        for option in options[tid]:
            if res.can_place(option, protected_map[tid]):
                res.add(option, protected_map[tid])
                dfs(i + 1, placed_count + 1)
                res.remove(option, protected_map[tid])
        # 跳过该行程
        dfs(i + 1, placed_count)

    dfs(0, 0)
    return best[0], nodes[0] > node_budget
