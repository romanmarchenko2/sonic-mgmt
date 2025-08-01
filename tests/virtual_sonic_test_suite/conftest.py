import pytest

from tests.common.config_reload import config_reload

@pytest.fixture()
def restore_topology_on_failure(duthosts, rand_one_dut_hostname, request):

    yield

    duthost = duthosts[rand_one_dut_hostname]
    if request.node.rep_call.failed:
        config_reload(duthost, config_source="minigraph", safe_reload=True)

