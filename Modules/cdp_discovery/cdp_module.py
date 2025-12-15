#!/usr/bin/env python3
"""
CDP Discovery Module - COMPLETE WORKING VERSION
Uses Netmiko (best) or paramiko for SSH to Cisco devices
"""

import json
import sys
import os
import re
import time
import ipaddress
from datetime import datetime
import uuid

print("=== CDP DISCOVERY MODULE STARTING ===", file=sys.stderr)

# ==================== SSH CONNECTION ====================

def connect_to_device(host, username, password):
    """
    Try multiple methods to connect to Cisco device
    Returns: (success, connection_object_or_error)
    """
    
    # Method 1: Try Netmiko first (best for network devices)
    try:
        from netmiko import ConnectHandler
        
        print(f"DEBUG: Trying Netmiko connection to {host}", file=sys.stderr)
        
        device = {
            'device_type': 'cisco_ios',
            'host': host,
            'username': username,
            'password': password,
            'port': 22,
            'secret': '',  # Enable password if needed
            'timeout': 15,
            'global_delay_factor': 2,
        }
        
        connection = ConnectHandler(**device)
        print(f"DEBUG: Netmiko connection successful to {host}", file=sys.stderr)
        return True, connection
        
    except ImportError:
        print("DEBUG: Netmiko not installed, trying paramiko", file=sys.stderr)
    except Exception as e:
        print(f"DEBUG: Netmiko failed: {str(e)[:200]}", file=sys.stderr)
    
    # Method 2: Try paramiko with legacy algorithms
    try:
        import paramiko
        
        print(f"DEBUG: Trying paramiko connection to {host}", file=sys.stderr)
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # For old Cisco devices with diffie-hellman-group1-sha1
        try:
            # Try with algorithm forcing
            transport = paramiko.Transport((host, 22))
            
            # Set legacy algorithms for old Cisco
            transport.get_security_options().kex = [
                'diffie-hellman-group1-sha1',
                'diffie-hellman-group14-sha1',
                'diffie-hellman-group-exchange-sha1',
            ]
            
            transport.get_security_options().ciphers = [
                'aes128-cbc', '3des-cbc', 'aes192-cbc', 'aes256-cbc',
            ]
            
            transport.connect(username=username, password=password)
            client._transport = transport
            
        except:
            # Fall back to normal connection
            client.connect(
                hostname=host,
                username=username,
                password=password,
                timeout=15,
                look_for_keys=False,
                allow_agent=False
            )
        
        print(f"DEBUG: Paramiko connection successful to {host}", file=sys.stderr)
        return True, client
        
    except Exception as e:
        error_msg = f"SSH failed: {type(e).__name__}: {str(e)[:200]}"
        print(f"DEBUG: {error_msg}", file=sys.stderr)
        return False, error_msg

def get_cdp_from_device(connection, is_netmiko=True):
    """Get CDP output from connected device"""
    try:
        print(f"DEBUG: Getting CDP neighbors", file=sys.stderr)
        
        if is_netmiko:
            # Netmiko connection
            output = connection.send_command(
                "show cdp neighbors detail",
                delay_factor=2,
                expect_string=r'[#>]'
            )
        else:
            # Paramiko connection
            channel = connection.invoke_shell()
            time.sleep(1)
            
            # Disable paging
            channel.send("terminal length 0\n")
            time.sleep(0.5)
            
            # Get CDP
            channel.send("show cdp neighbors detail\n")
            time.sleep(2)
            
            # Read output
            output = ""
            start_time = time.time()
            
            while time.time() - start_time < 10:
                if channel.recv_ready():
                    chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                    output += chunk
                    
                    # Handle paging
                    if "--More--" in chunk or "(q)uit" in chunk:
                        channel.send(" ")
                        time.sleep(0.2)
                
                time.sleep(0.1)
                
                # Check for completion
                if output.count('\n') > 10 and any(prompt in output for prompt in ['#', '>']):
                    break
            
            channel.close()
        
        print(f"DEBUG: Got {len(output)} bytes of CDP output", file=sys.stderr)
        return True, output
        
    except Exception as e:
        error_msg = f"Failed to get CDP: {str(e)[:200]}"
        print(f"DEBUG: {error_msg}", file=sys.stderr)
        return False, error_msg

def disconnect_device(connection, is_netmiko=True):
    """Close connection"""
    try:
        if is_netmiko:
            connection.disconnect()
        else:
            connection.close()
        print(f"DEBUG: Connection closed", file=sys.stderr)
    except:
        pass

# ==================== CDP PARSING ====================

def parse_cdp_output(cdp_output, source_ip):
    """Parse CDP output and extract neighbor information"""
    if not cdp_output:
        return []
    
    neighbors = []
    
    # Split by device entries (look for "Device ID:" lines)
    lines = cdp_output.split('\n')
    current_neighbor = {}
    capturing = False
    
    for line in lines:
        line = line.strip()
        
        # Start of a new neighbor entry
        if line.startswith('Device ID:'):
            if current_neighbor:  # Save previous neighbor
                neighbors.append(current_neighbor)
            
            current_neighbor = {
                'source_ip': source_ip,
                'device_id': line.replace('Device ID:', '').strip(),
                'ip_address': None,
                'platform': None,
                'capabilities': None,
                'local_interface': None,
                'remote_interface': None,
                'type': 'unknown'
            }
            capturing = True
        
        # IP address
        elif capturing and ('IP address:' in line or 'IPv4 Address:' in line):
            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
            if ip_match:
                current_neighbor['ip_address'] = ip_match.group(1)
        
        # Platform
        elif capturing and line.startswith('Platform:'):
            platform = line.replace('Platform:', '').split(',')[0].strip()
            current_neighbor['platform'] = platform
        
        # Capabilities
        elif capturing and line.startswith('Capabilities:'):
            caps = line.replace('Capabilities:', '').strip()
            current_neighbor['capabilities'] = caps
            current_neighbor['type'] = determine_device_type(caps, current_neighbor.get('platform', ''))
        
        # Interface
        elif capturing and line.startswith('Interface:'):
            interface = line.replace('Interface:', '').split(',')[0].strip()
            current_neighbor['local_interface'] = interface
        
        # Port ID (remote interface)
        elif capturing and ('Port ID' in line or 'Port id' in line):
            # Remove "Port ID (outgoing port):" or similar
            port_line = re.sub(r'Port ID\s*\([^)]*\):', '', line)
            port_line = port_line.replace('Port ID:', '').replace('Port id:', '').strip()
            current_neighbor['remote_interface'] = port_line
        
        # End of entry (empty line or separator)
        elif capturing and (not line or line.startswith('---')):
            if current_neighbor:
                neighbors.append(current_neighbor)
                current_neighbor = {}
                capturing = False
    
    # Add the last neighbor if exists
    if current_neighbor:
        neighbors.append(current_neighbor)
    
    print(f"DEBUG: Parsed {len(neighbors)} neighbors from {source_ip}", file=sys.stderr)
    return neighbors

def determine_device_type(capabilities, platform=""):
    """Determine device type from capabilities and platform"""
    # Handle None or non-string capabilities
    if not capabilities or not isinstance(capabilities, str):
        return "unknown"
    
    caps_lower = capabilities.lower()
    platform_lower = platform.lower() if isinstance(platform, str) else ""
    
    # Check capabilities
    if 'router' in caps_lower:
        return "router"
    elif 'switch' in caps_lower:
        return "switch"
    elif 'bridge' in caps_lower:
        return "switch"  # Bridge = switch
    elif 'transparent bridge' in caps_lower:
        return "transparent"
    elif 'access point' in caps_lower or 'ap' in platform_lower:
        return "ap"
    elif 'phone' in caps_lower or 'ipphone' in platform_lower:
        return "phone"
    elif 'host' in caps_lower or 'pc' in caps_lower:
        return "host"
    elif 'firewall' in caps_lower:
        return "firewall"
    
    # Check platform keywords
    if any(x in platform_lower for x in ['ws-c', 'catalyst', 'nexus', '2960', '3560', '3750', '3850']):
        return "switch"
    elif any(x in platform_lower for x in ['isr', 'asr', 'csr', '1900', '2900', '3900', '4000']):
        return "router"
    elif any(x in platform_lower for x in ['asa', 'firepower', 'pix']):
        return "firewall"
    elif any(x in platform_lower for x in ['air-cap', 'air-ap', 'wlc']):
        return "ap"
    
    return "unknown"

def ip_in_subnet(ip, network_ip, mask):
    """Check if IP is in subnet"""
    try:
        ip_obj = ipaddress.IPv4Address(ip)
        network = ipaddress.IPv4Network(f"{network_ip}/{mask}", strict=False)
        return ip_obj in network
    except:
        return False

# ==================== DATABASE OPERATIONS ====================

def update_or_add_device(database, site_name, device_info, credentials_used):
    """Add or update device in database"""
    devices = database.setdefault("devices", [])
    
    # Check if device already exists (by IP in same site)
    for i, device in enumerate(devices):
        if device.get("ip") == device_info["ip"] and device.get("site") == site_name:
            # Update existing device
            for key, value in device_info.items():
                if key not in ["id", "site", "discovered_at", "discovered_by"]:
                    devices[i][key] = value
            
            # Update discovery info
            devices[i]["last_seen"] = datetime.now().isoformat()
            if "cdp_discovery" not in devices[i].get("modules_successful", []):
                devices[i].setdefault("modules_successful", []).append("cdp_discovery")
            
            return devices[i]["id"]
    
    # Add new device
    new_device = {
        "id": f"dev_{str(uuid.uuid4())[:8]}",
        "site": site_name,
        "name": device_info.get("name", f"Device-{device_info['ip']}"),
        "ip": device_info["ip"],
        "type": device_info.get("type", "unknown"),
        "model": device_info.get("model", ""),
        "platform": device_info.get("platform", ""),
        "capabilities": device_info.get("capabilities", ""),
        "discovered_by": "cdp_discovery",
        "discovered_at": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
        "status": "online",
        "reachable": True,
        "config_backup": {"enabled": False},
        "connections": [],
        "credentials_used": credentials_used,
        "modules_successful": ["cdp_discovery"],
        "modules_failed": [],
        "locked": False,
        "notes": device_info.get("notes", f"Discovered via CDP")
    }
    
    devices.append(new_device)
    return new_device["id"]

def add_connection(database, source_device_id, target_device_id, connection_info):
    """Add connection between two devices"""
    devices = database.get("devices", [])
    
    # Find source device
    for device in devices:
        if device.get("id") == source_device_id:
            connection = {
                "id": f"conn_{str(uuid.uuid4())[:8]}",
                "local_interface": connection_info.get("local_interface", "unknown"),
                "remote_device": target_device_id,
                "remote_interface": connection_info.get("remote_interface", "unknown"),
                "protocol": "cdp",
                "discovered_at": datetime.now().isoformat(),
                "status": "up"
            }
            
            device.setdefault("connections", []).append(connection)
            return True
    
    return False

# ==================== MAIN FUNCTION ====================

def main():
    """Main function - called by the platform"""
    
    # 1. Read config from platform
    if len(sys.argv) < 2:
        error_msg = {"error": "No config file provided", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)
    
    config_path = sys.argv[1]
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except Exception as e:
        error_msg = {"error": f"Failed to read config: {str(e)}", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)
    
    # 2. Extract parameters
    site_name = config.get("site_name", "")
    params = config.get("parameters", {})
    
    root_ip = params.get("root_ip", "").strip()
    username = params.get("username", "").strip()
    password = params.get("password", "").strip()
    subnet_mask = params.get("subnet_mask", "24")
    max_hops = int(params.get("max_hops", 3))
    
    db_path = config.get("database_path", "database.json")
    
    # 3. Validate inputs
    if not site_name:
        error_msg = {"error": "Site name is required", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)
    
    if not root_ip:
        error_msg = {"error": "Root IP is required", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)
    
    if not username or not password:
        error_msg = {"error": "Username and password are required", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)
    
    print(f"DEBUG: Starting CDP discovery from {root_ip}/{subnet_mask}", file=sys.stderr)
    
    # 4. Read current database
    try:
        with open(db_path, 'r') as f:
            database = json.load(f)
        print(f"DEBUG: Database loaded, has {len(database.get('devices', []))} devices", file=sys.stderr)
    except Exception as e:
        error_msg = {"error": f"Failed to read database: {str(e)}", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)
    
    # 5. Check if site exists
    site_exists = any(s.get("name") == site_name for s in database.get("sites", []))
    if not site_exists:
        error_msg = {"error": f"Site '{site_name}' does not exist", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)
    
    # 6. Start discovery
    discovered_ips = set()  # IPs we've already processed
    scanned_ips = set()     # IPs we've already scanned for CDP
    device_map = {}         # ip -> device_id
    to_scan = []            # Queue of IPs to scan
    
    # Initialize with root device
    print(f"DEBUG: Starting with root device {root_ip}", file=sys.stderr)
    to_scan.append((root_ip, 0))  # (ip, hop_count)
    
    total_devices = 0
    total_connections = 0
    errors = []
    
    # Scan queue - process devices to get their CDP neighbors
    while to_scan:
        current_ip, current_hop = to_scan.pop(0)
        
        if current_ip in scanned_ips:
            print(f"DEBUG: Already scanned {current_ip}, skipping", file=sys.stderr)
            continue
            
        if current_hop > max_hops:
            print(f"DEBUG: Max hops reached for {current_ip} (hop {current_hop})", file=sys.stderr)
            continue
        
        print(f"DEBUG: Processing {current_ip} (hop {current_hop})...", file=sys.stderr)
        
        # Add current device to database if not already there (but don't count as discovered yet)
        if current_ip not in device_map:
            # Create a temporary device entry
            device_id = f"dev_{str(uuid.uuid4())[:8]}"
            device_map[current_ip] = device_id
        
        scanned_ips.add(current_ip)
        
        # Try to connect and get CDP
        success, connection = connect_to_device(current_ip, username, password)
        
        if not success:
            print(f"DEBUG: Could not connect to {current_ip}: {connection}", file=sys.stderr)
            errors.append(f"Failed to connect to {current_ip}: {connection}")
            
            # Even if we can't connect, we should record this device
            if current_ip not in discovered_ips:
                device_info = {
                    "ip": current_ip,
                    "name": f"Device-{current_ip}",
                    "type": "unknown",
                    "credentials_used": username,
                    "status": "unreachable",
                    "reachable": False
                }
                
                device_id = update_or_add_device(database, site_name, device_info, username)
                device_map[current_ip] = device_id
                discovered_ips.add(current_ip)
                total_devices += 1
            
            continue
        
        # Check if Netmiko or paramiko
        is_netmiko = hasattr(connection, 'send_command')
        
        # Get CDP output
        cdp_success, cdp_output = get_cdp_from_device(connection, is_netmiko)
        
        # Disconnect
        disconnect_device(connection, is_netmiko)
        
        if not cdp_success:
            print(f"DEBUG: Could not get CDP from {current_ip}: {cdp_output}", file=sys.stderr)
            errors.append(f"Failed to get CDP from {current_ip}: {cdp_output}")
            
            # Still record the device
            if current_ip not in discovered_ips:
                device_info = {
                    "ip": current_ip,
                    "name": f"Device-{current_ip}",
                    "type": "unknown",
                    "credentials_used": username,
                    "status": "online",
                    "reachable": True
                }
                
                device_id = update_or_add_device(database, site_name, device_info, username)
                device_map[current_ip] = device_id
                discovered_ips.add(current_ip)
                total_devices += 1
            
            continue
        
        # Now we have successfully connected and got CDP from this device
        # Update device info with what we learned
        if current_ip not in discovered_ips:
            # Try to determine device type from CDP output
            device_type = "unknown"
            platform = ""
            capabilities = ""
            
            # Parse CDP to get our own platform info
            if cdp_output:
                lines = cdp_output.split('\n')
                for line in lines:
                    line = line.strip()
                    if line.startswith('Device ID:'):
                        # This might be our device info
                        pass
                    elif line.startswith('Platform:'):
                        platform = line.replace('Platform:', '').split(',')[0].strip()
                    elif line.startswith('Capabilities:'):
                        capabilities = line.replace('Capabilities:', '').strip()
                        device_type = determine_device_type(capabilities, platform)
            
            device_info = {
                "ip": current_ip,
                "name": f"Device-{current_ip}",
                "type": device_type,
                "platform": platform,
                "capabilities": capabilities,
                "credentials_used": username,
                "status": "online",
                "reachable": True
            }
            
            device_id = update_or_add_device(database, site_name, device_info, username)
            device_map[current_ip] = device_id
            discovered_ips.add(current_ip)
            total_devices += 1
        
        current_device_id = device_map.get(current_ip)
        
        # Parse CDP neighbors
        neighbors = parse_cdp_output(cdp_output, current_ip)
        print(f"DEBUG: Found {len(neighbors)} neighbors from {current_ip}", file=sys.stderr)
        
        # Process neighbors
        for neighbor in neighbors:
            neighbor_ip = neighbor.get('ip_address')
            if not neighbor_ip:
                print(f"DEBUG: Neighbor has no IP address: {neighbor.get('device_id')}", file=sys.stderr)
                continue
            
            # Check if IP is in our target subnet
            try:
                if not ip_in_subnet(neighbor_ip, root_ip, subnet_mask):
                    print(f"DEBUG: Skipping {neighbor_ip} - outside subnet {root_ip}/{subnet_mask}", file=sys.stderr)
                    continue
            except Exception as e:
                print(f"DEBUG: Error checking subnet for {neighbor_ip}: {e}", file=sys.stderr)
                continue
            
            # Add neighbor to scan queue if not already scanned
            if neighbor_ip not in scanned_ips and (current_hop + 1) <= max_hops:
                if neighbor_ip not in [ip for ip, _ in to_scan]:
                    print(f"DEBUG: Adding {neighbor_ip} to scan queue (hop {current_hop + 1})", file=sys.stderr)
                    to_scan.append((neighbor_ip, current_hop + 1))
            
            # Add/update neighbor device in database
            if neighbor_ip not in device_map:
                neighbor_info = {
                    "ip": neighbor_ip,
                    "name": neighbor.get('device_id', f"Device-{neighbor_ip}"),
                    "type": neighbor.get('type', 'unknown'),
                    "platform": neighbor.get('platform', ''),
                    "capabilities": neighbor.get('capabilities', ''),
                    "credentials_used": username,
                    "notes": f"Discovered via CDP from {current_ip}"
                }
                
                neighbor_id = update_or_add_device(database, site_name, neighbor_info, username)
                device_map[neighbor_ip] = neighbor_id
                
                if neighbor_ip not in discovered_ips:
                    discovered_ips.add(neighbor_ip)
                    total_devices += 1
            
            neighbor_id = device_map.get(neighbor_ip)
            
            # Add connection from current device to neighbor
            if current_device_id and neighbor_id:
                connection_info = {
                    "local_interface": neighbor.get('local_interface', 'unknown'),
                    "remote_interface": neighbor.get('remote_interface', 'unknown')
                }
                
                # Check if connection already exists
                existing_conn = False
                for device in database.get("devices", []):
                    if device.get("id") == current_device_id:
                        for conn in device.get("connections", []):
                            if conn.get("remote_device") == neighbor_id:
                                existing_conn = True
                                break
                        break
                
                if not existing_conn:
                    if add_connection(database, current_device_id, neighbor_id, connection_info):
                        print(f"DEBUG: Added connection: {current_ip} -> {neighbor_ip}", file=sys.stderr)
                        total_connections += 1
        
        # Small delay to not overload devices
        time.sleep(1)
        
        # Debug: Show queue status
        print(f"DEBUG: Queue has {len(to_scan)} devices left to scan", file=sys.stderr)
        if to_scan:
            print(f"DEBUG: Next to scan: {to_scan[0][0]}", file=sys.stderr)
    
    print(f"DEBUG: Discovery complete. Found {total_devices} devices, {total_connections} connections", file=sys.stderr)
    
    # 7. Update site last_scan time
    for site in database["sites"]:
        if site["name"] == site_name:
            site["last_scan"] = datetime.now().isoformat()
            break
    
    # 8. Write updated database
    try:
        with open(db_path, 'w') as f:
            json.dump(database, f, indent=2)
        print(f"DEBUG: Database updated successfully", file=sys.stderr)
        
    except Exception as e:
        error_msg = {"error": f"Failed to write database: {str(e)}", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)
    
    # 9. Return results
    result = {
        "status": "success",
        "message": f"CDP discovery completed for site '{site_name}'",
        "data": {
            "devices_found": total_devices,
            "connections_found": total_connections,
            "total_processed": len(discovered_ips),
            "errors": errors[:5]  # Return first 5 errors if any
        }
    }
    
    print(json.dumps(result, indent=2))
    print("=== CDP DISCOVERY MODULE COMPLETED ===", file=sys.stderr)

if __name__ == "__main__":
    main()