import pytest

from kubelens.tools import ReadOnlyKube
from tests.fakes import FakeApps, FakeCore


@pytest.fixture
def core() -> FakeCore:
    return FakeCore()


@pytest.fixture
def kube(core: FakeCore) -> ReadOnlyKube:
    return ReadOnlyKube(core, FakeApps())
