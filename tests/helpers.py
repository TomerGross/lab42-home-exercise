import socket


def closed_port() -> int:
    """Return a localhost port that nothing is listening on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
