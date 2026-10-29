import re
import socket
import logging
import concurrent.futures
from typing import List, Dict

logger = logging.getLogger("network_utils.arp")

_CAMERA_OUI_MAP = {
    'e0:50:8b': 'Dahua/Imou', 'bc:ad:28': 'Dahua/Imou', 'a4:02:b9': 'Dahua/Imou',
    '4c:11:bf': 'Dahua/Imou', 'c4:64:13': 'Dahua/Imou', '6c:21:a2': 'Dahua/Imou',
    '90:02:a9': 'Dahua/Imou', '3c:ef:8c': 'Dahua/Imou', '30:24:50': 'Dahua/Imou',
    '44:19:b6': 'Hikvision', 'b4:a3:82': 'Hikvision', 'c0:56:e3': 'Hikvision',
    'bc:6e:76': 'Hikvision', '54:c4:15': 'Hikvision',
    'ac:cc:8e': 'Axis', 'b8:a4:4f': 'Axis', '00:40:8c': 'Axis',
    '50:c7:bf': 'TP-Link', '6c:5a:b0': 'TP-Link', 'b0:be:76': 'TP-Link',
    '9c:4e:36': 'Uniview',
    'ec:71:db': 'Reolink',
    '00:09:18': 'Hanwha',
    '00:07:5f': 'Bosch',
}


def _get_local_broadcast_addresses() -> List[str]:
    import subprocess
    import shutil

    addrs = []
    ip_cmd = shutil.which('ip')
    if ip_cmd:
        output = subprocess.check_output([ip_cmd, '-4', 'addr'], timeout=3, stderr=subprocess.DEVNULL).decode()
        for line in output.split('\n'):
            if 'brd' in line and 'inet' in line:
                parts = line.strip().split()
                for i, p in enumerate(parts):
                    if p == 'brd' and i + 1 < len(parts):
                        addrs.append(parts[i + 1])
    elif ifconfig_cmd := shutil.which('ifconfig'):
        output = subprocess.check_output([ifconfig_cmd], timeout=3, stderr=subprocess.DEVNULL).decode()
        for line in output.split('\n'):
            if 'broadcast' in line:
                parts = line.strip().split()
                for i, p in enumerate(parts):
                    if p == 'broadcast' and i + 1 < len(parts):
                        addrs.append(parts[i + 1])
    return addrs


def probe_ip(ip: str, ports=[80, 554, 8000, 37810, 37777, 8080], timeout=1.5) -> bool:
    for port in ports:
        for family, socktype, proto, _, address in socket.getaddrinfo(ip, port, type=socket.SOCK_STREAM):
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(timeout)
                if sock.connect_ex(address) == 0:
                    return True
    return False


def _get_local_ips(mcast_addr="239.255.255.250", mcast_port=3702) -> List[str]:
    import subprocess
    import shutil

    ips = []
    for command in (['ip', '-4', 'addr'], ['ifconfig']):
        if not shutil.which(command[0]):
            continue
        output = subprocess.check_output(command, timeout=3, stderr=subprocess.DEVNULL).decode()
        for line in output.split('\n'):
            if 'inet ' in line and '127.0.0.1' not in line:
                parts = line.strip().split()
                if command[0] == 'ip':
                    ips.extend(p.split('/')[0] for p in parts if '/' in p and p.split('/')[0].count('.') == 3)
                else:
                    for i, p in enumerate(parts):
                        if p == 'inet' and i + 1 < len(parts):
                            ips.append(parts[i + 1])
        if ips:
            break

    if not ips:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((mcast_addr, mcast_port))
            ips.append(sock.getsockname()[0])
    return list(dict.fromkeys(ips))


def lookup_mac_vendor(mac: str) -> str:
    if not mac:
        return ''
    prefix = mac.lower().replace('-', ':')[:8]
    return _CAMERA_OUI_MAP.get(prefix, '')


def read_arp_table() -> List[Dict]:
    import os
    import shutil
    import subprocess

    entries = []
    if os.path.isfile('/proc/net/arp'):
        with open('/proc/net/arp', 'r') as f:
            lines = f.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 6:
                ip, mac, iface = parts[0], parts[3].lower(), parts[5]
                if mac and mac != '00:00:00:00:00:00':
                    entries.append({'ip': ip, 'mac': mac, 'iface': iface})
        return entries
    arp_cmd = shutil.which('arp')
    if not arp_cmd:
        logger.debug("ARP command is unavailable")
        return entries
    output = subprocess.check_output([arp_cmd, '-a'], timeout=3, stderr=subprocess.DEVNULL).decode()
    for line in output.split('\n'):
        if '(incomplete)' in line:
            continue
        m = re.match(r'.*?\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([\da-fA-F:]+)\s+on\s+(\w+)', line)
        if m:
            ip, mac, iface = m.group(1), m.group(2).lower(), m.group(3)
            if mac and mac != 'ff:ff:ff:ff:ff:ff':
                entries.append({'ip': ip, 'mac': mac, 'iface': iface})
    return entries


def discover_from_arp(camera_ports: List[int] = None) -> List[Dict]:
    if camera_ports is None:
        camera_ports = [80, 554, 8000, 37777, 37810]

    arp_entries = read_arp_table()
    if not arp_entries:
        return []

    candidates = []
    for entry in arp_entries:
        ip, mac = entry['ip'], entry['mac']
        vendor = lookup_mac_vendor(mac)
        if vendor:
            candidates.append({
                'ip': ip, 'mac': mac, 'vendor': vendor,
                'is_online': True, 'source': 'arp_oui',
            })
    unknown_entries = [e for e in arp_entries if not lookup_mac_vendor(e['mac'])]
    if unknown_entries:
        def _check(entry):
            if probe_ip(entry['ip'], ports=camera_ports, timeout=0.8):
                return {'ip': entry['ip'], 'mac': entry['mac'], 'vendor': 'Unknown',
                        'is_online': True, 'source': 'arp_port'}
            return None
        max_workers = min(30, len(unknown_entries))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = executor.map(_check, unknown_entries)
            for r in results:
                if r:
                    candidates.append(r)

    logger.info(f"ARP discovery: {len(arp_entries)} ARP entries, {len(candidates)} camera candidates")
    return candidates
