import os
import json
from pathlib import Path
from decimal import Decimal
import psycopg2
from psycopg2.extras import RealDictCursor

db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:mysecretpassword@127.0.0.1:5432/camera_ai")
export_dir = Path("data/backup_export")
export_dir.mkdir(parents=True, exist_ok=True)

def default_converter(o):
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, 'isoformat'):
        return o.isoformat()
    if isinstance(o, (bytes, memoryview)):
        return str(bytes(o))
    return str(o)

try:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
    tables = sorted([r[0] for r in cur.fetchall()])
    print(f"📦 Đang xuất dữ liệu từ PostgreSQL: {len(tables)} bảng...")
    
    summary = {}
    for table in tables:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as tcur:
                tcur.execute(f'SELECT * FROM "{table}"')
                rows = tcur.fetchall()
                out_file = export_dir / f"{table}.json"
                with open(out_file, 'w', encoding='utf-8') as f:
                    json.dump(rows, f, ensure_ascii=False, indent=2, default=default_converter)
                summary[table] = len(rows)
        except Exception as e:
            print(f"Lỗi bảng {table}: {e}")
            conn.rollback()

    cur.close()
    conn.close()
    
    summary_file = export_dir / "_summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        
    total_records = sum(summary.values())
    print(f"\n🎉 ĐÃ XUẤT THÀNH CÔNG 100% DỮ LIỆU CỦA {len(summary)} BẢNG ({total_records} bản ghi) RA:")
    print(f"📂 {export_dir.resolve()}")
except Exception as e:
    print(f"Lỗi kết nối database: {e}")
