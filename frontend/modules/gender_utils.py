"""Gender prediction and honorific utility for Vietnamese names."""

from __future__ import annotations

FEMALE_KEYWORDS = {
    'thị', 'thi', 'huyền', 'huyen', 'hoa', 'thảo', 'thao', 'lan', 'hương', 'huong',
    'ngọc', 'ngoc', 'mai', 'phương', 'phuong', 'trang', 'linh', 'nhung', 'thủy', 'thuy',
    'tuyết', 'tuyet', 'thu', 'ngân', 'ngan', 'dung', 'hạnh', 'hanh', 'oanh', 'yến', 'yen',
    'liên', 'lien', 'vân', 'van', 'quỳnh', 'quynh', 'hằng', 'hang', 'diệp', 'diep',
    'loan', 'nga', 'bích', 'bich', 'hồng', 'hong', 'thoa', 'tươi', 'tuoi', 'diệu', 'dieu',
    'hiền', 'hien', 'mơ', 'mo', 'thơ', 'tho', 'gái', 'gai', 'nữ', 'nu', 'mỹ', 'my',
    'nhi', 'nương', 'nuong', 'phượng', 'phuong', 'cúc', 'cuc', 'đào', 'dao', 'ly',
    'trinh', 'xuyến', 'xuyen', 'lệ', 'le', 'nụ', 'nu'
}

MALE_KEYWORDS = {
    'văn', 'van', 'đức', 'duc', 'hùng', 'hung', 'dũng', 'dung', 'thành', 'thanh',
    'cường', 'cuong', 'tuấn', 'tuan', 'hoàng', 'hoang', 'huy', 'nam', 'quân', 'quan',
    'trung', 'hải', 'hai', 'minh', 'long', 'quuyết', 'quyet', 'thắng', 'thang',
    'sơn', 'son', 'tùng', 'tung', 'bách', 'bach', 'khang', 'khôi', 'khoi', 'phúc', 'phuc',
    'trọng', 'trong', 'hiệp', 'hiep', 'tiến', 'tien', 'toàn', 'toan', 'việt', 'viet',
    'bảo', 'bao', 'kiên', 'kien', 'đạt', 'dat', 'khoa', 'binh', 'bình', 'chiến', 'chien',
    'phong', 'lợi', 'loi', 'đáng', 'dang', 'thái', 'thai', 'duân', 'duan', 'lăng', 'lang',
    'yên', 'yen', 'thường', 'thuong', 'thiết', 'thiet', 'luân', 'luan', 'hữu', 'huu',
    'quang', 'mạnh', 'manh', 'duy', 'đình', 'dinh', 'khắc', 'khac', 'bá', 'ba', 'phú', 'phu',
    'thế', 'the', 'tuyên', 'tuyen', 'hà', 'ha', 'bắc', 'bac', 'hưng', 'hung'
}


def predict_gender(name: str | None) -> str:
    """Predict gender ('nam' or 'nữ') from a person's full name."""
    if not name:
        return 'nam'
    val = name.strip()
    if val.lower().startswith('ông '):
        return 'nam'
    if val.lower().startswith('bà '):
        return 'nữ'

    parts = val.lower().split()
    if not parts:
        return 'nam'

    # 1. 'thị' in middle words is 100% female
    if 'thị' in parts or 'thi' in parts:
        return 'nữ'

    # 2. 'văn' in middle words is 100% male
    if 'văn' in parts or 'van' in parts:
        return 'nam'

    given = parts[-1]
    middle = parts[-2] if len(parts) > 1 else ''

    if given in FEMALE_KEYWORDS:
        if middle in MALE_KEYWORDS:
            return 'nam'
        return 'nữ'

    if given in MALE_KEYWORDS:
        return 'nam'

    if middle in FEMALE_KEYWORDS:
        return 'nữ'
    if middle in MALE_KEYWORDS:
        return 'nam'

    return 'nam'


def get_honorific(name: str | None = None, gender: str | None = None) -> str:
    """Return 'Ông' or 'Bà' based on explicit gender or name prediction."""
    if gender:
        g = gender.strip().lower()
        if g in ('nữ', 'nu', 'female', 'f'):
            return 'Bà'
        if g in ('nam', 'male', 'm'):
            return 'Ông'

    val = (name or '').strip()
    if val.lower().startswith('bà '):
        return 'Bà'
    if val.lower().startswith('ông '):
        return 'Ông'

    return 'Bà' if predict_gender(val) == 'nữ' else 'Ông'


def split_rep_honorific(rep: str | None, gender: str | None = None) -> tuple[str, str]:
    """Split representative into (prefix_with_space, clean_name).
    e.g. ("Ông ", "NGUYỄN KIM QUYẾT") or ("Bà ", "VŨ THỊ HIẾN").
    If rep is empty, returns ("", "").
    """
    val = (rep or '').strip()
    if not val:
        return ("", "")

    clean_name = val
    if val.lower().startswith('ông '):
        clean_name = val[4:].strip()
    elif val.lower().startswith('bà '):
        clean_name = val[3:].strip()
    elif val.lower().startswith('(ông/bà) '):
        clean_name = val[9:].strip()
    elif val.lower().startswith('ông/bà '):
        clean_name = val[7:].strip()

    honorific = get_honorific(clean_name, gender)
    return (f"{honorific} ", clean_name)
