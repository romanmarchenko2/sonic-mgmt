"""
Test the feature of container_checker
"""
import time
import logging

import pytest

from pkg_resources import parse_version
from tests.common import config_reload
from tests.common.helpers.assertions import pytest_assert
from tests.common.helpers.assertions import pytest_require
from tests.common.helpers.dut_utils import check_container_state
from tests.common.helpers.dut_utils import is_container_running
from tests.common.plugins.loganalyzer.loganalyzer import LogAnalyzer
from tests.common.utilities import wait_until
from tests.common.helpers.dut_utils import get_disabled_container_list
from tests.common.helpers.multi_thread_utils import SafeThreadPoolExecutor

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.topology('any', 't1-multi-asic'),
    pytest.mark.disable_loganalyzer,
    pytest.mark.disable_memory_utilization
]

CONTAINER_CHECK_INTERVAL_SECS = 1
CONTAINER_STOP_THRESHOLD_SECS = 30
CONTAINER_RESTART_THRESHOLD_SECS = 180


@pytest.fixture(autouse=True, scope="module")
def config_reload_after_tests(duthosts, selected_rand_one_per_hwsku_hostname):
    """Restores the DuT.

    Args:
      duthosts: list of DUTs.
      selected_rand_one_per_hwsku_hostname: The fixture returns a dict of module
                                            to list of hostnames mapping

    Returns:
      None.
    """
    up_bgp_neighbors = {}
    for hostname in selected_rand_one_per_hwsku_hostname:
        duthost = duthosts[hostname]
        up_bgp_neighbors[duthost] = duthost.get_bgp_neighbors_per_asic("established")

    yield
    with SafeThreadPoolExecutor(max_workers=8) as executor:
        for hostname in selected_rand_one_per_hwsku_hostname:
            duthost = duthosts[hostname]
            logger.info("Reload config on DuT '{}' ...".format(duthost.hostname))
            executor.submit(config_reload, duthost, safe_reload=True, check_intf_up_ports=True, wait_for_bgp=True)

    with SafeThreadPoolExecutor(max_workers=8) as executor:
        for hostname in selected_rand_one_per_hwsku_hostname:
            duthost = duthosts[hostname]
            executor.submit(postcheck_critical_processes_status, duthost, up_bgp_neighbors[duthost])


@pytest.fixture(autouse=True, scope="module")
def check_image_version(duthosts, selected_rand_one_per_hwsku_hostname):
    """Skips this test if the SONiC image installed on DUT was 201911 or old version.

    Args:
      duthosts: list of DUTs.
      selected_rand_one_per_hwsku_hostname: The fixture returns a dict of module
                                            to list of hostnames mapping
    Returns:
      None.
    """
    for hostname in selected_rand_one_per_hwsku_hostname:
        duthost = duthosts[hostname]
        pytest_require(parse_version(duthost.kernel_version) > parse_version("4.9.0"),
                       "Test was not supported for 201911 and older image version!")


@pytest.fixture(autouse=True, scope="module")
def update_monit_service(duthosts, selected_rand_one_per_hwsku_hostname):
    """Update Monit configuration and restart it.

    This function will first reduce the monitoring interval of container checker
    from 5 minutes to 1 minute, then restart Monit service with delaying 10 seconds.
    After testing, these two changes will be rolled back.

    Args:
      duthosts: list of DUTs.
      selected_rand_one_per_hwsku_hostname: The fixture returns a dict of module
                                            to list of hostnames mapping
    Returns:
      None.
    """
    for hostname in selected_rand_one_per_hwsku_hostname:
        duthost = duthosts[hostname]
        logger.info("Back up Monit configuration files on DuT '{}' ...".format(duthost.hostname))
        duthost.shell("sudo cp -f /etc/monit/monitrc /tmp/")
        duthost.shell("sudo cp -f /etc/monit/conf.d/sonic-host /tmp/")

        temp_config_line = "    if status != 0 for 1 times within 1 cycles then alert repeat every 1 cycles"
        logger.info("Reduce the monitoring interval of container_checker.")
        duthost.shell("sudo sed -i '$s/^./#/' /etc/monit/conf.d/sonic-host")
        duthost.shell("echo '{}' | sudo tee -a /etc/monit/conf.d/sonic-host".format(temp_config_line))
        duthost.shell("sudo sed -i 's/with start delay 300/with start delay 10/' /etc/monit/monitrc")
        duthost.shell("sudo sed -i 's/set daemon 60/set daemon 10/' /etc/monit/monitrc")
        logger.info("Restart the Monit service without delaying to monitor.")
        duthost.shell("sudo systemctl restart monit")

    yield

    for hostname in selected_rand_one_per_hwsku_hostname:
        duthost = duthosts[hostname]
        logger.info("Roll back the Monit configuration of container checker on DuT '{}' ..."
                    .format(duthost.hostname))
        duthost.shell("sudo mv -f /tmp/monitrc /etc/monit/")
        duthost.shell("sudo mv -f /tmp/sonic-host /etc/monit/conf.d/")
        logger.info("Restart the Monit service and delay monitoring for 5 minutes.")
        duthost.shell("sudo systemctl restart monit")


def check_all_critical_processes_status(duthost):
    """Post-checks the status of critical processes.

    Args:
      duthost: Host DUT.

    Returns:
      This function will return True if all critical processes are running.
      Otherwise it will return False.
    """
    processes_status = duthost.all_critical_process_status()
    for container_name, processes in list(processes_status.items()):
        if processes["status"] is False or len(processes["exited_critical_process"]) > 0:
            return False

    return True


def post_test_check(duthost, up_bgp_neighbors):
    """Post-checks the status of critical processes and state of BGP sessions.

    Args:
      duthost: Host DUT.
      skip_containers: A list contains the container names which should be skipped.

    Returns:
      This function will return True if all critical processes are running and
      all BGP sessions are established. Otherwise it will return False.
    """
    return check_all_critical_processes_status(duthost) and \
        duthost.check_bgp_session_state_all_asics(up_bgp_neighbors, "established")


def postcheck_critical_processes_status(duthost, up_bgp_neighbors):
    """Calls the functions to post-check the status of critical processes and
       state of BGP sessions.

    Args:
      duthost: Host DUT.
      skip_containers: A list contains the container names which should be skipped.

    Returns:
      If all critical processes are running and all BGP sessions are established, it
      returns True. Otherwise it will call the function to do post-check every 30 seconds
      for 3 minutes. It will return False after timeout
    """
    logger.info("Post-checking status of critical processes and BGP sessions...")
    return wait_until(CONTAINER_RESTART_THRESHOLD_SECS, CONTAINER_CHECK_INTERVAL_SECS, 0,
                      post_test_check, duthost, up_bgp_neighbors)


def get_expected_alerting_message(container_name):
    """Generates the expected alerting message from the stopped container.

    Args:
      container_name: A string represents the container name.

    Return:
      A list contains the expected alerting message.
    """
    logger.info("Generating the expected alerting message for container '{}' ...".format(container_name))
    expected_alerting_messages = []

    expected_alerting_messages.append(".*Expected containers not running.*{}.*".format(container_name))

    logger.info("Generating the expected alerting message was done!")
    return expected_alerting_messages


def test_container_checker(duthosts, enum_rand_one_per_hwsku_hostname, enum_rand_one_asic_index, enum_dut_feature,
                           tbinfo, disable_container_autorestart):
    """Tests the feature of container checker.

    This function will check whether the container names will appear in the Monit
    alerting message if they are stopped explicitly or they hit start limitation.

    Args:
        duthosts: list of DUTs.
        enum_rand_one_per_hwsku_hostname: Fixture returning list of hostname selected per hwsku.
        enum_rand_one_asic_index: Fixture returning list of asics for selected duts.
        enum_dut_feature: A list contains features.
        tbinfo: Testbed information.

    Returns:
        None.
    """
    service_name = enum_dut_feature
    duthost = duthosts[enum_rand_one_per_hwsku_hostname]
    asic = duthost.asic_instance(enum_rand_one_asic_index)
    container_name = asic.get_docker_name(service_name)

    loganalyzer = LogAnalyzer(ansible_host=duthost, marker_prefix="container_checker_{}".format(container_name))
    sleep_time = 70
    disabled_containers = get_disabled_container_list(duthost)

    skip_containers = disabled_containers[:]

    # Skip 'radv' container on devices whose role is not T0/M0.
    # Skip 'radv' container on dualtor-aa as radv is forcefully killed on dualtor-aa (#13408 in sonic-buildimage)
    if tbinfo["topo"]["type"] not in ["t0", "m0"] or 'dualtor-aa' in tbinfo['topo']['name']:
        skip_containers.append("radv")
    pytest_require(service_name not in skip_containers,
                   "Container '{}' is skipped for testing.".format(container_name))
    feature_autorestart_states = duthost.get_container_autorestart_states()
    if feature_autorestart_states.get(service_name) == 'enabled':
        disable_container_autorestart(duthost)
        time.sleep(30)
    if not is_container_running(duthost, container_name):
        logger.info("Container '{}' is not running ...".format(container_name))
        logger.info("Reload config on DuT as Container is not up '{}' ...".format(duthost.hostname))
        config_reload(duthost, safe_reload=True)
        time.sleep(300)
        sleep_time = 80
    asic.stop_service(service_name)
    logger.info("Waiting until container '{}' is stopped...".format(container_name))
    stopped = wait_until(CONTAINER_STOP_THRESHOLD_SECS,
                         CONTAINER_CHECK_INTERVAL_SECS,
                         0,
                         check_container_state, duthost, container_name, False)
    pytest_assert(stopped, "Failed to stop container '{}'".format(container_name))
    logger.info("Container '{}' on DuT '{}' was stopped".format(container_name, duthost.hostname))

    loganalyzer.expect_regex = get_expected_alerting_message(container_name)
    with loganalyzer:
        # Wait for 70s to 80s  such that Monit has a chance to write alerting message into syslog.
        logger.info("Sleep '{}'s to wait for the alerting message...".format(sleep_time))
        time.sleep(sleep_time)


def test_container_checker_telemetry(duthosts, rand_one_dut_hostname):
    """Tests the feature of container checker.

    This function will verify container checker for telemetry.

    Args:
        duthosts: list of DUTs.
        rand_one_dut_hostname: Fixture returning dut hostname.

    Returns:
        None.
    """
    duthost = duthosts[rand_one_dut_hostname]
    container_name = "telemetry"

    # Reload config to restore the container
    config_reload(duthost, safe_reload=True)
    # Monit needs 300 seconds to start monitoring the container
    time.sleep(300)

    # Enable LogAnalyzer
    loganalyzer = LogAnalyzer(ansible_host=duthost, marker_prefix="container_checker_{}".format(container_name))
    loganalyzer.expect_regex = get_expected_alerting_message(container_name)
    marker = loganalyzer.init()

    # Enable telemetry in FEATURE table
    dut_command = "sonic-db-cli CONFIG_DB hset \"FEATURE|{}\" state enabled".format(container_name)
    duthost.command(dut_command, module_ignore_errors=True)

    # Monit checks services at 1-minute intervals
    # Add a 20-second delay to ensure Monit has time to write alert messages to syslog
    sleep_time = 80
    logger.info("Sleep '{}'s to wait for the alerting message...".format(sleep_time))
    time.sleep(sleep_time)
    analysis = loganalyzer.analyze(marker, fail=False)
    pytest_assert(analysis['total']['expected_match'] == 0, 'Monit error: {}'.format(analysis['expect_messages']))
