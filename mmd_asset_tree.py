#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolution generique de la hierarchie ``item_id -> location_id`` ESI."""
from collections import defaultdict


def asset_name_map(names):
    """Accepte les lignes ESI ``[{item_id, name}]`` ou un dict equivalent."""
    if isinstance(names, dict):
        source = names.items()
    else:
        source = ((row.get("item_id"), row.get("name")) for row in (names or []))
    out = {}
    for item_id, name in source:
        try:
            iid = int(item_id)
        except (TypeError, ValueError):
            continue
        clean = str(name or "").strip()
        if iid > 0 and clean:
            out[iid] = clean
    return out


class AssetGraph:
    """Index immutable en pratique, avec parcours proteges contre les cycles."""

    def __init__(self, assets, names=None):
        self.by_id = {}
        self.duplicates = set()
        for raw in assets or []:
            row = dict(raw)
            try:
                item_id = int(row["item_id"])
                row["location_id"] = int(row["location_id"])
                row["type_id"] = int(row["type_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Asset ESI invalide") from exc
            if item_id <= 0:
                raise ValueError("item_id asset invalide")
            if item_id in self.by_id:
                self.duplicates.add(item_id)
            row["item_id"] = item_id
            self.by_id[item_id] = row
        self.names = asset_name_map(names)
        self.parent = {}
        self.children = defaultdict(set)
        self.orphans = set()
        for item_id, row in self.by_id.items():
            if row.get("location_type") != "item":
                continue
            parent_id = row["location_id"]
            if parent_id in self.by_id:
                self.parent[item_id] = parent_id
                self.children[parent_id].add(item_id)
            else:
                self.orphans.add(item_id)
        self._root = {}
        self.cycles = []
        self.cyclic_ids = set()
        self._resolve_roots()

    def _resolve_roots(self):
        seen_cycles = set()
        for start in self.by_id:
            if start in self._root:
                continue
            chain = []
            positions = {}
            current = start
            root = None
            while True:
                if current in self._root:
                    root = self._root[current]
                    break
                if current in positions:
                    cycle = chain[positions[current]:]
                    signature = tuple(sorted(cycle))
                    if signature not in seen_cycles:
                        seen_cycles.add(signature)
                        self.cycles.append(signature)
                    self.cyclic_ids.update(cycle)
                    root = None
                    break
                positions[current] = len(chain)
                chain.append(current)
                parent_id = self.parent.get(current)
                if parent_id is None:
                    root = current
                    break
                current = parent_id
            for item_id in chain:
                self._root[item_id] = root

    def root_id(self, item_id):
        return self._root.get(int(item_id))

    def depth(self, item_id):
        current = int(item_id)
        visited = set()
        depth = 0
        while current in self.parent and current not in visited:
            visited.add(current)
            current = self.parent[current]
            depth += 1
        return depth

    def descendant_ids(self, container_id, include_container=False):
        """Retourne les descendants transitifs, une seule fois, ordre stable."""
        container_id = int(container_id)
        if container_id not in self.by_id:
            raise KeyError(container_id)
        found = {container_id} if include_container else set()
        visited = {container_id}
        stack = list(self.children.get(container_id, ()))
        while stack:
            item_id = stack.pop()
            if item_id in visited:
                continue
            visited.add(item_id)
            found.add(item_id)
            stack.extend(self.children.get(item_id, ()))
        return sorted(found)

    def assets_in(self, container_id, include_container=False):
        return [self.by_id[item_id] for item_id in self.descendant_ids(
            container_id, include_container=include_container)]

    def container_ids(self, top_level_only=True, type_ids=None):
        """Un conteneur est generiquement tout asset reference comme parent."""
        allowed = {int(type_id) for type_id in type_ids} if type_ids else None
        ids = []
        for item_id, children in self.children.items():
            if not children or item_id not in self.by_id:
                continue
            if top_level_only and item_id in self.parent:
                continue
            if allowed is not None and self.by_id[item_id]["type_id"] not in allowed:
                continue
            ids.append(item_id)
        return sorted(ids)

    def container_options(self, names=None, top_level_only=True, type_ids=None):
        labels = dict(self.names)
        labels.update(asset_name_map(names))
        options = []
        for item_id in self.container_ids(top_level_only, type_ids):
            row = self.by_id[item_id]
            root_id = self.root_id(item_id)
            root = self.by_id.get(root_id) if root_id is not None else None
            name = labels.get(item_id)
            options.append({
                "item_id": item_id,
                "type_id": row["type_id"],
                "name": name,
                "label": name or f"Type {row['type_id']} · {item_id}",
                "location_id": row["location_id"],
                "location_type": row.get("location_type"),
                "root_location_id": root.get("location_id") if root else None,
                "root_location_type": root.get("location_type") if root else None,
                "depth": self.depth(item_id),
                "descendant_count": len(self.descendant_ids(item_id)),
                "orphan": item_id in self.orphans,
                "cyclic": item_id in self.cyclic_ids,
            })
        return sorted(options, key=lambda row: (row["label"].casefold(), row["item_id"]))

    def diagnostics(self):
        return {
            "asset_count": len(self.by_id),
            "container_count": len(self.children),
            "duplicates": sorted(self.duplicates),
            "orphans": sorted(self.orphans),
            "cycles": [list(cycle) for cycle in self.cycles],
        }


def build_asset_tree(assets, names=None):
    return AssetGraph(assets, names=names)
