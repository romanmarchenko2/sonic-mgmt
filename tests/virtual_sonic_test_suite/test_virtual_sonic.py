import pytest
import os
import logging
import sys

from tests.common.helpers.assertions import pytest_assert
logger = logging.getLogger(__name__)

pytestmark = [
   pytest.mark.sanity_check(skip_sanity=True),
   pytest.mark.disable_loganalyzer,
   pytest.mark.topology('t0', 't1-16'),
   pytest.mark.device_type("vs")
]

class TestAnsibleModules:
    """
    Test suite that tests Ansible modules.
    Used Ansible modules:
        1. shell: Runs commands on the remote host.
        2. stat: Retrieves facts on specified file.
        3. copy: Used to copy local files to remote host.
        4. find: Finds list of files based on specified criteria.
        5. fetch: Retrieve files from the DUT to the test host.
        6. file: Copies file from remote host to local host.
        7. bgp_facts: Retrieves BGP information using Quagga.
        8. acl_facts: Retrieves ACL information from remote host.
        9. image_facts: Get information on image from remote host.
        10. feature_facts: Provides the statuses for all active features on a host.
    """
    @pytest.mark.parametrize("interface", [
        "Ethernet0",
        "Ethernet4"
    ])
    def test_interface_status(self, interface, duthosts, rand_one_dut_hostname):
        logging.info(f"Testing interface status for {interface}")
        duthost = duthosts[rand_one_dut_hostname]

        status_result = duthost.shell(f"show interface status {interface}")
        pytest_assert(status_result['rc'] == 0, "Shell command failed")

    def test_file_manipulation(self, duthosts, rand_one_dut_hostname):
        logging.info("Testing file copying, finding, fetching and deletion")
        duthost = duthosts[rand_one_dut_hostname]

        remote_copy_path = "/tmp/copied_file.txt"
        local_copy_path = "/tmp/fetched_file.txt"
        local_temp_file_path = "/tmp/local_file.txt"
        file_content = "Test file content"

        # Create local file
        with open(local_temp_file_path, "w") as f:
            f.write(file_content)

        # Copy local file to DUT
        duthost.copy(src=local_temp_file_path, dest=remote_copy_path)

        # Verify that copied file exist on DUT
        copy_stat = duthost.stat(path=remote_copy_path)["stat"]
        pytest_assert(copy_stat["exists"], f"{remote_copy_path} is not exists on DUT")
        
        # Find file to confirm file presence
        find_result = duthost.find(paths=["/tmp"], patterns=["copied_file.txt"])
        pytest_assert([remote_copy_path in f["path"] for f in find_result["files"]], "File was not found")

        # Fetch copied file back from DUT to local
        duthost.fetch(src=remote_copy_path, dest=local_copy_path, flat=True)
        pytest_assert(os.path.exists(local_copy_path), f"{local_copy_path} was not fetched")

        # Validate fetched content
        with open(local_copy_path, "r") as f:
            local_content = f.read().strip()
        pytest_assert(local_content == file_content, "Fetched file content does not match original")

        # Delete file on DUT
        duthost.file(path=remote_copy_path, state="absent")

        # Delete local file
        os.remove(local_copy_path)
        os.remove(local_temp_file_path)

    def test_acl_facts(self, duthosts, rand_one_dut_hostname):
        logging.info("Testing retrieval of ACL facts")
        duthost = duthosts[rand_one_dut_hostname]

        acl_tables = duthost.acl_facts()["ansible_facts"]["ansible_acl_facts"]
        pytest_assert(acl_tables, "No ACL facts returned")

    def test_image_facts(self, duthosts, rand_one_dut_hostname):
        logging.info("Testing that current image is among available images")
        duthost = duthosts[rand_one_dut_hostname]

        image_facts = duthost.image_facts()["ansible_facts"]["ansible_image_facts"]
        pytest_assert(image_facts["current"] in image_facts["available"], "Current image is not in available list")

    def test_feature_facts_status(self, duthosts, rand_one_dut_hostname):
        logging.info("Testing that all features have appropriate assigned status")
        duthost = duthosts[rand_one_dut_hostname]

        features = duthost.feature_facts()["ansible_facts"]["feature_facts"]
        pytest_assert(features, "Feature facts is empty")

        for status in features.values():
            pytest_assert(status in ["always_enabled", "enabled", "disabled", "always_disabled"], f"Unexpected feature status: {status}")

    def test_first_bgp_neighbor_status(self, duthosts, rand_one_dut_hostname):
        logging.info("Testing first BGP neighbor status is up")
        duthost = duthosts[rand_one_dut_hostname]

        bgp_facts = duthost.bgp_facts()
        neighbors = bgp_facts["ansible_facts"]["bgp_neighbors"]

        if not neighbors:
            pytest.skip("No BGP neighbors found")

        neighbor_ip = list(neighbors.keys())[0]
        pytest_assert(neighbors[neighbor_ip]["admin"].lower() == "up", "First BGP neighbor is not up")

class TestSonicAsicMethods:
    """
    Test suite that tests Sonic Asic Methods.
    Used Sonic Asic Methods:
        1. config_portchannel(): Creates or removes a portchannel on the ASIC instance.
        2. config_portchannel_member(): Adds or removes portchannel member for a specified portchannel on the ASIC instance.
        3. portchannel_on_asic(): Checks whether a specified portchannel is configured on ASIC instance.
        4. show_interface(): Show status and counter values for a given interface on the ASIC.
        5. ping_v4(): Pings specified ipv4 address via ASIC.
        6. port_exists(): Checks whether a provided port exists in the ASIC instance calling the method.
        7. get_active_ip_interfaces(): Provides a information on active IP interfaces. Works on ASIC devices. 
        8. shell(): Runs a shell command via the sonichost associated with the ASIC instance calling the method.
        9. show_interface(): Show status and counter values for a given interface on the ASIC.
        10. command(): Runs commands specified for the ASIC calling the method.
    """
    @pytest.fixture(scope="function")
    def portchannel_setup(self, duthosts, rand_one_dut_hostname, enum_frontend_asic_index):
        duthost = duthosts[rand_one_dut_hostname]
        sonic_asic = duthost.asic_instance(asic_index=enum_frontend_asic_index)

        portchannel_name = "PortChannel0001"
        interface_name = "Ethernet32"

        add_result = sonic_asic.config_portchannel(portchannel_name, op="add")
        pytest_assert(add_result["rc"] == 0, "Failed to add portchannel in setup")

        add_member_result = sonic_asic.config_portchannel_member(portchannel_name, interface_name, op="add")
        pytest_assert(add_member_result["rc"] == 0, "Failed to add member to portchannel")

        yield portchannel_name, interface_name

        del_member_result = sonic_asic.config_portchannel_member(portchannel_name, interface_name, op="del")
        pytest_assert(del_member_result["rc"] == 0, "Failed to delete member from portchannel")

        del_result = sonic_asic.config_portchannel(portchannel_name, op="del")
        pytest_assert(del_result["rc"] == 0, "Failed to delete portchannel")

    def test_portchannel_member_present(self, portchannel_setup, duthosts, rand_one_dut_hostname, enum_frontend_asic_index):
        logging.info("Testing portchannel member presence")
        portchannel_name, interface_name = portchannel_setup
        duthost = duthosts[rand_one_dut_hostname]
        sonic_asic = duthost.asic_instance(asic_index=enum_frontend_asic_index)

        pytest_assert(sonic_asic.portchannel_on_asic(portchannel_name), "Portchannel was not found")

    @pytest.mark.parametrize("interface,expected_status", [
        ("Ethernet0", "up"),
        ("Ethernet32", "down")
    ])
    def test_interface_status(self, duthosts, rand_one_dut_hostname, enum_frontend_asic_index, interface, expected_status, tbinfo):
        logging.info("Testing interfaces states")
        duthost = duthosts[rand_one_dut_hostname]
        sonic_asic = duthost.asic_instance(asic_index=enum_frontend_asic_index)
        
        # Check iface exists
        pytest_assert(sonic_asic.port_exists(interface), "Interface doesn't exist")
        
        result = sonic_asic.shell(f"ip link show {interface}")
        pytest_assert(interface in result["stdout"], "No interface")
        
        # Check that at least one active iface exists
        active_intfs = sonic_asic.get_active_ip_interfaces(tbinfo)
        pytest_assert(len(active_intfs) > 0, "Should be at least one active interface")
        
        # Check iface admin state
        int_status = sonic_asic.show_interface(command="status")['ansible_facts']['int_status'][interface]
        pytest_assert(int_status["admin_state"].lower() == expected_status, f"{interface} admin state is not {expected_status}")

    def test_active_interfaces_count(self, duthosts, rand_one_dut_hostname, enum_frontend_asic_index, tbinfo):
        logging.info("Testing active interfaces count")
        duthost = duthosts[rand_one_dut_hostname]
        sonic_asic = duthost.asic_instance(asic_index=enum_frontend_asic_index)

        iface_status = sonic_asic.show_interface(command="status")['ansible_facts']['int_status']
        active_ifaces = sum(1 for intf, data in iface_status.items() if data["admin_state"] == "up")

        topo_name = tbinfo["topo"]["name"]
        if topo_name == "t0":
            expected_ifaces = 8
        elif topo_name == "t1-16":
            expected_ifaces = 16
        else:
            pytest.skip(f"Topology {topo_type} not supported for this test")

        pytest_assert(active_ifaces == expected_ifaces, f"Expected {expected_ifaces} active interfaces, got {active_ifaces}")
    
    def test_ping_v4(self, duthosts, rand_one_dut_hostname, enum_frontend_asic_index, ip_address="10.0.0.5"):
        logging.info(f"Testing ping to {ip_address}")
        duthost = duthosts[rand_one_dut_hostname]
        sonic_asic = duthost.asic_instance(asic_index=enum_frontend_asic_index)

        if sys.version_info[0] >= 3:
            unicode = str
            
        pytest_assert(sonic_asic.ping_v4(unicode(ip_address)), f"Ping to {ip_address} failed")
        
    def test_asic_command_execution(self, duthosts, rand_one_dut_hostname, enum_frontend_asic_index):
        logging.info("Testing ASIC command execution")
        duthost = duthosts[rand_one_dut_hostname]
        sonic_asic = duthost.asic_instance(asic_index=enum_frontend_asic_index)

        result = sonic_asic.command("show ip bgp summary")
        pytest_assert(result["rc"] == 0, "Failed to execute command'")
        pytest_assert("BGP router identifier" in result["stdout"], "Did not return expected output")

class TestSonicHostMethods:
    """
    Test suite that uses SONiC host methods.
    Used SONiC host methods:
        1. hostname: Provides hostname for device.
        2. get_asic_name(): Returns name of current ASIC. For use in multi-ASIC environments.
        3. mgmt_ip: Provides management ip for host.
        4. kernel_version: Provides version of Sonic kernel on remote host.
        5. os_version: Provides string representing the version of SONiC being used.
        6. facts: Returns platform information facts about the sonic device.
        7. num_asics(): Provides number of asics.
        8 is_multi_asic: Returns whether remote host is multi-ASIC.
        9. is_service_running(service_name, container_name): Checks if a specified service is running. Can be a service within a docker.
        10. is_container_running(container_name): Checks whether a docker container is running.
    """
    def test_sonic_properties(self, duthosts, rand_one_dut_hostname):
        logging.info("Testing getting SONiC properties")
        duthost = duthosts[rand_one_dut_hostname]

        pytest_assert(isinstance(duthost.hostname, str), "Should be str")
        pytest_assert(isinstance(duthost.get_asic_name(), str), "Should be str")
        pytest_assert(isinstance(duthost.mgmt_ip, str), "Should be str")
        pytest_assert(isinstance(duthost.kernel_version, str), "Should be str")
        pytest_assert(isinstance(duthost.os_version, str), "Should be str")
        pytest_assert(duthost.facts["asic_type"] == "vs", "Unexpected ASIC type")

        num = duthost.num_asics()
        pytest_assert(isinstance(num, int), "Should be int")

        if duthost.is_multi_asic:
            pytest_assert(num > 1, "Expected multiple ASICs for multi-asic device")
        else:
            pytest_assert(num == 1, "Expected one ASIC for single-asic device")

    @pytest.mark.parametrize("service_name,container_name", [
    ("bgpcfgd", "bgp"),
    ("orchagent", "swss"),
    ("syncd", "syncd"),
    ("lldpd", "lldp"),
    ("snmpd", "snmp"),
    ("teamsyncd", "teamd"),
    ])
    def test_sonic_services_running(self, duthosts, rand_one_dut_hostname, service_name, container_name):
        logging.info(f"Testing that {service_name} is running in {container_name} container")
        duthost = duthosts[rand_one_dut_hostname]

        pytest_assert(
            duthost.is_service_running(service_name, container_name),
            f"{service_name} service in {container_name} container is not running"
        )

    @pytest.mark.parametrize("container_name", [
        "bgp",
        "database",
        "teamd",
        "syncd",
    ])
    def test_container_running(self, duthosts, rand_one_dut_hostname, container_name):
        logging.info(f"Testing that {container_name} is running")
        duthost = duthosts[rand_one_dut_hostname]

        pytest_assert(duthost.is_container_running(container_name), f"Container '{container_name}' is not running")
