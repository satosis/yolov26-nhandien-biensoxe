import math
import pandas as pd

class DoriController:
    def calculate_dori(self, res_w, res_h, scene_w, hfov, mount_h, target_h):
        DORI_PPM = {"Detection": 25, "Observation": 62, "Recognition": 125, "Identification": 250}
        rows = []
        for level, ppm in DORI_PPM.items():
            max_dist_h = res_w / (ppm * scene_w)
            max_dist_v = res_h / (ppm * target_h)
            max_dist = min(max_dist_h, max_dist_v)
            focal_mm = round(max_dist * 1000 / (2 * math.tan(math.radians(hfov / 2)) * 1000), 1)
            rows.append({
                "Mức DORI": level,
                "PPM yêu cầu": ppm,
                "Khoảng cách tối đa (m)": round(max_dist, 2),
                "Gợi ý focal (mm)": focal_mm,
                "Đạt tiêu chuẩn": "✅" if max_dist >= 1.0 else "⚠️ Quá gần",
            })
        return pd.DataFrame(rows)

    def generate_camera_name(self, region, site, building, zone, cam_num, desc):
        desc_clean = desc.upper().replace(" ", "_")
        return f"{region.upper()}-{site.upper()}-{building.upper()}-{zone.upper()}-C{cam_num:03d}-{desc_clean}"
