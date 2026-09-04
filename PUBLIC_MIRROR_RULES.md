# Public mirror rules

`mmd-public-trader` is the distributable public mirror. Its public runtime
identity is an invariant and must be preserved whenever New Eden changes are
synchronized from the private workspace.

## Public invariants

- Keep the public entry points `mmd_gui.py` and `mmd_gui.bat`.
- Keep the OAuth callback at `http://127.0.0.1:8766/callback` in runtime,
  `.env.example`, and documentation.
- Keep public module names (`mmd_*`), `platform_state.state_path()`, and
  persistent state below `%APPDATA%/MMD-Trader`.
- Keep user agents and repository links pointed at
  `https://github.com/mdpwbe-sys/mmd-public-trader`.
- Keep documentation neutral: no private paths, personal configuration, or
  private module names.
- Preserve the existing public CHANGELOG history. Add new notes only under
  `[Unreleased]`.

## New Eden synchronization boundary

New Eden logic, rendering, assets, and tests may be synchronized. Do not copy
README.md, CHANGELOG.md, `.env.example`, SSO files, GUI bootstrap files, OAuth
callbacks, user agents, storage paths, or environment-specific imports without
an explicit public adaptation. `eve_map_runtime.py` is the only environment
adapter used by New Eden modules.

## Required pre-commit checks

Run these checks while on `main` before every public commit:

```bash
git branch --show-current
git remote -v
git status --short
git diff --check
git grep -n "evernus_" -- README.md CHANGELOG.md eve_map*.py gui/
git grep -n "8765" -- README.md .env.example mmd_sso.py
git grep -n "mdpwbe-sys/mmd)" -- eve_map*.py
```

The first grep must have no matches outside documented historical context; the
second and third must have no matches. Compare README scopes directly with the
`SCOPES` list in `mmd_sso.py`.
