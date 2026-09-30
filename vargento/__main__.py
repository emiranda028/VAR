"""Arranca el servidor: python -m vargento [--host 0.0.0.0] [--port 8000]"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor VARgento")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("vargento.api:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
