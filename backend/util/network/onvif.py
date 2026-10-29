import re
import socket
import select
import time
import logging
import uuid as uuid_mod
import concurrent.futures
import xml.etree.ElementTree as ET
from typing import List, Dict

from .arp import probe_ip, _get_local_ips

logger = logging.getLogger("network_utils.onvif")

WS_DISCOVERY_MCAST_ADDR = "239.255.255.250"
WS_DISCOVERY_PORT = 3702


def _guess_provider_type(manufacturer: str, model: str) -> str:
    combined = f"{manufacturer} {model}".lower()
    if any(k in combined for k in ['dahua', 'imou']):
        return 'imou'
    if any(k in combined for k in ['hikvision', 'ezviz', 'hilook']):
        return 'hikvision'
    if any(k in combined for k in ['tp-link', 'tapo', 'vigi']):
        return 'tplink'
    if any(k in combined for k in ['axis']):
        return 'axis'
    if any(k in combined for k in ['uniview', 'unv']):
        return 'uniview'
    if any(k in combined for k in ['reolink']):
        return 'reolink'
    if any(k in combined for k in ['hanwha', 'samsung', 'wisenet']):
        return 'hanwha'
    if any(k in combined for k in ['bosch']):
        return 'bosch'
    return 'onvif'


def _parse_scopes(scope_text: str) -> dict:
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


def discover_onvif_cameras(timeout=6):
    cameras = []
    seen_ips = set()

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

    probe_data = probe_xml.encode()
    local_ips = _get_local_ips(WS_DISCOVERY_MCAST_ADDR, WS_DISCOVERY_PORT)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        for _ in range(3):
            sock.sendto(probe_data, (WS_DISCOVERY_MCAST_ADDR, WS_DISCOVERY_PORT))

            for local_ip in local_ips:
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(local_ip)
                )
                sock.sendto(probe_data, (WS_DISCOVERY_MCAST_ADDR, WS_DISCOVERY_PORT))
            time.sleep(0.15)

        start_time = time.time()
        while time.time() - start_time < timeout:
            ready = select.select([sock], [], [], 0.5)
            if ready[0]:
                data, addr = sock.recvfrom(65535)
                ip = addr[0]
                if ip in seen_ips:
                    continue

                xml_str = data.decode('utf-8', errors='replace')
                if '\ufffd' in xml_str:
                    logger.debug("ONVIF Discovery: Invalid UTF-8 from %s", ip)
                    continue
                cam_info = _parse_probe_match(xml_str, ip)
                if cam_info:
                    cameras.append(cam_info)
                    seen_ips.add(ip)
                    logger.info(f"ONVIF Discovery: Found {cam_info.get('manufacturer', '?')} "
                                f"{cam_info.get('model', '?')} @ {ip}")

    logger.info(f"ONVIF WS-Discovery found {len(cameras)} camera(s) in {timeout}s "
                f"(probed {len(local_ips)} interface(s))")
    return cameras


def probe_onvif_unicast(ip: str, port: int = 80) -> dict | None:
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

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(probe_xml.encode(), (ip, WS_DISCOVERY_PORT))
        if not select.select([sock], [], [], 3)[0]:
            logger.debug("Unicast ONVIF probe to %s:%s timed out", ip, port)
            return None
        data, _ = sock.recvfrom(65535)
    xml_str = data.decode('utf-8', errors='replace')
    if '\ufffd' in xml_str:
        logger.debug("ONVIF unicast response has invalid UTF-8 from %s", ip)
        return None
    return _parse_probe_match(xml_str, ip)


def _parse_probe_match(xml_str: str, fallback_ip: str) -> dict | None:
    xml_str = xml_str.lstrip('\ufeff')
    if '\ufffd' in xml_str:
        logger.debug("ONVIF ProbeMatch has invalid UTF-8")
        return None
    root = ET.fromstring(xml_str)

    ns = {
        's': 'http://www.w3.org/2003/05/soap-envelope',
        'd': 'http://schemas.xmlsoap.org/ws/2005/04/discovery',
        'a': 'http://schemas.xmlsoap.org/ws/2004/08/addressing',
        'd04': 'http://schemas.xmlsoap.org/ws/2004/08/discovery',
    }

    probe_match = root.find('.//d:ProbeMatch', ns)
    if probe_match is None:
        probe_match = root.find('.//d04:ProbeMatch', ns)
    if probe_match is None:
        logger.debug("ONVIF response has no ProbeMatch")
        return None

    urn = ""
    epr = probe_match.find('.//a:EndpointReference/a:Address', ns)
    if epr is not None and epr.text:
        urn = epr.text.strip()

    urn_id = ""
    urn_match = re.search(r'uuid:([a-zA-Z0-9\-]+)', urn)
    if urn_match:
        urn_id = urn_match.group(1)

    raw_xaddrs = ""
    xaddrs_el = probe_match.find('d:XAddrs', ns)
    if xaddrs_el is None:
        xaddrs_el = probe_match.find('d04:XAddrs', ns)
    if xaddrs_el is not None and xaddrs_el.text:
        raw_xaddrs = xaddrs_el.text.strip()

    ip = fallback_ip
    onvif_port = 80
    xaddrs_text = ""
    if raw_xaddrs:
        for xaddr in raw_xaddrs.split():
            address = re.fullmatch(
                r"(?P<scheme>https?)://(?P<host>\[[0-9A-Fa-f:.%]+\]|[A-Za-z0-9.-]+)"
                r"(?::(?P<port>[0-9]{1,5}))?(?:/[^\s#]*)?",
                xaddr,
                re.IGNORECASE,
            )
            if address is None:
                logger.debug("ONVIF ProbeMatch contains an invalid XAddr")
                continue
            candidate_port = int(address.group("port") or (443 if address.group("scheme").lower() == "https" else 80))
            if not 1 <= candidate_port <= 65535:
                logger.debug("ONVIF ProbeMatch contains an invalid XAddr port")
                continue
            ip = address.group("host").strip("[]")
            onvif_port = candidate_port
            xaddrs_text = xaddr.split("?", 1)[0]
            break

    if not urn_id and not xaddrs_text:
        logger.debug("ONVIF ProbeMatch has no valid device identity or address")
        return None

    scopes_text = ""
    scopes_el = probe_match.find('d:Scopes', ns)
    if scopes_el is None:
        scopes_el = probe_match.find('d04:Scopes', ns)
    if scopes_el is not None and scopes_el.text:
        scopes_text = scopes_el.text.strip()

    scopes = _parse_scopes(scopes_text)

    types_text = ""
    types_el = probe_match.find('d:Types', ns)
    if types_el is None:
        types_el = probe_match.find('d04:Types', ns)
    if types_el is not None and types_el.text:
        types_text = types_el.text.strip()

    manufacturer = scopes.get('manufacturer', '')
    if not manufacturer:
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
    }


def get_onvif_full_info(ip: str, port: int = 80, username: str = 'admin',
                        password: str = '', timeout: int = 6) -> dict | None:
    from onvif import ONVIFCamera
    from zeep.transports import Transport
    from requests import Session
    from urllib.parse import quote, urlunparse

    if not probe_ip(ip, ports=[port], timeout=timeout):
        return None

    session = Session()
    session.timeout = timeout
    transport = Transport(timeout=timeout, session=session)
    cam = ONVIFCamera(ip, port, username, password,
                      no_cache=True, transport=transport)

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

    media_service = cam.create_media_service()
    profiles = media_service.GetProfiles() or []
    result['has_ptz'] = any(bool(getattr(profile, 'PTZConfiguration', None)) for profile in profiles)
    for i, profile in enumerate(profiles):
        token = profile.token
        name = getattr(profile, 'Name', token)

        width, height, encoding = 0, 0, ''
        vec = getattr(profile, 'VideoEncoderConfiguration', None)
        if vec:
            encoding = getattr(vec, 'Encoding', '')
            res = getattr(vec, 'Resolution', None)
            if res:
                width = getattr(res, 'Width', 0)
                height = getattr(res, 'Height', 0)

        stream_setup = media_service.create_type('GetStreamUri')
        stream_setup.StreamSetup = {
            'Stream': 'RTP-Unicast',
            'Transport': {'Protocol': 'RTSP'}
        }
        stream_setup.ProfileToken = token

        try:
            uri_resp = media_service.GetStreamUri(stream_setup)
            rtsp_uri = uri_resp.Uri if uri_resp else ''

            if rtsp_uri and username and password:
                user_enc = quote(username, safe='')
                pass_enc = quote(password, safe='')
                if '@' not in rtsp_uri.split('://', 1)[-1].split('/', 1)[0]:
                    scheme, rest = rtsp_uri.split('://', 1)
                    rtsp_uri = f"{scheme}://{user_enc}:{pass_enc}@{rest}"

            profile_info = {
                'token': token,
                'name': name,
                'encoding': encoding,
                'width': width,
                'height': height,
                'rtsp_url': rtsp_uri,
            }
            result['onvif_profiles'].append(profile_info)

            if i == 0 and rtsp_uri:
                result['rtsp_main'] = rtsp_uri
            elif i == 1 and rtsp_uri:
                result['rtsp_sub'] = rtsp_uri

        except Exception as e:
            logger.debug("Failed to get stream URI for profile %s: %s", token, e)

    logger.info("ONVIF full info for %s:%s: %s %s, %s profiles, PTZ=%s",
                ip, port, result['manufacturer'], result['model'],
                len(result['onvif_profiles']), result['has_ptz'])
    return result


def _run_bounded_probes(entries, probe, max_workers: int, timeout: float, label: str):
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = [executor.submit(probe, entry) for entry in entries]
        _, pending = concurrent.futures.wait(futures, timeout=timeout)
        if pending:
            logger.error("%s timed out for %s camera(s)", label, len(pending))
            raise TimeoutError(f"{label} timed out for {len(pending)} camera(s)")
        return [future.result() for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def discover_onvif_only(timeout: int = 5, probe_details: bool = True,
                        onvif_username: str = 'admin', onvif_password: str = '') -> list[dict]:
    raw_cameras = discover_onvif_cameras(timeout=timeout)

    results = []
    online_entries = []

    for cam in raw_cameras:
        ip = cam['ip']
        onvif_port = cam.get('onvif_port', 80)

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
            'rtsp_url_onvif': '',
            'rtsp_sub_onvif': '',
            'onvif_profiles': [],
            'has_ptz': False,
        }

        results.append(entry)
        if probe_details and is_online:
            online_entries.append(entry)

    if online_entries and onvif_password:
        def _probe_full(entry):
            return get_onvif_full_info(
                entry['ip'], entry['onvif_port'],
                username=onvif_username, password=onvif_password, timeout=5
            )

        full_results = _run_bounded_probes(
            online_entries, _probe_full, max_workers=4, timeout=10,
            label="ONVIF full probe",
        )
        for entry, full in zip(online_entries, full_results):
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

    for entry in results:
        if not entry['name']:
            parts = [entry['manufacturer'], entry['model']]
            entry['name'] = ' '.join(p for p in parts if p) or f"Camera @ {entry['ip']}"

    logger.info(f"ONVIF-only discovery: {len(results)} camera(s) found")
    return results
