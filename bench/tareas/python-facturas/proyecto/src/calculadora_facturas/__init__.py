import sys


def main() -> None:
    from calculadora_facturas.cli import main as cli_main

    sys.exit(cli_main())
