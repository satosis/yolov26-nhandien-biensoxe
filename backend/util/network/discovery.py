import socket
import select
import time
import logging
import json
from typing import List, Dict

from backend.camera import camera_service as cam_svc
from backend.services.database import db
from backend.services.settings_service import get_app_setting, set_app_setting

from .arp import _get_local_broadcast_addresses, discover_from_arp
from .onvif import (
    discover_onvif_only,
    probe_onvif_unicast,
    get_onvif_full_info,
    _run_bounded_probes,
    _guess_provider_type,
)

logger = logging.getLogger("network_utils.discovery")


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
                    raise RuntimeError("Discovered camera RTSP reset did not affect a row")
                if not cam_svc.store_onvif_detected_rtsp(matched['id'], rtsp_url):
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
            if new_id is None:
                raise RuntimeError("Automatic camera registration did not return a valid ID")
            if rtsp_url and not cam_svc.store_onvif_detected_rtsp(new_id, rtsp_url):
                raise RuntimeError("New camera RTSP update did not affect a row")
            if not cam_svc.upsert_camera_asset(
                cam_id=serial or ip, name=name, ip=ip, serial=serial,
                model=model, rtsp_url=rtsp_url, mac=mac,
                onvif_port=dev.get('onvif_port'),
                onvif_profiles=dev.get('onvif_profiles'),
            ):
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
                raise RuntimeError("Discovered camera asset could not be stored")

    return stats


def discover_and_update_all_cameras():
    from backend.util.network.imou_api import ImouClient
    
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

    for dev in local_found:
        dev_id = (dev.get('id') or dev.get('serial') or '').lower()
        if dev_id and dev_id not in found_ips:
            found_ips[dev_id] = dev['ip']
    
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
            did = dev['deviceId'].lower()
            lip = dev.get('deviceLocalIp')

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
                raise RuntimeError("Discovered camera asset could not be stored")

    if new_devices:
        reg_result = auto_register_discovered_cameras(new_devices)
        results['auto_registered'] = reg_result.get('registered', 0)
        if reg_result['registered'] > 0:
            logger.info(f"Auto-registration: {reg_result}")

    if not set_app_setting("LAST_DISCOVERY_AT", time.strftime("%Y-%m-%d %H:%M:%S")):
        raise RuntimeError("Camera discovery timestamp could not be stored")

    return results
