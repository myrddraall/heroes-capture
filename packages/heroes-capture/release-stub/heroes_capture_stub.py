"""A stand-in heroes-capture.exe for testing the release pipeline (PLAN.md, stage 4): it only
prints its version. The real command replaces it."""

import sys

from _version import VERSION


def main() -> None:
    if sys.argv[1:] == ["--version"]:
        print(VERSION)
        return
    print(f"heroes-capture {VERSION}: a stand-in for testing the release pipeline; it does nothing yet.")


if __name__ == "__main__":
    main()
