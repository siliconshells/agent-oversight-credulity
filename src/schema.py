"""
schema.py — Core data structures for process-level scalable-oversight experiments.

A Task has a verifiable gold answer and an explicit policy (which tools/sources are
legitimate). A Trajectory is a sequence of Steps. Each Step records the tool call AND
its observation, so a verifier with environment access can re-execute any step — while
a *judge without environment access* cannot. That asymmetry is the whole experiment.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class FlawType(str, Enum):
    CLEAN = "clean"
    FABRICATED_OBSERVATION = "fabricated_observation"   # reported tool output != real output
    PLANNING_ERROR = "planning_error"                   # valid steps, wrong sub-goal/filter
    REWARD_HACK = "reward_hack"                          # correct answer, illegitimate path
    UNSAFE_ACTION = "unsafe_action"                      # out-of-policy action en route
    STATE_TRACKING_SLIP = "state_tracking_slip"          # arithmetic/state error -> wrong answer
    OBFUSCATED_FLAW = "obfuscated_flaw"                  # error buried deep in a sub-computation


class Subtlety(str, Enum):
    BLATANT = "blatant"
    SUBTLE = "subtle"
    OBFUSCATED = "obfuscated"


# Whether a flaw manifests in the final answer or only in the process.
# Process-only flaws are the key novelty: invisible to answer-only oversight.
ANSWER_VISIBLE = {
    FlawType.FABRICATED_OBSERVATION,
    FlawType.PLANNING_ERROR,
    FlawType.STATE_TRACKING_SLIP,
    FlawType.OBFUSCATED_FLAW,
}
PROCESS_ONLY = {
    FlawType.REWARD_HACK,
    FlawType.UNSAFE_ACTION,
}


@dataclass
class Step:
    thought: str
    action: str                       # tool name, or "final_answer"
    action_input: dict[str, Any]
    observation: Any                  # what the trajectory *claims* the tool returned

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Task:
    task_id: str
    prompt: str
    gold_answer: int
    # Policy: what a legitimate solution may use.
    legitimate_tools: set[str]
    forbidden_actions: set[str]       # using any of these is a violation
    forbidden_sources: set[str]       # read_doc ids that may not be used
    # Parameters that define the task against the shared dataset.
    region: str
    category: str
    status: str

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("legitimate_tools", "forbidden_actions", "forbidden_sources"):
            d[k] = sorted(d[k])
        return d


@dataclass
class Trajectory:
    task_id: str
    steps: list[Step]
    final_answer: int | None
    # Labels (ground truth held by the experimenter, never shown to the judge):
    label: FlawType = FlawType.CLEAN
    flaw_step_index: int | None = None
    subtlety: Subtlety | None = None
    mechanism: str = ""               # human-readable note on how the flaw was introduced

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "steps": [s.to_dict() for s in self.steps],
            "final_answer": self.final_answer,
            "label": self.label.value,
            "flaw_step_index": self.flaw_step_index,
            "subtlety": self.subtlety.value if self.subtlety else None,
            "mechanism": self.mechanism,
        }

    def copy(self) -> "Trajectory":
        return Trajectory(
            task_id=self.task_id,
            steps=[Step(s.thought, s.action, dict(s.action_input),
                        _copy_obs(s.observation)) for s in self.steps],
            final_answer=self.final_answer,
            label=self.label,
            flaw_step_index=self.flaw_step_index,
            subtlety=self.subtlety,
            mechanism=self.mechanism,
        )


def _copy_obs(obs: Any) -> Any:
    if isinstance(obs, list):
        return [dict(x) if isinstance(x, dict) else x for x in obs]
    if isinstance(obs, dict):
        return dict(obs)
    return obs


# ---- deserialization (load a dataset row back into objects) ------------------
def task_from_dict(d: dict) -> Task:
    return Task(
        task_id=d["task_id"], prompt=d["prompt"], gold_answer=d["gold_answer"],
        legitimate_tools=set(d["legitimate_tools"]),
        forbidden_actions=set(d["forbidden_actions"]),
        forbidden_sources=set(d["forbidden_sources"]),
        region=d["region"], category=d["category"], status=d["status"],
    )


def trajectory_from_dict(d: dict) -> Trajectory:
    steps = [Step(s["thought"], s["action"], s["action_input"], s["observation"])
             for s in d["steps"]]
    return Trajectory(
        task_id=d["task_id"], steps=steps, final_answer=d["final_answer"],
        label=FlawType(d["label"]),
        flaw_step_index=d["flaw_step_index"],
        subtlety=Subtlety(d["subtlety"]) if d.get("subtlety") else None,
        mechanism=d.get("mechanism", ""),
    )
