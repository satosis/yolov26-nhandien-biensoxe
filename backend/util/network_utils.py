
import socket
import select
import time
import logging
import xml.etree.ElementTree as ET
import re
import concurrent.futures
from typing import List, Dict

logger = logging.getLogger("network_utils")

WS_DISCOVERY_MCAST_ADDR = "239.255.255.250"
WS_DISCOVERY_PORT = 3702
WS_DISCOVERY_PROBE = (
    '<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    '<Header><MessageID xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
    'uuid:826017ba-ffaa-45c1-96db-0ee89f2a0001</MessageID>'
    '<To xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">urn:schemas-xmlsoap-org:ws:2004:08:addressing:discovery</To>'
    '<Action xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">http://schemas.xmlsoap.org/ws/2004/08/discovery/Probe</Action>'
    '</Header><Body><Probe xmlns="http://schemas.xmlsoap.org/ws/2004/08/discovery">'
    '<Types>dn:NetworkVideoTransmitter</Types></Probe></Body></Envelope>'
)

def discover_onvif_cameras(timeout=3):
    """
    Scan local network for ONVIF-compatible cameras using WS-Discovery.
    Returns a list of dicts: [{'ip': '...', 'id': '...', 'urn': '...'}]
    """
    cameras = []
    seen_ips = set()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setblocking(False)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    
    sock.sendto(WS_DISCOVERY_PROBE.encode(), (WS_DISCOVERY_MCAST_ADDR, WS_DISCOVERY_PORT))
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        ready = select.select([sock], [], [], 0.5)
        if ready[0]:
            data, addr = sock.recvfrom(2048)
            ip = addr[0]
            if ip in seen_ips: continue
            
            xml_str = data.decode()
            # Extracts URN (which often contains Serial Number for Dahua/Imou)
            urn_match = re.search(r'uuid:([a-zA-Z0-9\-]+)', xml_str)
            urn = urn_match.group(1) if urn_match else ""
            
            # For Dahua/Imou, the URN often ends with the S/N
            # Example: uuid:7a37452c-474c-4c47-8a4d-0c1a1a002468
            cameras.append({
                'ip': ip,
                'urn': urn,
                'raw_xml': xml_str
            })
            seen_ips.add(ip)
                
    sock.close()
    return cameras

def get_local_ip() -> str:
    """Get the local IP of the machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def get_local_subnet() -> str:
    """Get the local subnet (e.g., 192.168.1)."""
    ip = get_local_ip()
    return ".".join(ip.split(".")[:3])

def probe_sn_from_ip(ip: str, timeout: float = 2.0) -> str:
    """Try to fetch the Device Serial Number (deviceId) from a Dahua/Imou IP via UDP 37810."""
    import uuid
    # Standard Dahua/Imou discovery packet
    # This is a simplified version of the ConfigTool probe
    search_xml = f'<?xml version="1.0" encoding="utf-8"?><Envelope><Header><Action>Probe</Action><MessageID>{uuid.uuid4()}</MessageID></Header><Body><Probe><DeviceType>All</DeviceType></Probe></Body></Envelope>'
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(search_xml.encode(), (ip, 37810))
        data, addr = sock.recvfrom(4096)
        resp = data.decode('utf-8', errors='ignore')
        # Simple regex for deviceId
        match = re.search(r'<deviceId>(.*?)</deviceId>', resp)
        if match:
            return match.group(1).strip()
    except:
        pass
    finally:
        sock.close()
    return None

def probe_ip(ip: str, ports: List[int] = [37810, 554, 80], timeout: float = 0.5) -> bool:
    """Check if any of the specified ports are open on an IP."""
    for port in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((ip, port)) == 0:
                    return True
        except:
            continue
    return False

def scan_subnet_for_cameras(subnet: str = "192.168.1", max_workers: int = 50) -> List[str]:
    """Scan a subnet for active IPs on common camera ports."""
    active_ips = []
    ips = [f"{subnet}.{i}" for i in range(1, 255)]
    
    logger.info(f"Scanning subnet {subnet}.0/24 for cameras...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ip = {executor.submit(probe_ip, ip): ip for ip in ips}
        for future in concurrent.futures.as_completed(future_to_ip):
            ip = future_to_ip[future]
            try:
                if future.result():
                    active_ips.append(ip)
            except Exception:
                pass
    logger.info(f"Subnet scan found {len(active_ips)} active IP(s).")
    return active_ips

def discover_and_update_all_cameras(scan_subnet: bool = False):
    """
    Combine ONVIF (local) scan and Imou Cloud (AppID/Secret) scan to find camera IPs.
    Returns a dict with summary results: {'found_local': 0, 'found_cloud': 0, 'updated': 0, 'total': 0}
    """
    from backend.core.database import DatabaseManager
    from backend.util.imou_api import ImouClient
    db = DatabaseManager()
    
    results = {'found_local': 0, 'found_cloud': 0, 'updated': 0, 'total': 0, 'error': None, 'new_devices': []}
    
    # 1. Get all cameras from DB to match them
    all_cams = db._execute_query("SELECT id, camera_name, imou_device_id, camera_ip FROM cameras", fetchall=True)
    results['total'] = len(all_cams)
    
    existing_ids = [str(cam.get('imou_device_id')).strip().lower() for cam in all_cams if cam.get('imou_device_id')]
    logger.info(f"Discovery check: {len(all_cams)} cameras in DB. Existing IDs: {existing_ids}")
    
    found_ips = {} # device_id -> ip
    cloud_new_devices = []
    
    # --- METHOD A: LOCAL ONVIF SCAN ---
    logger.info("Starting Local ONVIF scan...")
    local_found = discover_onvif_cameras(timeout=3)
    logger.info(f"Local scan found {len(local_found)} unique device(s).")
    
    for dev in local_found:
        ip = dev['ip']
        urn = dev['urn'].lower()
        logger.info(f" - Found Device: IP={ip}, URN={urn}")
        
        for cam in all_cams:
            did_raw = cam.get('imou_device_id')
            if not did_raw: continue
            
            did = did_raw.strip().lower()
            # Match if device_id is in URN or vice-versa, or if URN ends with device_id
            if did in urn or urn in did or urn.endswith(did):
                logger.info(f"   [MATCH LOCAL] Camera '{cam['camera_name']}' matches IP {ip} via ID {did}")
                found_ips[did] = ip
                results['found_local'] += 1
                break
                
    # --- METHOD B: IMOU CLOUD API SYNC ---
    imou_keys = db.get_imou_api_keys()
    cloud_new_devices = []
    if imou_keys.get('app_id') and imou_keys.get('app_secret'):
        logger.info("Starting Imou Cloud API sync...")
        try:
            client = ImouClient(imou_keys['app_id'], imou_keys['app_secret'])
            cloud_devices = client.list_devices()
            logger.info(f"Cloud API found {len(cloud_devices)} device(s).")
            
            # AUTOMATION: If any cloud device has no IP, trigger a quick subnet sweep
            # Only do this if we haven't found IP via local scan
            unknown_cloud_devices = [d for d in cloud_devices if not d.get('deviceLocalIp')]
            auto_active_ips = {} # sn -> ip
            
            if unknown_cloud_devices and scan_subnet:
                subnet = get_local_subnet()
                logger.info(f"Triggering AUTO subnet scan for {len(unknown_cloud_devices)} unknown devices on {subnet}.0/24")
                # Quick sweep for port 37810
                active_ips = scan_subnet_for_cameras(subnet, max_workers=60)
                
                # Probe S/N for found IPs
                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                    future_to_ip = {executor.submit(probe_sn_from_ip, ip): ip for ip in active_ips}
                    for future in concurrent.futures.as_completed(future_to_ip):
                        ip = future_to_ip[future]
                        sn = future.result()
                        if sn:
                            auto_active_ips[sn.lower()] = ip
                            logger.info(f"   [AUTO FIND] Found device S/N {sn} @ {ip}")

            for dev in cloud_devices:
                did_raw = dev.get('deviceId')
                lip = dev.get('deviceLocalIp')
                logger.debug(f"DEBUG: Raw Imou device data: {dev}")
                
                if did_raw:
                    did = did_raw.strip().lower()
                    # If IP is missing, check our auto-discovered list
                    if (not lip or lip == "0.0.0.0") and did in auto_active_ips:
                        lip = auto_active_ips[did]
                        logger.info(f"   [AUTO MATCH] Matched unknown device {did} to local IP {lip}")
                    
                    logger.info(f" - Cloud Device: ID={did}, LocalIP={lip}")
                    results['found_cloud'] += 1
                    
                    # Track if it matches an existing DB camera
                    is_new = True
                    if did in existing_ids:
                        is_new = False
                        # If we have a valid IP, update found_ips for existing cameras
                        if lip and lip != "0.0.0.0":
                            logger.info(f"   [MATCH CLOUD] Existing camera with ID {did} matches IP {lip}")
                            if did not in found_ips:
                                found_ips[did] = lip
                    
                    if is_new:
                        logger.info(f"   [NEW CLOUD] ID {did} is NOT in DB. Adding to suggestions.")
                        # More robust online status check
                        # Imou Cloud often uses 'status' (1: online, 0: offline)
                        # or 'deviceOnline' (same) or string 'online'/'offline'
                        # We use a helper list for all possible status-like keys
                        status_keys = ['status', 'deviceOnline', 'online']
                        raw_status = next((dev.get(k) for k in status_keys if dev.get(k) is not None), '')
                        status_val = str(raw_status).lower()
                        
                        is_online = False
                        if status_val in ['1', 'online', 'run', 'on', 'true']:
                            is_online = True
                        elif status_val in ['0', 'offline', 'sleep', 'off', 'false', '']:
                            is_online = False
                        else:
                            # Default to online if it's not explicitly a known offline value
                            is_online = True

                        cloud_new_devices.append({
                            'id': did,
                            'name': dev.get('deviceModel', 'Imou ') + ' ' + did[-4:],
                            'ip': lip if lip and lip != '0.0.0.0' else None,
                            'type': 'imou',
                            'source': 'cloud',
                            'is_online': is_online,
                            'auto_found': lip in auto_active_ips.values() if lip else False
                        })
                    else:
                        logger.info(f"   [SKIP CLOUD] ID {did} is already in DB.")

        except Exception as e:
            results['error'] = str(e)
            logger.error(f"Discovery error: {e}")
    else:
        logger.warning("Imou Cloud API keys NOT configured. Skipping cloud sync.")

    # --- METHOD C: MERGE AND IDENTIFY NEW DEVICES ---
    # Merge local and cloud (cloud prioritized for detail)
    new_devices = cloud_new_devices[:]
    seen_ids = set(d['id'] for d in new_devices)
    
    for dev in local_found:
        did = dev['urn'].lower()
        # If it's a serial number format (often the end of the URN)
        for did_part in did.split(':'):
            if len(did_part) > 10: # Likely S/N
                did = did_part
                break
                
        if did not in seen_ids:
            # Check if it exists in DB
            is_new = True
            for cam in all_cams:
                cam_did = (cam.get('imou_device_id') or "").strip().lower()
                if cam_did == did:
                    is_new = False
                    break
            
            if is_new:
                new_devices.append({
                    'id': did,
                    'ip': dev['ip'],
                    'type': 'imou', # Default to imou for now
                    'source': 'local'
                })
                seen_ids.add(did)

    results['new_devices'] = new_devices

    # --- APPLY UPDATES TO DB ---
    updated_ips = []
    for cam in all_cams:
        did = cam.get('imou_device_id')
        if did and did in found_ips:
            new_ip = found_ips[did]
            if cam['camera_ip'] != new_ip:
                logger.info(f"UPDATING IP for camera '{cam['camera_name']}': {cam['camera_ip']} -> {new_ip}")
                db._execute_query("UPDATE cameras SET camera_ip = %s WHERE id = %s", (new_ip, cam['id']))
                results['updated'] += 1
                updated_ips.append({'name': cam['camera_name'], 'ip': new_ip})
                
    return results

def update_camera_ips_by_discovery():
    """Compatibility wrapper for legacy calls."""
    res = discover_and_update_all_cameras()
    return res['updated']

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cams = discover_onvif_cameras()
    print(f"Discovered: {cams}")
