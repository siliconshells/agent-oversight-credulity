# Credulity, Not Verification: The Limits of Structured Assistance for Tool-Blind Oversight of AI Agents

This repository contains the code and data for a controlled study that investigates
whether structured assistance, including debate, critique, or consultancy with honest helpers
that hold ground-truth access, enables a weak, tool-blind overseer to detect failures in
multi-step agent tool-use trajectories. The study also examines the nature of the assistance
provided.

## Key Findings

The study isolates a failure mode that a tool-blind overseer is structurally unable to
verify: a fabricated tool output in which a returned value is altered but remains consistent
throughout the remainder of the trajectory, resulting in internal coherence. The only
evidence of error exists in the environment, which the overseer is unable to query.

1. A structural gap is identified: the unaided overseer detects fabricated tool outputs with
   a detection rate of 0.00 at all capability tiers (weak, mid, strong). Increased capability
   does not compensate for a lack of access to tools.
2. Apparent amplification is observed: assistance from honest helpers restores a weak
   overseer to approximately 1.00, reproducing the familiar amplification result. However,
   the benefit diminishes as the overseer's capability increases. Critique performance
   declines from 1.00 to 0.86 to 0.11. Consultancy and debate collapse a tier earlier:
   consultancy falls from 1.00 to 0.00 at the mid tier (0.00 at strong), and debate falls
   from 0.99 to 0.03 (its results are likely an upper bound, because the honest helper
   assigned the false side tends to concede rather than argue it.
3. The results indicate that credulity, rather than verification, is the primary mechanism.
   A lying-verifier ablation gives the critic a falsified verification report, its only source
   of ground truth, so the honest critic relays a claim that a clean trajectory is flawed.
   The weak overseer subsequently identifies 100% of clean trajectories as flawed (the
   false-positive rate decreases from 1.00 to 0.70 to 0.20 across tiers). The same trust
   that enables effective assistance also renders the overseer susceptible to deception,
   and both effects diminish as the overseer becomes more skeptical.
4. A matched-capability control rules out source capability as the explanation: giving the
   strong overseer an equally strong helper leaves detection near the floor (0.18, versus the
   approximately 0.86 that capability parity predicts from the study's own grid), while a
   stronger helper relaying the same false report raises the false-positive rate from 0.20
   to 0.35. Greater helper capability buys persuasion, not verification.

When the overseer cannot verify the transmitted evidence, amplification and gullibility
become the same phenomenon. Honest assistance cannot address a structural verification gap;
it can only be trusted or distrusted. The primary implication for agent oversight is to
prioritize surfacing verifiable ground truth, such as replayable tool execution, rather than
relying on unverifiable argumentation. It is also recommended to report a credulity control
alongside any amplification metrics for weak overseers.

## Repository layout

```
src/                 
  schema.py env.py flaws.py generate.py     
  models.py prompts.py protocols.py         
  eval.py run_experiment.py                 
  ablation_lying_helper.py                  
  control_helper_tier.py                    
  plot_scaling_figure.py                    
  sample_transcripts.py                     
data/                
results/
  run1_weak_tier/    
  run2_scaling/      
  sample_transcripts/ 
```

## Method

A deterministic records-analyst environment, consisting of a seeded 240-record database with
pure query and calculator tools, includes a verifier that maintains exact ground truth by
re-executing any step. A reference solver generates clean trajectories, while validated
injectors produce six flaw types across 80 tasks (560 trajectories: one clean and one of each
flaw per task). The overseer is presented with the task, a policy, and the trajectory with
claimed observations, but not the gold answer or the verifier report. Helpers receive the
verifier report, which is their only access to ground truth. The study compares judge-alone, consultancy, critique, and debate across three
overseer capability tiers, with helpers fixed at the mid tier. A matched-helper control
 reruns the critique protocol at the strong tier with a strong helper on the
fabricated-observation and clean subsets (80 each).

## To reproduce

```bash
pip install -r requirements.txt          
cd src

PYTHONHASHSEED=0 python generate.py --tasks 80 --out ../data/dataset.jsonl

python run_experiment.py --mock --protocols judge_alone,consultancy,critique,debate \
    --judge-levels weak,mid,strong --n 560 \
    --dataset ../data/dataset.jsonl --meta ../data/dataset.meta.json --out ../results/_mock

export ANTHROPIC_API_KEY=...
python run_experiment.py --real --protocols judge_alone,consultancy,critique,debate \
    --judge-levels weak --n 560 --workers 12 \
    --dataset ../data/dataset.jsonl --meta ../data/dataset.meta.json --out ../results/run1_weak_tier
python run_experiment.py --real --protocols judge_alone,consultancy,critique,debate \
    --judge-levels weak,mid,strong --n 560 --workers 12 \
    --dataset ../data/dataset.jsonl --meta ../data/dataset.meta.json --out ../results/run2_scaling
python plot_scaling_figure.py            

python ablation_lying_helper.py          
python control_helper_tier.py            
python sample_transcripts.py             
```

Overseer tiers use a weak / mid / strong model triple with helpers fixed at the mid model;
the exact model identifiers are set at the top of `src/models.py` (`JUDGE_MODELS`,
`EXPERT_MODEL`) and are the only thing to update as model strings change.

## Scope

The study uses honest helpers whose only access to ground truth is the verifier report. The
single falsified report in the ablation simulates a lying verifier and isolates overseer
credulity, not strategic deception. Deliberately excluded from
scope are adversarial or untrusted helpers, real-agent rollouts, and richer environments.