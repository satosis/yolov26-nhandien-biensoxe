import concurrent.futures
import json
import logging
import select
import socket
import time
from typing import List, Dict

from backend.services.database import db
from backend.services.settings_service import get_app_setting

from .arp import _get_local_broadcast_addresses, lan_networks
from .onvif import discover_onvif_only

logger = logging.getLogger("network_utils.discovery")

RTSP_PORTS = (554, 8554)
MAX_SWEEP_HOSTS_PREFIX = 22  # subnets up to /22 (1022 hosts) are swept for RTSP; bigger ones are not


def discover_dahua_cameras(timeout=5.0) -> List[Dict]:
    devices = []
    DAHUA_DISCOVERY_PORT = 37810

    probe = {
        "method": "client.index",
        "params": {"index": 1},
        "id": 1,
        "session": 0
    }
    probe_str = json.dumps(probe)

    broadcast_addrs = _get_local_broadcast_addresses()
    broadcast_addrs.append('255.255.255.255')
    broadcast_addrs = list(dict.fromkeys(broadcast_addrs))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    try:
        logger.info(f"Dahua Discovery: Broadcasting to {broadcast_addrs}")
        for addr in broadcast_addrs:
            sock.sendto(probe_str.encode(), (addr, DAHUA_DISCOVERY_PORT))
        
        start_time = time.time()
        seen_serials = set()
        
        while time.time() - start_time < timeout:
            remaining = timeout - (time.time() - start_time)
            if not select.select([sock], [], [], max(0, remaining))[0]:
                break
            data, addr = sock.recvfrom(4096)
            resp_str = data.decode('utf-8', errors='replace')
            if '\ufffd' in resp_str:
                logger.debug("Dahua Discovery: Invalid UTF-8 from %s", addr[0])
                continue
            if '"result"' not in resp_str or '"sn"' not in resp_str:
                continue
            resp = json.loads(resp_str)
            res = resp.get('result') if isinstance(resp, dict) else None
            if not isinstance(res, dict):
                logger.debug("Dahua Discovery: Invalid result from %s", addr[0])
                continue
            sn = res.get('sn')
            if not isinstance(sn, str):
                logger.debug("Dahua Discovery: Invalid serial from %s", addr[0])
                continue
            sn = sn.strip().lower()
            port = res.get('port', 37777)
            model = res.get('deviceType', '')
            if not sn or len(sn) > 128 or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                logger.debug("Dahua Discovery: Invalid device identity or port from %s", addr[0])
                continue
            if not isinstance(model, str):
                model = ''
            if sn and sn not in seen_serials:
                devices.append({
                    'id': sn,
                    'ip': addr[0],
                    'port': port,
                    'model': model,
                    'manufacturer': 'Dahua/Imou',
                    'serial': sn,
                    'source': 'dahua_udp'
                })
                seen_serials.add(sn)
                logger.info("Dahua Discovery: Found %s @ %s", sn, addr[0])
    finally:
        sock.close()
    
    return devices


def open_rtsp_hosts(networks) -> dict:
    """{ip: port} of every host of the local subnets that accepts a TCP connection on an RTSP port."""
    own = {ip for ip, _ in networks}
    targets = [(str(host), port) for _, network in networks if network.prefixlen >= MAX_SWEEP_HOSTS_PREFIX
               for host in network.hosts() if str(host) not in own for port in RTSP_PORTS]

    def check(target):
        ip, port = target
        with socket.socket() as sock:
            sock.settimeout(0.6)
            return (ip, port) if sock.connect_ex((ip, port)) == 0 else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        return {ip: port for ip, port in filter(None, pool.map(check, targets))}


def scan_lan_cameras() -> dict:
    """The camera streams on the local network right now: ONVIF WS-Discovery and Dahua broadcast on every local
    interface, plus every host of the local subnets that accepts an RTSP connection. Nothing is saved and nothing
    already in the system fills anything in; the ONVIF credentials from the settings only read a found camera's
    stream address. `_exists` marks a camera whose IP is already in the system."""
    networks = lan_networks()
    password = get_app_setting("ONVIF_PASSWORD", "")
    devices = {dev["ip"]: dev for dev in discover_onvif_only(
        timeout=5.0, probe_details=bool(password),
        onvif_username=get_app_setting("ONVIF_USERNAME", "") or "admin", onvif_password=password)}
    for dev in discover_dahua_cameras(timeout=4.0):
        found = devices.setdefault(dev["ip"], {
            **dev, "name": f"Dahua/Imou {dev['model']}".strip(), "is_online": True, "type": "imou",
            "rtsp_url_onvif": "", "rtsp_sub_onvif": "", "onvif_profiles": [], "has_ptz": False})
        found["serial"] = found.get("serial") or dev["serial"]
    for ip in open_rtsp_hosts(networks):
        devices.setdefault(ip, {
            "id": "", "ip": ip, "name": "", "manufacturer": "", "model": "", "source": "rtsp",
            "is_online": True, "type": "onvif", "rtsp_url_onvif": "", "rtsp_sub_onvif": "", "onvif_profiles": [],
            "has_ptz": False})
    saved = {row["camera_ip"] for row in db._execute_query(
        "SELECT camera_ip FROM cameras WHERE camera_ip IS NOT NULL", fetchall=True) or []}
    ordered = sorted(devices.values(), key=lambda dev: tuple(int(part) for part in dev["ip"].split(".")))
    for dev in ordered:
        dev["_exists"] = dev["ip"] in saved
    return {"devices": ordered,
            "networks": [str(network) for _, network in networks if network.prefixlen >= MAX_SWEEP_HOSTS_PREFIX]}
