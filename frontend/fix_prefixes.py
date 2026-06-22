import os
import glob
import re

template_dir = r"D:\project\yolov26-nhandien-biensoxe\frontend\templates"

# Map of old prefixes to new prefix /admin/
# We use regex to match href="/..." or fetch('/...') or fetch(`/...`)
patterns = [
    (r'(href=["\'])/camera/', r'\1/admin/'),
    (r'(href=["\'])/providers', r'\1/admin/providers'),
    (r'(href=["\'])/login', r'\1/login'),
    
    (r"(fetch\(['\`\"])/camera/detection/", r"\1/admin/detection/"),
    (r"(fetch\(['\`\"])/detection/", r"\1/admin/detection/"),
    (r"(fetch\(['\`\"])/cameras/", r"\1/admin/cameras/"),
    
    # Also fix rtspFeed.src
    (r"(src\s*=\s*['\`\"])/detection/", r"\1/admin/detection/"),
]

for root, _, files in os.walk(template_dir):
    for file in files:
        if file.endswith(".html"):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = content
            for pattern, repl in patterns:
                new_content = re.sub(pattern, repl, new_content)
                
            if new_content != content:
                print(f"Updated {path}")
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)

print("Done fixing prefixes.")
