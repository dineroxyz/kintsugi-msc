"""The recovery loop: one incident in, one terminal outcome out.

Chapter three, section 3.4, assembled. Reproduce (or refuse and escalate),
pack evidence, propose, gate with up to two regenerations fed by the failure
evidence of the previous candidate, then hand the signed record to the deploy
controller. Every terminal outcome returns the full trail; the certificate
issuer (phase 4b) will serialise exactly this structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from control_plane.diagnosis.packer import pack
from control_plane.diagnosis.proposer import propose
from control_plane.gate.verifier import GateVerdict, gate
from control_plane.reproduction.compiler import compile_reproduction
from control_plane.reproduction.engine import reproduce

MAX_CANDIDATES = 3      # one proposal plus two regenerations


@dataclass
class Outcome:
    status: str                       # "deployed" | "escalated"
    reason: str
    signature: str | None = None
    reproduction: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    verification_record: str | None = None
    patched_tree: str | None = None
    regression_test: str | None = None


def heal(capture: dict, service_spec: str, workdir: str, *,
         complete=None, incident_id: int | str = "x",
         suite_path: str | None = "tests",
         memory_notes: list[str] | None = None,
         require_reproduction: bool = True,
         max_candidates: int | None = None) -> Outcome:
    """Run the loop for one captured incident.

    `complete` is the diagnosis completion function (None = production model).
    `require_reproduction=False` is configuration C2 of the study — the
    ungated arm — retained here so every arm runs through the same code.
    `max_candidates` overrides the regeneration budget (the study's
    regeneration-off ablation runs with 1).
    """
    budget = max_candidates if max_candidates is not None else MAX_CANDIDATES
    trail: list[dict] = []

    result = reproduce(capture, service_spec, workdir=workdir)
    repro = {"status": result.status, "reason": result.reason,
             "signature": result.signature, "verdicts": result.verdicts}

    if require_reproduction and not result.reproduced:
        return Outcome("escalated",
                       f"reproduction refused: {result.reason}",
                       reproduction=repro)

    signature = result.signature or f"{capture['exception_type']}@unreproduced"
    prior_failures: list[dict] = []

    for attempt in range(1, budget + 1):
        package = pack(capture, signature, workdir,
                       prior_failures=prior_failures,
                       memory_notes=memory_notes)
        proposal = propose(package, complete=complete)
        candidate = {"attempt": attempt, "diff": proposal["diff"]}

        if require_reproduction:
            verdict = gate(capture, signature, proposal["diff"],
                           service_spec, workdir, suite_path=suite_path)
        else:
            # C2: suite-only verification, honestly recorded as such
            from control_plane.gate import verifier
            tree, err = verifier._apply_patch(workdir, proposal["diff"])
            if tree is None:
                verdict = GateVerdict(False, {"applies": {"passed": False,
                                                          "error": err}})
            else:
                suite = verifier._suite_passes(tree, suite_path)
                verdict = GateVerdict(suite["passed"], {"suite": suite},
                                      patched_tree=tree)
                if suite["passed"]:
                    payload = {"signature": signature, "mode": "ungated",
                               "conditions": {"suite": True}}
                    import json as _json
                    verdict.record = _json.dumps(
                        {"payload": payload, "hmac": verifier._sign(payload)})

        candidate["conditions"] = {k: v.get("passed") for k, v in
                                   verdict.conditions.items()}
        trail.append(candidate)

        if verdict.admitted:
            regression = compile_reproduction(
                capture, service_spec, signature, incident_id) \
                if result.reproduced else None
            return Outcome("deployed",
                           f"admitted on candidate {attempt}",
                           signature=signature, reproduction=repro,
                           candidates=trail,
                           verification_record=verdict.record,
                           patched_tree=verdict.patched_tree,
                           regression_test=regression)

        prior_failures.append({"attempt": attempt,
                               "rejected_diff": proposal["diff"][:4000],
                               "failures": verdict.failure_report()})
        from control_plane.gate.verifier import discard_patched_tree
        discard_patched_tree(verdict.patched_tree)   # rejected: nothing needs it

    return Outcome("escalated",
                   f"no candidate passed the gate in {budget} attempts",
                   signature=signature, reproduction=repro, candidates=trail)
