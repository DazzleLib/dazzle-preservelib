"""Allow running as: python -m dazzle_preservelib

dazzle-preservelib is a LIBRARY, not a CLI -- the `preserve` command lives in the
DazzleTools/preserve tool (STACK-MAP P3). Running the module just reports the
installed version.
"""
from . import __app_name__, __version__


def main() -> None:
    print(f"{__app_name__} {__version__}")
    print("Library only -- the preserve CLI lives in DazzleTools/preserve.")


if __name__ == "__main__":
    main()
