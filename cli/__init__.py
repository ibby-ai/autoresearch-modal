"""Developer-facing CLI package for `autoresearch-modal`.

The package is intentionally small:

- `main.py` defines the public argparse surface
- `commands.py` resolves that surface into Modal command plans
- `__main__.py` makes `python -m cli` behave like the installed console script
"""
