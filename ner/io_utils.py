from contextlib import contextmanager
from pathlib import Path
import tempfile


@contextmanager
def atomic_output(destination):
    """Replace a destination only after a complete output has been written."""
    destination = Path(destination).expanduser()
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=destination.suffix,
                                     delete=False) as handle:
        temporary = Path(handle.name)
    try:
        yield temporary
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
