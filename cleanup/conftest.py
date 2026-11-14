"""
Pytest fixtures rooted inside the workspace.

The execution sandbox blocks writes to the system temp directory, so
tmp paths are created under ``.test_tmp`` next to the repo root and
removed at teardown.
"""

import os
import shutil
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_ROOT = os.path.join(ROOT, ".test_tmp")


@pytest.fixture()
def tmp_dir():
    if not os.path.isdir(TMP_ROOT):
        os.makedirs(TMP_ROOT)
    path = os.path.join(TMP_ROOT, uuid.uuid4().hex)
    os.makedirs(path)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def fixture_dir(tmp_dir):
    from cleanup.fixture import build_fixture

    return build_fixture(os.path.join(tmp_dir, "data"))
