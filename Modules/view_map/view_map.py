#!/usr/bin/env python3
"""
View map module - opens existing map or generates new one
"""

import json
import sys
import os

def main():
    # Read config
    with open(sys.argv[1], 'r') as f:
        config = json.load(f)
    
    site_name = config.get("site_name", "")
    db_path = config.get("database_path", "database.json")
    
    # Check for existing map
    map_file = f"{site_name}_map.html"
    if os.path.exists(map_file):
        result = {
            "status": "success",
            "message": f"Map for {site_name} found",
            "data": {
                "map_url": f"/static/maps/{site_name}_map.html",
                "map_path": os.path.abspath(map_file)
            }
        }
    else:
        result = {
            "status": "warning",
            "message": f"No map found for {site_name}",
            "data": {
                "map_url": None,
                "map_path": None
            }
        }
    
    print(json.dumps(result))

if __name__ == "__main__":
    main()