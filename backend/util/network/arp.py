import ipaddress
import re
import socket
import logging
import concurrent.futures
from typing import List, Dict

import psutil

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


def lan_networks() -> List[tuple]:
    """(IPv4 address, network) of every up interface that can have neighbours: not loopback, link-local or a /32
    (Tailscale). Works on Windows, Linux and macOS; the old `ip`/`ifconfig` parsing found nothing on Windows."""
    found = []
    stats = psutil.net_if_stats()
    for name, addresses in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for address in addresses:
            if address.family != socket.AF_INET or not address.netmask:
                continue
            ip = ipaddress.ip_address(address.address)
            network = ipaddress.ip_interface(f"{address.address}/{address.netmask}").network
            if not (ip.is_loopback or ip.is_link_local) and network.prefixlen < 32:
                found.append((address.address, network))
    return found


def _get_local_broadcast_addresses() -> List[str]:
    return list(dict.fromkeys(str(network.broadcast_address) for _, network in lan_networks()))


def probe_ip(ip: str, ports=[80, 554, 8000, 37810, 37777, 8080], timeout=1.5) -> bool:
    for port in ports:
        for family, socktype, proto, _, address in socket.getaddrinfo(ip, port, type=socket.SOCK_STREAM):
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(timeout)
                if sock.connect_ex(address) == 0:
                    return True
    return False


def _get_local_ips() -> List[str]:
    return list(dict.fromkeys(ip for ip, _ in lan_networks()))


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
            continue
        w = re.match(r'\s*(\d+\.\d+\.\d+\.\d+)\s+((?:[\da-fA-F]{2}-){5}[\da-fA-F]{2})\s+dynamic', line)  # Windows `arp -a`
        if w:
            entries.append({'ip': w.group(1), 'mac': w.group(2).lower().replace('-', ':'), 'iface': ''})
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
