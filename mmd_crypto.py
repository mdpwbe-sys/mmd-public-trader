#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mmd_crypto.py - chiffrement du cache SSO (refresh tokens) sans dépendance
externe lourde. Utilise 'cryptography' (deja present).

La clef Fernet est derivee de l'empreinte machine via PBKDF2 avec un sel
aleatoire propre a l'installation. Le sel n'est pas secret, mais evite qu'une
meme empreinte produise la meme clef sur toutes les installations.
"""
import os, base64, uuid, getpass
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

HERE = os.path.dirname(os.path.abspath(__file__))
_SALT_FILE = os.path.join(HERE, ".env.cache.salt")
_LEGACY_SALT = b"MmdOrderManager-Salt-v1"
_cache_key = None


def _machine_identity():
    try:
        user = os.getlogin()
    except OSError:
        user = getpass.getuser()
    return f"{uuid.getnode()}-{user}".encode()


def _derive_key(salt, iterations=200_000):
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return base64.urlsafe_b64encode(kdf.derive(_machine_identity()))


def _load_or_create_salt():
    try:
        with open(_SALT_FILE, "rb") as f:
            salt = f.read()
        if len(salt) < 16:
            raise ValueError("sel SSO local invalide")
        return salt
    except FileNotFoundError:
        salt = os.urandom(32)
        try:
            fd = os.open(_SALT_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(salt)
                f.flush()
                os.fsync(f.fileno())
            return salt
        except FileExistsError:
            with open(_SALT_FILE, "rb") as f:
                return f.read()


def _key():
    global _cache_key
    if _cache_key is None:
        _cache_key = _derive_key(_load_or_create_salt())
    return _cache_key


def _decrypt_with_status(token):
    try:
        return Fernet(_key()).decrypt(token.encode()).decode(), False
    except InvalidToken:
        # Migration transparente des caches crees avant le sel aleatoire.
        legacy_key = _derive_key(_LEGACY_SALT, iterations=100_000)
        return Fernet(legacy_key).decrypt(token.encode()).decode(), True


def encrypt_text(plain: str) -> str:
    return Fernet(_key()).encrypt(plain.encode()).decode()


def decrypt_text(token: str) -> str:
    return _decrypt_with_status(token)[0]


def encrypt_json(obj) -> str:
    return encrypt_text(__import__("json").dumps(obj))


def decrypt_json_with_status(blob: str):
    plain, used_legacy_key = _decrypt_with_status(blob)
    return __import__("json").loads(plain), used_legacy_key


def decrypt_json(blob: str):
    return decrypt_json_with_status(blob)[0]
