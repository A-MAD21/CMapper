#!/usr/bin/env python3
"""
Text Map Generator
Creates hierarchical text-based network topology maps
"""

import json
import os
from datetime import datetime

def generate_map_from_database(site_name=None):
    """Generate a network map from database.json for a specific site"""

    try:
        # Read database
        with open('database.json', 'r') as f:
            data = json.load(f)

        # Get all sites if no site specified
        sites = data.get('sites', [])
        if not sites:
            return {"status": "error", "message": "No sites in database"}

        # If no site specified, use the first site
        if not site_name:
            site_name = sites[0]['name']

        # Find the selected site
        selected_site = None
        for site in sites:
            if site['name'] == site_name:
                selected_site = site
                break

        if not selected_site:
            return {"status": "error", "message": f"Site '{site_name}' not found"}

        # Filter devices by site
        all_devices = data.get('devices', [])
        devices = [d for d in all_devices if d.get('site') == site_name]

        if not devices:
            return {"status": "error", "message": f"No devices found for site '{site_name}'"}

        # Build device mapping
        device_map = {}
        for device in devices:
            device_map[device['id']] = device

        # Build connection graph (only within the site)
        connections = []
        for device in devices:
            for conn in device.get('connections', []):
                if conn['remote_device'] in device_map:  # Only connections within the site
                    connections.append({
                        'source': device['id'],
                        'target': conn['remote_device'],
                        'local_port': conn['local_interface'],
                        'protocol': conn['protocol']
                    })

        # Count unique connections (remove duplicates)
        unique_connections = set()
        for conn in connections:
            key = tuple(sorted([conn['source'], conn['target']]))
            unique_connections.add(key)

        # Build connection tree function
        def build_connection_tree(devices, connections):
            """Build a hierarchical tree structure from connections"""
            # Create adjacency list
            adj_list = {}
            for device in devices:
                adj_list[device['id']] = []

            for conn in connections:
                adj_list[conn['source']].append({
                    'target': conn['target'],
                    'port': conn['local_port'],
                    'protocol': conn['protocol']
                })

            # Find root device (device with most connections or site root)
            root_device = None
            max_connections = 0

            # First try to find device matching site root IP
            for device in devices:
                if device.get('ip') == selected_site.get('root_ip'):
                    root_device = device
                    break

            # If not found, use device with most connections
            if not root_device:
                for device in devices:
                    conn_count = len(adj_list.get(device['id'], []))
                    if conn_count > max_connections:
                        max_connections = conn_count
                        root_device = device

            if not root_device:
                root_device = devices[0] if devices else None

            if not root_device:
                return ""

            # Build tree structure
            def build_tree_html(device_id, visited, depth=0, is_last=False):
                if device_id in visited:
                    return ""
                visited.add(device_id)

                device = device_map.get(device_id, {})
                if not device:
                    return ""

                # Determine device type from platform/model info
                device_type = device.get('type', 'unknown')
                platform = device.get('platform', '').lower()
                model = device.get('model', '').lower()

                if 'ws-c' in platform or 'catalyst' in platform or 'cisco' in platform and ('2960' in platform or '3650' in platform or '3850' in platform or 'switch' in platform):
                    device_type = 'switch'
                elif 'cisco' in platform and ('2911' in platform or '2921' in platform or 'ISR' in platform or 'ASR' in platform or 'router' in platform):
                    device_type = 'router'
                elif 'mikrotik' in platform:
                    device_type = 'router'  # MikroTik devices are typically routers
                elif 'access' in platform or 'ap' in platform or 'aironet' in platform:
                    device_type = 'access-point'
                elif 'phone' in platform or 'ip phone' in platform:
                    device_type = 'ip-phone'
                elif 'ws-c' in platform or 'catalyst' in platform:
                    device_type = 'switch'
                elif 'cisco' in platform:
                    device_type = 'switch'  # Default Cisco devices to switch

                indent = "  " * depth
                prefix = ""
                if depth > 0:
                    prefix = "└─ " if is_last else "├─ "

                status = device.get('status', 'unknown')
                status_icon = "🟢" if status == 'online' else "🔴" if status == 'offline' else "🟡"

                html = f"{indent}{prefix}{status_icon} {device.get('name', 'Unknown')} ({device_type})\n"
                html += f"{indent}│  IP: {device.get('ip', 'N/A')}\n"
                html += f"{indent}│  Platform: {device.get('platform', 'Unknown')}\n"

                # Show connections
                neighbors = adj_list.get(device_id, [])
                if neighbors:
                    html += f"{indent}│  Connections: {len(neighbors)}\n"
                    for i, neighbor in enumerate(neighbors):
                        neighbor_device = device_map.get(neighbor['target'], {})
                        is_last_neighbor = (i == len(neighbors) - 1)
                        branch_prefix = "└─ " if is_last_neighbor else "├─ "
                        html += f"{indent}│  {branch_prefix}{neighbor_device.get('name', 'Unknown')} ({neighbor['port']})\n"

                        # Recursively build subtree with proper indentation
                        subtree = build_tree_html(neighbor['target'], visited, depth + 1, is_last_neighbor)
                        if subtree:
                            # Add connecting line for subtree
                            connector = "   " if is_last_neighbor else "│  "
                            subtree_lines = subtree.split('\n')
                            modified_subtree = []
                            for line in subtree_lines:
                                if line.strip():  # Only modify non-empty lines
                                    modified_subtree.append(f"{indent}│  {connector}{line}")
                                else:
                                    modified_subtree.append("")
                            html += '\n'.join(modified_subtree) + '\n'
                else:
                    html += f"{indent}│  (No connections)\n"

                return html

            visited = set()
            tree_html = build_tree_html(root_device['id'], visited)

            return tree_html

        # Generate hierarchical tree
        tree_html = build_connection_tree(devices, connections)

        # Create HTML with tree structure
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Network Topology Tree - {site_name}</title>
    <style>
        body {{
            font-family: 'Courier New', monospace;
            margin: 20px;
            background-color: #f5f5f5;
            line-height: 1.4;
        }}
        .header {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .tree-container {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow-x: auto;
        }}
        .tree {{
            white-space: pre;
            font-size: 14px;
            color: #333;
        }}
        .stats {{
            display: flex;
            gap: 20px;
            margin-top: 20px;
        }}
        .stat-card {{
            background: #e3f2fd;
            padding: 15px;
            border-radius: 6px;
            text-align: center;
            flex: 1;
        }}
        .stat-value {{
            font-size: 24px;
            font-weight: bold;
            color: #1976d2;
        }}
        .stat-label {{
            font-size: 12px;
            color: #666;
            margin-top: 5px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Network Topology Tree - {site_name}</h1>
        <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{len(devices)}</div>
                <div class="stat-label">Total Devices</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(unique_connections)}</div>
                <div class="stat-label">Connections</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len([d for d in devices if d.get('status') == 'online'])}</div>
                <div class="stat-label">Online</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len([d for d in devices if 'cisco' in d.get('platform', '').lower()])}</div>
                <div class="stat-label">Cisco Devices</div>
            </div>
        </div>
    </div>

    <div class="tree-container">
        <h2>Network Hierarchy</h2>
        <div class="tree">{tree_html}</div>
    </div>

    <div class="tree-container" style="margin-top: 20px;">
        <h2>Device Details</h2>
        <table style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr style="background: #f5f5f5;">
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Device Name</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">IP Address</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Type</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Platform</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Status</th>
                </tr>
            </thead>
            <tbody>"""

        for device in sorted(devices, key=lambda x: x.get('name', '')):
            # Determine device type
            device_type = device.get('type', 'unknown')
            platform = device.get('platform', '').lower()
            model = device.get('model', '').lower()

            if 'ws-c' in platform or 'catalyst' in platform or 'cisco' in platform and ('2960' in platform or '3650' in platform or '3850' in platform or 'switch' in platform):
                device_type = 'switch'
            elif 'cisco' in platform and ('2911' in platform or '2921' in platform or 'ISR' in platform or 'ASR' in platform or 'router' in platform):
                device_type = 'router'
            elif 'mikrotik' in platform:
                device_type = 'router'  # MikroTik devices are typically routers
            elif 'access' in platform or 'ap' in platform or 'aironet' in platform:
                device_type = 'access-point'
            elif 'phone' in platform or 'ip phone' in platform:
                device_type = 'ip-phone'
            elif 'ws-c' in platform or 'catalyst' in platform:
                device_type = 'switch'
            elif 'cisco' in platform:
                device_type = 'switch'  # Default Cisco devices to switch

            status = device.get('status', 'unknown')
            status_color = '#4CAF50' if status == 'online' else '#f44336' if status == 'offline' else '#ff9800'

            html += f"""
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">{device.get('name', 'Unknown')}</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{device.get('ip', 'N/A')}</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{device_type.title()}</td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{device.get('platform', 'Unknown')}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; color: {status_color};">{status.title()}</td>
                </tr>"""

        html += """
            </tbody>
        </table>
    </div>
</body>
</html>"""

        # Write to file
        output_file = f'{site_name.replace(" ", "_")}_map.html'
        os.makedirs('generated_maps', exist_ok=True)
        output_path = f'generated_maps/{output_file}'
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"Generated {output_file}")
        return {
            "status": "success",
            "message": f"Map generated for site '{site_name}' with {len(devices)} devices",
            "map_file": output_file,
            "map_url": f"/generated_maps/{output_file}",
            "site_name": site_name,
            "device_count": len(devices),
            "connection_count": len(connections)
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == '__main__':
    import sys
    site_name = sys.argv[1] if len(sys.argv) > 1 else None
    result = generate_map_from_database(site_name)
    print(json.dumps(result, indent=2))