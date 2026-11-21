"""读取 samples/ 下的原始 JSON 并建立分析用索引。

所有时间均为分钟半开区间；空值（None）表示无观测，绝不当作数值零。
"""

import json
from dataclasses import dataclass
from pathlib import Path


def load_raw(folder: str | Path | None = None) -> dict:
    folder = Path(folder) if folder else Path(__file__).resolve().parent.parent / "samples"
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(folder.glob("*.json"))}


@dataclass(frozen=True)
class Dataset:
    raw: dict
    rooms: dict
    trips: dict
    duration: dict
    transfer: dict
    candidates: dict
    frames: dict
    observations: list
    followup: list
    decisions: list
    policy: dict
    scenarios: dict
    bookings: list
    accepted_changes: list
    assistance: list
    trip_frame: dict
    locks: dict
    room_activities: dict

    @classmethod
    def build(cls, folder: str | Path | None = None) -> "Dataset":
        raw = load_raw(folder)
        rooms = {r["id"]: r for r in raw["rooms"]}
        trips = {t["id"]: t for t in raw["trips"]}
        transfer = {(x["left"], x["right"]): x["minutes"] for x in raw["transfer"]}
        candidates = {c["id"]: c for c in raw["candidates"]}
        frames = {f["id"]: f for f in raw["sampling_frame"]}

        trip_frame = {}
        for frame in raw["sampling_frame"]:
            for tid in frame["trip_ids"]:
                trip_frame[tid] = frame["id"]

        room_activities = {}
        for room in raw["rooms"]:
            for act in room["activities"]:
                room_activities.setdefault(act, []).append(room["id"])

        # 固定预约与已接受换约：同一（行程, 事项）上换约覆盖原预约。
        locks = {}
        for row in raw["bookings"]:
            locks[(row["trip"], row["activity"])] = dict(row, source="固定预约")
        for row in raw["accepted_changes"]:
            locks[(row["trip"], row["activity"])] = dict(row, source="已接受换约")

        return cls(
            raw=raw,
            rooms=rooms,
            trips=trips,
            duration=raw["service_duration"],
            transfer=transfer,
            candidates=candidates,
            frames=frames,
            observations=raw["observations"],
            followup=raw["followup"],
            decisions=raw["decisions"],
            policy=raw["review_policy"],
            scenarios={s["id"]: s for s in raw["scenarios"]},
            bookings=raw["bookings"],
            accepted_changes=raw["accepted_changes"],
            assistance=raw["shared_assistance"],
            trip_frame=trip_frame,
            locks=locks,
            room_activities=room_activities,
        )

    def walk_time(self, left: str, right: str) -> int:
        if left == right:
            return 0
        return self.transfer[(left, right)]

    def locks_for(self, trip_id: str) -> list:
        return [lock for (tid, _), lock in self.locks.items() if tid == trip_id]
