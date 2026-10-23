import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def closed_port() -> int:
    """Return a localhost port that nothing is listening on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def listening_port() -> Iterator[int]:
    """Yield a localhost port that accepts connections until the block ends."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        yield listener.getsockname()[1]
