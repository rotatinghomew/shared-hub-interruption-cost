"""把评审结果序列化为机器可读 JSON 与评审用 Markdown。

报告严格区分：
- 确定性记录（已登记全量、已确证身份）与区间估计（抽样、未覆盖）
- 基线既有损失与停用新增损失
- 可确认窗口与待议组合（含原因）
"""

import json
from pathlib import Path

from .data import Dataset
from .engine import Review
from .scheduler import ScheduleResult


def _interval_dict(iv):
    return {"low": round(iv.low, 4), "high": round(iv.high, 4),
            "point": None if iv.point is None else round(iv.point, 4),
            "note": iv.note}


def serialize_review(ds: Dataset, review: Review) -> dict:
    out = {
        "cutoff": review.cutoff,
        "active_candidates": sorted(review.active),
        "partitions": [
            {"name": p.name, "merges": [list(m) for m in p.merges],
             "dropped": sorted(p.dropped)}
            for p in review.partitions],
        "uncovered": {
            fid: {"window": row["window"],
                  "observed_passes": row["observed_passes"],
                  "household_bounds": _interval_dict(row["household_bounds"])}
            for fid, row in review.uncovered.items()},
        "baseline": _baseline_block(ds, review),
        "combinations": [],
        "rankings": {name: [list(c) for c in rank]
                     for name, rank in review.rankings.items()},
        "ranking_swaps": [[list(x), list(y)] for x, y in review.swaps],
        "indistinguishable_pairs": [[list(x), list(y)]
                                    for x, y in review.indistinguishable],
        "key_groups": review.key_groups,
        "followup_targets": review.followup_targets,
    }
    for combo, ev in review.evaluations.items():
        block = {"combo": list(combo), "confirmable": ev.verdict.confirmable,
                 "verdict_reasons": list(ev.verdict.reasons),
                 "new_protected_gap": ev.verdict.protected_gap,
                 "desk_triage": ev.verdict.desk_categories,
                 "by_partition": {}}
        for part in review.partitions:
            result = ev.results[part.name]
            agg = ev.aggregate[part.name]
            base_fail = set(review.baseline[part.name].unfinished)
            rescued = sorted(base_fail - set(result.unfinished))
            block["by_partition"][part.name] = {
                "unfinished_count": result.unfinished_count,
                "structurally_infeasible": sorted(result.structurally_infeasible),
                "unfinished_reasons": dict(ev.reasons[part.name]),
                "incremental_unfinished": ev.incremental[part.name],
                "capacity_reallocation_rescued": rescued,
                "protected_unfinished": result.protected_unfinished,
                "walking_minutes": result.walking,
                "substitutions": result.substitutions,
                "aggregate": {
                    "demand": _interval_dict(agg["demand"]),
                    "unfinished": _interval_dict(agg["unfinished"]),
                    "loss_rate": _interval_dict(agg["loss_rate"])},
            }
        out["combinations"].append(block)
    return out


def _baseline_block(ds, review: Review) -> dict:
    block = {}
    for part in review.partitions:
        result = review.baseline[part.name]
        block[part.name] = {
            "unfinished": sorted(result.unfinished),
            "reasons": {k: v for k, v in _count(result).items()},
            "note": "无任何停用时仍存在的结构性/容量性损失，不计入保养账",
        }
    return block


def _count(result: ScheduleResult):
    from collections import Counter
    return Counter(result.unfinished.values())


def write_json(ds: Dataset, review: Review, path: str | Path):
    Path(path).write_text(
        json.dumps(serialize_review(ds, review), ensure_ascii=False, indent=2),
        encoding="utf-8")


def render_markdown(ds: Dataset, review: Review) -> str:
    lines = []
    lines.append(f"# 停用窗口代价评审（信息时点 cutoff={review.cutoff}）\n")
    lines.append(f"有效候选工段：{', '.join(sorted(review.active))}（被取消者已退出）")
    lines.append("\n## 身份解释")
    for part in review.partitions:
        lines.append(f"- **{part.name}**：合并 {len(part.merges)} 对，"
                     f"需求户数 {len(part.demand_ids(list(ds.trips)))}")
    lines.append("\n## 基线既有损失（与保养无关）")
    for part in review.partitions:
        b = review.baseline[part.name]
        lines.append(f"- {part.name}：{b.unfinished_count} 户未完成，"
                     f"其中结构性失败 {len(b.structurally_infeasible)} 户"
                     f"（{sorted(b.structurally_infeasible)}）、"
                     f"容量/陪同竞争 {b.unfinished_count - len(b.structurally_infeasible)} 户；"
                     f"受保护 {b.protected_unfinished} 户，原因 {dict(_count(b))}")

    lines.append("\n## 停用组合比较（同一供给，整链口径）")
    header = ("| 组合 | 解释 | 未完成 | 新增 | 重排救活 | 受护新增 | 损失率区间(点估计) | "
              "替代行走(分) | 换场 | 结论 |")
    lines.append(header)
    lines.append("|" + "---|" * 10)
    rank0 = review.rankings[review.partitions[0].name]
    for combo in rank0:
        ev = review.evaluations[combo]
        base = {p.name: set(review.baseline[p.name].unfinished)
                for p in review.partitions}
        for part in review.partitions:
            r = ev.results[part.name]
            inc = len(set(r.unfinished) - base[part.name])
            rescued = len(base[part.name] - set(r.unfinished))
            lr = ev.aggregate[part.name]["loss_rate"]
            verdict = "可确认" if ev.verdict.confirmable else "待议"
            lines.append(
                f"| {'+'.join(combo) if combo else '基线'} | {part.name[:2]} | "
                f"{r.unfinished_count} | {inc} | {rescued} | "
                f"{ev.verdict.protected_gap} | "
                f"[{lr.low:.3f}, {lr.high:.3f}] ({lr.point:.3f}) | "
                f"{r.walking} | {r.substitutions} | {verdict} |")
        if ev.verdict.reasons:
            lines.append(f"| | 待议原因 | {'; '.join(ev.verdict.reasons)} |||||||||")

    lines.append("\n## 排序敏感性")
    if review.swaps:
        for x, y in review.swaps:
            lines.append(f"- **排序互换**：{'+'.join(x) or '基线'} ⇄ {'+'.join(y)}，"
                         "两种完整解释下优先方案不同，不得发公告")
    else:
        lines.append("- 两种身份解释下点估计排序一致，无互换")
    if review.indistinguishable:
        for x, y in review.indistinguishable:
            lines.append(
                f"- **区间不可稳健区分（排序待议，不影响窗口本身可确认性）**："
                f"{'+'.join(x) if x else '基线'} 与 {'+'.join(y) if y else '基线'} "
                "损失率区间重叠，二者先后不得仅凭点估计对外公告")
    for grp in review.key_groups:
        lines.append(f"  - 关键行程组 {grp['pair']}：{grp['by_frame']}")

    lines.append("\n## 未覆盖时段（不宣称零需求）")
    for fid, row in review.uncovered.items():
        iv = row["household_bounds"]
        lines.append(f"- {fid} {row['window']}：{row['observed_passes']} 次经过，"
                     f"户数边界 {iv.low}~{iv.high}，无点估计；补点不能填平该时段")

    lines.append("\n## 针对性补点建议（只校验可覆盖框的实际人群）")
    for t in review.followup_targets:
        lines.append(f"- {t['frame']} {t['window']}：{t['trips']} —— {t['question']}")

    lines.append("\n## 服务台分类")
    for combo in rank0:
        triage = review.evaluations[combo].verdict.desk_categories
        if triage:
            lines.append(f"- {'+'.join(combo) if combo else '基线'}：{triage}")
    return "\n".join(lines) + "\n"
