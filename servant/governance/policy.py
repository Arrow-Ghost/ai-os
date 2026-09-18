"""
Tier resolution: given a tool and the brain's confidence, what do we do?

This is deliberately boring and readable. When a judge (or your future self)
asks "why was the agent allowed to do that?", the answer is one function.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import Tier, ToolSpec

AUTO, APPROVE, BLOCK = "auto", "approve", "block"


@dataclass
class Verdict:
    action: str          # auto | approve | block
    tier: Tier
    reason: str = ""


class PolicyEngine:
    def __init__(self, config):
        gov = config.get("governance", {}) or {}
        self.tier_policy: dict[str, str] = gov.get("tier_policy", {})
        self.tier_overrides: dict[str, str] = gov.get("tier_overrides", {})
        self.denylist: set[str] = set(gov.get("denylist", []))
        self.confidence_floor: float = float(gov.get("confidence_floor", 0.0))

    def resolve_tier(self, spec: ToolSpec) -> Tier:
        """Owner override always beats the feature author's declaration."""
        override = self.tier_overrides.get(spec.name)
        return Tier(override) if override else spec.tier

    def evaluate(self, spec: ToolSpec, confidence: float | None = None) -> Verdict:
        tier = self.resolve_tier(spec)

        if spec.name in self.denylist:
            return Verdict(BLOCK, tier, f"'{spec.name}' is on the denylist")

        action = self.tier_policy.get(tier.value, APPROVE)

        if action == AUTO and confidence is not None and confidence < self.confidence_floor:
            return Verdict(
                APPROVE, tier,
                f"confidence {confidence:.0%} below floor {self.confidence_floor:.0%}",
            )

        reasons = {
            AUTO: f"tier '{tier.value}' runs automatically",
            APPROVE: f"tier '{tier.value}' requires a human",
            BLOCK: f"tier '{tier.value}' is blocked by policy",
        }
        return Verdict(action, tier, reasons.get(action, "unknown policy action"))
