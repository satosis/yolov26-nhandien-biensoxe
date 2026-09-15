import sys
import os
import json

sys.path.insert(0, '/Users/mac/Documents/yolov26-nhandien-biensoxe')

from backend.util.network_utils import discover_dahua_cameras, discover_onvif_cameras, discover_from_arp

print("=== STARTING DAHUA DISCOVERY ===")
try:
    dahua_cams = discover_dahua_cameras(timeout=8.0)
    print(f"Found {len(dahua_cams)} Dahua/Imou devices:")
    for c in dahua_cams:
        print(f" - {c.get('ip')}:{c.get('port')} (Serial: {c.get('serial')})")
except Exception as e:
    print("Dahua discovery error:", e)

print("\n=== STARTING ONVIF DISCOVERY ===")
try:
    onvif_cams = discover_onvif_cameras(timeout=8.0)
    print(f"Found {len(onvif_cams)} ONVIF devices:")
    for c in onvif_cams:
        print(f" - {c.get('ip')}:{c.get('onvif_port')} (Mfr: {c.get('manufacturer')}, Model: {c.get('model')})")
except Exception as e:
    print("ONVIF discovery error:", e)

print("\n=== ARP DISCOVERY ===")
try:
    arp_cams = discover_from_arp()
    print(f"Found {len(arp_cams)} devices from ARP:")
    for c in arp_cams:
        print(f" - {c.get('ip')} (MAC: {c.get('mac')})")
except Exception as e:
    print("ARP discovery error:", e)
