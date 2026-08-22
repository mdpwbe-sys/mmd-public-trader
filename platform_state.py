"""Chemins de donnees persistants (hors du dossier d'extraction PyInstaller).

En onefile exe, __file__ pointe dans le dossier temp _MEIxxxx -> tout ce qui
est ecrit la est PERDU a la fermeture. On redirige la base, le .env et les
logs vers un dossier stable : %APPDATA%/MMD-Trader (Win) ou ~/.mmd-trader.
"""
import os
import sys


def state_dir():
    """Dossier de donnees persistant (cree si besoin)."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "MMD-Trader")
    else:
        d = os.path.expanduser("~/.mmd-trader")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def state_path(*parts):
    """Joint des chemins relatifs au dossier persistant."""
    return os.path.join(state_dir(), *parts)
