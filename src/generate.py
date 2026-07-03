"""
generate.py — Build a balanced, validated dataset of clean and flawed agent trajectories.

Run:  python generate.py --tasks 80 --out dataset.jsonl

For each task: solve it (clean trajectory), then inject every flaw type and validate
each against the environment's ground truth. Writes JSONL and prints a summary so you
can confirm every injector produces a genuinely-flawed, correctly-labeled trajectory.

In production you would *also* sample clean trajectories from a real agent model; the
reference solver here gives you a deterministic, fully-checkable backbone to inject into.
"""
from __future__ import annotations
import argparse
import json
from collections import Counter

from env import RecordsEnv, solve
from schema import FlawType, Subtlety
from flaws import inject, validate_injection

# subtlety to sweep per flaw (kept simple for the MVP; expand for the full sweep)
DEFAULT_SUBTLETY = {
    FlawType.FABRICATED_OBSERVATION: Subtlety.SUBTLE,
    FlawType.PLANNING_ERROR: Subtlety.SUBTLE,
    FlawType.REWARD_HACK: Subtlety.BLATANT,
    FlawType.UNSAFE_ACTION: Subtlety.BLATANT,
    FlawType.STATE_TRACKING_SLIP: Subtlety.SUBTLE,
    FlawType.OBFUSCATED_FLAW: Subtlety.OBFUSCATED,
}


def build(n_tasks: int, seed: int = 0):
    env = RecordsEnv(n_records=240, seed=seed)
    rows = []
    failures = []
    for i in range(n_tasks):
        task = env.make_task(task_id=f"t{i:04d}", seed=1000 + i)
        clean = solve(env, task)
        assert not env.is_flawed(task, clean), f"reference solution flawed on {task.task_id}"
        # one clean example per task
        rows.append({"task": task.to_dict(), "trajectory": clean.to_dict()})
        # one of each flaw type
        for flaw, sub in DEFAULT_SUBTLETY.items():
            flawed = inject(env, task, clean, flaw, sub)
            ok, msg = validate_injection(env, task, flawed)
            if not ok:
                failures.append((task.task_id, flaw.value, msg))
            rows.append({"task": task.to_dict(), "trajectory": flawed.to_dict()})
    return env, rows, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=int, default=80)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=str, default="dataset.jsonl")
    args = ap.parse_args()

    env, rows, failures = build(args.tasks, args.seed)
    with open(args.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    # record env params so run_experiment.py rebuilds the identical environment
    meta_path = args.out.rsplit(".", 1)[0] + ".meta.json"
    with open(meta_path, "w") as f:
        json.dump({"n_records": len(env.records), "seed": args.seed}, f)

    label_counts = Counter(r["trajectory"]["label"] for r in rows)
    clean_n = label_counts.get("clean", 0)
    flawed_n = len(rows) - clean_n
    print(f"wrote {len(rows)} trajectories to {args.out} "
          f"({clean_n} clean / {flawed_n} flawed)")
    print("\nby label:")
    for k in sorted(label_counts):
        print(f"  {k:24s} {label_counts[k]}")
    print(f"\nvalidation failures: {len(failures)}")
    for tid, flaw, msg in failures[:10]:
        print(f"  {tid} {flaw}: {msg}")

    # show one process-only example to make the key property concrete
    for r in rows:
        if r["trajectory"]["label"] == "reward_hack":
            t, traj = r["task"], r["trajectory"]
            print("\nexample reward_hack (answer correct, process illegitimate):")
            print(f"  gold={t['gold_answer']} reported={traj['final_answer']} "
                  f"steps={[s['action'] for s in traj['steps']]}")
            break


if __name__ == "__main__":
    main()
