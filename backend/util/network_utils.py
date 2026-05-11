
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

# ─── MAC OUI → Camera Vendor (offline lookup, ~50 entries) ──────────────────
_CAMERA_OUI_MAP = {
    # Dahua / Imou
    'e0:50:8b': 'Dahua/Imou', 'bc:ad:28': 'Dahua/Imou', 'a4:02:b9': 'Dahua/Imou',
    '4c:11:bf': 'Dahua/Imou', 'c4:64:13': 'Dahua/Imou', '6c:21:a2': 'Dahua/Imou',
    '90:02:a9': 'Dahua/Imou', '3c:ef:8c': 'Dahua/Imou', '30:24:50': 'Dahua/Imou',
    # Hikvision
    '44:19:b6': 'Hikvision', 'b4:a3:82': 'Hikvision', 'c0:56:e3': 'Hikvision',
    'bc:6e:76': 'Hikvision', '54:c4:15': 'Hikvision',
    # Axis
    'ac:cc:8e': 'Axis', 'b8:a4:4f': 'Axis', '00:40:8c': 'Axis',
    # TP-Link / Tapo / Vigi
    '50:c7:bf': 'TP-Link', '6c:5a:b0': 'TP-Link', 'b0:be:76': 'TP-Link',
    # Uniview
    '9c:4e:36': 'Uniview',
    # Reolink
    'ec:71:db': 'Reolink',
    # Hanwha / Samsung
    '00:09:18': 'Hanwha',
    # Bosch
    '00:07:5f': 'Bosch',
}

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


# ─── Local Network Helpers ──────────────────────────────────
def _get_local_broadcast_addresses() -> List[str]:
    """Get broadcast addresses for all local network interfaces."""
    import subprocess
    addrs = []
    try:
        # Works on Linux and macOS
        output = subprocess.check_output(['ip', '-4', 'addr'], timeout=3, stderr=subprocess.DEVNULL).decode()
        for line in output.split('\n'):
            if 'brd' in line and 'inet' in line:
                parts = line.strip().split()
                for i, p in enumerate(parts):
                    if p == 'brd' and i + 1 < len(parts):
                        addrs.append(parts[i + 1])
    except Exception:
        try:
            # macOS fallback
            output = subprocess.check_output(['ifconfig'], timeout=3, stderr=subprocess.DEVNULL).decode()
            for line in output.split('\n'):
                if 'broadcast' in line:
                    parts = line.strip().split()
                    for i, p in enumerate(parts):
                        if p == 'broadcast' and i + 1 < len(parts):
                            addrs.append(parts[i + 1])
        except Exception:
            pass
    return addrs


# ─── Dahua / Imou Discovery ──────────────────────────────────
def discover_dahua_cameras(timeout=5.0) -> List[Dict]:
    """
    Discover Dahua/Imou devices using their proprietary UDP protocol (Port 37810).
    This is extremely reliable for getting the Serial Number (deviceId).
    Sends broadcasts on all local subnets for multi-network coverage.
    """
    import json
    devices = []
    DAHUA_DISCOVERY_PORT = 37810

    # Modern Dahua JSON-based discovery probe
    probe = {
        "method": "client.index",
        "params": {"index": 1},
        "id": 1,
        "session": 0
    }
    probe_str = json.dumps(probe)

    # Get all local broadcast addresses for multi-subnet coverage
    broadcast_addrs = _get_local_broadcast_addresses()
    broadcast_addrs.extend(['255.255.255.255', '<broadcast>'])
    # Deduplicate
    broadcast_addrs = list(dict.fromkeys(broadcast_addrs))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)

    try:
        logger.info(f"Dahua Discovery: Broadcasting to {broadcast_addrs}")
        for addr in broadcast_addrs:
            try:
                sock.sendto(probe_str.encode(), (addr, DAHUA_DISCOVERY_PORT))
            except Exception as e:
                logger.debug(f"Dahua Discovery: Failed to send to {addr}: {e}")
        
        start_time = time.time()
        seen_serials = set()
        
        while time.time() - start_time < timeout:
            try:
                data, addr = sock.recvfrom(4096)
                resp_str = data.decode('utf-8', errors='ignore')
                if '"result"' in resp_str and '"sn"' in resp_str:
                    resp = json.loads(resp_str)
                    res = resp.get('result', {})
                    sn = res.get('sn', '').lower()
                    if sn and sn not in seen_serials:
                        devices.append({
                            'id': sn,
                            'ip': addr[0],
                            'port': res.get('port', 37777),
                            'model': res.get('deviceType', ''),
                            'manufacturer': 'Dahua/Imou',
                            'serial': sn,
                            'source': 'dahua_udp'
                        })
                        seen_serials.add(sn)
                        logger.info(f"Dahua Discovery: Found {sn} @ {addr[0]}")
            except socket.timeout:
                break
            except Exception as e:
                logger.debug(f"Dahua Discovery parse error: {e}")
    finally:
        sock.close()
    
    return devices

def probe_ip(ip: str, ports=[80, 554, 8000, 37810, 37777, 8080], timeout=1.5) -> bool:
    """Check if any of the target ports are open on the given IP. Higher timeout for Wi-Fi."""
    for port in ports:
        try:
            with socket.create_connection((ip, port), timeout=timeout) as sock:
                return True
        except OSError:
            continue
    return False

def scan_subnet_for_cameras(subnet_prefixes: List[str], max_workers: int = 150) -> List[str]:
    """
    Scans multiple /24 subnets for active devices on common camera ports.
    subnet_prefixes should be like ['192.168.1', '192.168.0']
    """
    if isinstance(subnet_prefixes, str):
        subnet_prefixes = [subnet_prefixes]
        
    logger.info(f"Subnet Scan: Scanning {len(subnet_prefixes)} subnet(s)...")
    found_ips = []
    
    def check_ip(ip):
        if probe_ip(ip):
            return ip
        return None

    ips_to_check = []
    for prefix in subnet_prefixes:
        ips_to_check.extend([f"{prefix}.{i}" for i in range(1, 255)])
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ip = {executor.submit(check_ip, ip): ip for ip in ips_to_check}
        for future in concurrent.futures.as_completed(future_to_ip):
            ip = future.result()
            if ip:
                found_ips.append(ip)
                
    logger.info(f"Subnet Scan: Found {len(found_ips)} active IP(s) across all subnets")
    return found_ips


def _get_local_ips() -> List[str]:
    """Get local non-loopback IPv4 addresses for multi-interface multicast."""
    import subprocess
    ips = []
    try:
        output = subprocess.check_output(['ip', '-4', 'addr'], timeout=3, stderr=subprocess.DEVNULL).decode()
        for line in output.split('\n'):
            if 'inet ' in line and '127.0.0.1' not in line:
                parts = line.strip().split()
                for p in parts:
                    if '/' in p and p.split('/')[0].count('.') == 3:
                        ips.append(p.split('/')[0])
    except Exception:
        try:
            output = subprocess.check_output(['ifconfig'], timeout=3, stderr=subprocess.DEVNULL).decode()
            for line in output.split('\n'):
                if 'inet ' in line and '127.0.0.1' not in line:
                    parts = line.strip().split()
                    for i, p in enumerate(parts):
                        if p == 'inet' and i + 1 < len(parts):
                            ips.append(parts[i + 1])
        except Exception:
            pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('239.255.255.250', 3702))
            ips = [s.getsockname()[0]]
            s.close()
        except Exception:
            pass
    return ips


def discover_onvif_cameras(timeout=6):
    """
    Scan local network for ONVIF-compatible cameras using WS-Discovery.
    Sends multicast probes on ALL local interfaces for multi-subnet coverage.
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
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    probe_data = probe_xml.encode()
    local_ips = _get_local_ips()

    # Send probe 3 times on each interface for reliability
    for _ in range(3):
        # Default send (OS picks interface)
        try:
            sock.sendto(probe_data, (WS_DISCOVERY_MCAST_ADDR, WS_DISCOVERY_PORT))
        except Exception as e:
            logger.debug(f"ONVIF Discovery: Default send failed: {e}")

        # Send on each local interface via IP_MULTICAST_IF
        for local_ip in local_ips:
            try:
                import struct
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(local_ip)
                )
                sock.sendto(probe_data, (WS_DISCOVERY_MCAST_ADDR, WS_DISCOVERY_PORT))
            except Exception as e:
                logger.debug(f"ONVIF Discovery: Send on {local_ip} failed: {e}")
        time.sleep(0.15)

    start_time = time.time()
    while time.time() - start_time < timeout:
        ready = select.select([sock], [], [], 0.5)
        if ready[0]:
            try:
                data, addr = sock.recvfrom(65535)
                ip = addr[0]
                if ip in seen_ips:
                    continue

                xml_str = data.decode('utf-8', errors='ignore')
                cam_info = _parse_probe_match(xml_str, ip)
                if cam_info:
                    cameras.append(cam_info)
                    seen_ips.add(ip)
                    logger.info(f"ONVIF Discovery: Found {cam_info.get('manufacturer', '?')} "
                               f"{cam_info.get('model', '?')} @ {ip}")
            except Exception as e:
                logger.debug(f"ONVIF Discovery: Error processing response from {addr}: {e}")

    sock.close()
    logger.info(f"ONVIF WS-Discovery found {len(cameras)} camera(s) in {timeout}s "
                f"(probed {len(local_ips)} interface(s))")
    return cameras


def probe_onvif_unicast(ip: str, port: int = 80) -> dict | None:
    """
    Send a WS-Discovery Probe directly to a specific IP via UDP unicast.
    Useful for cameras that don't respond to multicast (firewall, VLAN, firmware bug).
    Returns parsed ProbeMatch dict or None.
    """
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

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.sendto(probe_xml.encode(), (ip, WS_DISCOVERY_PORT))
        data, addr = sock.recvfrom(65535)
        sock.close()
        xml_str = data.decode('utf-8', errors='ignore')
        return _parse_probe_match(xml_str, ip)
    except Exception as e:
        logger.debug(f"Unicast ONVIF probe to {ip}:{port} failed: {e}")
        return None


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
                except Exception:
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
        except Exception:
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


def get_onvif_full_info(ip: str, port: int = 80, username: str = 'admin',
                        password: str = '', timeout: int = 6) -> dict | None:
    """
    Connect to an ONVIF camera and retrieve device info + media profiles + RTSP URLs.
    Combines GetDeviceInformation, GetProfiles, and GetStreamUri in a single SOAP session
    to minimize WSDL parsing overhead (important for ARM/Orange Pi).

    Returns dict with:
        manufacturer, model, firmware, serial, hardware,
        rtsp_main (first profile), rtsp_sub (second profile if exists),
        onvif_profiles (list of profile dicts), has_ptz (bool)
    """
    try:
        from onvif import ONVIFCamera
        from zeep.transports import Transport
        from requests import Session

        session = Session()
        session.timeout = timeout
        transport = Transport(timeout=timeout, session=session)

        cam = ONVIFCamera(ip, port, username, password,
                          no_cache=True, transport=transport)

        # --- Device Information ---
        device_service = cam.create_devicemgmt_service()
        info = device_service.GetDeviceInformation()
        result = {
            'manufacturer': getattr(info, 'Manufacturer', ''),
            'model': getattr(info, 'Model', ''),
            'firmware': getattr(info, 'FirmwareVersion', ''),
            'serial': getattr(info, 'SerialNumber', ''),
            'hardware': getattr(info, 'HardwareId', ''),
            'rtsp_main': '',
            'rtsp_sub': None,
            'onvif_profiles': [],
            'has_ptz': False,
        }

        # --- Media Profiles + Stream URIs ---
        try:
            media_service = cam.create_media_service()
            profiles = media_service.GetProfiles()

            for i, profile in enumerate(profiles or []):
                token = profile.token
                name = getattr(profile, 'Name', token)

                # Extract resolution from VideoEncoderConfiguration
                width, height, encoding = 0, 0, ''
                vec = getattr(profile, 'VideoEncoderConfiguration', None)
                if vec:
                    encoding = getattr(vec, 'Encoding', '')
                    res = getattr(vec, 'Resolution', None)
                    if res:
                        width = getattr(res, 'Width', 0)
                        height = getattr(res, 'Height', 0)

                # Get RTSP stream URI
                rtsp_url = ''
                try:
                    stream_setup = media_service.create_type('GetStreamUri')
                    stream_setup.ProfileToken = token
                    stream_setup.StreamSetup = {
                        'Stream': 'RTP-Unicast',
                        'Transport': {'Protocol': 'RTSP'}
                    }
                    uri_resp = media_service.GetStreamUri(stream_setup)
                    rtsp_url = getattr(uri_resp, 'Uri', '') or ''

                    # Normalize: inject credentials if URL lacks auth
                    if rtsp_url and username:
                        parsed = urlparse(rtsp_url)
                        if not parsed.username:
                            from urllib.parse import urlunparse
                            netloc = f"{username}:{password}@{parsed.hostname}"
                            if parsed.port:
                                netloc += f":{parsed.port}"
                            rtsp_url = urlunparse((
                                parsed.scheme, netloc, parsed.path,
                                parsed.params, parsed.query, parsed.fragment
                            ))
                except Exception as e:
                    logger.debug(f"GetStreamUri failed for profile {token} on {ip}: {e}")

                profile_info = {
                    'token': token,
                    'name': str(name),
                    'rtsp_url': rtsp_url,
                    'width': width,
                    'height': height,
                    'encoding': str(encoding),
                }
                result['onvif_profiles'].append(profile_info)

                if i == 0 and rtsp_url:
                    result['rtsp_main'] = rtsp_url
                elif i == 1 and rtsp_url:
                    result['rtsp_sub'] = rtsp_url

        except Exception as e:
            logger.debug(f"Media service probe failed for {ip}:{port}: {e}")

        # --- PTZ Detection ---
        try:
            ptz_service = cam.create_ptz_service()
            ptz_service.GetConfigurations()
            result['has_ptz'] = True
        except Exception:
            pass

        logger.info(f"ONVIF full info for {ip}:{port}: {result['manufacturer']} {result['model']}, "
                     f"{len(result['onvif_profiles'])} profiles, PTZ={result['has_ptz']}")
        return result

    except Exception as e:
        logger.debug(f"ONVIF full info probe for {ip}:{port} failed: {e}")
        return None


def get_onvif_device_info(ip: str, port: int = 80, username: str = 'admin',
                           password: str = '', timeout: int = 5) -> dict | None:
    """
    Connect to an ONVIF camera and retrieve device information.
    Thin wrapper around get_onvif_full_info() — returns only device fields.
    """
    full = get_onvif_full_info(ip, port, username, password, timeout)
    if full:
        return {
            'manufacturer': full['manufacturer'],
            'model': full['model'],
            'firmware': full['firmware'],
            'serial': full['serial'],
            'hardware': full['hardware'],
        }
    return None


def discover_onvif_only(timeout: int = 5, probe_details: bool = True,
                        onvif_username: str = 'admin', onvif_password: str = '') -> list[dict]:
    """
    ONVIF-only discovery. Returns list of discovered cameras with:
    - ip, onvif_port, manufacturer, model, firmware, serial
    - is_online (verified by TCP port check)
    - xaddrs, scopes, urn
    - rtsp_url_onvif, rtsp_sub_onvif, onvif_profiles, has_ptz (when probe_details=True)

    If probe_details=True, attempts to get full ONVIF info including RTSP stream URIs.
    Uses ThreadPoolExecutor(max_workers=4) for concurrent probes on ARM.
    """
    # Step 1: WS-Discovery multicast scan
    raw_cameras = discover_onvif_cameras(timeout=timeout)

    results = []
    online_entries = []

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
            'serial': '',
            'xaddrs': cam.get('xaddrs', ''),
            'profiles': cam.get('profiles', []),
            'is_online': is_online,
            'source': 'onvif',
            'type': _guess_provider_type(cam.get('manufacturer', ''), cam.get('model', '')),
            # ONVIF stream fields (populated by full probe)
            'rtsp_url_onvif': '',
            'rtsp_sub_onvif': '',
            'onvif_profiles': [],
            'has_ptz': False,
        }

        results.append(entry)
        if probe_details and is_online:
            online_entries.append(entry)

    # Step 3: Concurrent ONVIF full-info probes (ThreadPoolExecutor, 4 workers for ARM)
    if online_entries and onvif_password:
        def _probe_full(entry):
            try:
                full = get_onvif_full_info(
                    entry['ip'], entry['onvif_port'],
                    username=onvif_username, password=onvif_password, timeout=5
                )
                if full:
                    if full.get('manufacturer'):
                        entry['manufacturer'] = full['manufacturer']
                    if full.get('model'):
                        entry['model'] = full['model']
                    if full.get('firmware'):
                        entry['firmware'] = full['firmware']
                    if full.get('serial'):
                        entry['serial'] = full['serial']
                        entry['id'] = full['serial']
                    if full.get('hardware'):
                        entry['hardware'] = full['hardware']
                    entry['rtsp_url_onvif'] = full.get('rtsp_main', '')
                    entry['rtsp_sub_onvif'] = full.get('rtsp_sub', '') or ''
                    entry['onvif_profiles'] = full.get('onvif_profiles', [])
                    entry['has_ptz'] = full.get('has_ptz', False)
                    entry['type'] = _guess_provider_type(entry['manufacturer'], entry['model'])
            except Exception as e:
                logger.debug(f"Full probe failed for {entry['ip']}: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_probe_full, entry): entry for entry in online_entries}
            # Overall timeout: 10s for all probes
            done, not_done = concurrent.futures.wait(futures, timeout=10)
            for f in not_done:
                f.cancel()
                ip = futures[f]['ip']
                logger.warning(f"ONVIF full probe timed out for {ip}")

    # Build display names for entries that still lack one
    for entry in results:
        if not entry['name']:
            parts = [entry['manufacturer'], entry['model']]
            entry['name'] = ' '.join(p for p in parts if p) or f"Camera @ {entry['ip']}"

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


# ─── ARP-Based Zero-Cost Discovery ────────────────────────────────────────
def lookup_mac_vendor(mac: str) -> str:
    """Return vendor name for a MAC address using offline OUI table."""
    if not mac:
        return ''
    prefix = mac.lower().replace('-', ':')[:8]
    return _CAMERA_OUI_MAP.get(prefix, '')


def read_arp_table() -> List[Dict]:
    """
    Read the kernel ARP cache from /proc/net/arp (Linux) or arp -a (macOS).
    Zero cost: no network packets, no root required, works inside Docker.
    Returns list of {'ip': str, 'mac': str, 'iface': str}.
    """
    entries = []
    # Try Linux /proc/net/arp first
    try:
        with open('/proc/net/arp', 'r') as f:
            lines = f.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 6:
                ip, mac, iface = parts[0], parts[3].lower(), parts[5]
                if mac and mac != '00:00:00:00:00:00':
                    entries.append({'ip': ip, 'mac': mac, 'iface': iface})
        return entries
    except FileNotFoundError:
        pass
    # Fallback: macOS `arp -a`
    try:
        import subprocess
        output = subprocess.check_output(['arp', '-a'], timeout=3, stderr=subprocess.DEVNULL).decode()
        for line in output.split('\n'):
            if '(incomplete)' in line:
                continue
            # Format: ? (ip) at mac on iface [ifscope] [ethernet]
            m = re.match(r'.*?\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([\da-fA-F:]+)\s+on\s+(\w+)', line)
            if m:
                ip, mac, iface = m.group(1), m.group(2).lower(), m.group(3)
                if mac and mac != 'ff:ff:ff:ff:ff:ff':
                    entries.append({'ip': ip, 'mac': mac, 'iface': iface})
    except Exception as e:
        logger.debug(f"ARP table read error: {e}")
    return entries


def discover_from_arp(camera_ports: List[int] = None) -> List[Dict]:
    """
    ARP-based camera discovery pipeline:
    1. Read kernel ARP table (free, instant)
    2. Filter by MAC OUI for known camera vendors
    3. TCP port check for unknown MACs
    Returns camera candidates with ip, mac, vendor, is_online.
    """
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
    # Port-check unknown MACs in parallel (only if few candidates from OUI)
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


# ─── Smart Discovery Pipeline ─────────────────────────────────────────────
def smart_discover_pipeline(
    onvif_username: str = 'admin',
    onvif_password: str = '',
    max_onvif_workers: int = 3,
) -> List[Dict]:
    """
    Three-stage pipeline optimized for ARM:
    Stage 1 (free):    ARP table + MAC OUI filter
    Stage 2 (cheap):   TCP port check for unknown MACs
    Stage 3 (costly):  ONVIF unicast probe + GetStreamUri (only if credentials set)
    """
    # Stage 1+2
    arp_candidates = discover_from_arp()

    if not onvif_password or not arp_candidates:
        return arp_candidates

    # Stage 3: ONVIF unicast probe for each candidate
    def _probe_candidate(candidate):
        ip = candidate['ip']
        ws_result = probe_onvif_unicast(ip)
        if ws_result:
            candidate['urn'] = ws_result.get('urn', '')
            candidate['onvif_port'] = ws_result.get('onvif_port', 80)
            if ws_result.get('manufacturer'):
                candidate['vendor'] = ws_result['manufacturer']
            candidate['model'] = ws_result.get('model', '')

        onvif_port = candidate.get('onvif_port', 80)
        full = get_onvif_full_info(ip, onvif_port,
                                   username=onvif_username,
                                   password=onvif_password, timeout=4)
        if full:
            candidate.update({
                'serial': full.get('serial', ''),
                'model': full.get('model', candidate.get('model', '')),
                'firmware': full.get('firmware', ''),
                'rtsp_url': full.get('rtsp_main', ''),
                'rtsp_sub': full.get('rtsp_sub', ''),
                'onvif_profiles': full.get('onvif_profiles', []),
                'has_ptz': full.get('has_ptz', False),
                'manufacturer': full.get('manufacturer', candidate.get('vendor', '')),
            })
        return candidate

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_onvif_workers) as executor:
        futures = {executor.submit(_probe_candidate, c): c for c in arp_candidates}
        done, not_done = concurrent.futures.wait(futures, timeout=15)
        for f in not_done:
            f.cancel()
        for f in done:
            try:
                results.append(f.result())
            except Exception as e:
                logger.debug(f"Pipeline probe error: {e}")
    # Include timed-out candidates with basic info
    timed_out_ips = {futures[f]['ip'] for f in not_done}
    for c in arp_candidates:
        if c['ip'] in timed_out_ips:
            results.append(c)

    logger.info(f"Smart pipeline: {len(results)} cameras after ONVIF probe")
    return results


# ─── Auto-Registration ────────────────────────────────────────────────────
def auto_register_discovered_cameras(discovered: List[Dict], db) -> dict:
    """
    Auto-register newly discovered cameras into the DB.
    Only active when app_setting AUTO_REGISTER_NEW_CAMERAS = 'true'.
    Also updates IP/RTSP for existing cameras that changed.
    Returns {'registered': int, 'updated': int, 'skipped': int}.
    """
    auto_enabled = (db.get_app_setting('AUTO_REGISTER_NEW_CAMERAS', 'false') or 'false').lower() == 'true'
    stats = {'registered': 0, 'updated': 0, 'skipped': 0}

    all_cams = db._execute_query(
        "SELECT id, camera_ip, imou_device_id, onvif_rtsp_detected FROM cameras",
        fetchall=True
    ) or []

    # O(1) lookup dicts instead of O(N) per device
    ip_to_cam = {c['camera_ip']: c for c in all_cams if c.get('camera_ip')}
    serial_to_cam = {c['imou_device_id'].strip().upper(): c for c in all_cams if c.get('imou_device_id')}

    # Auto-assign site if only 1 active site exists
    auto_site_ids = None
    try:
        sites = db._execute_query(
            "SELECT id FROM sites WHERE is_active = TRUE", fetchall=True
        ) or []
        if len(sites) == 1:
            auto_site_ids = [sites[0]['id']]
    except Exception:
        pass

    for dev in discovered:
        ip = dev.get('ip')
        serial = (dev.get('serial') or '').strip().upper()
        mac = dev.get('mac', '')
        rtsp_url = dev.get('rtsp_url_onvif') or dev.get('rtsp_url', '')
        vendor = dev.get('vendor') or dev.get('manufacturer', 'Unknown')
        model = dev.get('model', '')

        # Match to existing camera by IP or serial — O(1)
        matched = ip_to_cam.get(ip) or (serial_to_cam.get(serial) if serial else None)

        if matched:
            # Update IP and RTSP if changed
            needs_update = False
            if ip and matched.get('camera_ip') != ip:
                db._execute_query(
                    "UPDATE cameras SET camera_ip = %s, updated_at = NOW() WHERE id = %s",
                    (ip, matched['id']), commit=True
                )
                needs_update = True
            if rtsp_url and matched.get('onvif_rtsp_detected') != rtsp_url:
                db.regenerate_rtsp_url(matched['id'])
                db.store_onvif_detected_rtsp(matched['id'], rtsp_url)
                needs_update = True
            stats['updated' if needs_update else 'skipped'] += 1
        elif auto_enabled:
            # New camera — auto-register
            mk = db.get_app_setting('IMOU_MK', '') or ''
            cam_type = _guess_provider_type(vendor, model)
            name = f"{vendor} {model}".strip() or f"Camera @ {ip}"

            new_id = db.add_camera(
                name=name,
                cam_type=cam_type,
                imou_device_id=serial if serial else None,
                rtsp_url=rtsp_url if rtsp_url else None,
                imou_channel_id='0',
                site_ids=auto_site_ids,
                camera_ip=ip,
                camera_port=554,
                provider_keys={'mk': mk, 'mac': mac} if mk else {'mac': mac},
            )
            if new_id:
                if rtsp_url:
                    db.store_onvif_detected_rtsp(new_id, rtsp_url)
                db.upsert_camera_asset(
                    cam_id=serial or ip, name=name, ip=ip, serial=serial,
                    model=model, rtsp_url=rtsp_url, mac=mac,
                    onvif_port=dev.get('onvif_port'),
                    onvif_profiles=dev.get('onvif_profiles'),
                )
                stats['registered'] += 1
                logger.info(f"Auto-registered: {name} @ {ip}")
            else:
                stats['skipped'] += 1
        else:
            stats['skipped'] += 1

        # Always cache in asset table
        if mac or serial:
            try:
                db.upsert_camera_asset(
                    cam_id=serial or mac or ip, name=f"{vendor} {model}".strip() or ip,
                    ip=ip, serial=serial, model=model, rtsp_url=rtsp_url, mac=mac,
                    onvif_port=dev.get('onvif_port'),
                    onvif_profiles=dev.get('onvif_profiles'),
                )
            except Exception:
                pass

    return stats

def discover_and_update_all_cameras():
    """
    Combine ONVIF (local) scan and Imou Cloud (AppID/Secret) scan to find camera IPs.
    ONVIF is the PRIMARY source for IP and status.
    Returns a dict with summary results.
    """
    from backend.core.database import DatabaseManager
    from backend.util.imou_api import ImouClient
    db = DatabaseManager()
    
    results = {'found_local': 0, 'found_cloud': 0, 'updated': 0, 'total': 0, 'error': None, 'new_devices': [], 'updated_cameras': []}
    
    # 1. Get all cameras from DB
    all_cams = db._execute_query("SELECT id, camera_name, imou_device_id, camera_ip FROM cameras", fetchall=True)
    results['total'] = len(all_cams)
    
    existing_ids = [str(cam.get('imou_device_id')).strip().lower() for cam in all_cams if cam.get('imou_device_id')]
    existing_ips = [cam.get('camera_ip') for cam in all_cams if cam.get('camera_ip')]
    logger.info(f"Discovery: {len(all_cams)} cameras in DB. Existing IDs: {existing_ids}")
    
    seen_ids = set()
    seen_ips = set(existing_ips)
    found_ips = {}  # device_id -> ip
    
    # 1. LOCAL DISCOVERY (Dahua UDP + ONVIF)
    logger.info("Starting local discovery (Dahua UDP + ONVIF)...")
    
    # METHOD A: Dahua Proprietary Discovery (Primary for Imou)
    dahua_found = discover_dahua_cameras(timeout=5.0)
    logger.info(f"Dahua UDP scan found {len(dahua_found)} device(s).")
    results['found_local'] += len(dahua_found)
    for dev in dahua_found:
        found_ips[dev['serial'].lower()] = dev['ip']

    # Get global camera password (used for credential resolution)
    imou_mk = db.get_app_setting("IMOU_MK", "")

    # METHOD B: ONVIF Discovery (Standard) with full RTSP detection
    # Resolve ONVIF credentials: ONVIF_USERNAME/PASSWORD → IMOU_MK → admin/""
    onvif_user = db.get_app_setting("ONVIF_USERNAME", "") or "admin"
    onvif_pass = db.get_app_setting("ONVIF_PASSWORD", "") or imou_mk or ""
    has_onvif_creds = bool(onvif_pass)

    logger.info(f"Starting ONVIF WS-Discovery scan (full_probe={'yes' if has_onvif_creds else 'no'})...")
    local_found = discover_onvif_only(
        timeout=5.0,
        probe_details=has_onvif_creds,
        onvif_username=onvif_user,
        onvif_password=onvif_pass,
    )
    logger.info(f"ONVIF scan found {len(local_found)} device(s).")
    results['found_local'] += len(local_found)

    # Phase 2.1: Probe serial for Dahua/Imou cameras that weren't fully probed (CONCURRENT)
    if imou_mk:
        dahua_to_probe = [
            dev for dev in local_found
            if any(k in (dev.get('manufacturer') or '').lower() for k in ['dahua', 'imou'])
            and dev.get('is_online') and not dev.get('serial')
        ]
        if dahua_to_probe:
            def _probe_dahua_serial(dev):
                try:
                    full = get_onvif_full_info(
                        dev['ip'], dev.get('onvif_port', 80),
                        username='admin', password=imou_mk, timeout=4
                    )
                    if full:
                        if full.get('serial'):
                            dev['serial'] = full['serial']
                            dev['id'] = full['serial'].lower()
                        if full.get('model'):
                            dev['model'] = full['model']
                        if full.get('firmware'):
                            dev['firmware'] = full['firmware']
                        if full.get('rtsp_main') and not dev.get('rtsp_url_onvif'):
                            dev['rtsp_url_onvif'] = full['rtsp_main']
                        if full.get('rtsp_sub') and not dev.get('rtsp_sub_onvif'):
                            dev['rtsp_sub_onvif'] = full['rtsp_sub']
                        if full.get('onvif_profiles') and not dev.get('onvif_profiles'):
                            dev['onvif_profiles'] = full['onvif_profiles']
                        if full.get('has_ptz'):
                            dev['has_ptz'] = True
                        logger.info(f"  ONVIF full probe (Imou): {dev['ip']} → S/N={dev.get('serial', '?')}")
                except Exception as e:
                    logger.debug(f"ONVIF serial probe failed for {dev['ip']}: {e}")

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {executor.submit(_probe_dahua_serial, dev): dev for dev in dahua_to_probe}
                concurrent.futures.wait(futures, timeout=12)

    # Phase 2.3: Unicast ONVIF probe for Dahua IPs not found by multicast
    onvif_ips = {dev['ip'] for dev in local_found}
    for dev in dahua_found:
        if dev['ip'] not in onvif_ips:
            unicast_result = probe_onvif_unicast(dev['ip'])
            if unicast_result:
                unicast_result['source'] = 'onvif'
                # Merge basic fields + add ONVIF stream fields
                entry = {
                    'id': dev.get('serial', '').lower() or unicast_result.get('urn', ''),
                    'ip': dev['ip'],
                    'onvif_port': unicast_result.get('onvif_port', 80),
                    'manufacturer': unicast_result.get('manufacturer', dev.get('manufacturer', 'Unknown')),
                    'model': unicast_result.get('model', dev.get('model', '')),
                    'name': unicast_result.get('name', ''),
                    'firmware': unicast_result.get('firmware', dev.get('firmware', '')),
                    'serial': dev.get('serial', ''),
                    'xaddrs': unicast_result.get('xaddrs', ''),
                    'profiles': unicast_result.get('profiles', []),
                    'is_online': True,
                    'source': 'onvif',
                    'type': _guess_provider_type(
                        unicast_result.get('manufacturer', ''), unicast_result.get('model', '')),
                    'rtsp_url_onvif': '',
                    'rtsp_sub_onvif': '',
                    'onvif_profiles': [],
                    'has_ptz': False,
                }
                local_found.append(entry)
                onvif_ips.add(dev['ip'])
                logger.info(f"  Unicast ONVIF probe: found {dev['ip']}")

    # Merge ONVIF into found_ips if not already there
    onvif_ip_map = {} # serial/id -> ip
    for dev in local_found:
        ip = dev['ip']
        dev_id = (dev.get('id') or dev.get('serial') or '').lower()
        if dev_id:
            onvif_ip_map[dev_id] = ip
            if dev_id not in found_ips:
                found_ips[dev_id] = ip
    
    # --- METHOD B: IMOU CLOUD API SYNC (SECONDARY) ---
    imou_keys = db.get_imou_api_keys()
    cloud_new_devices = []
    if imou_keys.get('app_id') and imou_keys.get('app_secret'):
        logger.info("Starting Imou Cloud API sync...")
        try:
            client = ImouClient(imou_keys['app_id'], imou_keys['app_secret'])
            cloud_devices = client.list_devices()
            logger.info(f"Cloud API found {len(cloud_devices)} device(s).")

            # Build combined IP map from BOTH Dahua UDP and ONVIF (Phase 1.2 fix)
            # Do NOT re-assign — use combined_ip_map built from all sources
            combined_ip_map = {}
            for dev in dahua_found:
                if dev.get('serial'):
                    combined_ip_map[dev['serial'].lower()] = dev['ip']
            for dev in local_found:
                dev_id = (dev.get('id') or dev.get('serial') or '').lower()
                if dev_id:
                    combined_ip_map[dev_id] = dev['ip']

            for dev in cloud_devices:
                did_raw = dev.get('deviceId')
                lip = dev.get('deviceLocalIp')

                if did_raw:
                    did = did_raw.strip().lower()

                    # Phase 2.2: Normalized serial matching (exact uppercase compare)
                    if (not lip or lip == "0.0.0.0"):
                        for local_id, local_ip in combined_ip_map.items():
                            if did.upper() == local_id.upper():
                                lip = local_ip
                                logger.info(f"  [LOCAL→CLOUD] Matched {did} to {lip} via serial match")
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
                            'name': dev.get('deviceName') or (dev.get('deviceModel', 'Imou Camera') + ' ' + did[-4:]),
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
    
    # --- ARP IP RESOLUTION for cloud devices without IP ---
    # Cloud API often doesn't return local IP. Use ARP + Dahua UDP + port scan to find it.
    cloud_no_ip = [d for d in cloud_new_devices if not d.get('ip')]
    if cloud_no_ip:
        logger.info(f"Resolving IP for {len(cloud_no_ip)} cloud device(s) via ARP + port scan...")
        arp_candidates = discover_from_arp()
        # Also include Dahua UDP results (already have serial→IP mapping)
        arp_ips_with_camera_ports = {c['ip'] for c in arp_candidates if c.get('is_online')}

        # For each cloud device without IP, try to match via Dahua UDP serial
        for dev in cloud_no_ip:
            did = dev['id'].upper()
            # 1. Check Dahua UDP found_ips (serial match)
            matched_ip = found_ips.get(dev['id'])
            if matched_ip:
                dev['ip'] = matched_ip
                logger.info(f"  [ARP→CLOUD] Resolved {did} → {matched_ip} via Dahua serial")
                continue

            # 2. Probe ARP candidates with ONVIF to find serial match
            if imou_mk:
                for arp_ip in list(arp_ips_with_camera_ports):
                    if arp_ip in seen_ips or arp_ip in [d.get('ip') for d in cloud_new_devices if d.get('ip')]:
                        continue
                    try:
                        full = get_onvif_full_info(arp_ip, 80, 'admin', imou_mk, timeout=4)
                        if full and full.get('serial', '').upper() == did:
                            dev['ip'] = arp_ip
                            # Also capture RTSP URL
                            if full.get('rtsp_main'):
                                dev['rtsp_url_onvif'] = full['rtsp_main']
                            if full.get('rtsp_sub'):
                                dev['rtsp_sub_onvif'] = full['rtsp_sub']
                            found_ips[dev['id']] = arp_ip
                            logger.info(f"  [ONVIF→CLOUD] Resolved {did} → {arp_ip} via ONVIF serial match")
                            break
                    except Exception as e:
                        logger.debug(f"  ONVIF probe {arp_ip} for {did}: {e}")

        resolved = sum(1 for d in cloud_no_ip if d.get('ip'))
        logger.info(f"ARP IP resolution: {resolved}/{len(cloud_no_ip)} cloud device(s) resolved")

    # --- METHOD C: MERGE — ONVIF first, then cloud new devices ---
    new_devices = []
    existing_devices = []  # Devices already in DB (for update)
    matched_cam_ids = set()  # Track DB camera IDs already matched

    def _find_matching_cam(dev_id, ip):
        """Find matching camera in DB by device_id or IP. Returns cam dict or None."""
        for cam in all_cams:
            if cam['id'] in matched_cam_ids:
                continue
            cam_did = (cam.get('imou_device_id') or '').strip().lower()
            cam_ip = cam.get('camera_ip', '')
            if (cam_did and dev_id and cam_did.upper() == dev_id.upper()) or (cam_ip and cam_ip == ip):
                return cam
        return None

    # Add ONVIF-found devices — both new and existing
    for dev in local_found:
        dev_id = (dev.get('id') or dev.get('serial') or dev.get('urn', '')).lower()
        ip = dev['ip']

        matched_cam = _find_matching_cam(dev_id, ip)
        device_entry = {
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
            'auto_found': False,
            # ONVIF-detected RTSP URLs and profiles
            'rtsp_url_onvif': dev.get('rtsp_url_onvif', ''),
            'rtsp_sub_onvif': dev.get('rtsp_sub_onvif', ''),
            'onvif_profiles': dev.get('onvif_profiles', []),
            'has_ptz': dev.get('has_ptz', False),
        }

        if matched_cam:
            device_entry['camera_id'] = matched_cam['id']
            device_entry['camera_name'] = matched_cam['camera_name']
            device_entry['_exists'] = True
            existing_devices.append(device_entry)
            matched_cam_ids.add(matched_cam['id'])
        elif dev_id not in seen_ids and ip not in seen_ips:
            new_devices.append(device_entry)

        seen_ids.add(dev_id)
        seen_ips.add(ip)

    # Phase 1.3: Add Dahua UDP devices — both new and existing
    for dev in dahua_found:
        dev_id = dev.get('serial', '').lower()
        ip = dev['ip']
        if dev_id in seen_ids or ip in seen_ips:
            continue

        matched_cam = _find_matching_cam(dev_id, ip)
        device_entry = {
            'id': dev_id or ip,
            'name': f"{dev.get('manufacturer', 'Dahua/Imou')} {dev.get('model', '')}".strip() or f"Camera @ {ip}",
            'ip': ip,
            'onvif_port': dev.get('port', 37777),
            'type': 'imou',
            'source': 'dahua_udp',
            'manufacturer': dev.get('manufacturer', 'Dahua/Imou'),
            'model': dev.get('model', ''),
            'serial': dev.get('serial', ''),
            'is_online': True,
            'auto_found': False
        }

        if matched_cam:
            device_entry['camera_id'] = matched_cam['id']
            device_entry['camera_name'] = matched_cam['camera_name']
            device_entry['_exists'] = True
            existing_devices.append(device_entry)
            matched_cam_ids.add(matched_cam['id'])
        else:
            new_devices.append(device_entry)

        seen_ids.add(dev_id)
        seen_ips.add(ip)

    # Add cloud devices — both new and existing
    for dev in cloud_new_devices:
        if dev['id'] in seen_ids:
            continue
        ip = dev.get('ip')
        if ip and ip in seen_ips:
            continue

        matched_cam = _find_matching_cam(dev['id'], ip)
        if matched_cam:
            dev['camera_id'] = matched_cam['id']
            dev['camera_name'] = matched_cam['camera_name']
            dev['_exists'] = True
            existing_devices.append(dev)
            matched_cam_ids.add(matched_cam['id'])
        else:
            new_devices.append(dev)
        seen_ids.add(dev['id'])
        if ip:
            seen_ips.add(ip)

    # Gộp tất cả camera quét được thành 1 list, đánh dấu _exists để UI biết add/update
    all_discovered = []
    for dev in new_devices:
        dev['_exists'] = False
        all_discovered.append(dev)
    for dev in existing_devices:
        dev['_exists'] = True
        all_discovered.append(dev)

    results['devices'] = all_discovered
    results['new_devices'] = new_devices        # giữ cho backward compat
    results['existing_devices'] = existing_devices

    # --- APPLY UPDATES TO DB ---
    for cam in all_cams:
        did = cam.get('imou_device_id')
        if did:
            did_lower = did.strip().lower()
            if did_lower in found_ips:
                new_ip = found_ips[did_lower]
                if cam['camera_ip'] != new_ip:
                    logger.info(f"UPDATING IP: '{cam['camera_name']}' {cam['camera_ip']} → {new_ip}")
                    db._execute_query("UPDATE cameras SET camera_ip = %s WHERE id = %s", (new_ip, cam['id']), commit=True)
                    # Regenerate RTSP URL with new IP
                    db.regenerate_rtsp_url(cam['id'])
                    results['updated_cameras'].append({
                        'name': cam['camera_name'],
                        'old_ip': cam['camera_ip'] or '',
                        'new_ip': new_ip
                    })
                    results['updated'] += 1

    # Phase 2.3: Cache all discovered devices into cameras_asset
    try:
        all_discovered = list(local_found) + list(dahua_found) + list(cloud_new_devices)
        for dev in all_discovered:
            cam_id = (dev.get('serial') or dev.get('id') or dev.get('ip', '')).strip()
            if cam_id and not cam_id.startswith('unidentified_'):
                db.upsert_camera_asset(
                    cam_id=cam_id,
                    name=dev.get('name', dev.get('manufacturer', '')),
                    ip=dev.get('ip'),
                    serial=dev.get('serial', ''),
                    model=dev.get('model', ''),
                    firmware=dev.get('firmware', ''),
                    rtsp_url=dev.get('rtsp_url_onvif', ''),
                    onvif_port=dev.get('onvif_port'),
                    onvif_profiles=dev.get('onvif_profiles'),
                    mac=dev.get('mac', ''),
                )
    except Exception as e:
        logger.debug(f"Asset cache error: {e}")

    # Save last discovery timestamp
    db.set_app_setting("LAST_DISCOVERY_AT", time.strftime("%Y-%m-%d %H:%M:%S"))

    # Auto-register new cameras if enabled
    if new_devices:
        try:
            reg_result = auto_register_discovered_cameras(new_devices, db)
            results['auto_registered'] = reg_result.get('registered', 0)
            if reg_result['registered'] > 0:
                logger.info(f"Auto-registration: {reg_result}")
        except Exception as e:
            logger.warning(f"Auto-registration error: {e}")

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
