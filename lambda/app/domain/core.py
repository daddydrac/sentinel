"""Compatibility facade for the modular agent runtime."""

from domain.model import append_trace, canonical_hash, load_scenarios, scenario_summaries, utc_now
from domain.planning import build_plan
from domain.policy import READ_ONLY_TOOLS, REVERSIBLE_TOOLS, decide_policy
from domain.retrieval import retrieve, tokenize
from domain.tools import compensate_simulated, execute_simulated, verify_simulated

__all__ = [
    "READ_ONLY_TOOLS",
    "REVERSIBLE_TOOLS",
    "append_trace",
    "build_plan",
    "canonical_hash",
    "compensate_simulated",
    "decide_policy",
    "execute_simulated",
    "load_scenarios",
    "retrieve",
    "scenario_summaries",
    "tokenize",
    "utc_now",
    "verify_simulated",
]
