"""
eval.py — Run a protocol over the dataset, score it, and produce metrics + plots.

Metrics:
  * detection accuracy  — pred_flawed == true_flawed  (overall and per flaw type)
  * localization accuracy — among truly-flawed items the judge flagged, pred_step == flaw_step
  * error amplification — judge error rate under OPEN protocols
  * debater competence — fraction of flawed items whose ground-truth report contains a
    citeable flaw (an upper bound on what any debater could surface), to separate
    "protocol didn't help" from "evidence was insufficient"

All cell estimates come with bootstrap 95% CIs.
"""
from __future__ import annotations
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

import numpy as np
import pandas as pd

from schema import task_from_dict, trajectory_from_dict, FlawType, PROCESS_ONLY, ANSWER_VISIBLE
from env import RecordsEnv
import protocols as P


# flaw-class axis: the paper's main claim lives here, not in per-flaw-type detail.
_PROCESS_ONLY_VALUES = {f.value for f in PROCESS_ONLY}
_ANSWER_VISIBLE_VALUES = {f.value for f in ANSWER_VISIBLE}


def flaw_class(flaw_type: str) -> str:
    if flaw_type == FlawType.CLEAN.value:
        return "clean"
    if flaw_type in _PROCESS_ONLY_VALUES:
        return "process_only"
    if flaw_type in _ANSWER_VISIBLE_VALUES:
        return "answer_visible"
    return "other"


def _cache_key(task_id, protocol, judge_level, seed) -> str:
    return hashlib.sha256(f"{task_id}|{protocol}|{judge_level}|{seed}".encode()).hexdigest()[:16]


def _save_cache(cache: dict | None, cache_path: str | None) -> None:
    """Atomically persist the cache so an interrupted run resumes without re-spending.
    Written to a temp file then os.replace'd, so a crash mid-write cannot corrupt the
    existing cache.json (a half-written cache would otherwise lose ALL prior progress)."""
    if cache is None or not cache_path:
        return
    tmp = f"{cache_path}.tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, cache_path)


def _dispatch(protocol: str, clients: dict, env: RecordsEnv, task, traj, seed: int):
    """Run one protocol on one trajectory; return the scoreable JudgeOutput fields.
    Pure w.r.t. shared state (reads clients, never mutates), so it is safe to call from
    many threads concurrently — that is what lets run_protocol parallelize the item loop."""
    if protocol == "judge_alone":
        o = P.judge_alone(clients["judge"], task, traj)
    elif protocol == "critique":
        o = P.critique(clients["judge"], clients["critic"], env, task, traj)
    elif protocol == "consultancy":
        o = P.consultancy(clients["judge"], clients["consultant"], env, task, traj,
                          assigned=None)            # open
    elif protocol == "debate":
        o = P.debate(clients["judge"], clients["expert_a"], clients["expert_b"],
                     env, task, traj, seed=seed)
    else:
        raise ValueError(protocol)
    return o.verdict, o.flawed_step, o.report_found_flaw


def run_protocol(rows, env: RecordsEnv, protocol: str, clients: dict, judge_level: str,
                 seed: int = 0, cache: dict | None = None, workers: int = 1,
                 cache_path: str | None = None, flush_every: int = 25) -> pd.DataFrame:
    """clients: {'judge':..., 'expert_a':..., 'expert_b':..., 'critic':..., 'consultant':...}

    workers>1 runs the per-item model calls concurrently (I/O-bound). Results are assembled
    in the original row order and each item has a distinct cache key, so the output is
    identical to the sequential path regardless of worker count.

    When cache_path is given, the cache is flushed to disk every flush_every items AND in a
    finally block, so an interruption (crash / Ctrl-C / credit cut-off) keeps every item
    completed so far; the resume re-spends only the items that never finished.
    """
    n = len(rows)
    tasks_trajs = [None] * n
    results: list[tuple | None] = [None] * n     # (verdict, pred_step, found) per row
    pending = []                                  # (idx, ck) for rows that must be computed
    for idx, row in enumerate(rows):
        task = task_from_dict(row["task"])
        traj = trajectory_from_dict(row["trajectory"])
        tasks_trajs[idx] = (task, traj)
        # NOTE: task_id is shared across a task's 7 trajectories, so the cache key must
        # fingerprint trajectory CONTENT or sibling trajectories collide.
        tfp = hashlib.sha256(json.dumps(row["trajectory"], sort_keys=True).encode()).hexdigest()[:12]
        ck = _cache_key(tfp, protocol, judge_level, seed)
        if cache is not None and ck in cache:
            out = cache[ck]
            results[idx] = (out["verdict"], out["pred_step"], out["report_found_flaw"])
        else:
            pending.append((idx, ck))

    def _compute(idx):
        task, traj = tasks_trajs[idx]
        return _dispatch(protocol, clients, env, task, traj, seed)

    def _record(idx, ck, res):
        # Cache writes happen only on the main thread (workers just compute), so the
        # shared cache dict is never mutated concurrently.
        results[idx] = res
        if cache is not None:
            cache[ck] = {"verdict": res[0], "pred_step": res[1], "report_found_flaw": res[2]}

    try:
        if workers > 1 and len(pending) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_compute, idx): (idx, ck) for idx, ck in pending}
                for k, fut in enumerate(as_completed(futs), 1):
                    idx, ck = futs[fut]
                    _record(idx, ck, fut.result())      # re-raises any worker exception
                    if k % flush_every == 0:
                        _save_cache(cache, cache_path)
        else:
            for k, (idx, ck) in enumerate(pending, 1):
                _record(idx, ck, _compute(idx))
                if k % flush_every == 0:
                    _save_cache(cache, cache_path)
    finally:
        _save_cache(cache, cache_path)                  # persist all completed work, even on crash

    recs = []
    for idx, row in enumerate(rows):
        task, traj = tasks_trajs[idx]
        verdict, pred_step, found = results[idx]
        true_flawed = traj.label != FlawType.CLEAN
        pred_flawed = verdict == "flawed"
        recs.append({
            "task_id": task.task_id, "protocol": protocol, "judge_level": judge_level,
            "flaw_type": traj.label.value, "flaw_class": flaw_class(traj.label.value),
            "true_flawed": true_flawed,
            "pred_flawed": pred_flawed,
            "detect_correct": pred_flawed == true_flawed,
            "true_step": traj.flaw_step_index, "pred_step": pred_step,
            "localized": bool(true_flawed and pred_flawed and pred_step == traj.flaw_step_index),
            "report_found_flaw": bool(found),
        })
    return pd.DataFrame.from_records(recs)


# ---- metrics ----------------------------------------------------------------
def _bootstrap_ci(x: np.ndarray, iters: int = 2000, seed: int = 0):
    if len(x) == 0:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), size=(iters, len(x)))].mean(axis=1)
    return float(x.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def detection_by_flaw(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (proto, level, ftype), g in df.groupby(["protocol", "judge_level", "flaw_type"]):
        m, lo, hi = _bootstrap_ci(g["detect_correct"].to_numpy(dtype=float))
        out.append({"protocol": proto, "judge_level": level, "flaw_type": ftype,
                    "n": len(g), "detection": m, "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(out).sort_values(["protocol", "judge_level", "flaw_type"])


def detection_by_class(df: pd.DataFrame) -> pd.DataFrame:
    """Figure 1 data: detection by protocol × judge_level × {clean, answer_visible, process_only}.
    This grouping is the paper's central claim — assistance should lift process_only most."""
    out = []
    for (proto, level, klass), g in df.groupby(["protocol", "judge_level", "flaw_class"]):
        m, lo, hi = _bootstrap_ci(g["detect_correct"].to_numpy(dtype=float))
        out.append({"protocol": proto, "judge_level": level, "flaw_class": klass,
                    "n": len(g), "detection": m, "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(out).sort_values(["judge_level", "flaw_class", "protocol"])


def localization_by_flaw(df: pd.DataFrame) -> pd.DataFrame:
    flagged = df[(df["true_flawed"]) & (df["pred_flawed"])]
    out = []
    for (proto, level, ftype), g in flagged.groupby(["protocol", "judge_level", "flaw_type"]):
        m, lo, hi = _bootstrap_ci(g["localized"].to_numpy(dtype=float))
        out.append({"protocol": proto, "judge_level": level, "flaw_type": ftype,
                    "n_flagged": len(g), "localization": m, "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(out)


def debater_competence(df: pd.DataFrame) -> pd.DataFrame:
    # report coverage is a property of the trajectory (protocol-invariant); measure once,
    # only on report-bearing protocols (judge_alone builds no report).
    rep = df[df["protocol"].isin(["debate", "critique", "consultancy"])]
    rep = rep.drop_duplicates(subset=["task_id", "flaw_type"])
    flawed = rep[rep["true_flawed"]]
    out = []
    for ftype, g in flawed.groupby("flaw_type"):
        out.append({"flaw_type": ftype, "report_finds_flaw_rate": g["report_found_flaw"].mean(),
                    "n": len(g)})
    return pd.DataFrame(out)


# ---- plotting ---------------------------------------------------------------
def plot_detection(det: pd.DataFrame, judge_level: str, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sub = det[det["judge_level"] == judge_level]
    flaws = sorted(sub["flaw_type"].unique())
    protos = sorted(sub["protocol"].unique())
    x = np.arange(len(flaws))
    w = 0.8 / max(1, len(protos))
    fig, ax = plt.subplots(figsize=(11, 5))
    for i, proto in enumerate(protos):
        s = sub[sub["protocol"] == proto].set_index("flaw_type").reindex(flaws)
        vals = s["detection"].to_numpy()
        err = np.vstack([vals - s["ci_lo"].to_numpy(), s["ci_hi"].to_numpy() - vals])
        ax.bar(x + i * w, vals, w, yerr=err, capsize=2, label=proto)
    ax.set_xticks(x + 0.4 - w / 2)
    ax.set_xticklabels(flaws, rotation=25, ha="right")
    ax.set_ylabel("detection accuracy")
    ax.set_ylim(0, 1.05)
    ax.set_title(f"Detection accuracy by protocol × flaw type (judge={judge_level})")
    ax.legend()
    ax.axhline(0.5, ls="--", lw=0.8, color="gray")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_detection_by_class(det_class: pd.DataFrame, judge_level: str, path: str):
    """Figure 1: detection accuracy grouped into clean / answer-visible / process-only,
    one bar series per protocol. The process-only group is the headline contrast."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = ["clean", "answer_visible", "process_only"]
    pretty = {"clean": "clean", "answer_visible": "answer-visible", "process_only": "process-only"}
    sub = det_class[det_class["judge_level"] == judge_level]
    classes = [c for c in order if c in set(sub["flaw_class"])]
    protos = sorted(sub["protocol"].unique())
    x = np.arange(len(classes))
    w = 0.8 / max(1, len(protos))
    fig, ax = plt.subplots(figsize=(8, 5))
    for i, proto in enumerate(protos):
        s = sub[sub["protocol"] == proto].set_index("flaw_class").reindex(classes)
        vals = np.nan_to_num(s["detection"].to_numpy(dtype=float))
        lo = np.nan_to_num(s["ci_lo"].to_numpy(dtype=float))
        hi = np.nan_to_num(s["ci_hi"].to_numpy(dtype=float))
        err = np.vstack([vals - lo, hi - vals])
        ax.bar(x + i * w, vals, w, yerr=err, capsize=3, label=proto)
    ax.set_xticks(x + 0.4 - w / 2)
    ax.set_xticklabels([pretty[c] for c in classes])
    ax.set_ylabel("detection accuracy")
    ax.set_ylim(0, 1.05)
    ax.set_title(f"Detection by flaw class × protocol (judge={judge_level})")
    ax.axhline(0.5, ls="--", lw=0.8, color="gray")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_scaling_by_class(det_class: pd.DataFrame, levels: list[str], path: str,
                          classes=("answer_visible", "process_only")):
    """Figure 2: detection vs judge capability, one panel per flaw class, a line per protocol.
    Expected: on process-only the judge-alone line climbs toward the assisted ones (capability
    gap); on answer-visible judge-alone stays at the floor (structural gap that capability
    cannot close), so the assisted–judge_alone gap persists."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order_levels = [l for l in levels if l in set(det_class["judge_level"])]
    protos = sorted(det_class["protocol"].unique())
    pretty = {"answer_visible": "answer-visible", "process_only": "process-only", "clean": "clean"}
    classes = [c for c in classes if c in set(det_class["flaw_class"])]
    fig, axes = plt.subplots(1, len(classes), figsize=(5.2 * len(classes), 4.5), sharey=True)
    if len(classes) == 1:
        axes = [axes]
    for ax, klass in zip(axes, classes):
        sub = det_class[det_class["flaw_class"] == klass]
        for proto in protos:
            s = sub[sub["protocol"] == proto].set_index("judge_level").reindex(order_levels)
            ax.plot(range(len(order_levels)), s["detection"].to_numpy(dtype=float),
                    marker="o", label=proto)
        ax.set_xticks(range(len(order_levels)))
        ax.set_xticklabels(order_levels)
        ax.set_xlabel("judge tier")
        ax.set_title(pretty.get(klass, klass))
        ax.set_ylim(0, 1.05)
        ax.axhline(0.5, ls="--", lw=0.8, color="gray")
    axes[0].set_ylabel("detection accuracy")
    axes[-1].legend()
    fig.suptitle("Figure 2 — scaling of detection by judge capability, per flaw class")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
