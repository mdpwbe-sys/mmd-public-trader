# MMD — déploiement

Application de trading EVE Online (multi-compte, sparkline, quickbar).

## Données SDE (obligatoire au runtime)
`reference/sde/types.json` (mapping type_id -> nom) N'EST PAS inclus (volumineux ~240MB).
Le placer manuellement depuis une archive SDE tranquility, ou via le script de téléchargement SDE.
Sans ce fichier, la résolution noms->type_id échoue (le reste fonctionne).

## Configuration
- Copier `.env.example` -> `.env` et renseigner CLIENT_ID / CLIENT_SECRET ESI.
- `pip install -r requirements.txt` (pywebview, etc.).
- Lancer `mmd_gui.bat` (Windows) ou `python mmd_gui.py`.

## Neutralité
Ce dépôt est volontairement neutre : aucune référence à des comptes/personnages,
ni aux noms de code historiques (Mmd / Elinor). Les modules `mmd_*` ont été
renommés `mmd_*`.
