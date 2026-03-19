"""Allow `python -m cli` to mirror the installed console script behavior.

This is mainly useful during local package development when a developer wants
to exercise the CLI entrypoint without relying on the console-script shim.
"""

from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
