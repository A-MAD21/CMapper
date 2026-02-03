#!/usr/bin/env python3
"""
Network Visualization Module - Generates interactive topology maps
"""

import json
import sys
import os
import uuid
import shutil
from datetime import datetime
from pathlib import Path

def generate_network_map(database, site_name, output_dir="static/maps"):
    """
    Generate interactive HTML network map from database
    Returns: (success, map_path_or_error)
    """
    
    try:
        # Find devices for this site
        site_devices = [
            d for d in database.get("devices", []) 
            if d.get("site") == site_name
        ]
        
        if not site_devices:
            return False, f"No devices found for site '{site_name}'"
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Prepare data for visualization
        devices_data = []
        connections_data = []
        device_lookup = {}
        
        # Device type to color mapping
        type_colors = {
            'router': '#FF6B6B',      # Red
            'switch': '#4ECDC4',       # Teal
            'firewall': '#FFD166',     # Yellow
            'ap': '#06D6A0',           # Green
            'phone': '#EF476F',        # Pink
            'host': '#118AB2',         # Blue
            'unknown': '#073B4C'       # Dark blue
        }
        
        base_dir = os.path.dirname(output_dir) if os.path.isabs(output_dir) else os.path.dirname(
            os.path.join(os.getcwd(), output_dir)
        )
        icons_dir = os.path.join(base_dir, "icons", "map")
        icon_web_base = "/static/icons/map"
        blank_icon = "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="

        icon_map = {
            "router": "router.png",
            "switch": "switch.png",
            "firewall": "firewall.png",
            "ap": "ap.png",
            "phone": "phone.png",
            "host": "host.png",
            "server": "server.png",
            "nvr": "nvr.png",
            "pda": "pda.png",
            "unknown": "unknown.png"
        }

        # Process each device
        for device in site_devices:
            device_id = device.get("id", f"dev_{uuid.uuid4().hex[:8]}")
            device_type = device.get("type", "unknown").lower()
            color = type_colors.get(device_type, '#073B4C')

            icon_name = icon_map.get(device_type, icon_map["unknown"])
            icon_path = os.path.join(icons_dir, icon_name)
            if os.path.exists(icon_path):
                icon_url = f"{icon_web_base}/{icon_name}"
            else:
                icon_url = blank_icon
            
            device_node = {
                'id': device_id,
                'label': device.get("name", device.get("ip", "Unknown")),
                'title': f"""
                <strong>{device.get('name', device.get('ip', 'Unknown'))}</strong><br>
                IP: {device.get('ip', 'N/A')}<br>
                Type: {device.get('type', 'Unknown')}<br>
                Platform: {device.get('platform', 'N/A')}<br>
                Status: {device.get('status', 'unknown')}
                """,
                'color': {
                    'border': '#3B82F6',
                    'background': '#DBEAFE',
                    'highlight': {
                        'border': '#F59E0B',
                        'background': '#FEF3C7'
                    },
                    'hover': {
                        'border': '#60A5FA',
                        'background': '#E0F2FE'
                    }
                },
                'shape': 'circularImage',
                'image': icon_url,
                'brokenImage': blank_icon,
                'size': 36,
                'data': {
                    'ip': device.get("ip"),
                    'type': device.get("type", "unknown"),
                    'platform': device.get("platform", ""),
                    'status': device.get("status", "unknown"),
                    'reachable': device.get("reachable", False)
                }
            }
            
            devices_data.append(device_node)
            device_lookup[device["ip"]] = device_id
            
            # Process connections
            for conn in device.get("connections", []):
                if 'remote_device' in conn:
                    connection_edge = {
                        'id': conn.get("id", f"conn_{uuid.uuid4().hex[:8]}"),
                        'from': device_id,
                        'to': conn['remote_device'],
                        'label': f"{conn.get('local_interface', '')} ↔ {conn.get('remote_interface', '')}",
                        'title': f"Local: {conn.get('local_interface', 'N/A')}<br>Remote: {conn.get('remote_interface', 'N/A')}",
                        'color': '#A0A0A0',
                        'width': 2,
                        'arrows': 'to'
                    }
                    connections_data.append(connection_edge)
        
        # Generate HTML with vis.js
        html_content = self._generate_html_template(
            site_name=site_name,
            devices_count=len(devices_data),
            connections_count=len(connections_data),
            devices_json=json.dumps(devices_data),
            connections_json=json.dumps(connections_data)
        )
        
        # Save map file
        map_filename = f"{site_name.lower().replace(' ', '_')}_map.html"
        map_path = os.path.join(output_dir, map_filename)
        
        with open(map_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # Also create a simple JSON data file for API access
        data_path = os.path.join(output_dir, f"{site_name.lower().replace(' ', '_')}_data.json")
        map_data = {
            "site": site_name,
            "generated": datetime.now().isoformat(),
            "devices": devices_data,
            "connections": connections_data,
            "map_url": f"/static/maps/{map_filename}"
        }
        
        with open(data_path, 'w', encoding='utf-8') as f:
            json.dump(map_data, f, indent=2)
        
        return True, map_path
        
    except Exception as e:
        return False, f"Error generating map: {str(e)}"

def _generate_html_template(self, site_name, devices_count, connections_count, devices_json, connections_json):
    """Generate complete HTML template with vis.js"""
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Network Map - {site_name}</title>
    
    <!-- Vis.js Network Library -->
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <link rel="stylesheet" type="text/css" href="https://unpkg.com/vis-network/styles/vis-network.min.css" />
    
    <!-- Font Awesome for icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        
        .header {{
            background: linear-gradient(90deg, #4b6cb7 0%, #182848 100%);
            color: white;
            padding: 25px 30px;
        }}
        
        .header h1 {{
            font-size: 28px;
            margin-bottom: 5px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .header h1 i {{
            color: #FFD700;
        }}
        
        .stats {{
            display: flex;
            gap: 20px;
            margin-top: 15px;
            flex-wrap: wrap;
        }}
        
        .stat-box {{
            background: rgba(255,255,255,0.1);
            padding: 10px 20px;
            border-radius: 8px;
            backdrop-filter: blur(10px);
        }}
        
        .visualization-area {{
            padding: 20px;
            height: 700px;
            border-bottom: 1px solid #eee;
        }}
        
        #network-container {{
            width: 100%;
            height: 100%;
            border: 1px solid #e0e0e0;
            border-radius: 10px;
            background: #f9f9f9;
        }}
        
        .controls {{
            padding: 20px;
            display: flex;
            gap: 15px;
            background: #f8f9fa;
            border-bottom: 1px solid #e0e0e0;
        }}
        
        .btn {{
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: all 0.3s;
        }}
        
        .btn-primary {{
            background: #4b6cb7;
            color: white;
        }}
        
        .btn-primary:hover {{
            background: #3a559f;
            transform: translateY(-2px);
        }}
        
        .legend {{
            padding: 20px;
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            background: white;
        }}
        
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .legend-color {{
            width: 20px;
            height: 20px;
            border-radius: 4px;
        }}
        
        .device-panel {{
            padding: 20px;
            background: #f8f9fa;
            min-height: 200px;
            max-height: 300px;
            overflow-y: auto;
        }}
        
        .device-card {{
            background: white;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            border-left: 4px solid #4b6cb7;
        }}
        
        .device-card h4 {{
            color: #333;
            margin-bottom: 5px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .device-card p {{
            color: #666;
            font-size: 14px;
            margin: 3px 0;
        }}
        
        .status-indicator {{
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 5px;
        }}
        
        .status-up {{
            background: #28a745;
        }}
        
        .status-down {{
            background: #dc3545;
        }}
        
        .timestamp {{
            color: #888;
            font-size: 12px;
            margin-top: 10px;
            text-align: right;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><i class="fas fa-project-diagram"></i> Network Topology: {site_name}</h1>
            <p>Interactive visualization of discovered network devices</p>
            
            <div class="stats">
                <div class="stat-box">
                    <i class="fas fa-server"></i> Devices: <strong>{devices_count}</strong>
                </div>
                <div class="stat-box">
                    <i class="fas fa-link"></i> Connections: <strong>{connections_count}</strong>
                </div>
                <div class="stat-box">
                    <i class="fas fa-sync-alt"></i> Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
                </div>
            </div>
        </div>
        
        <div class="controls">
            <button class="btn btn-primary" onclick="network.fit()">
                <i class="fas fa-expand"></i> Fit to Screen
            </button>
            <button class="btn btn-primary" onclick="togglePhysics()">
                <i class="fas fa-magnet"></i> Toggle Physics
            </button>
            <button class="btn btn-primary" onclick="downloadMap()">
                <i class="fas fa-download"></i> Export as PNG
            </button>
            <button class="btn btn-primary" onclick="refreshMap()">
                <i class="fas fa-redo"></i> Refresh Data
            </button>
        </div>
        
        <div class="visualization-area">
            <div id="network-container"></div>
        </div>
        
        <div class="legend">
            <div class="legend-item"><div class="legend-color" style="background: #FF6B6B;"></div> Routers</div>
            <div class="legend-item"><div class="legend-color" style="background: #4ECDC4;"></div> Switches</div>
            <div class="legend-item"><div class="legend-color" style="background: #FFD166;"></div> Firewalls</div>
            <div class="legend-item"><div class="legend-color" style="background: #06D6A0;"></div> Access Points</div>
            <div class="legend-item"><div class="legend-color" style="background: #118AB2;"></div> Hosts/Phones</div>
            <div class="legend-item"><div class="legend-color" style="background: #073B4C;"></div> Unknown Devices</div>
        </div>
        
        <div class="device-panel">
            <h3><i class="fas fa-info-circle"></i> Device Details</h3>
            <div id="device-details">
                <p>Click on any device in the map to see detailed information here.</p>
            </div>
        </div>
        
        <div class="timestamp">
            Generated by CMapper Network Discovery System | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
    </div>

    <script>
        // Initialize network data
        const devices = new vis.DataSet({devices_json});
        const connections = new vis.DataSet({connections_json});
        
        // Network container
        const container = document.getElementById('network-container');
        const data = {{
            nodes: devices,
            edges: connections
        }};
        
        // Network options
        const options = {{
            nodes: {{
                shape: 'circularImage',
                size: 36,
                font: {{
                    size: 14,
                    face: 'Segoe UI',
                    color: '#333'
                }},
                borderWidth: 2,
                borderWidthSelected: 4,
                shadow: true
            }},
            edges: {{
                width: 2,
                color: {{
                    color: '#A0A0A0',
                    highlight: '#FF6B6B',
                    hover: '#4ECDC4'
                }},
                smooth: {{
                    type: 'dynamic',
                    roundness: 0.5
                }},
                arrows: {{
                    to: {{ enabled: true, scaleFactor: 0.8 }}
                }},
                selectionWidth: 3,
                hoverWidth: 2.5
            }},
            physics: {{
                enabled: true,
                solver: 'forceAtlas2Based',
                forceAtlas2Based: {{
                    gravitationalConstant: -100,
                    centralGravity: 0.01,
                    springLength: 200,
                    springConstant: 0.08,
                    damping: 0.4
                }},
                stabilization: {{
                    enabled: true,
                    iterations: 1000,
                    updateInterval: 100
                }}
            }},
            interaction: {{
                hover: true,
                tooltipDelay: 200,
                hideEdgesOnDrag: false,
                hideEdgesOnZoom: false,
                zoomView: true,
                dragView: true
            }}
        }};
        
        // Create network
        const network = new vis.Network(container, data, options);
        
        // Device click handler
        network.on('click', function(params) {{
            if (params.nodes.length > 0) {{
                const nodeId = params.nodes[0];
                const node = devices.get(nodeId);
                const nodeData = node.data || {{}};
                
                let html = `
                    <div class="device-card">
                        <h4>
                            <i class="fas fa-{{getDeviceIcon(nodeData.type)}}"></i>
                            ${{node.label}}
                            <span class="status-indicator ${{nodeData.reachable ? 'status-up' : 'status-down'}}"></span>
                        </h4>
                        <p><strong>IP Address:</strong> ${{nodeData.ip || 'N/A'}}</p>
                        <p><strong>Device Type:</strong> ${{nodeData.type || 'Unknown'}}</p>
                        <p><strong>Platform:</strong> ${{nodeData.platform || 'N/A'}}</p>
                        <p><strong>Status:</strong> ${{nodeData.status || 'unknown'}}</p>
                        <p><strong>Connections:</strong> ${{getConnectionCount(nodeId) || '0'}}</p>
                    </div>
                `;
                
                document.getElementById('device-details').innerHTML = html;
            }}
        }});
        
        // Helper functions
        function getDeviceIcon(deviceType) {{
            const icons = {{
                'router': 'server',
                'switch': 'sitemap',
                'firewall': 'shield-alt',
                'ap': 'wifi',
                'phone': 'phone',
                'host': 'desktop',
                'unknown': 'question-circle'
            }};
            return icons[deviceType?.toLowerCase()] || 'question-circle';
        }}
        
        function getConnectionCount(nodeId) {{
            return connections.get({{
                filter: function(edge) {{
                    return edge.from === nodeId || edge.to === nodeId;
                }}
            }}).length;
        }}
        
        // Control functions
        let physicsEnabled = true;
        
        function togglePhysics() {{
            physicsEnabled = !physicsEnabled;
            network.setOptions({{ physics: {{ enabled: physicsEnabled }} }});
            document.querySelector('[onclick="togglePhysics()"] i').className = 
                physicsEnabled ? 'fas fa-magnet' : 'fas fa-ban';
        }}
        
        function downloadMap() {{
            const canvas = document.querySelector('#network-container canvas');
            if (canvas) {{
                const link = document.createElement('a');
                link.download = 'network-map-{site_name}-{datetime.now().strftime('%Y%m%d')}.png';
                link.href = canvas.toDataURL('image/png');
                link.click();
            }} else {{
                alert('Canvas not found. Try again after map is fully loaded.');
            }}
        }}
        
        function refreshMap() {{
            if (confirm('Refresh map data from server?')) {{
                window.location.reload();
            }}
        }}
        
        // Fit network when stabilized
        network.on('stabilizationIterationsDone', function() {{
            network.fit();
        }});
        
        // Add some animation on load
        setTimeout(() => {{
            network.fit();
        }}, 500);
    </script>
</body>
</html>
"""

def main():
    """Main entry point for the module"""
    try:
        # Read config
        if len(sys.argv) < 2:
            error_msg = {"error": "No config file provided", "status": "failed"}
            print(json.dumps(error_msg))
            sys.exit(1)
        
        config_path = sys.argv[1]
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        site_name = config.get("site_name", "")
        db_path = config.get("database_path", "database.json")
        
        # Load database
        with open(db_path, 'r') as f:
            database = json.load(f)
        
        # Check if site exists
        site_exists = any(s.get("name") == site_name for s in database.get("sites", []))
        if not site_exists:
            error_msg = {"error": f"Site '{site_name}' does not exist", "status": "failed"}
            print(json.dumps(error_msg))
            sys.exit(1)
        
        # Generate map
        success, result = generate_network_map(
            database, 
            site_name,
            output_dir=os.path.join(os.path.dirname(db_path), "static", "maps")
        )
        
        if success:
            result_data = {
                "status": "success",
                "message": f"Network map generated for {site_name}",
                "data": {
                    "map_path": result,
                    "map_url": f"/static/maps/{os.path.basename(result)}",
                    "devices_count": len([d for d in database.get("devices", []) if d.get("site") == site_name])
                }
            }
        else:
            result_data = {
                "status": "error",
                "message": result,
                "data": {}
            }
        
        print(json.dumps(result_data, indent=2))
        
    except Exception as e:
        error_msg = {"error": f"Module failed: {str(e)}", "status": "failed"}
        print(json.dumps(error_msg))
        sys.exit(1)

if __name__ == "__main__":
    main()
