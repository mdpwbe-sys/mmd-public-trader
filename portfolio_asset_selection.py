#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sélection locale transitive d'un conteneur personnel, racine ou imbriqué."""
from collections import defaultdict

import repositories.portfolio_repository as repo


def load_selected(character_id, container_item_id):
    bundle = repo.load_latest_assets(character_id, None)
    items = bundle["items"]
    if container_item_id is None:
        return bundle
    target = int(container_item_id)
    children = defaultdict(list)
    known = set()
    for row in items:
        item_id = int(row["item_id"])
        known.add(item_id)
        if row.get("parent_item_id") is not None:
            children[int(row["parent_item_id"])].append(item_id)
    if target not in known:
        return {"snapshot": bundle["snapshot"], "items": []}
    selected, stack = {target}, [target]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in selected:
                selected.add(child)
                stack.append(child)
    return {"snapshot": bundle["snapshot"],
            "items": [row for row in items if int(row["item_id"]) in selected]}
