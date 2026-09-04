# Changelog — EVE Market Manager (MMD)

Tous les changements notables sont documentés ici. Le format suit les
principes de [Keep a Changelog](https://keepachangelog.com/), le versionnage
est en [SemVer](https://semver.org/).

## [Unreleased]

### Corrigé
- **New Eden security** : les valeurs brutes SDE/ESI à partir de `0.45` sont
  maintenant classées high-sec, conformément à l'affichage en jeu. Le routage
  high-sec utilise la même règle.
- **Combat intel** : R2Z2 live et l'historique zKill de système/constellation/
  région se complètent par `killmail_id`; aucun fan-out par système pour une
  sélection de zone.
- **Combat markers** : les alertes tactiques restent visibles 30 minutes (au
  lieu de cinq), avec une rétention locale de combat de 60 minutes.
- **Map cache** : le cache live de la carte est désormais écrit sous
  `%APPDATA%/MMD-Trader/cache` au lieu du répertoire historique Evernus.

## [0.1.2] — 2026-08-09

### Corrigé (stabilisation post-0.1.1)
- **Synchronisation** : élimination du yoyo global des ordres à mettre à jour
  (`ext_by_type` reconstruit à chaque scan + ESI `None`→`[]` sans distinction).
  Fallback 24h sur le dernier livre valide, fusion uniquement des types échoués,
  boot unique, cache JSON atomique + réhydratation (`d040fd1`).
- **Raccourcis clavier** : Alt+Shift+F / Ctrl+Shift+F ne meurent plus (listener
  démarré après le chargement du WebView, exception isolée, bridge explicite,
  handler en phase capture). Import multi-personnages : le Refresh ne supprime
  plus les autres persos (`fb67f83`).
- **Son de navigation** : click aigu (suivant) / click grave (précédent) sur
  les raccourcis, Web Audio API, failure-safe (`e4b9eb7`).
- **Volume son** : gain doublé (0.035 → 0.070) sans complexifier les Settings
  (`bfad01d`).
- **Yoyo du 3e personnage** : union monotone des snapshots (disque + mémoire),
  perso connu jamais supprimé par un Refresh incomplet, RLock, `move_window`
  pywebview × scale 1.5 supprimé (`6abce53`, `3347e6e`).
- **ResourceWarning** : tous les `open()` ESI/fichiers via context manager
  (`with`), zéro fuite de descripteur (`d7639a3`).
- **Saut de fenêtre au fermeture des popups** : thread de drag arrêté proprement
  (flag `_DRAG_STOP`), garde couvrant tous les overlays, topmost centralisé,
  alias `set_topmost` réparé (`7a38753`).
- **Double drag de fenêtre** : `easy_drag` pywebview (`WIN.move` × 1.5 = saut
  200px) désactivé explicitement (`easy_drag=False`). Un seul drag (GetCursorPos
  1:1). Pin réparé (HWND 64-bit, état mémorisé après succès Win32) (`6d96cf9`).
- **Suivi « Orders to Update » fiable + lisible** : fin du double comptage
  (recalcul live vs compteur backend écrivant la même clé `char_<perso>`).
  Le backend produit `orders_to_update_by_char` une seule fois ; l'interface
  ingère la map une fois par sync ; `updateDynamicMetrics` est readonly.
  `Tous` = somme des derniers compteurs connus (ou `–` si perso non fiable).
  Pagination ESI privée atomique (succès / échec partiel / échec total
  distingués). Sparkline : ligne + points réels, grille Y chiffrée, dernier
  point `17 (14:32)`, infobulle date+source (`3347e6e`).

### Tests
- `tests_db.py` 28/28 · `test_ticks.py` 5/5 · `test_sync_stability.py` 3/3 ·
  `test_regression_d040.py` 3/3 · `test_yoyo_char3.py` 1/1 ·
  `test_window_stability.py` 5/5 · `test_orders_to_update.py` 9/9 ·
  `test_popup_stability.js` / `test_keyboard_navigation.js` OK.
- Aucune régression sur `evernus_margin.py` (moteur de marge) ni `fifo_overtaken`.

## [0.1.1] — précédente release étiquetée
- Base de calcul de marge validée (ticks, station matching, plancher 100 ISK,
  scaling physique). Référence pour la branche de correctifs 0.1.2.

[0.1.2]: #012
[0.1.1]: #011
