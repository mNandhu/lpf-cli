import os
import tempfile

# Point lpf at a throwaway config dir before any lpf module is imported, so
# tests never touch the real tunnels in ~/.config/lpf.
TEST_CONFIG_DIR = tempfile.mkdtemp(prefix="lpf-test-")
os.environ["LPF_CONFIG_DIR"] = TEST_CONFIG_DIR
os.environ["LPF_NO_UPDATE_CHECK"] = "1"
# Wide enough that Rich doesn't wrap the messages tests look for.
os.environ["COLUMNS"] = "250"

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from lpf import config  # noqa: E402
from lpf.utils import ensure_config_dirs  # noqa: E402


@pytest.fixture(autouse=True)
def clean_config_dir():
    assert config.CONFIG_DIR == Path(TEST_CONFIG_DIR)
    shutil.rmtree(config.CONFIG_DIR, ignore_errors=True)
    ensure_config_dirs()
    yield
