"""Read-only New Eden map access for pywebview and offline tooling."""
import json
import math
from collections import deque
from pathlib import Path


HIGHSEC_THRESHOLD = 0.45


class EveMapService:
    def __init__(self, dataset_path=None):
        self.dataset_path = Path(dataset_path or Path(__file__).parent / "gui" / "data" / "eve_map.json")
        self._dataset = None
        self._systems = None
        self._adjacency = None

    def _load(self):
        if self._dataset is None:
            self._dataset = json.loads(self.dataset_path.read_text(encoding="utf-8"))
            self._systems = {int(system["id"]): system for system in self._dataset.get("systems", [])}
            self._adjacency = {system_id: set() for system_id in self._systems}
            for gate in self._dataset.get("gates", []):
                source, target = int(gate["source"]), int(gate["target"])
                if source in self._adjacency and target in self._adjacency:
                    self._adjacency[source].add(target)
                    self._adjacency[target].add(source)

    def get_map_data(self):
        self._load()
        return self._dataset

    def get_system(self, system_id):
        self._load()
        return self._systems.get(int(system_id))

    def distance_m(self, source_id, target_id):
        source, target = self.get_system(source_id), self.get_system(target_id)
        if not source or not target:
            return None
        return math.dist(tuple(source["position_m"].values()), tuple(target["position_m"].values()))

    @staticmethod
    def security_class(security_status):
        """Match EVE's displayed security band from the raw SDE/ESI value."""
        security_status = float(security_status or 0)
        return "high" if security_status >= HIGHSEC_THRESHOLD else "low" if security_status > 0 else "null"

    def find_route(self, source_id, target_id, min_security=None):
        self._load()
        source_id, target_id = int(source_id), int(target_id)
        if source_id not in self._systems or target_id not in self._systems:
            return {"error": "unknown_system", "systems": [], "jumps": 0}
        high_sec_only = min_security == "high"
        if min_security is not None:
            if high_sec_only:
                is_allowed = lambda system_id: self.security_class(self._systems[system_id].get("security", 0)) == "high"
            else:
                minimum = float(min_security)
                is_allowed = lambda system_id: float(self._systems[system_id].get("security", 0)) >= minimum
            if any(not is_allowed(system_id) for system_id in (source_id, target_id)):
                return {"error": "unsafe_endpoint", "systems": [], "jumps": 0}
        queue, previous = deque([source_id]), {source_id: None}
        while queue:
            current = queue.popleft()
            if current == target_id:
                route = []
                while current is not None:
                    route.append(current)
                    current = previous[current]
                route.reverse()
                return {"systems": route, "jumps": len(route) - 1}
            for neighbor in sorted(self._adjacency[current]):
                if min_security is not None and not is_allowed(neighbor):
                    continue
                if neighbor not in previous:
                    previous[neighbor] = current
                    queue.append(neighbor)
        return {"error": "unreachable", "systems": [], "jumps": 0}


_default_service = EveMapService()


def get_map_data():
    return _default_service.get_map_data()


def find_route(source_id, target_id, min_security=None):
    return _default_service.find_route(source_id, target_id, min_security)
