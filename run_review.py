#!/usr/bin/env python3 -I
"""评审命令行入口。

用法：
  python3 -I run_review.py                # 两个信息时点各出一份报告
  python3 -I run_review.py --cutoff 4     # 只出修订时点
输出写到 reports/（JSON 机器可读 + Markdown 评审稿）。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from planner.data import Dataset
from planner.engine import evaluate
from planner.report import write_json, render_markdown


def main():
    parser = argparse.ArgumentParser(description="停用窗口代价评审")
    parser.add_argument("--cutoff", type=int, choices=[2, 4],
                        help="只评估指定信息时点")
    parser.add_argument("--out", default=str(ROOT / "reports"))
    args = parser.parse_args()

    ds = Dataset.build()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cutoffs = [args.cutoff] if args.cutoff else [
        ds.policy["initial_cutoff"], ds.policy["revised_cutoff"]]

    for cutoff in cutoffs:
        review = evaluate(ds, cutoff)
        stem = out_dir / f"review_cutoff{cutoff}"
        write_json(ds, review, stem.with_suffix(".json"))
        stem.with_suffix(".md").write_text(
            render_markdown(ds, review), encoding="utf-8")
        print(f"已写出 {stem}.json / {stem}.md")


if __name__ == "__main__":
    main()
