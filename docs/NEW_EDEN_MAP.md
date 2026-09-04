# New Eden 3D Map

The **New Eden** title-bar action opens a self-contained topology view. The SDE map and routing work offline; optional overlays use public ESI plus zKillboard/R2Z2 only while the map is open. It does not read corporation data.

Raw security status follows the in-game display class: high-sec starts at `0.45` (displayed as `0.5`), low-sec is above `0` and below `0.45`, and null-sec is `0` or below. The **High-sec route** mode uses that same class rather than a separate numeric threshold.

R2Z2 markers remain visible on the map for 30 minutes and fade with age. The stream retains recent combat for one hour; selected systems, constellations and regions supplement this live window with one lazy, cached zKillboard query at the selected scope.

`gui/data/eve_map.json` is built from CCP's official JSONL SDE. `position_m` retains CCP's original metre coordinates; `position` is only the normalized visual projection. The browser consumes the latter while route and distance tools retain the former.

To refresh the bundled dataset after an SDE release:

```powershell
python tools/build_eve_map.py --output gui/data/eve_map.json
```

The builder resolves the current official build from CCP's `latest.jsonl` manifest. To use an already downloaded archive, pass `--archive path\\to\\sde.zip`.

The Python seam is `EveMapService`: `get_map_data()`, `get_system(id)`, `find_route(source, target)` and `distance_m(source, target)`. `evernus_gui.Api` merely exposes read-only map data and breadth-first stargate routes to pywebview.
