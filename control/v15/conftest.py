"""Make a fresh clone's plain `pytest` run self-contained."""
from build_synthetic_db import build


def pytest_sessionstart(session):
    build()
