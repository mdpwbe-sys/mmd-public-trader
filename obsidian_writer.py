#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
obsidian_writer.py - ecriture centralisee dans le vault Obsidian (memoire lisible).

Regles:
- SQLite = source de verite (detail complet). Obsidian = vues derivees.
- Ecritures atomiques: fichier temporaire -> flush -> remplacement atomique.
- Une panne Obsidian NE DOIT JAMAIS annuler/corrompre une transaction SQLite
  deja valide (on appelle le writer APRES commit, en best-effort).
- Aucun secret (token/secret) dans les notes.
- Structure TradingVault/ avec dossiers par domaine + _System/indexes.
"""
import os
import json
import time
import tempfile

# Vault racine: configurable via env, defaut local (aucun chemin prive embarque).
VAULT_ROOT = os.environ.get(
    "TRADING_VAULT",
    os.path.join(os.path.expanduser("~"), "MMD-Trader", "TradingVault"))

SUBDIRS = [
    "00_Inbox", "01_Dashboard", "10_Characters", "20_Items", "30_Locations",
    "40_Orders", "50_Market_Snapshots", "60_Trade_Decisions", "70_Sessions",
    "80_Agent_Memory", "80_Agent_Memory/Validated_Rules",
    "80_Agent_Memory/Proposed_Rules", "90_Reports", "_System", "_System/schemas",
    "_System/indexes", "_System/prompts", "_System/archive",
]


def _ensure_vault():
    for d in SUBDIRS:
        try:
            os.makedirs(os.path.join(VAULT_ROOT, d), exist_ok=True)
        except Exception:
            pass


def _atomic_write(path, content):
    """Ecrit atomiquement: tmp -> flush -> os.replace."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _frontmatter(meta):
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}")
    lines.append("---")
    return "\n".join(lines)


class ObsidianMemoryWriter:
    def __init__(self, vault_root=None):
        self.root = vault_root or VAULT_ROOT
        _ensure_vault()

    # --- ecriture bas niveau ---
    def write_note(self, subdir, filename, body, frontmatter=None):
        path = os.path.join(self.root, subdir, filename)
        content = (_frontmatter(frontmatter) + "\n\n" if frontmatter else "") + body
        _atomic_write(path, content)
        return path

    # --- evenements importants ---
    def record_event(self, event):
        """Evenement important -> 00_Inbox (journal)."""
        ts = event.get("timestamp") or _now_iso()
        line = f"- `{ts}` — {event.get('kind','event')}: {event.get('message','')}"
        path = os.path.join(self.root, "00_Inbox", "events.md")
        self._append_lines(path, [line])

    def _append_lines(self, path, lines):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        existing = ""
        if os.path.exists(path):
            existing = open(path, encoding="utf-8").read().rstrip() + "\n"
        _atomic_write(path, existing + "\n".join(lines) + "\n")

    # --- resume personnage ---
    def update_character_summary(self, character_id, data):
        fm = {
            "type": "character", "character_id": character_id,
            "name": data.get("character_name"), "updated_at": _now_iso(),
            "confidence": "verified", "source_type": "sqlite",
        }
        body = (f"# {data.get('character_name', character_id)}\n\n"
                f"## Faits verifies\n"
                f"- character_id: {character_id}\n"
                f"- corporation_id: {data.get('corporation_id')}\n"
                f"- ordres actifs: {data.get('order_count', '?')}\n\n"
                f"## Hypotheses\n(none)\n\n## Decisions utilisateur\n(none)\n\n"
                f"## A verifier\n- standings Jita = bruts Caldari State / Caldari Navy\n")
        self.write_note("10_Characters", f"character_{character_id}.md", body, fm)

    # --- note ordre ---
    def update_order_note(self, order):
        oid = order.get("order_id")
        fm = {"type": "order", "order_id": oid, "type_id": order.get("type_id"),
              "character_id": order.get("character_id"),
              "updated_at": _now_iso(), "confidence": "verified"}
        body = (f"# Order {oid}\n\n"
                f"- type_id: {order.get('type_id')}\n"
                f"- side: {'BUY' if order.get('is_buy_order') else 'SELL'}\n"
                f"- price: {order.get('price_cents',0)/100.0:.2f} ISK\n"
                f"- location: {order.get('location_id')}\n"
                f"- volume_remain: {order.get('volume_remain')}\n")
        self.write_note("40_Orders", f"order_{oid}.md", body, fm)

    # --- rapport quotidien ---
    def write_daily_report(self, date, summary):
        fm = {"type": "daily_report", "date": date, "updated_at": _now_iso()}
        body = f"# Rapport quotidien {date}\n\n" + summary
        self.write_note("90_Reports", f"daily_{date}.md", body, fm)

    # --- rapport hebdo ---
    def write_weekly_report(self, week, summary):
        fm = {"type": "weekly_report", "week": week, "updated_at": _now_iso()}
        body = f"# Rapport hebdomadaire {week}\n\n" + summary
        self.write_note("90_Reports", f"weekly_{week}.md", body, fm)

    # --- proposition memoire agent ---
    def propose_agent_memory(self, proposal):
        """Proposition de regle -> 80_Agent_Memory/Proposed_Rules (jamais validee auto)."""
        name = proposal.get("name", "proposal") + ".md"
        fm = {"type": "proposed_rule", "confidence": "inferred",
              "updated_at": _now_iso(),
              "provenance": proposal.get("provenance", "agent_inference")}
        body = (f"# {proposal.get('name','Proposition')}\n\n"
                f"## Faits verifie\n{proposal.get('facts','')}\n\n"
                f"## Hypothese\n{proposal.get('hypothesis','')}\n\n"
                f"## Decision utilisateur\n(en attente)\n\n## A verifier\n"
                f"- seuil statistique avant validation\n")
        self.write_note("80_Agent_Memory/Proposed_Rules", name, body, fm)

    # --- memoire de session / travail ---
    def write_session_memory(self, content):
        fm = {"type": "session_memory", "updated_at": _now_iso()}
        self.write_note("80_Agent_Memory", "current_session.md", content, fm)

    def write_working_memory(self, content):
        fm = {"type": "working_memory", "updated_at": _now_iso()}
        self.write_note("80_Agent_Memory", "working_memory.md", content, fm)

    # --- index leger pour l agent ---
    def write_agent_indexes(self, indexes):
        """indexes: dict nom_fichier -> dict donnees. Vues derivees regenerables."""
        d = os.path.join(self.root, "_System", "indexes")
        os.makedirs(d, exist_ok=True)
        for name, data in indexes.items():
            _atomic_write(os.path.join(d, name),
                          json.dumps(data, indent=2, ensure_ascii=False))


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    w = ObsidianMemoryWriter()
    w.write_session_memory("# Session\n- personnage actif: CHARACTER_THREE\n- station cible: Jita 4-4\n")
    w.write_working_memory("# Working Memory\n- CHARACTER_THREE = ventes Jita\n"
                            "- standings Jita = bruts Caldari State/Caldari Navy\n"
                            "- Perimeter owner fee = 0%\n- gap 0 ISK = TIED (pas UPDATE_REQUIRED)\n")
    w.write_agent_indexes({"active_orders.json": {"generated_at": _now_iso(),
                                                  "count": 0, "note": "vue derivee"}})
    print("Obsidian writer OK ->", VAULT_ROOT)
