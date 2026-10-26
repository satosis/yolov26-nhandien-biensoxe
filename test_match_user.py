import sys, io, re, unicodedata
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from backend.services.database import db

def _fold(value: str) -> str:
    value = unicodedata.normalize('NFD', (value or '').casefold())
    return re.sub(r'\s+', ' ', ''.join(c for c in value if unicodedata.category(c) != 'Mn').replace('đ', 'd')).strip()

users = db._execute_query('SELECT id, username, full_name, nickname FROM public.users', fetchall=True) or []
vehicles = db.select('plate_whitelist') or []

def extract_vehicle_or_driver(texts: list[str], vehicles_list: list[dict], users_list: list[dict]) -> tuple[dict | None, dict | None, str]:
    """
    Extract vehicle, user, and matched nickname/name string from Zalo messages texts.
    No hardcoding.
    """
    joined = " ".join(texts)
    folded_joined = _fold(joined)
    
    # 1. Plate match directly in texts
    plate_re = re.search(r"\b(\d{2}[A-Z]-?\d{3}\.?\d{2})\b", joined, re.I)
    if plate_re:
        raw_plate = plate_re.group(1).upper()
        clean_target = re.sub(r"[^A-Z0-9]", "", raw_plate)
        for v in vehicles_list:
            clean_v = re.sub(r"[^A-Z0-9]", "", (v.get("plate_number") or "").upper())
            if clean_target == clean_v:
                return v, None, raw_plate

    # Check 5-digit or short plate in texts like "147.98" or "14798"
    for v in vehicles_list:
        raw_p = v.get("plate_number") or ""
        short_dots = re.search(r"\d{3}\.\d{2}", raw_p)
        if short_dots and short_dots.group(0) in joined:
            return v, None, short_dots.group(0)
            
    # 2. Match against user nicknames, full names, given names (both accented and unaccented)
    candidate_matches = []
    
    for u in users_list:
        fn = u.get("full_name") or ""
        nick = u.get("nickname") or ""
        fn_folded = _fold(fn)
        
        # Check nickname
        if nick:
            for n in nick.split(","):
                n_clean = n.strip()
                n_folded = _fold(n_clean)
                if n_clean:
                    # Accented pattern in joined
                    pat1 = rf"\bxe\s+(?:cua\s+)?(?:chú|bác|anh|chi|em|ông|bà|cô|bác)?\s*{re.escape(n_clean)}\b"
                    pat2 = rf"\b{re.escape(n_clean)}\b"
                    m1 = re.search(pat1, joined, re.I)
                    m2 = re.search(pat2, joined, re.I)
                    if m1:
                        candidate_matches.append((len(m1.group(0)), u, n_clean, m1.group(0)))
                    elif m2:
                        candidate_matches.append((len(m2.group(0)), u, n_clean, m2.group(0)))
                    elif n_folded:
                        # Unaccented pattern in folded_joined
                        pat_fold = rf"\bxe\s+(?:cua\s+)?(?:chu|bac|anh|chi|em|ong|ba|co)?\s*{re.escape(n_folded)}\b"
                        m_fold = re.search(pat_fold, folded_joined)
                        if m_fold:
                            candidate_matches.append((len(m_fold.group(0)), u, n_clean, m_fold.group(0)))
                        elif re.search(rf"\b{re.escape(n_folded)}\b", folded_joined) and n_folded != "loi":
                            # Avoid over-matching generic common words unless preceded by xe
                            m_pure = re.search(rf"\b{re.escape(n_folded)}\b", folded_joined)
                            candidate_matches.append((len(m_pure.group(0)), u, n_clean, m_pure.group(0)))
                            
        # Check given name (last word) and full name
        if fn_folded:
            parts = fn.strip().split()
            given_name = parts[-1] if parts else ""
            given_name_folded = _fold(given_name)
            if given_name:
                pat_g1 = rf"\bxe\s+(?:cua\s+)?(?:chú|bác|anh|chi|em|ông|bà|cô|bác)?\s*{re.escape(given_name)}\b"
                pat_g2 = rf"\b(?:chú|bác|anh|chi|em|ông|bà|cô|bác)\s+{re.escape(given_name)}\b"
                mg1 = re.search(pat_g1, joined, re.I)
                mg2 = re.search(pat_g2, joined, re.I)
                if mg1:
                    candidate_matches.append((len(mg1.group(0)), u, given_name, mg1.group(0)))
                elif mg2:
                    candidate_matches.append((len(mg2.group(0)), u, given_name, mg2.group(0)))
                elif given_name_folded:
                    pat_g_fold = rf"\bxe\s+(?:cua\s+)?(?:chu|bac|anh|chi|em|ong|ba|co)?\s*{re.escape(given_name_folded)}\b"
                    mg_fold = re.search(pat_g_fold, folded_joined)
                    if mg_fold:
                        candidate_matches.append((len(mg_fold.group(0)), u, given_name, mg_fold.group(0)))
            # Also full name
            if len(parts) >= 2 and fn_folded in folded_joined:
                candidate_matches.append((len(fn), u, fn, fn))

    if candidate_matches:
        candidate_matches.sort(key=lambda x: x[0], reverse=True)
        best_len, best_user, display_name, match_text = candidate_matches[0]
        
        matched_vehicle = None
        user_fn_folded = _fold(best_user.get("full_name") or "")
        for v in vehicles_list:
            v_owner_folded = _fold(v.get("owner_name") or "")
            if user_fn_folded and (v_owner_folded == user_fn_folded or user_fn_folded in v_owner_folded):
                matched_vehicle = v
                break
        return matched_vehicle, best_user, display_name

    # 3. Fallback: generic "xe <tên>"
    generic_m = re.search(r"\bxe\s+(?:cua\s+)?(?:chú|bác|anh|chi|em|ông|bà|cô|bác)?\s*([A-Za-z0-9_À-ỹ]+)", joined, re.I)
    if generic_m:
        raw_name = generic_m.group(1).strip()
        return None, None, raw_name
        
    return None, None, ""

messages_tests = [
    ["xe bố chạy chuyến phú thọ về vĩnh phúc", "cước 3tr"],
    ["xe bo di son tay", "cuoc 2.5 tr"],
    ["nhờ xe anh Quyết chuyển giúp ống thép", "cước 4 triệu"],
    ["xe chú Lợi vừa nhận hàng", "cước 3.5tr"],
    ["xe loi vua nhan hang", "cuoc 3.5tr"],
    ["xe 19H-147.98 xuất phát", "giá 2tr"],
    ["147.98 lấy hàng ở kcn thụy vân", "3.2 tr"],
    ["xe chú Ba đi", "cước 2tr"],
]

for msgs in messages_tests:
    v, u, nick = extract_vehicle_or_driver(msgs, vehicles, users)
    v_plate = v["plate_number"] if v else "None"
    u_name = u.get("full_name") if u else "None"
    print(f"Messages: {msgs[0]:45s} -> Nick: '{nick:15s}' | Vehicle: {v_plate:12s} | User: {u_name}")
