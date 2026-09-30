"""Gestión de usuarios por consola (por ejemplo, si se pierde la clave del admin).

    python -m vargento.users crear USUARIO ROL      (ROL: periodista, editor, admin)
    python -m vargento.users clave USUARIO
    python -m vargento.users listar
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import auth
from .db import Database


def _ask_password() -> str:
    password = getpass.getpass("Contraseña nueva: ")
    if password != getpass.getpass("Repetir: "):
        sys.exit("Las contraseñas no coinciden.")
    try:
        auth.validate_password(password)
    except ValueError as exc:
        sys.exit(str(exc))
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description="Usuarios de VARgento")
    parser.add_argument("--db", default="vargento.db")
    sub = parser.add_subparsers(dest="cmd", required=True)
    crear = sub.add_parser("crear")
    crear.add_argument("usuario")
    crear.add_argument("rol", choices=sorted(auth.ROLES))
    clave = sub.add_parser("clave")
    clave.add_argument("usuario")
    sub.add_parser("listar")
    args = parser.parse_args()

    db = Database(args.db)
    if args.cmd == "listar":
        for u in db.list_users():
            estado = "activo" if u["active"] else "inactivo"
            print(f"{u['id']:>3}  {u['username']:<20} {u['role']:<10} {estado}")
    elif args.cmd == "crear":
        u = db.add_user(args.usuario, auth.hash_password(_ask_password()), args.rol)
        print(f"Usuario {u['username']} creado con rol {u['role']}.")
    else:
        cred = db.get_user_credentials(args.usuario)
        if not cred:
            sys.exit("Usuario no encontrado.")
        db.update_user(cred["id"], password_hash=auth.hash_password(_ask_password()), active=1)
        print("Contraseña actualizada (y usuario reactivado).")


if __name__ == "__main__":
    main()
