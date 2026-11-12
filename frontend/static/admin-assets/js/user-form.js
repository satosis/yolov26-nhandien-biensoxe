/* User add/edit forms: guess the gender from the name. */
const FEMALE_KEYWORDS = new Set([
    'thị', 'thi', 'huyền', 'huyen', 'hoa', 'thảo', 'thao', 'lan', 'hương', 'huong',
    'ngọc', 'ngoc', 'mai', 'phương', 'phuong', 'trang', 'linh', 'nhung', 'thủy', 'thuy',
    'tuyết', 'tuyet', 'thu', 'ngân', 'ngan', 'dung', 'hạnh', 'hanh', 'oanh', 'yến', 'yen',
    'liên', 'lien', 'vân', 'van', 'quỳnh', 'quynh', 'hằng', 'hang', 'diệp', 'diep',
    'loan', 'nga', 'bích', 'bich', 'hồng', 'hong', 'thoa', 'tươi', 'tuoi', 'diệu', 'dieu',
    'hiền', 'hien', 'mơ', 'mo', 'thơ', 'tho', 'gái', 'gai', 'nữ', 'nu', 'mỹ', 'my',
    'nhi', 'nương', 'nuong', 'phượng', 'phuong', 'cúc', 'cuc', 'đào', 'dao', 'ly',
    'trinh', 'xuyến', 'xuyen', 'lệ', 'le', 'nụ', 'nu'
  ]);
const MALE_KEYWORDS = new Set([
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
  ]);

function predictGender(name) {
    if (!name) return 'nam';
    const parts = name.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!parts.length) return 'nam';
    if (parts.includes('thị') || parts.includes('thi')) return 'nữ';
    if (parts.includes('văn') || parts.includes('van')) return 'nam';
    const given = parts[parts.length - 1];
    const middle = parts.length > 1 ? parts[parts.length - 2] : '';
    if (FEMALE_KEYWORDS.has(given)) {
      if (MALE_KEYWORDS.has(middle)) return 'nam';
      return 'nữ';
    }
    if (MALE_KEYWORDS.has(given)) return 'nam';
    if (FEMALE_KEYWORDS.has(middle)) return 'nữ';
    if (MALE_KEYWORDS.has(middle)) return 'nam';
    return 'nam';
  }
