
import socket
import select
import errno
import time
import logging
import xml.etree.ElementTree as ET
import re
import uuid as uuid_mod
import concurrent.futures
from typing import List, Dict
from urllib.parse import unquote

from backend.camera import camera_service as cam_svc
from backend.services.database import db
from backend.services.settings_service import get_app_setting, set_app_setting

logger = logging.getLogger("network_utils")

WS_DISCOVERY_MCAST_ADDR = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

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


def discover_dahua_cameras(timeout=5.0) -> List[Dict]:
    import json
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

def probe_ip(ip: str, ports=[80, 554, 8000, 37810, 37777, 8080], timeout=1.5) -> bool:
    last_error = None
    for port in ports:
        for family, socktype, proto, _, address in socket.getaddrinfo(ip, port, type=socket.SOCK_STREAM):
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(timeout)
                error = sock.connect_ex(address)
                if error == 0:
                    return True
                last_error = errno.errorcode.get(error, str(error))
    logger.debug("TCP probe found no open camera ports on %s (%s)", ip, last_error)
    return False


def _get_local_ips() -> List[str]:
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
            sock.connect((WS_DISCOVERY_MCAST_ADDR, WS_DISCOVERY_PORT))
            ips.append(sock.getsockname()[0])
    return list(dict.fromkeys(ips))


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
    local_ips = _get_local_ips()

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
        stream_setup.ProfileToken = token
        stream_setup.StreamSetup = {
            'Stream': 'RTP-Unicast',
            'Transport': {'Protocol': 'RTSP'}
        }
        uri_resp = media_service.GetStreamUri(stream_setup)
        uri = getattr(uri_resp, 'Uri', '') or ''
        rtsp_url = ''
        if uri:
            parsed = urlparse(uri)
            stream_port = parsed.port
            if parsed.scheme in ('rtsp', 'rtsps') and parsed.hostname:
                rtsp_url = uri
                if username and not parsed.username:
                    host = parsed.hostname
                    if ':' in host:
                        host = f"[{host}]"
                    netloc = f"{quote(username, safe='')}:{quote(password or '', safe='')}@{host}"
                    if stream_port:
                        netloc += f":{stream_port}"
                    rtsp_url = urlunparse((
                        parsed.scheme, netloc, parsed.path,
                        parsed.params, parsed.query, parsed.fragment
                    ))
            else:
                logger.debug("GetStreamUri returned an invalid RTSP endpoint on %s", ip)

        result['onvif_profiles'].append({
            'token': token,
            'name': str(name),
            'rtsp_url': rtsp_url,
            'width': width,
            'height': height,
            'encoding': str(encoding),
        })
        if i == 0 and rtsp_url:
            result['rtsp_main'] = rtsp_url
        elif i == 1 and rtsp_url:
            result['rtsp_sub'] = rtsp_url

    logger.info("ONVIF full info for %s:%s: %s %s, %s profiles, PTZ=%s",
                ip, port, result['manufacturer'], result['model'],
                len(result['onvif_profiles']), result['has_ptz'])
    return result


def check_rtsp_quick(rtsp_url: str, timeout: float = 2.0) -> tuple[bool, str]:
    import hashlib

    host = None
    port = None

    def failed(message: str, reason: str) -> tuple[bool, str]:
        logger.warning("RTSP probe failed on %s:%s: %s", host or "<invalid host>", port or "?", reason)
        return False, message

    if not rtsp_url or not rtsp_url.startswith("rtsp://"):
        return failed("RTSP URL không hợp lệ hoặc để trống", "missing or unsupported URL")

    target = re.fullmatch(
        r"rtsp://(?:(?P<credentials>[^@/?#\s]+)@)?"
        r"(?P<host>\[[0-9A-Fa-f:.%]+\]|[A-Za-z0-9.-]+)"
        r"(?::(?P<port>[0-9]{1,5}))?(?P<path>/[^\s#]*)?",
        rtsp_url,
        re.IGNORECASE,
    )
    if target is None or (target.group("port") and not 1 <= int(target.group("port")) <= 65535):
        return failed("RTSP URL không hợp lệ", "invalid host or port")
    host = target.group("host").strip("[]")
    port = int(target.group("port") or 554)
    credentials = target.group("credentials") or ""
    username, _, password = credentials.partition(":")
    password = unquote(password)
    path = target.group("path") or ""
    uri_host = f"[{host}]" if ':' in host else host
    clean_url = f"rtsp://{uri_host}:{port}{path}"
    req = f"DESCRIBE {clean_url} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: ParkingCamera\r\nAccept: application/sdp\r\n\r\n"

    def _receive(sock):
        if not select.select([sock], [], [], timeout)[0]:
            return None
        return sock.recv(2048).decode('utf-8', errors='ignore')

    last_error = None
    for family, socktype, proto, _, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(timeout)
            error = sock.connect_ex(address)
            if error:
                last_error = error
                continue
            sock.sendall(req.encode())
            resp = _receive(sock)
            if resp is None:
                return failed("Hết thời gian chờ (Timeout): Không phản hồi từ camera.", "timeout")

            first_line = resp.split('\r\n')[0]
            if '200 OK' in first_line:
                return True, "OK"

            if '401' in first_line:
                if not password:
                    return failed("Camera yêu cầu mật khẩu (401 Unauthorized). Vui lòng điền Safety Code.", "authentication required")

                auth_header = re.search(r'WWW-Authenticate: Digest (.*)', resp)
                if auth_header:
                    params = dict(re.findall(r'(\w+)="([^"]+)"', auth_header.group(1)))
                    realm = params.get('realm', '')
                    nonce = params.get('nonce', '')
                    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
                    ha2 = hashlib.md5(f"DESCRIBE:{clean_url}".encode()).hexdigest()
                    response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
                    auth_str = f'Digest username="{username}", realm="{realm}", nonce="{nonce}", uri="{clean_url}", response="{response}"'
                    req2 = f"DESCRIBE {clean_url} RTSP/1.0\r\nCSeq: 2\r\nUser-Agent: ParkingCamera\r\nAuthorization: {auth_str}\r\nAccept: application/sdp\r\n\r\n"
                    sock.sendall(req2.encode())
                    resp2 = _receive(sock)
                    if resp2 is None:
                        return failed("Hết thời gian chờ (Timeout): Không phản hồi từ camera.", "authentication timeout")
                    if '200 OK' in resp2.split('\r\n')[0]:
                        return True, "OK"
                    return failed("Sai mật khẩu camera (401 Unauthorized). Vui lòng kiểm tra lại Safety Code dưới đáy camera.", "authentication rejected")
                return failed("Xác thực thất bại (401 Unauthorized).", "digest challenge missing")

            status = re.match(r'^RTSP/\d(?:\.\d)?\s+(\d{3})', first_line)
            if status:
                return failed(f"Camera phản hồi mã {status.group(1)}", f"status {status.group(1)}")
            return failed("Camera phản hồi không hợp lệ", "invalid response")

    if last_error in (errno.ETIMEDOUT, errno.EWOULDBLOCK, errno.EAGAIN):
        return failed("Hết thời gian chờ (Timeout): Không phản hồi từ camera.", "connect timeout")
    if last_error == errno.ECONNREFUSED:
        return failed(f"Kết nối bị từ chối: Cổng RTSP {port} không mở hoặc sai IP.", "connection refused")
    return failed("Không thể kết nối tới camera.", f"connect error {errno.errorcode.get(last_error, last_error)}")





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
        # Running probes have their own socket timeouts; do not let pool shutdown
        # extend this discovery call beyond its deadline.
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


def _guess_provider_type(manufacturer: str, model: str) -> str:
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


def auto_register_discovered_cameras(discovered: List[Dict]) -> dict:
    auto_enabled = (get_app_setting('AUTO_REGISTER_NEW_CAMERAS', 'false') or 'false').lower() == 'true'
    stats = {'registered': 0, 'updated': 0, 'skipped': 0}

    all_cams = db._execute_query(
        "SELECT id, camera_ip, imou_device_id, onvif_rtsp_detected FROM cameras",
        fetchall=True
    )

    ip_to_cam = {c['camera_ip']: c for c in all_cams if c.get('camera_ip')}
    serial_to_cam = {c['imou_device_id'].strip().upper(): c for c in all_cams if c.get('imou_device_id')}

    sites = db._execute_query(
        "SELECT id FROM sites WHERE is_active = TRUE", fetchall=True
    )
    auto_site_ids = [sites[0]['id']] if len(sites) == 1 else None

    for dev in discovered:
        asset_written = False
        ip = dev.get('ip')
        serial = (dev.get('serial') or '').strip().upper()
        mac = dev.get('mac', '')
        rtsp_url = dev.get('rtsp_url_onvif') or dev.get('rtsp_url', '')
        vendor = dev.get('vendor') or dev.get('manufacturer', 'Unknown')
        model = dev.get('model', '')

        matched = ip_to_cam.get(ip) or (serial_to_cam.get(serial) if serial else None)

        if matched:
            needs_update = False
            if ip and matched.get('camera_ip') != ip:
                updated = db._execute_query(
                    "UPDATE cameras SET camera_ip = %s, updated_at = NOW() WHERE id = %s",
                    (ip, matched['id']), commit=True
                )
                if not updated:
                    logger.error("Discovered camera IP update affected no row (camera_id=%s)", matched['id'])
                    raise RuntimeError("Discovered camera IP update did not affect a row")
                needs_update = True
            if rtsp_url and matched.get('onvif_rtsp_detected') != rtsp_url:
                if not cam_svc.regenerate_rtsp_url(matched['id']):
                    logger.error("Discovered camera RTSP reset affected no row (camera_id=%s)", matched['id'])
                    raise RuntimeError("Discovered camera RTSP reset did not affect a row")
                if not cam_svc.store_onvif_detected_rtsp(matched['id'], rtsp_url):
                    logger.error("Discovered camera RTSP update affected no row (camera_id=%s)", matched['id'])
                    raise RuntimeError("Discovered camera RTSP update did not affect a row")
                needs_update = True
            stats['updated' if needs_update else 'skipped'] += 1
        elif auto_enabled:
            mk = get_app_setting('IMOU_MK', '') or ''
            cam_type = _guess_provider_type(vendor, model)
            name = f"{vendor} {model}".strip() or f"Camera @ {ip}"

            new_id = cam_svc.add_camera(
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
            if isinstance(new_id, bool) or not isinstance(new_id, int) or new_id <= 0:
                logger.error("Automatic camera registration returned no valid ID")
                raise RuntimeError("Automatic camera registration did not return a valid ID")
            if rtsp_url and not cam_svc.store_onvif_detected_rtsp(new_id, rtsp_url):
                logger.error("New camera RTSP update affected no row (camera_id=%s)", new_id)
                raise RuntimeError("New camera RTSP update did not affect a row")
            if not cam_svc.upsert_camera_asset(
                cam_id=serial or ip, name=name, ip=ip, serial=serial,
                model=model, rtsp_url=rtsp_url, mac=mac,
                onvif_port=dev.get('onvif_port'),
                onvif_profiles=dev.get('onvif_profiles'),
            ):
                logger.error("New camera asset write affected no row (camera_id=%s)", new_id)
                raise RuntimeError("New camera asset could not be stored")
            asset_written = True
            stats['registered'] += 1
            logger.info(f"Auto-registered: {name} @ {ip}")
        else:
            stats['skipped'] += 1

        if (mac or serial) and not asset_written:
            if not cam_svc.upsert_camera_asset(
                cam_id=serial or mac or ip, name=f"{vendor} {model}".strip() or ip,
                ip=ip, serial=serial, model=model, rtsp_url=rtsp_url, mac=mac,
                onvif_port=dev.get('onvif_port'),
                onvif_profiles=dev.get('onvif_profiles'),
            ):
                logger.error("Discovered camera asset write affected no row")
                raise RuntimeError("Discovered camera asset could not be stored")

    return stats

def discover_and_update_all_cameras():
    from backend.util.imou_api import ImouClient
    
    results = {'found_local': 0, 'found_cloud': 0, 'updated': 0, 'total': 0, 'new_devices': [], 'updated_cameras': []}
    
    all_cams = db._execute_query("SELECT id, camera_name, imou_device_id, camera_ip FROM cameras", fetchall=True)
    results['total'] = len(all_cams)
    
    existing_ids = [str(cam.get('imou_device_id')).strip().lower() for cam in all_cams if cam.get('imou_device_id')]
    existing_ips = [cam.get('camera_ip') for cam in all_cams if cam.get('camera_ip')]
    logger.info(f"Discovery: {len(all_cams)} cameras in DB. Existing IDs: {existing_ids}")
    
    seen_ids = set()
    seen_ips = set(existing_ips)
    found_ips = {}
    
    logger.info("Starting local discovery (Dahua UDP + ONVIF)...")
    
    dahua_found = discover_dahua_cameras(timeout=5.0)
    logger.info(f"Dahua UDP scan found {len(dahua_found)} device(s).")
    results['found_local'] += len(dahua_found)
    for dev in dahua_found:
        found_ips[dev['serial'].lower()] = dev['ip']

    imou_mk = get_app_setting("IMOU_MK", "")

    onvif_user = get_app_setting("ONVIF_USERNAME", "") or "admin"
    onvif_pass = get_app_setting("ONVIF_PASSWORD", "") or imou_mk or ""
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

    if imou_mk:
        dahua_to_probe = [
            dev for dev in local_found
            if any(k in (dev.get('manufacturer') or '').lower() for k in ['dahua', 'imou'])
            and dev.get('is_online') and not dev.get('serial')
        ]
        if dahua_to_probe:
            def _probe_dahua_serial(dev):
                return get_onvif_full_info(
                    dev['ip'], dev.get('onvif_port', 80),
                    username='admin', password=imou_mk, timeout=4
                )

            full_results = _run_bounded_probes(
                dahua_to_probe, _probe_dahua_serial, max_workers=3, timeout=12,
                label="ONVIF serial probe",
            )
            for dev, full in zip(dahua_to_probe, full_results):
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

    onvif_ips = {dev['ip'] for dev in local_found}
    for dev in dahua_found:
        if dev['ip'] not in onvif_ips:
            unicast_result = probe_onvif_unicast(dev['ip'])
            if unicast_result:
                unicast_result['source'] = 'onvif'
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

    onvif_ip_map = {}
    for dev in local_found:
        ip = dev['ip']
        dev_id = (dev.get('id') or dev.get('serial') or '').lower()
        if dev_id:
            onvif_ip_map[dev_id] = ip
            if dev_id not in found_ips:
                found_ips[dev_id] = ip
    
    imou_keys = cam_svc.get_imou_api_keys()
    cloud_new_devices = []
    if imou_keys.get('app_id') and imou_keys.get('app_secret'):
        logger.info("Starting Imou Cloud API sync...")
        client = ImouClient(imou_keys['app_id'], imou_keys['app_secret'])
        cloud_devices = client.list_devices()
        logger.info(f"Cloud API found {len(cloud_devices)} device(s).")

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

                if not lip or lip == "0.0.0.0":
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
    else:
        logger.warning("Imou Cloud API keys NOT configured. Skipping cloud sync.")
    
    cloud_no_ip = [d for d in cloud_new_devices if not d.get('ip')]
    if cloud_no_ip:
        logger.info(f"Resolving IP for {len(cloud_no_ip)} cloud device(s) via ARP + port scan...")
        arp_candidates = discover_from_arp()
        arp_ips_with_camera_ports = {c['ip'] for c in arp_candidates if c.get('is_online')}

        for dev in cloud_no_ip:
            did = dev['id'].upper()
            matched_ip = found_ips.get(dev['id'])
            if matched_ip:
                dev['ip'] = matched_ip
                logger.info(f"  [ARP→CLOUD] Resolved {did} → {matched_ip} via Dahua serial")
                continue

            if imou_mk:
                for arp_ip in list(arp_ips_with_camera_ports):
                    if arp_ip in seen_ips or arp_ip in [d.get('ip') for d in cloud_new_devices if d.get('ip')]:
                        continue
                    full = get_onvif_full_info(arp_ip, 80, 'admin', imou_mk, timeout=4)
                    if full and full.get('serial', '').upper() == did:
                        dev['ip'] = arp_ip
                        if full.get('rtsp_main'):
                            dev['rtsp_url_onvif'] = full['rtsp_main']
                        if full.get('rtsp_sub'):
                            dev['rtsp_sub_onvif'] = full['rtsp_sub']
                        found_ips[dev['id']] = arp_ip
                        logger.info(f"  [ONVIF→CLOUD] Resolved {did} → {arp_ip} via ONVIF serial match")
                        break

        resolved = sum(1 for d in cloud_no_ip if d.get('ip'))
        logger.info(f"ARP IP resolution: {resolved}/{len(cloud_no_ip)} cloud device(s) resolved")

    new_devices = []
    existing_devices = []
    matched_cam_ids = set()

    def _find_matching_cam(dev_id, ip):
        for cam in all_cams:
            if cam['id'] in matched_cam_ids:
                continue
            cam_did = (cam.get('imou_device_id') or '').strip().lower()
            cam_ip = cam.get('camera_ip', '')
            if (cam_did and dev_id and cam_did.upper() == dev_id.upper()) or (cam_ip and cam_ip == ip):
                return cam
        return None

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

    all_discovered = []
    for dev in new_devices:
        dev['_exists'] = False
        all_discovered.append(dev)
    for dev in existing_devices:
        dev['_exists'] = True
        all_discovered.append(dev)

    results['devices'] = all_discovered
    results['new_devices'] = new_devices
    results['existing_devices'] = existing_devices

    for cam in all_cams:
        did = cam.get('imou_device_id')
        if did:
            did_lower = did.strip().lower()
            if did_lower in found_ips:
                new_ip = found_ips[did_lower]
                if cam['camera_ip'] != new_ip:
                    logger.info(f"UPDATING IP: '{cam['camera_name']}' {cam['camera_ip']} → {new_ip}")
                    if not db._execute_query(
                        "UPDATE cameras SET camera_ip = %s WHERE id = %s",
                        (new_ip, cam['id']), commit=True,
                    ):
                        logger.error("Discovered camera IP update affected no row (camera_id=%s)", cam['id'])
                        raise RuntimeError("Discovered camera IP update did not affect a row")
                    if not cam_svc.regenerate_rtsp_url(cam['id']):
                        logger.error("Discovered camera RTSP reset affected no row (camera_id=%s)", cam['id'])
                        raise RuntimeError("Discovered camera RTSP reset did not affect a row")
                    results['updated_cameras'].append({
                        'name': cam['camera_name'],
                        'old_ip': cam['camera_ip'] or '',
                        'new_ip': new_ip
                    })
                    results['updated'] += 1

    all_discovered = list(local_found) + list(dahua_found) + list(cloud_new_devices)
    for dev in all_discovered:
        cam_id = (dev.get('serial') or dev.get('id') or dev.get('ip', '')).strip()
        if cam_id and not cam_id.startswith('unidentified_'):
            if not cam_svc.upsert_camera_asset(
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
            ):
                logger.error("Discovered camera asset write affected no row")
                raise RuntimeError("Discovered camera asset could not be stored")

    if new_devices:
        reg_result = auto_register_discovered_cameras(new_devices)
        results['auto_registered'] = reg_result.get('registered', 0)
        if reg_result['registered'] > 0:
            logger.info(f"Auto-registration: {reg_result}")

    if not set_app_setting("LAST_DISCOVERY_AT", time.strftime("%Y-%m-%d %H:%M:%S")):
        logger.error("Camera discovery timestamp setting write affected no row")
        raise RuntimeError("Camera discovery timestamp could not be stored")

    return results
