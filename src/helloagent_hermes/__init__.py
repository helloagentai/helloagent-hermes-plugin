"""HelloAgent platform plugin for Hermes Agent."""

__version__ = "0.1.0"


def register(ctx):
    """Hermes plugin entry point.

    Keep this import lazy so package metadata discovery does not require
    Hermes modules to be importable outside a Hermes environment.
    """
    from .adapter import register as _register

    return _register(ctx)

__all__ = ["register"]
