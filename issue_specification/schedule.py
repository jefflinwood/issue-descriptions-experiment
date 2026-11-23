"""Seeded randomized complete-block schedule."""
from __future__ import annotations

import random


def cells(config):
    return [(c["id"], d) for c in config["configurations"] for d in sorted(config["descriptions"])]


def attempt_id(index, block, config_id, description):
    return f"{index:03d}-b{block}-{config_id}-{description.lower()}"


def build_schedule(config):
    """Each block contains every configuration×description cell exactly once."""
    rng = random.Random(config["seed"])
    plan = []
    for block in range(1, config["blocks"] + 1):
        order = cells(config)
        rng.shuffle(order)
        for position, (config_id, description) in enumerate(order, start=1):
            index = len(plan)
            plan.append(dict(index=index, block=block, position=position, configuration=config_id,
                             description=description,
                             attempt_id=attempt_id(index, block, config_id, description)))
    return plan
