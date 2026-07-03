"""
run_experiment.py — Orchestrate oversight protocols across conditions, save results.

Verify the whole pipeline offline (no API key, no spend):
    python run_experiment.py --mock --protocols judge_alone,critique,debate --n 240

Run for real (uses ANTHROPIC_API_KEY; costs money):
    python run_experiment.py --real --protocols judge_alone,debate \
        --judge-levels weak,mid --n 300

Outputs (in --out): per_item.jsonl, detection.csv, localization.csv,
debater_competence.csv, detection_<level>.png, and a printed summary.
"""
from __future__ import annotations
import argparse
import json
import os

import pandas as pd

from env import RecordsEnv
import eval as E
from models import AnthropicClient, MockClient, EXPERT_MODEL, JUDGE_MODELS


def load_dataset(path: str):
    with open(path) as f:
        return [json.loads(line) for line in f]


def load_meta(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"n_records": 240, "seed": 0}        # generator defaults


def build_clients(mock: bool, judge_level: str, seed: int) -> dict:
    if mock:
        return {
            "judge": MockClient(role="judge", seed=seed),
            "expert_a": MockClient(role="advocate", seed=seed),
            "expert_b": MockClient(role="advocate", seed=seed + 1),
            "critic": MockClient(role="critic", seed=seed),
            "consultant": MockClient(role="advocate", seed=seed),
        }
    judge_model = JUDGE_MODELS[judge_level]
    return {
        "judge": AnthropicClient(model=judge_model),
        "expert_a": AnthropicClient(model=EXPERT_MODEL),
        "expert_b": AnthropicClient(model=EXPERT_MODEL),
        "critic": AnthropicClient(model=EXPERT_MODEL),
        "consultant": AnthropicClient(model=EXPERT_MODEL),
    }


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--mock", action="store_true", help="offline mock clients (no spend)")
    g.add_argument("--real", action="store_true", help="real Anthropic clients (costs money)")
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--meta", default="dataset.meta.json")
    ap.add_argument("--protocols", default="judge_alone,critique,debate")
    ap.add_argument("--judge-levels", default="weak")
    ap.add_argument("--n", type=int, default=240, help="max trajectories per (protocol,level) cell")
    ap.add_argument("--out", default="results")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--workers", type=int, default=1,
                    help="concurrent per-item model calls (I/O-bound). >1 cuts wall-clock; "
                         "output is identical to sequential. Try 8-16 for real runs.")
    args = ap.parse_args()

    mock = not args.real            # default to mock unless --real
    os.makedirs(args.out, exist_ok=True)
    rows_all = load_dataset(args.dataset)
    meta = load_meta(args.meta)
    env = RecordsEnv(n_records=meta["n_records"], seed=meta["seed"])
    protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]
    levels = [l.strip() for l in args.judge_levels.split(",") if l.strip()]
    rows = rows_all[: args.n]

    if not mock:
        n_calls = len(rows) * len(protocols) * len(levels)
        print(f"[real] ~{n_calls} judged items across {len(protocols)} protocols × "
              f"{len(levels)} judge levels (debate/critique make several model calls each). "
              f"Ctrl-C now to abort.")

    cache: dict = {}
    cache_path = os.path.join(args.out, "cache.json")
    if not args.no_cache and os.path.exists(cache_path):
        cache = json.load(open(cache_path))

    frames = []
    try:
        for level in levels:
            for proto in protocols:
                clients = build_clients(mock, level, args.seed)
                df = E.run_protocol(rows, env, proto, clients, level, seed=args.seed,
                                    cache=None if args.no_cache else cache,
                                    workers=args.workers,
                                    cache_path=None if args.no_cache else cache_path)
                frames.append(df)
                acc = df["detect_correct"].mean()
                print(f"  {proto:12s} judge={level:6s} n={len(df):4d}  detection={acc:.3f}")
                E._save_cache(None if args.no_cache else cache, cache_path)
    except BaseException as e:                          # incl. KeyboardInterrupt
        E._save_cache(None if args.no_cache else cache, cache_path)
        print(f"\n[interrupted] {type(e).__name__}: {e}")
        print(f"[resume] re-run the SAME command — items already in {cache_path} are "
              f"skipped, so only unfinished work is re-spent.")
        raise
    per_item = pd.concat(frames, ignore_index=True)

    per_item.to_json(os.path.join(args.out, "per_item.jsonl"), orient="records", lines=True)
    if not args.no_cache:
        E._save_cache(cache, cache_path)

    det = E.detection_by_flaw(per_item)
    det_class = E.detection_by_class(per_item)            # Figure 1 data
    loc = E.localization_by_flaw(per_item)
    comp = E.debater_competence(per_item)
    det.to_csv(os.path.join(args.out, "detection.csv"), index=False)
    det_class.to_csv(os.path.join(args.out, "detection_by_class.csv"), index=False)
    loc.to_csv(os.path.join(args.out, "localization.csv"), index=False)
    comp.to_csv(os.path.join(args.out, "debater_competence.csv"), index=False)
    for level in levels:
        E.plot_detection(det, level, os.path.join(args.out, f"detection_{level}.png"))
        E.plot_detection_by_class(det_class, level,
                                  os.path.join(args.out, f"detection_by_class_{level}.png"))
    if len(levels) >= 2:
        E.plot_scaling_by_class(det_class, levels, os.path.join(args.out, "scaling_by_class.png"))

    print("\n=== FIGURE 1: detection by flaw class × protocol ===")
    show_c = det_class.pivot_table(index="flaw_class", columns="protocol", values="detection")
    order = [c for c in ["clean", "answer_visible", "process_only"] if c in show_c.index]
    print(show_c.reindex(order).round(3).to_string())
    print("\n=== detection accuracy by protocol × flaw type (detail) ===")
    show = det.pivot_table(index="flaw_type", columns="protocol", values="detection")
    print(show.round(3).to_string())
    print(f"\nwrote results to {args.out}/ "
          f"(per_item.jsonl, detection.csv, detection_by_class.csv, localization.csv, "
          f"debater_competence.csv, detection_*.png, detection_by_class_*.png)")


if __name__ == "__main__":
    main()
