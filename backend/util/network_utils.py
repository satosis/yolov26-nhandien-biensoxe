
import socket
import select
import time
import logging
import xml.etree.ElementTree as ET
import re
import uuid as uuid_mod
import concurrent.futures
from typing import List, Dict
from urllib.parse import urlparse

logger = logging.getLogger("network_utils")

WS_DISCOVERY_MCAST_ADDR = "239.255.255.250"
WS_DISCOVERY_PORT = 3702
WS_DISCOVERY_PROBE = (
    '<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    '<Header><MessageID xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
    f'uuid:{uuid_mod.uuid4()}</MessageID>'
    '<To xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">urn:schemas-xmlsoap-org:ws:2004:08:addressing:discovery</To>'
    '<Action xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">http://schemas.xmlsoap.org/ws/2004/08/discovery/Probe</Action>'
    '</Header><Body><Probe xmlns="http://schemas.xmlsoap.org/ws/2004/08/discovery">'
    '<Types>dn:NetworkVideoTransmitter</Types></Probe></Body></Envelope>'
)

# ─── ONVIF Scope Parsers ─────────────────────────────────────────────
def _parse_scopes(scope_text: str) -> dict:
    """Parse ONVIF scope URIs into structured data.
    Scopes look like: onvif://www.onvif.org/name/MyCamera onvif://www.onvif.org/hardware/IPC-xxx
    """
    info = {}
    if not scope_text:
        return info
    for token in scope_text.strip().split():
        token = token.strip()
        lower = token.lower()
        if '/name/' in lower:
            info['name'] = token.split('/name/')[-1].replace('%20', ' ')
        elif '/hardware/' in lower:
            info['hardware'] = token.split('/hardware/')[-1].replace('%20', ' ')
        elif '/location/' in lower:
            info['location'] = token.split('/location/')[-1].replace('%20', ' ')
        elif '/type/' in lower:
            info['type'] = token.split('/type/')[-1]
        elif '/manufacturer/' in lower or '/mfr/' in lower:
            part = token.split('/manufacturer/')[-1] if '/manufacturer/' in lower else token.split('/mfr/')[-1]
            info['manufacturer'] = part.replace('%20', ' ')
        elif '/model/' in lower:
            info['model'] = token.split('/model/')[-1].replace('%20', ' ')
        elif '/firmware/' in lower:
            info['firmware'] = token.split('/firmware/')[-1].replace('%20', ' ')
        elif '/profile/' in lower:
            profiles = info.get('profiles', [])
            profiles.append(token.split('/profile/')[-1])
            info['profiles'] = profiles
    return info


def discover_onvif_cameras(timeout=4):
    """
    Scan local network for ONVIF-compatible cameras using WS-Discovery.
    Returns a list of dicts with rich info:
    [{'ip': '...', 'urn': '...', 'xaddrs': '...', 'scopes': {...}, 'types': '...'}]
    """
    cameras = []
    seen_ips = set()
    
    # Create a fresh probe with unique UUID each time
    probe_xml = (
        '<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        '<Header><MessageID xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
        f'uuid:{uuid_mod.uuid4()}</MessageID>'
        '<To xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
        'urn:schemas-xmlsoap-org:ws:2004:08:addressing:discovery</To>'
        '<Action xmlns="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
        'http://schemas.xmlsoap.org/ws/2004/08/discovery/Probe</Action>'
        '</Header><Body><Probe xmlns="http://schemas.xmlsoap.org/ws/2004/08/discovery">'
        '<Types>dn:NetworkVideoTransmitter</Types></Probe></Body></Envelope>'
    )
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setblocking(False)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    # Allow reuse
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    # Send probe twice for reliability
    for _ in range(2):
        sock.sendto(probe_xml.encode(), (WS_DISCOVERY_MCAST_ADDR, WS_DISCOVERY_PORT))
        time.sleep(0.1)
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        ready = select.select([sock], [], [], 0.5)
        if ready[0]:
            try:
                data, addr = sock.recvfrom(65535)  # bigger buffer for full responses
                ip = addr[0]
                if ip in seen_ips:
                    continue
                
                xml_str = data.decode('utf-8', errors='ignore')
                
                # Parse XML to extract structured data
                cam_info = _parse_probe_match(xml_str, ip)
                if cam_info:
                    cameras.append(cam_info)
                    seen_ips.add(ip)
                    logger.info(f"ONVIF Discovery: Found {cam_info.get('manufacturer', '?')} "
                               f"{cam_info.get('model', '?')} @ {ip}")
            except Exception as e:
                logger.debug(f"ONVIF Discovery: Error processing response from {addr}: {e}")
                
    sock.close()
    logger.info(f"ONVIF WS-Discovery found {len(cameras)} camera(s) in {timeout}s")
    return cameras


def _parse_probe_match(xml_str: str, fallback_ip: str) -> dict | None:
    """Parse a WS-Discovery ProbeMatch XML response into structured data."""
    try:
        # Remove BOM if present
        xml_str = xml_str.lstrip('\ufeff')
        root = ET.fromstring(xml_str)
        
        # Namespace map for ONVIF WS-Discovery responses
        ns = {
            's': 'http://www.w3.org/2003/05/soap-envelope',
            'd': 'http://schemas.xmlsoap.org/ws/2005/04/discovery',
            'a': 'http://schemas.xmlsoap.org/ws/2004/08/addressing',
            # Some devices use 2004 discovery ns
            'd04': 'http://schemas.xmlsoap.org/ws/2004/08/discovery',
        }
        
        # Try both 2005 and 2004 discovery namespaces
        probe_match = root.find('.//d:ProbeMatch', ns) or root.find('.//d04:ProbeMatch', ns)
        if probe_match is None:
            # Fallback: regex extraction
            return _parse_probe_match_regex(xml_str, fallback_ip)
        
        # Extract EndpointReference (URN)
        urn = ""
        epr = probe_match.find('.//a:EndpointReference/a:Address', ns)
        if epr is not None and epr.text:
            urn = epr.text.strip()
        
        # Extract URN from Address (uuid part)
        urn_id = ""
        urn_match = re.search(r'uuid:([a-zA-Z0-9\-]+)', urn)
        if urn_match:
            urn_id = urn_match.group(1)
        
        # Extract XAddrs (ONVIF service URL)
        xaddrs_text = ""
        xaddrs_el = probe_match.find('d:XAddrs', ns) or probe_match.find('d04:XAddrs', ns)
        if xaddrs_el is not None and xaddrs_el.text:
            xaddrs_text = xaddrs_el.text.strip()
        
        # Extract IP from XAddrs if possible
        ip = fallback_ip
        onvif_port = 80
        if xaddrs_text:
            for xaddr in xaddrs_text.split():
                try:
                    parsed = urlparse(xaddr.strip())
                    if parsed.hostname:
                        ip = parsed.hostname
                        onvif_port = parsed.port or 80
                        break
                except:
                    pass
        
        # Extract Scopes
        scopes_text = ""
        scopes_el = probe_match.find('d:Scopes', ns) or probe_match.find('d04:Scopes', ns)
        if scopes_el is not None and scopes_el.text:
            scopes_text = scopes_el.text.strip()
        
        scopes = _parse_scopes(scopes_text)
        
        # Extract Types
        types_text = ""
        types_el = probe_match.find('d:Types', ns) or probe_match.find('d04:Types', ns)
        if types_el is not None and types_el.text:
            types_text = types_el.text.strip()
        
        # Determine manufacturer from scopes or heuristics
        manufacturer = scopes.get('manufacturer', '')
        if not manufacturer:
            # Try to guess from XAddrs or scope content
            combined = (xaddrs_text + scopes_text + urn).lower()
            if 'dahua' in combined or 'imou' in combined:
                manufacturer = 'Dahua/Imou'
            elif 'hikvision' in combined or 'hikv' in combined:
                manufacturer = 'Hikvision'
            elif 'axis' in combined:
                manufacturer = 'Axis'
            elif 'tapo' in combined or 'tp-link' in combined or 'vigi' in combined:
                manufacturer = 'TP-Link'
            elif 'uniview' in combined or 'unv' in combined:
                manufacturer = 'Uniview'
            elif 'hanwha' in combined or 'samsung' in combined:
                manufacturer = 'Hanwha'
            elif 'bosch' in combined:
                manufacturer = 'Bosch'
        
        return {
            'ip': ip,
            'urn': urn_id,
            'urn_full': urn,
            'xaddrs': xaddrs_text,
            'onvif_port': onvif_port,
            'scopes': scopes,
            'scopes_raw': scopes_text,
            'types': types_text,
            'manufacturer': manufacturer or scopes.get('manufacturer', 'Unknown'),
            'model': scopes.get('model', scopes.get('hardware', '')),
            'name': scopes.get('name', ''),
            'firmware': scopes.get('firmware', ''),
            'profiles': scopes.get('profiles', []),
            'raw_xml': xml_str
        }
        
    except ET.ParseError:
        return _parse_probe_match_regex(xml_str, fallback_ip)
    except Exception as e:
        logger.debug(f"Error parsing ProbeMatch: {e}")
        return _parse_probe_match_regex(xml_str, fallback_ip)


def _parse_probe_match_regex(xml_str: str, fallback_ip: str) -> dict | None:
    """Fallback regex-based parser for malformed XML responses."""
    urn_match = re.search(r'uuid:([a-zA-Z0-9\-]+)', xml_str)
    urn = urn_match.group(1) if urn_match else ""
    
    # Try to find XAddrs
    xaddrs_match = re.search(r'<[^>]*XAddrs[^>]*>(.*?)</[^>]*XAddrs>', xml_str, re.DOTALL)
    xaddrs = xaddrs_match.group(1).strip() if xaddrs_match else ""
    
    ip = fallback_ip
    onvif_port = 80
    if xaddrs:
        try:
            parsed = urlparse(xaddrs.split()[0])
            if parsed.hostname:
                ip = parsed.hostname
                onvif_port = parsed.port or 80
        except:
            pass
    
    # Extract scopes
    scopes_match = re.search(r'<[^>]*Scopes[^>]*>(.*?)</[^>]*Scopes>', xml_str, re.DOTALL)
    scopes_text = scopes_match.group(1).strip() if scopes_match else ""
    scopes = _parse_scopes(scopes_text)
    
    return {
        'ip': ip,
        'urn': urn,
        'urn_full': f'urn:uuid:{urn}' if urn else '',
        'xaddrs': xaddrs,
        'onvif_port': onvif_port,
        'scopes': scopes,
        'scopes_raw': scopes_text,
        'types': '',
        'manufacturer': scopes.get('manufacturer', 'Unknown'),
        'model': scopes.get('model', scopes.get('hardware', '')),
        'name': scopes.get('name', ''),
        'firmware': scopes.get('firmware', ''),
        'profiles': scopes.get('profiles', []),
        'raw_xml': xml_str
    }


def get_onvif_device_info(ip: str, port: int = 80, username: str = 'admin', 
                           password: str = '', timeout: int = 5) -> dict | None:
    """
    Connect to an ONVIF camera and retrieve device information.
    Uses onvif-zeep library for proper ONVIF SOAP communication.
    Returns dict with manufacturer, model, firmware, serial, hardware info.
    """
    try:
        from onvif import ONVIFCamera
        cam = ONVIFCamera(ip, port, username, password, 
                          no_cache=True)
        device_service = cam.create_devicemgmt_service()
        info = device_service.GetDeviceInformation()
        return {
            'manufacturer': getattr(info, 'Manufacturer', ''),
            'model': getattr(info, 'Model', ''),
            'firmware': getattr(info, 'FirmwareVersion', ''),
            'serial': getattr(info, 'SerialNumber', ''),
            'hardware': getattr(info, 'HardwareId', ''),
        }
    except Exception as e:
        logger.debug(f"ONVIF device info probe for {ip}:{port} failed: {e}")
        return None


def discover_onvif_only(timeout: int = 5, probe_details: bool = True) -> list[dict]:
    """
    ONVIF-only discovery. Returns list of discovered cameras with:
    - ip, onvif_port, manufacturer, model, firmware, serial
    - is_online (verified by TCP port check)
    - xaddrs, scopes, urn
    
    If probe_details=True, attempts to get device info via ONVIF SOAP (requires credentials).
    """
    # Step 1: WS-Discovery multicast scan
    raw_cameras = discover_onvif_cameras(timeout=timeout)
    
    results = []
    for cam in raw_cameras:
        ip = cam['ip']
        onvif_port = cam.get('onvif_port', 80)
        
        # Step 2: Verify camera is reachable (TCP port check)
        is_online = probe_ip(ip, ports=[onvif_port, 554, 80, 8080], timeout=1.0)
        
        entry = {
            'id': cam.get('urn', ''),
            'ip': ip,
            'onvif_port': onvif_port,
            'manufacturer': cam.get('manufacturer', 'Unknown'),
            'model': cam.get('model', ''),
            'name': cam.get('name', ''),
            'firmware': cam.get('firmware', ''),
            'serial': '',  # Will be filled by device info probe
            'xaddrs': cam.get('xaddrs', ''),
            'profiles': cam.get('profiles', []),
            'is_online': is_online,
            'source': 'onvif',
            'type': _guess_provider_type(cam.get('manufacturer', ''), cam.get('model', '')),
        }
        
        # Step 3: Optional detailed probe via ONVIF SOAP
        if probe_details and is_online:
            try:
                dev_info = get_onvif_device_info(ip, onvif_port, timeout=3)
                if dev_info:
                    if dev_info.get('manufacturer'):
                        entry['manufacturer'] = dev_info['manufacturer']
                    if dev_info.get('model'):
                        entry['model'] = dev_info['model']
                    if dev_info.get('firmware'):
                        entry['firmware'] = dev_info['firmware']
                    if dev_info.get('serial'):
                        entry['serial'] = dev_info['serial']
                        entry['id'] = dev_info['serial']  # Use serial as primary ID
                    if dev_info.get('hardware'):
                        entry['hardware'] = dev_info['hardware']
                    # Re-evaluate provider type with better info
                    entry['type'] = _guess_provider_type(entry['manufacturer'], entry['model'])
            except Exception as e:
                logger.debug(f"Detail probe failed for {ip}: {e}")
        
        # Build a display name
        if not entry['name']:
            parts = [entry['manufacturer'], entry['model']]
            entry['name'] = ' '.join(p for p in parts if p) or f'Camera @ {ip}'
        
        results.append(entry)
    
    logger.info(f"ONVIF-only discovery: {len(results)} camera(s) found")
    return results


def _guess_provider_type(manufacturer: str, model: str) -> str:
    """Guess the provider_key from manufacturer/model strings."""
    combined = f"{manufacturer} {model}".lower()
    if any(k in combined for k in ['dahua', 'imou', 'lechange']):
        return 'imou'
    if any(k in combined for k in ['hikvision', 'hikv', 'ds-']):
        return 'hikvision'
    if any(k in combined for k in ['axis']):
        return 'axis'
    if any(k in combined for k in ['tapo', 'tp-link', 'vigi']):
        return 'tapo'
    if any(k in combined for k in ['uniview', 'unv']):
        return 'uniview'
    if any(k in combined for k in ['hanwha', 'samsung', 'wisenet']):
        return 'hanwha'
    if any(k in combined for k in ['bosch']):
        return 'bosch'
    return 'onvif'

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
    ONVIF is the PRIMARY source for IP and status.
    Returns a dict with summary results.
    """
    from backend.core.database import DatabaseManager
    from backend.util.imou_api import ImouClient
    db = DatabaseManager()
    
    results = {'found_local': 0, 'found_cloud': 0, 'updated': 0, 'total': 0, 'error': None, 'new_devices': []}
    
    # 1. Get all cameras from DB
    all_cams = db._execute_query("SELECT id, camera_name, imou_device_id, camera_ip FROM cameras", fetchall=True)
    results['total'] = len(all_cams)
    
    existing_ids = [str(cam.get('imou_device_id')).strip().lower() for cam in all_cams if cam.get('imou_device_id')]
    existing_ips = [cam.get('camera_ip') for cam in all_cams if cam.get('camera_ip')]
    logger.info(f"Discovery: {len(all_cams)} cameras in DB. Existing IDs: {existing_ids}")
    
    found_ips = {}  # device_id -> ip
    
    # --- METHOD A: LOCAL ONVIF SCAN (PRIMARY) ---
    logger.info("Starting ONVIF WS-Discovery scan...")
    local_found = discover_onvif_only(timeout=5, probe_details=False)
    logger.info(f"ONVIF scan found {len(local_found)} device(s).")
    results['found_local'] = len(local_found)
    
    # Match ONVIF results with existing DB cameras
    for dev in local_found:
        ip = dev['ip']
        dev_id = (dev.get('id') or dev.get('serial') or '').lower()
        
        for cam in all_cams:
            did_raw = cam.get('imou_device_id')
            if not did_raw:
                continue
            did = did_raw.strip().lower()
            # Match: device_id in URN/serial, or IP matches
            if (did_raw and (did in dev_id or dev_id in did)) or cam.get('camera_ip') == ip:
                logger.info(f"  [MATCH ONVIF] Camera '{cam['camera_name']}' matches {ip}")
                found_ips[did] = ip
                break
    
    # --- METHOD B: IMOU CLOUD API SYNC (SECONDARY) ---
    imou_keys = db.get_imou_api_keys()
    cloud_new_devices = []
    if imou_keys.get('app_id') and imou_keys.get('app_secret'):
        logger.info("Starting Imou Cloud API sync...")
        try:
            client = ImouClient(imou_keys['app_id'], imou_keys['app_secret'])
            cloud_devices = client.list_devices()
            logger.info(f"Cloud API found {len(cloud_devices)} device(s).")
            
            # Auto-match IPs from ONVIF for cloud devices missing IP
            onvif_ip_map = {d.get('id', '').lower(): d['ip'] for d in local_found if d.get('id')}
            
            for dev in cloud_devices:
                did_raw = dev.get('deviceId')
                lip = dev.get('deviceLocalIp')
                
                if did_raw:
                    did = did_raw.strip().lower()
                    
                    # Try to match IP from ONVIF results
                    if (not lip or lip == "0.0.0.0"):
                        for onvif_id, onvif_ip in onvif_ip_map.items():
                            if did in onvif_id or onvif_id in did:
                                lip = onvif_ip
                                logger.info(f"  [ONVIF→CLOUD] Matched {did} to {lip} via ONVIF")
                                break
                    
                    results['found_cloud'] += 1
                    
                    if did in existing_ids:
                        if lip and lip != "0.0.0.0" and did not in found_ips:
                            found_ips[did] = lip
                    else:
                        # New device from cloud
                        status_keys = ['status', 'deviceOnline', 'online', 'deviceStatus', 'state']
                        raw_status = next((dev.get(k) for k in status_keys if dev.get(k) is not None), None)
                        
                        is_online = True
                        if raw_status is not None:
                            status_val = str(raw_status).lower()
                            if status_val in ['0', 'offline', 'sleep', 'off', 'false']:
                                is_online = False
                        
                        cloud_new_devices.append({
                            'id': did,
                            'name': dev.get('deviceModel', 'Imou') + ' ' + did[-4:],
                            'ip': lip if lip and lip != '0.0.0.0' else None,
                            'type': 'imou',
                            'source': 'cloud',
                            'manufacturer': 'Dahua/Imou',
                            'model': dev.get('deviceModel', ''),
                            'is_online': is_online,
                            'auto_found': False
                        })
        except Exception as e:
            results['error'] = str(e)
            logger.error(f"Cloud discovery error: {e}")
    else:
        logger.warning("Imou Cloud API keys NOT configured. Skipping cloud sync.")
    
    # --- METHOD C: MERGE — ONVIF first, then cloud new devices ---
    new_devices = []
    seen_ids = set()
    seen_ips = set(existing_ips)
    
    # Add ONVIF-found devices that are NOT in DB
    for dev in local_found:
        dev_id = (dev.get('id') or dev.get('serial') or dev.get('urn', '')).lower()
        ip = dev['ip']
        
        # Check if this device is already in DB (by ID or IP match)
        in_db = False
        for cam in all_cams:
            cam_did = (cam.get('imou_device_id') or '').strip().lower()
            cam_ip = cam.get('camera_ip', '')
            if (cam_did and (cam_did in dev_id or dev_id in cam_did)) or cam_ip == ip:
                in_db = True
                break
        
        if not in_db and dev_id not in seen_ids and ip not in seen_ips:
            new_devices.append({
                'id': dev_id or ip,
                'name': dev.get('name', f"Camera @ {ip}"),
                'ip': ip,
                'onvif_port': dev.get('onvif_port', 80),
                'type': dev.get('type', 'onvif'),
                'source': 'onvif',
                'manufacturer': dev.get('manufacturer', 'Unknown'),
                'model': dev.get('model', ''),
                'firmware': dev.get('firmware', ''),
                'serial': dev.get('serial', ''),
                'is_online': dev.get('is_online', True),
                'profiles': dev.get('profiles', []),
                'auto_found': False
            })
            seen_ids.add(dev_id)
            seen_ips.add(ip)
    
    # Add cloud-only new devices
    for dev in cloud_new_devices:
        if dev['id'] not in seen_ids:
            ip = dev.get('ip')
            if ip and ip in seen_ips:
                continue  # Already found via ONVIF
            new_devices.append(dev)
            seen_ids.add(dev['id'])
            if ip:
                seen_ips.add(ip)
    
    results['new_devices'] = new_devices
    
    # --- APPLY UPDATES TO DB ---
    for cam in all_cams:
        did = cam.get('imou_device_id')
        if did and did in found_ips:
            new_ip = found_ips[did]
            if cam['camera_ip'] != new_ip:
                logger.info(f"UPDATING IP: '{cam['camera_name']}' {cam['camera_ip']} → {new_ip}")
                db._execute_query("UPDATE cameras SET camera_ip = %s WHERE id = %s", (new_ip, cam['id']))
                results['updated'] += 1
    
    return results


def update_camera_ips_by_discovery():
    """Compatibility wrapper for legacy calls."""
    res = discover_and_update_all_cameras()
    return res['updated']

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== ONVIF Camera Discovery ===")
    cams = discover_onvif_only(timeout=5, probe_details=False)
    for c in cams:
        print(f"  {c['manufacturer']} {c['model']} @ {c['ip']}:{c['onvif_port']} "
              f"[{'ONLINE' if c['is_online'] else 'OFFLINE'}] ID={c['id']}")
    print(f"\nTotal: {len(cams)} camera(s) found")
