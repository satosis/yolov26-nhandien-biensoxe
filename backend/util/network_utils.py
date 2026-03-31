
import socket
import select
import time
import logging
import xml.etree.ElementTree as ET
import re

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

def discover_and_update_all_cameras():
    """
    Combine ONVIF (local) scan and Imou Cloud (AppID/Secret) scan to find camera IPs.
    Returns a dict with summary results: {'found_local': 0, 'found_cloud': 0, 'updated': 0, 'total': 0}
    """
    from backend.core.database import DatabaseManager
    from backend.util.imou_api import ImouClient
    db = DatabaseManager()
    
    results = {'found_local': 0, 'found_cloud': 0, 'updated': 0, 'total': 0}
    
    # 1. Get all cameras from DB to match them
    all_cams = db._execute_query("SELECT id, camera_name, imou_device_id, camera_ip FROM cameras", fetchall=True)
    results['total'] = len(all_cams)
    if results['total'] == 0: return results
    
    found_ips = {} # device_id -> ip
    
    # --- METHOD A: LOCAL ONVIF SCAN ---
    logger.info("Starting Local ONVIF scan...")
    local_found = discover_onvif_cameras(timeout=3)
    for dev in local_found:
        ip = dev['ip']
        urn = dev['urn']
        for cam in all_cams:
            did = cam.get('imou_device_id')
            if did and (did.lower() in urn.lower() or urn.lower().endswith(did.lower())):
                found_ips[did] = ip
                results['found_local'] += 1
                break
                
    # --- METHOD B: IMOU CLOUD API SYNC ---
    imou_keys = db.get_imou_api_keys()
    if imou_keys.get('app_id') and imou_keys.get('app_secret'):
        logger.info("Starting Imou Cloud API sync...")
        client = ImouClient(imou_keys['app_id'], imou_keys['app_secret'])
        cloud_devices = client.list_devices()
        for dev in cloud_devices:
            did = dev.get('deviceId')
            lip = dev.get('deviceLocalIp')
            if did and lip and lip != "0.0.0.0":
                # Only use cloud IP if local scan didn't find it (or as confirmation)
                if did not in found_ips:
                    found_ips[did] = lip
                    results['found_cloud'] += 1
                else:
                    # Just mark it as found even if redundant
                    results['found_cloud'] += 1

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
