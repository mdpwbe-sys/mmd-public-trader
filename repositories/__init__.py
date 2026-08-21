#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repositories - couche d'acces unique a SQLite (app_data.db).

Tous les modules GUI/ESI/Watchdog passent par ici. Aucun SQL disperse ailleurs.
Connexions courtes/dediees par thread (database.get_connection).
"""
