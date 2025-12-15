#!/bin/bash
# Test the CDP module manually

echo "Creating test config..."
cat > test_cdp_config.json << EOF
{
  "site_name": "TestSite",
  "parameters": {
    "root_ip": "10.192.178.1",
    "username": "admin",
    "password": "password",
    "subnet_mask": "24",
    "max_hops": 3
  },
  "database_path": "database.json",
  "module_id": "cdp_discovery"
}
EOF

echo "Running CDP module..."
python modules/cdp_discovery/cdp_module.py test_cdp_config.json

echo "Checking database..."
python -c "
import json
with open('database.json', 'r') as f:
    data = json.load(f)
print(f'Total devices: {len(data.get(\"devices\", []))}')
for device in data.get(\"devices\", []):
    print(f'  - {device[\"name\"]} ({device[\"ip\"]})')
"