#!/usr/bin/env python3
"""
Simple HTML map generator from platform database
"""

import json
import os
from datetime import datetime

def generate_site_map(database, site_name, output_dir="."):
    """Generate simple HTML map for a site"""
    
    # Filter devices for this site
    site_devices = [d for d in database.get("devices", []) if d.get("site") == site_name]
    
    # Create nodes and edges
    nodes = []
    edges = []
    
    for device in site_devices:
        nodes.append({
            "id": device["id"],
            "label": f"{device.get('name', 'Unknown')}\\n{device.get('ip', '')}",
            "group": device.get("type", "unknown"),
            "title": f"Type: {device.get('type', 'unknown')}\\nIP: {device.get('ip', '')}"
        })
        
        # Add connections
        for conn in device.get("connections", []):
            edges.append({
                "from": device["id"],
                "to": conn.get("remote_device"),
                "label": f"{conn.get('local_interface', '?')} → {conn.get('remote_interface', '?')}",
                "title": f"Protocol: {conn.get('protocol', 'cdp')}"
            })
    
    # Generate HTML
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Network Map - {site_name}</title>
        <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 20px;
                background-color: #0f172a;
                color: #e5e7eb;
            }}
            #network {{
                width: 100%;
                height: 800px;
                border: 1px solid #1e293b;
                border-radius: 8px;
                background-color: #020617;
            }}
            .header {{
                margin-bottom: 20px;
            }}
            .stats {{
                background-color: #1e293b;
                padding: 10px;
                border-radius: 8px;
                margin-bottom: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Network Map: {site_name}</h1>
            <p>Generated: {timestamp}</p>
        </div>
        
        <div class="stats">
            <p>Devices: {device_count} | Connections: {connection_count}</p>
        </div>
        
        <div id="network"></div>
        
        <script type="text/javascript">
            var nodes = new vis.DataSet({nodes_json});
            var edges = new vis.DataSet({edges_json});
            
            var container = document.getElementById('network');
            var data = {{
                nodes: nodes,
                edges: edges
            }};
            
            var options = {{
                nodes: {{
                    shape: 'dot',
                    size: 20,
                    font: {{
                        size: 12,
                        color: '#e5e7eb'
                    }},
                    borderWidth: 2
                }},
                edges: {{
                    width: 2,
                    color: {{
                        color: '#475569',
                        highlight: '#D71920'
                    }},
                    font: {{
                        size: 10,
                        color: '#94a3b8'
                    }},
                    smooth: {{
                        type: 'continuous'
                    }}
                }},
                physics: {{
                    enabled: true,
                    stabilization: {{
                        iterations: 100
                    }}
                }},
                interaction: {{
                    hover: true,
                    tooltipDelay: 200
                }}
            }};
            
            var network = new vis.Network(container, data, options);
        </script>
    </body>
    </html>
    """
    
    # Count connections
    connection_count = sum(len(d.get("connections", [])) for d in site_devices)
    
    # Format JSON for JavaScript
    nodes_json = json.dumps(nodes, indent=2)
    edges_json = json.dumps(edges, indent=2)
    
    # Fill template
    html_content = html_template.format(
        site_name=site_name,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        device_count=len(site_devices),
        connection_count=connection_count,
        nodes_json=nodes_json,
        edges_json=edges_json
    )
    
    # Write HTML file
    output_path = os.path.join(output_dir, f"{site_name}_map.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    return output_path