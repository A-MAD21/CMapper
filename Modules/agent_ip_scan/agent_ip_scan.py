#!/usr/bin/env python3
import json
import sys

if __name__ == "__main__":
    print(json.dumps({"status": "error", "message": "agent_only"}))
    sys.exit(0)
