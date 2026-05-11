import decimal
from backend.core.database import DatabaseManager

db = DatabaseManager()
try:
    res = db._execute_query("SELECT fuel_liters FROM cargo_trips WHERE fuel_liters IS NOT NULL LIMIT 1", fetch=True)
    if res:
        val = res['fuel_liters']
        print(f"Value: {val}, Type: {type(val)}")
        import json
        try:
            print(f"JSON: {json.dumps(res)}")
        except Exception as e:
            print(f"JSON Error: {e}")
    else:
        print("No cargo trips with fuel_liters found.")
except Exception as e:
    print(f"Error: {e}")
