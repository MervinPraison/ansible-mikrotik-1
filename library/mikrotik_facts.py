#!/usr/bin/env python
# coding: utf-8
"""MikroTik RouterOS ansible facts gathering module"""

import sys
import re
import socket

HAS_SSHCLIENT = True
SHELLMODE = False
SHELLDEFS = {
    'username': 'admin',
    'password': '',
    'key_filename': None,
    'timeout': 30,
    'port': 22,
    'verbose': False
}
MIKROTIK_MODULE = '[github.com/nekitamo/ansible-mikrotik] v2017.07'
DOCUMENTATION = """
---

module: mikrotik_facts
short_description: Gather facts from MikroTik RouterOS devices
description:
    - Gather fact data (characteristics) of MikroTik RouterOS devices.
    - If you create router user 'ansible' with ssh-key you can omit username/password in playbooks    
return_data:
    - identity
    - license
    - resources
    - routerboard
    - health
    - users
    - packages
    - interfaces
    - ip addresses
    - mac addresses
    - misc info
options:
    verbose:
        description:
            - Gather even more device facts (slower)
        required: no
        default: false
    port:
        description:
            - SSH listening port of the MikroTik device
        required: no
        default: 22
    hostname:
        description:
            - IP Address or hostname of the MikroTik device
        required: yes
        default: null
    username:
        description:
            - Username used to login to the device
        required: no
        default: ansible
    password:
        description:
            - Password used to login to the device
        required: no
        default: null

"""
EXAMPLES = """
- name: Gather MikroTik facts
    mikrotik_facts:
        hostname: "{{ inventory_hostname }}"
        username: admin
"""
RETURN = """
ansible_facts:
    description: Returns facts collected from the device
    returned: always
    type: dict
"""
SHELL_USAGE = """
mikrotik_facts.py --hostname=<hostname> [--verbose] [--port=<port>]
                 [--username=<username>] [--password=<password>]
"""

try:
    import paramiko
except ImportError as import_error:
    HAS_SSHCLIENT = False

try:
    from ansible.module_utils.basic import AnsibleModule
except ImportError:
    SHELLMODE = True
else:
    if sys.stdin.isatty():
        SHELLMODE = True

def safe_fail(module, device=None, **kwargs):
    """closes device before module fail"""
    if device:
        device.close()
    module.fail_json(**kwargs)

def safe_exit(module, device=None, **kwargs):
    """closes device before module exit"""
    if device:
        device.close()
    module.exit_json(**kwargs)

def parse_opts(cmdline):
    """returns SHELLMODE command line options as dict"""
    options = SHELLDEFS
    for opt in cmdline:
        if opt.startswith('--'):
            try:
                arg, val = opt.split("=", 1)
            except ValueError:
                arg = opt
                val = True
            else:
                if val.lower() in ('no', 'false', '0'):
                    val = False
                elif val.lower() in ('yes', 'true', '1'):
                    val = True
            arg = arg[2:]
            if arg in options or arg == 'hostname':
                options[arg] = val
            else:
                print SHELL_USAGE
                sys.exit("Unknown option: --%s" % arg)
    if 'hostname' not in options:
        print SHELL_USAGE
        sys.exit("Hostname is required, specify with --hostname=<hostname>")
    return options

def device_connect(module, device, rosdev):
    """open ssh connection with or without ssh keys"""
    if SHELLMODE:
        sys.stdout.write("Opening SSH connection to %s(%s:%s)... "
                         % (rosdev['hostname'], rosdev['ipaddress'], rosdev['port']))
        sys.stdout.flush()
    try:
        device.connect(rosdev['ipaddress'], username=rosdev['username'],
                       key_filename=rosdev['key_filename'], port=rosdev['port'],
                       timeout=rosdev['timeout'], password=rosdev['password'])
    except Exception:
        try:
            device.connect(rosdev['ipaddress'], username=rosdev['username'],
                           password=rosdev['password'], port=rosdev['port'],
                           timeout=rosdev['timeout'], allow_agent=False,
                           look_for_keys=False)
        except Exception as ssh_error:
            if SHELLMODE:
                sys.exit("failed!\nSSH error: " + str(ssh_error))
            safe_fail(module, device, msg=str(ssh_error),
                      description='error opening ssh connection to %s(%s:%s)' %
                      (rosdev['hostname'], rosdev['ipaddress'], rosdev['port']))
    if SHELLMODE:
        print "succes."

def sshcmd(module, device, timeout, command):
    """executes a command on the device, returns string"""
    try:
        _stdin, stdout, _stderr = device.exec_command(command, timeout=timeout)
    except Exception as ssh_error:
        if SHELLMODE:
            sys.exit("SSH command error: " + str(ssh_error))
        safe_fail(module, device, msg=str(ssh_error),
                  description='SSH error while executing command')
    response = stdout.read()
    if not 'bad command name ' in response:
        if not 'syntax error ' in response:
            if not 'failure: ' in response:
                return response.rstrip()
    if SHELLMODE:
        print "Command: " + str(command)
        sys.exit("Error: " + str(response))
    safe_fail(module, device, msg=str(ssh_error),
              description='bad command name or syntax error')

def parse_terse(device, key, command):
    """executes a command and returns list"""
    _stdin, stdout, _stderr = device.exec_command(command)
    vals = []
    for line in stdout.readlines():
        if key in line:
            val = line.split(key+'=')[1]
            vals.append(val.split(' ')[0])
    return vals

def parse_facts(device, command, pfx=""):
    """executes a command and returns dict"""
    _stdin, stdout, _stderr = device.exec_command(command)
    facts = {}
    for line in stdout.readlines():
        if ':' in line:
            fact, value = line.partition(":")[::2]
            fact = fact.replace('-', '_')
            if pfx not in fact:
                facts[pfx + fact.strip()] = str(value.strip())
            else:
                facts[fact.strip()] = str(value.strip())
    return facts

def vercmp(ver1, ver2):
    """quick and dirty version comparison from stackoverflow"""
    def normalize(ver):
        return [int(x) for x in re.sub(r'(\.0+)*$', '', ver).split(".")]
    return cmp(normalize(ver1), normalize(ver2))

def main():
    rosdev = {}
    mtfacts = {}
    cmd_timeout = 30
    changed = False
    if not SHELLMODE:
        module = AnsibleModule(
            argument_spec=dict(
                verbose=dict(default=False, type='bool'),
                port=dict(default=22, type='int'),
                timeout=dict(default=30, type='float'),
                hostname=dict(required=True),
                key_filename=dict(default=None, type='path'),
                username=dict(default='ansible', type='str'),
                password=dict(default='', type='str', no_log=True),
            ), supports_check_mode=False
        )
        if not HAS_SSHCLIENT:
            safe_fail(module, msg='There was a problem loading module: ',
                      error=str(import_error))
        verbose = module.params['verbose']
        rosdev['hostname'] = module.params['hostname']
        rosdev['username'] = module.params['username']
        rosdev['password'] = module.params['password']
        rosdev['key_filename'] = module.params['key_filename']
        rosdev['port'] = module.params['port']
        rosdev['timeout'] = module.params['timeout']

    else:
        if not HAS_SSHCLIENT:
            sys.exit("SSH client error: " + str(import_error))
        rosdev['hostname'] = SHELLOPTS['hostname']
        rosdev['username'] = SHELLOPTS['username']
        rosdev['password'] = SHELLOPTS['password']
        rosdev['key_filename'] = SHELLOPTS['key_filename']
        rosdev['port'] = SHELLOPTS['port']
        rosdev['timeout'] = SHELLOPTS['timeout']
        verbose = SHELLOPTS['verbose']
        module = None

    try:
        rosdev['ipaddress'] = socket.gethostbyname(rosdev['hostname'])
    except socket.gaierror as dns_error:
        if SHELLMODE:
            sys.exit("Hostname error: " + str(dns_error))
        safe_fail(module, msg=str(dns_error),
                  description='error getting device address from hostname')

    device = paramiko.SSHClient()
    device.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    device_connect(module, device, rosdev)

    mgmt = None
    mtfacts['management_ip_address'] = rosdev['ipaddress']
    identity = sshcmd(module, device, cmd_timeout, "system identity print")
    mtfacts['identity'] = str(identity.split(": ")[1])
    user_ssh_keys = parse_terse(device, "key-owner",
            "user ssh-keys print terse where user=" + rosdev['username'])
    if user_ssh_keys:
        mtfacts['user_ssh_keys'] = user_ssh_keys
    src = parse_terse(device, "address",
            'user active print terse where name="' + rosdev['username'] + '" and via=ssh')
    if len(src) == 1:
        mtfacts['management_source_ip'] = src[0]
        con = parse_terse(device, "dst-address",
            'ip firewall connection print terse where tcp-state=established and '
            + 'src-address~"' + src[0] + '" and dst-address~".*:' + str(rosdev['port'])
            + '"')
        if len(con) == 1:
            ifc = parse_terse(device, "interface",
                'ip address print terse where address~"' + str(con[0]).split(":")[0] + '"')
        else:
            ifc = parse_terse(device, "interface",
                'ip address print terse where address~"' + rosdev['ipaddress'] + '"')
        if len(ifc) == 1:
            mgmt = str(ifc[0])

    mtfacts.update(parse_facts(device, "system resource print without-paging"))
    mtfacts.update(parse_facts(device, "system routerboard print without-paging"))
    mtfacts.update(parse_facts(device, "system health print without-paging", "health_"))
    mtfacts.update(parse_facts(device, "system license print without-paging", "license_"))
    mtfacts.update(parse_facts(device, "ip cloud print without-paging", "cloud_"))
    if " " in mtfacts['version']:
        mtfacts['routeros_version'] = mtfacts['version'].split(" ")[0]

    mtfacts['enabled_packages'] = parse_terse(device, "name",
            "system package print terse without-paging where disabled=no")
    for pkg in mtfacts['enabled_packages']:
        if 'routeros' in pkg:
            mtfacts['enabled_packages'].remove(pkg)
    mtfacts['enabled_interfaces'] = parse_terse(device, "name",
            "interface print terse without-paging where disabled=no")
    if mgmt and mgmt in mtfacts['enabled_interfaces']:
        mtfacts['management_interface'] = mgmt
    mtfacts['ip_addresses'] = parse_terse(device, "address",
            "ip address print terse without-paging where disabled=no")
    mtfacts['mac_addresses'] = parse_terse(device, "mac-address",
            "interface print terse without-paging where disabled=no")
    mtfacts['remote_syslog'] = parse_terse(device, "remote",
            "system logging action print terse without-paging")
    email_server = parse_terse(device, "address", "tool e-mail export hide-sensitive")
    if email_server:
        mtfacts['email_server'] = email_server
    if 'wireless' in mtfacts['enabled_packages']:
        wifaces = parse_terse(device, "name",
                "interface wireless print terse without-paging")
        if wifaces:
            mtfacts['wireless_interfaces'] = wifaces
    if 'ipv6' in mtfacts['enabled_packages']:
        mtfacts['ipv6_addresses'] = parse_terse(device, "address",
                "ipv6 address print terse without-paging where disabled=no")

    if verbose:
        mtfacts.update(parse_facts(device, "ip ssh print without-paging", "ssh_"))
        mtfacts.update(parse_facts(device, "ip settings print without-paging", "ipv4_"))
        mtfacts.update(parse_facts(device, "system clock print without-paging", "clock_"))
        mtfacts.update(parse_facts(device, "snmp print without-paging", "snmp_"))
        mtfacts['disabled_packages'] = parse_terse(device, "name",
            "system package print terse without-paging where disabled=yes")
        mtfacts['scheduled_packages'] = parse_terse(device, "name",
            'system package print terse without-paging where scheduled~"scheduled"')
        mtfacts['disabled_interfaces'] = parse_terse(device, "name",
            "interface print terse without-paging where disabled=yes")
        mtfacts.update(parse_facts(device,
            "interface bridge settings print without-paging", "bridge_"))
        mtfacts.update(parse_facts(device,
            "ip firewall connection tracking print without-paging", "conntrack_"))
        mtfacts['users'] = parse_terse(device, "name",
            "user print terse without-paging where disabled=no")
        mtfacts['mac_server_interfaces'] = parse_terse(device, "interface",
            "tool mac-server print terse without-paging where disabled=no")
        mtfacts['mac_winbox_interfaces'] = parse_terse(device, "interface",
            "tool mac-server mac-winbox print terse without-paging where disabled=no")
        mtfacts['ip_services'] = parse_terse(device, "name",
            "ip service print terse without-paging where disabled=no")
        mtfacts['neighbor_discovery_interfaces'] = parse_terse(device, "name",
            "ip neighbor discovery print terse without-paging where disabled=no")
        mtfacts['ethernet_interfaces'] = parse_terse(device, "name",
            "interface ethernet print terse without-paging")
        mtfacts['ethernet_switch_types'] = parse_terse(device, "type",
            "interface ethernet switch print terse without-paging")
        mtfacts['bridge_interfaces'] = parse_terse(device, "name",
            "interface bridge print terse without-paging")
        mtfacts.update(parse_facts(device,
            "system ntp client print without-paging", "ntp_client_"))
        if 'ntp' in mtfacts['enabled_packages']:
            mtfacts.update(parse_facts(device,
                "system ntp server print without-paging", "ntp_server_"))
        if 'ipv6' in mtfacts['enabled_packages']:
            mtfacts.update(parse_facts(device, "ipv6 settings print without-paging",
                "ipv6_"))

    if SHELLMODE:
        device.close()
        for fact in sorted(mtfacts):
            if isinstance(mtfacts[fact], list):
                print "%s: %s" % (fact, ', '.join(mtfacts[fact]))
            else:
                print "%s: %s" % (fact, mtfacts[fact])
        sys.exit(0)

    safe_exit(module, device, ansible_facts=mtfacts, changed=changed)

if __name__ == '__main__':
    if len(sys.argv) > 1 or SHELLMODE:
        print "Ansible MikroTik Library %s" % MIKROTIK_MODULE
        SHELLOPTS = parse_opts(sys.argv)
        SHELLMODE = True
    main()
