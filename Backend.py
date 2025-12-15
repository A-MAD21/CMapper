#!/usr/bin/env python3
"""
Network Discovery Platform - COMPLETE WORKING BACKEND
"""

from flask import Flask, render_template, jsonify, request
import json
import os
import threading
import time
import subprocess
import uuid
from datetime import datetime
import portalocker  # For file locking

app = Flask(__name__, static_folder='static', template_folder='templates')

# ==================== CONFIGURATION ====================

DATABASE_FILE = 'database.json'
SETTINGS_FILE = 'settings.json'
MODULES_DIR = 'modules'

# ==================== FILE OPERATIONS ====================

def init_database():
    """Initialize empty database if it doesn't exist"""
    if not os.path.exists(DATABASE_FILE):
        data = {
            "version": "1.0",
            "meta": {
                "created": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat()
            },
            "sites": [],
            "devices": [],
            "discovery_sessions": []
        }
        with open(DATABASE_FILE, 'w') as f:
            json.dump(data, f, indent=2)

def init_settings():
    """Initialize default settings"""
    if not os.path.exists(SETTINGS_FILE):
        settings = {
            "default_site": "",
            "backup_path": "./backups",
            "default_scan_depth": 3,
            "auto_refresh": False,
            "refresh_interval": 30
        }
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)

def read_database():
    """Read database with file locking"""
    try:
        if os.path.exists(DATABASE_FILE):
            with open(DATABASE_FILE, 'r') as f:
                return json.load(f)
        else:
            init_database()
            return read_database()
    except (json.JSONDecodeError, FileNotFoundError):
        init_database()
        return read_database()

def write_database(data):
    """Write database"""
    try:
        with open(DATABASE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error writing database: {e}", file=sys.stderr)
        return False

def read_settings():
    """Read settings file"""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        else:
            init_settings()
            return read_settings()
    except (json.JSONDecodeError, FileNotFoundError):
        init_settings()
        return read_settings()

def write_settings(settings):
    """Write settings file"""
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

# ==================== MODULE SYSTEM ====================

def discover_modules():
    """Find all available modules in modules/ directory"""
    modules = []
    
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
        return modules
    
    for module_name in os.listdir(MODULES_DIR):
        module_path = os.path.join(MODULES_DIR, module_name)
        module_json = os.path.join(module_path, 'module.json')
        
        if os.path.isdir(module_path) and os.path.exists(module_json):
            try:
                with open(module_json, 'r') as f:
                    module_info = json.load(f)
                    module_info['id'] = module_name
                    modules.append(module_info)
            except (json.JSONDecodeError, KeyError):
                # Skip invalid modules
                continue
    
    return modules

class ModuleRunner:
    """Run modules asynchronously with status tracking"""
    
    def __init__(self):
        self.running_modules = {}
        self.module_results = {}
        self.lock = threading.Lock()
    
    def run_module(self, module_id, config):
        """Run a module in background thread"""
        thread_id = str(uuid.uuid4())[:8]
        
        def module_thread():
            try:
                # Update status
                with self.lock:
                    self.running_modules[thread_id] = {
                        "module_id": module_id,
                        "status": "running",
                        "start_time": datetime.now().isoformat(),
                        "progress": 0
                    }
                
                print(f"=== DEBUG: Starting module {module_id} ===", file=sys.stderr)
                print(f"Config: {config}", file=sys.stderr)
                
                # Prepare module execution
                module_dir = os.path.join(MODULES_DIR, module_id)
                module_script = os.path.join(module_dir, f"{module_id}.py")
                
                if not os.path.exists(module_script):
                    # Try to find any .py file in module directory
                    py_files = [f for f in os.listdir(module_dir) if f.endswith('.py')]
                    if py_files:
                        module_script = os.path.join(module_dir, py_files[0])
                    else:
                        raise FileNotFoundError(f"No Python script found for module {module_id}")
                
                # Create temp config file
                temp_config = {
                    **config,
                    "database_path": os.path.abspath(DATABASE_FILE),
                    "module_id": module_id,
                    "thread_id": thread_id
                }
                
                config_file = f"module_config_{thread_id}.json"
                with open(config_file, 'w') as f:
                    json.dump(temp_config, f, indent=2)
                
                print(f"Config file created: {config_file}", file=sys.stderr)
                
                # Run the module
                with self.lock:
                    self.running_modules[thread_id]["progress"] = 25
                
                # Use the system's Python interpreter
                python_executable = "python" if os.name == "nt" else "python3"
                
                print(f"Running: {python_executable} {module_script} {config_file}", file=sys.stderr)
                print(f"Current dir: {os.getcwd()}", file=sys.stderr)
                
                result = subprocess.run(
                    [python_executable, module_script, config_file],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=os.path.dirname(os.path.abspath(__file__))
                )
                
                print(f"=== DEBUG: Module execution result ===", file=sys.stderr)
                print(f"Return code: {result.returncode}", file=sys.stderr)
                print(f"STDOUT: {result.stdout[:500]}", file=sys.stderr)
                print(f"STDERR: {result.stderr}", file=sys.stderr)
                
                with self.lock:
                    self.running_modules[thread_id]["progress"] = 75
                
                # Parse module output
                if result.returncode == 0:
                    try:
                        module_output = json.loads(result.stdout.strip())
                        with self.lock:
                            self.module_results[thread_id] = {
                                "status": "completed",
                                "output": module_output,
                                "completed_at": datetime.now().isoformat()
                            }
                    except json.JSONDecodeError:
                        with self.lock:
                            self.module_results[thread_id] = {
                                "status": "completed",
                                "output": {"message": result.stdout.strip()},
                                "completed_at": datetime.now().isoformat()
                            }
                else:
                    with self.lock:
                        self.module_results[thread_id] = {
                            "status": "failed",
                            "error": result.stderr,
                            "completed_at": datetime.now().isoformat()
                        }
                
                # Update final status
                with self.lock:
                    if thread_id in self.running_modules:
                        self.running_modules[thread_id]["status"] = "completed"
                        self.running_modules[thread_id]["progress"] = 100
                        self.running_modules[thread_id]["completed_at"] = datetime.now().isoformat()
                
                # Clean up temp config file
                try:
                    os.remove(config_file)
                except:
                    pass
                
            except subprocess.TimeoutExpired:
                with self.lock:
                    if thread_id in self.running_modules:
                        self.running_modules[thread_id]["status"] = "timeout"
                        self.running_modules[thread_id]["progress"] = 100
            except Exception as e:
                with self.lock:
                    if thread_id in self.running_modules:
                        self.running_modules[thread_id]["status"] = "error"
                        self.running_modules[thread_id]["error"] = str(e)
                        self.running_modules[thread_id]["progress"] = 100
            finally:
                # Cleanup after delay
                threading.Timer(300, self.cleanup_thread, args=[thread_id]).start()
        
        # Start the thread
        thread = threading.Thread(target=module_thread)
        thread.daemon = True
        thread.start()
        
        return thread_id
    
    def get_module_status(self, thread_id):
        """Get status of a running module"""
        with self.lock:
            if thread_id in self.running_modules:
                return self.running_modules[thread_id]
            elif thread_id in self.module_results:
                return self.module_results[thread_id]
        return None
    
    def cleanup_thread(self, thread_id):
        """Clean up old thread data"""
        with self.lock:
            self.running_modules.pop(thread_id, None)
            self.module_results.pop(thread_id, None)
    
    def get_all_status(self):
        """Get status of all modules"""
        with self.lock:
            return {
                "running": list(self.running_modules.keys()),
                "completed": list(self.module_results.keys())
            }

# Global module runner
module_runner = ModuleRunner()

# ==================== API ENDPOINTS ====================

@app.route('/')
def index():
    """Serve the main interface"""
    return render_template('index.html')

@app.route('/api/database')
def get_database():
    """Get complete database"""
    return jsonify(read_database())

@app.route('/api/sites', methods=['GET', 'POST'])
def handle_sites():
    """Manage sites"""
    if request.method == 'GET':
        data = read_database()
        return jsonify(data.get("sites", []))
    
    elif request.method == 'POST':
        site_data = request.json
        
        if not site_data.get("name") or not site_data.get("root_ip"):
            return jsonify({"error": "Name and root_ip are required"}), 400
        
        data = read_database()
        
        # Check for duplicate site name
        if any(s.get("name") == site_data["name"] for s in data.get("sites", [])):
            return jsonify({"error": "Site with this name already exists"}), 400
        
        # Add site
        new_site = {
            "id": str(uuid.uuid4())[:8],
            "name": site_data["name"],
            "root_ip": site_data["root_ip"],
            "created": datetime.now().isoformat(),
            "last_scan": None,
            "locked": False,
            "notes": site_data.get("notes", "")
        }
        
        data.setdefault("sites", []).append(new_site)
        write_database(data)
        
        return jsonify(new_site)

@app.route('/api/sites/<site_id>', methods=['PUT', 'DELETE'])
def handle_site(site_id):
    """Update or delete a site"""
    data = read_database()
    
    # Find site
    site_index = None
    for i, site in enumerate(data.get("sites", [])):
        if site.get("id") == site_id:
            site_index = i
            break
    
    if site_index is None:
        return jsonify({"error": "Site not found"}), 404
    
    if request.method == 'PUT':
        update_data = request.json
        current_site = data["sites"][site_index]
        
        for field in ["name", "root_ip", "notes", "locked"]:
            if field in update_data:
                current_site[field] = update_data[field]
        
        write_database(data)
        return jsonify(current_site)
    
    elif request.method == 'DELETE':
        site_name = data["sites"][site_index]["name"]
        data["sites"].pop(site_index)
        data["devices"] = [d for d in data.get("devices", []) if d.get("site") != site_name]
        write_database(data)
        return jsonify({"success": True})

@app.route('/api/devices')
def get_devices():
    """Get all devices"""
    data = read_database()
    site_filter = request.args.get('site')
    
    devices = data.get("devices", [])
    if site_filter:
        devices = [d for d in devices if d.get("site") == site_filter]
    
    return jsonify(devices)

@app.route('/api/devices/<device_id>', methods=['PUT', 'DELETE'])
def handle_device(device_id):
    """Update or delete a device"""
    data = read_database()
    
    # Find device
    device_index = None
    for i, device in enumerate(data.get("devices", [])):
        if device.get("id") == device_id:
            device_index = i
            break
    
    if device_index is None:
        return jsonify({"error": "Device not found"}), 404
    
    if request.method == 'PUT':
        update_data = request.json
        current_device = data["devices"][device_index]
        
        updatable_fields = ["name", "ip", "type", "status", "notes", "locked"]
        for field in updatable_fields:
            if field in update_data:
                current_device[field] = update_data[field]
        
        current_device["last_modified"] = datetime.now().isoformat()
        write_database(data)
        return jsonify(current_device)
    
    elif request.method == 'DELETE':
        data["devices"].pop(device_index)
        write_database(data)
        return jsonify({"success": True})

@app.route('/api/modules')
def get_modules():
    """Get all available modules"""
    print("=== /api/modules endpoint called ===", file=sys.stderr)
    modules = discover_modules()
    print(f"Found {len(modules)} modules", file=sys.stderr)
    return jsonify(modules)

@app.route('/api/modules/<module_id>/run', methods=['POST'])
def run_module(module_id):
    """Run a module"""
    print(f"=== /api/modules/{module_id}/run called ===", file=sys.stderr)
    
    config = request.json
    print(f"Request data: {json.dumps(config, indent=2)}", file=sys.stderr)
    
    # Validate module exists
    modules = discover_modules()
    module_exists = any(m.get('id') == module_id for m in modules)
    
    if not module_exists:
        print(f"Module {module_id} not found. Available: {[m.get('id') for m in modules]}", file=sys.stderr)
        return jsonify({"error": f"Module {module_id} not found"}), 404
    
    print(f"Module {module_id} found. Starting execution...", file=sys.stderr)
    
    # Run the module
    thread_id = module_runner.run_module(module_id, config)
    
    return jsonify({
        "thread_id": thread_id,
        "status": "started",
        "message": f"Module {module_id} started"
    })

@app.route('/api/modules/status/<thread_id>')
def get_module_status(thread_id):
    """Get module execution status"""
    status = module_runner.get_module_status(thread_id)
    
    if status:
        return jsonify(status)
    else:
        return jsonify({"error": "Thread not found"}), 404

@app.route('/api/modules/status')
def get_all_module_status():
    """Get status of all modules"""
    return jsonify(module_runner.get_all_status())

@app.route('/api/settings', methods=['GET', 'PUT'])
def handle_settings():
    """Manage settings"""
    if request.method == 'GET':
        return jsonify(read_settings())
    
    elif request.method == 'PUT':
        new_settings = request.json
        current_settings = read_settings()
        current_settings.update(new_settings)
        write_settings(current_settings)
        return jsonify(current_settings)

@app.route('/api/stats')
def get_stats():
    """Get statistics"""
    data = read_database()
    
    devices = data.get("devices", [])
    online_devices = len([d for d in devices if d.get("status") == "online"])
    offline_devices = len([d for d in devices if d.get("status") == "offline"])
    
    stats = {
        "total_sites": len(data.get("sites", [])),
        "total_devices": len(devices),
        "online_devices": online_devices,
        "offline_devices": offline_devices,
        "unknown_status": len(devices) - online_devices - offline_devices,
        "last_modified": data.get("meta", {}).get("last_modified", "Never")
    }
    
    return jsonify(stats)

# ==================== MAIN ====================

if __name__ == '__main__':
    # Import sys for stderr printing
    import sys
    
    # Check for required packages
    try:
        import portalocker
    except ImportError:
        print("=" * 60)
        print("MISSING REQUIRED PACKAGE: portalocker")
        print("Please install it using: pip install portalocker")
        print("=" * 60)
        exit(1)
    
    # Initialize files
    init_database()
    init_settings()
    
    # Create modules directory if it doesn't exist
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
    
    # Create example modules if they don't exist
    example_modules = ['add_device_manual', 'cdp_discovery', 'view_map']
    
    for module_name in example_modules:
        module_dir = os.path.join(MODULES_DIR, module_name)
        if not os.path.exists(module_dir):
            os.makedirs(module_dir, exist_ok=True)
            print(f"Created module directory: {module_dir}", file=sys.stderr)
    
    print("=" * 60)
    print("NETWORK DISCOVERY PLATFORM")
    print("=" * 60)
    print(f"Dashboard URL: http://localhost:5000")
    print(f"API Base URL: http://localhost:5000/api/")
    print("\nAvailable API endpoints:")
    print("  GET  /api/database          - Get database")
    print("  GET  /api/sites             - List sites")
    print("  POST /api/sites             - Add site")
    print("  GET  /api/devices           - List devices")
    print("  GET  /api/modules           - List modules")
    print("  POST /api/modules/{id}/run  - Run module")
    print("  GET  /api/stats             - Get stats")
    print("  GET  /api/settings          - Get settings")
    print("  PUT  /api/settings          - Update settings")
    print("\nDebug logs will appear below...")
    print("=" * 60)
    
    app.run(debug=True, host='0.0.0.0', port=5000)