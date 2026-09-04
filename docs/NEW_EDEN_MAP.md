# New Eden 3D Map

The **New Eden** title-bar action opens a self-contained, offline topology view. It never calls ESI, changes OAuth scopes, or reads corporation data.

`gui/data/eve_map.json` is built from CCP's official JSONL SDE. `position_m` retains CCP's original metre coordinates; `position` is only the normalized visual projection. The browser consumes the latter while route and distance tools retain the former.

To refresh the bundled dataset after an SDE release:

```powershell
python tools/build_eve_map.py --output gui/data/eve_map.json
```

The builder resolves the current official build from CCP's `latest.jsonl` manifest. To use an already downloaded archive, pass `--archive path\\to\\sde.zip`.

The Python seam is `EveMapService`: `get_map_data()`, `get_system(id)`, `find_route(source, target)` and `distance_m(source, target)`. `evernus_gui.Api` merely exposes read-only map data and breadth-first stargate routes to pywebview.
