import json
import os
import sys
from datetime import datetime

def generate_map_from_database():
    """Generate a network map from database.json"""
    
    try:
        # Read database
        with open('database.json', 'r') as f:
            data = json.load(f)
        
        # Extract devices and connections
        devices = data.get('devices', [])
        
        if not devices:
            return {"status": "error", "message": "No devices in database"}
        
        # Build device mapping
        device_map = {}
        for device in devices:
            device_map[device['id']] = device
        
        # Build connection graph
        connections = []
        for device in devices:
            for conn in device.get('connections', []):
                if conn['remote_device'] in device_map:
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
        
        # Generate HTML
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Network Topology Map - Roodan</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: system-ui, 'Segoe UI', Inter, sans-serif;
            background: #0f172a;
            color: #e5e7eb;
            padding: 20px;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        .header {{
            background: #020617;
            border: 1px solid #1e293b;
            border-radius: 20px;
            padding: 25px;
            margin-bottom: 25px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.3);
        }}
        
        .header h1 {{
            color: #e5e7eb;
            font-size: 28px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        
        .header h1 i {{ color: #D71920; }}
        
        .stats {{
            display: flex;
            gap: 20px;
            margin-top: 20px;
            flex-wrap: wrap;
        }}
        
        .stat-card {{
            background: rgba(255,255,255,0.05);
            border: 1px solid #1e293b;
            border-radius: 12px;
            padding: 15px;
            min-width: 150px;
        }}
        
        .stat-value {{
            font-size: 32px;
            font-weight: bold;
            color: #D71920;
            margin-bottom: 5px;
        }}
        
        .stat-label {{
            font-size: 13px;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .devices-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .device-card {{
            background: #020617;
            border: 1px solid #1e293b;
            border-radius: 16px;
            padding: 20px;
            transition: all 0.3s ease;
        }}
        
        .device-card:hover {{
            border-color: #D71920;
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(215, 25, 32, 0.2);
        }}
        
        .device-header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 15px;
        }}
        
        .device-icon {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: #D71920;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
        }}
        
        .device-name {{
            font-weight: bold;
            font-size: 16px;
            color: #e5e7eb;
        }}
        
        .device-ip {{
            font-family: monospace;
            color: #94a3b8;
            font-size: 13px;
            margin-top: 2px;
        }}
        
        .device-status {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            margin-top: 10px;
        }}
        
        .status-online {{ background: rgba(16, 185, 129, 0.2); color: #10B981; }}
        .status-unreachable {{ background: rgba(239, 68, 68, 0.2); color: #EF4444; }}
        
        .connections {{
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid #1e293b;
        }}
        
        .connection-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        
        .connection-port {{
            font-family: monospace;
            color: #D71920;
            font-weight: bold;
            font-size: 12px;
        }}
        
        .connection-target {{
            font-size: 12px;
            color: #94a3b8;
        }}
        
        .topology-summary {{
            background: #020617;
            border: 1px solid #1e293b;
            border-radius: 16px;
            padding: 25px;
            margin-top: 30px;
        }}
        
        .summary-title {{
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 20px;
            color: #e5e7eb;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .connection-line {{
            padding: 12px;
            background: rgba(255,255,255,0.03);
            border-radius: 8px;
            margin-bottom: 8px;
            border-left: 3px solid #D71920;
            font-family: monospace;
            font-size: 13px;
        }}
        
        .timestamp {{
            text-align: center;
            margin-top: 30px;
            padding: 15px;
            background: rgba(255,255,255,0.02);
            border-radius: 10px;
            border: 1px solid #1e293b;
            color: #94a3b8;
            font-size: 12px;
        }}
        
        @media (max-width: 768px) {{
            .devices-grid {{
                grid-template-columns: 1fr;
            }}
            .stats {{
                flex-direction: column;
            }}
        }}
    </style>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><i class="fas fa-project-diagram"></i> Roodan Network Topology</h1>
            <p style="color: #94a3b8; margin-top: 5px;">Auto-generated from database.json</p>
            
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value">{len(devices)}</div>
                    <div class="stat-label">Devices</div>
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
                    <div class="stat-label">Cisco</div>
                </div>
            </div>
        </div>
        
        <div class="devices-grid">
"""
        
        # Add device cards
        for device in devices:
            # Determine device type and icon
            platform = device.get('platform', '').lower()
            if 'cisco' in platform:
                if '2911' in platform or 'router' in platform:
                    icon = '<i class="fas fa-router"></i>'
                    device_type = 'Router'
                else:
                    icon = '<i class="fas fa-network-wired"></i>'
                    device_type = 'Switch'
            elif 'mikrotik' in platform:
                icon = '<i class="fas fa-wifi"></i>'
                device_type = 'MikroTik'
            else:
                icon = '<i class="fas fa-server"></i>'
                device_type = 'Unknown'
            
            # Status
            status = device.get('status', 'unknown')
            status_class = 'status-online' if status == 'online' else 'status-unreachable'
            
            html += f"""
            <div class="device-card">
                <div class="device-header">
                    <div class="device-icon">{icon}</div>
                    <div>
                        <div class="device-name">{device.get('name', 'Unknown')}</div>
                        <div class="device-ip">{device.get('ip', 'N/A')}</div>
                        <div class="device-status {status_class}">{status.upper()}</div>
                    </div>
                </div>
                
                <div style="margin: 10px 0; font-size: 12px; color: #94a3b8;">
                    {device_type} • {device.get('platform', 'Unknown platform')}
                </div>
"""
            
            # Add connections
            connections = device.get('connections', [])
            if connections:
                html += '<div class="connections">'
                html += '<div style="font-size: 12px; color: #94a3b8; margin-bottom: 8px;"><strong>Connections:</strong></div>'
                
                for conn in connections[:5]:  # Show max 5 connections
                    remote = device_map.get(conn['remote_device'], {})
                    remote_name = remote.get('name', 'Unknown')
                    
                    html += f"""
                    <div class="connection-item">
                        <span class="connection-port">{conn['local_interface']}</span>
                        <span class="connection-target">→ {remote_name}</span>
                    </div>
                    """
                
                if len(connections) > 5:
                    html += f'<div style="font-size: 11px; color: #64748b; margin-top: 5px;">+ {len(connections) - 5} more</div>'
                
                html += '</div>'
            
            html += '</div>'
        
        # Add topology summary
        html += f"""
        </div>
        
        <div class="topology-summary">
            <div class="summary-title"><i class="fas fa-sitemap"></i> Network Topology Summary</div>
            
            <div style="color: #94a3b8; margin-bottom: 15px;">
                This network has <strong>{len(devices)} devices</strong> with <strong>{len(unique_connections)} unique connections</strong>.
            </div>
"""
        
        # Show key connections
        html += '<div style="margin-top: 20px;">'
        html += '<div style="font-size: 13px; color: #94a3b8; margin-bottom: 10px;"><strong>Key Connections:</strong></div>'
        
        # Show first 10 unique connections
        shown_connections = set()
        for device in devices:
            for conn in device.get('connections', []):
                if conn['remote_device'] in device_map:
                    source = device.get('name', device['id'])
                    target = device_map[conn['remote_device']].get('name', conn['remote_device'])
                    
                    # Avoid duplicates
                    connection_key = tuple(sorted([source, target]))
                    if connection_key not in shown_connections and len(shown_connections) < 10:
                        shown_connections.add(connection_key)
                        html += f"""
                        <div class="connection-line">
                            {source} <span style="color: #D71920;">→</span> {target}
                            <div style="font-size: 11px; color: #64748b; margin-top: 3px;">
                                Port: {conn['local_interface']} • Protocol: {conn['protocol'].upper()}
                            </div>
                        </div>
                        """
        
        html += """
            </div>
        </div>
        
        <div class="timestamp">
            <i class="fas fa-clock"></i> Generated: """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """<br>
            <small>Data source: database.json • Auto-refresh available in the web interface</small>
        </div>
    </div>
    
    <script>
        // Add click handlers to device cards
        document.querySelectorAll('.device-card').forEach(card => {
            card.style.cursor = 'pointer';
            card.addEventListener('click', function() {
                const name = this.querySelector('.device-name').textContent;
                const ip = this.querySelector('.device-ip').textContent;
                alert(`Selected device:\\n${name}\\n${ip}`);
            });
        });
        
        // Auto-refresh every 60 seconds
        setTimeout(() => {
            if (confirm('Refresh with latest data?')) {
                location.reload();
            }
        }, 60000);
    </script>
</body>
</html>
"""
        
        # Write to file
        output_file = 'Roodan_map.html'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"Generated {output_file}")
        return {
            "status": "success",
            "message": f"Map generated with {len(devices)} devices",
            "map_file": output_file,
            "map_url": f"/static/maps/{output_file}",
            "device_count": len(devices),
            "connection_count": len(unique_connections)
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == '__main__':
    result = generate_map_from_database()
    print(json.dumps(result, indent=2))