import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rc_controller.protocol.profile import Profile, load_profile  # noqa: E402

DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / "rc_controller" / "resources" / "profiles" / "default.toml"


@pytest.fixture(scope="session")
def profile() -> Profile:
    return load_profile(DEFAULT_PROFILE)
