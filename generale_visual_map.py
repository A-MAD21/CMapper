#!/usr/bin/env python3
"""
Visual Map Generator
Creates interactive D3.js network topology maps
"""

import json
import os
from datetime import datetime

def generate_visual_map(site_name=None):
    """Generate a visual network map for a specific site"""

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

        # Build connection graph
        connections = []
        for device in devices:
            for conn in device.get('connections', []):
                # Only add connections within the site
                if any(d['id'] == conn['remote_device'] for d in devices):
                    connections.append({
                        'source': device['id'],
                        'target': conn['remote_device'],
                        'local_port': conn['local_interface'],
                        'protocol': conn['protocol']
                    })

        # Create D3.js visualization
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Visual Network Map - {site_name}</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        body {{ margin: 0; font-family: Arial, sans-serif; }}
        .node {{ stroke: #fff; stroke-width: 1.5px; }}
        .link {{ stroke: #999; stroke-opacity: 0.6; }}
        .node-text {{ font-size: 12px; text-anchor: middle; }}
        .info-panel {{ position: absolute; top: 10px; right: 10px; background: white; padding: 10px; border: 1px solid #ccc; }}
    </style>
</head>
<body>
    <div class="info-panel">
        <h3>{site_name} Network</h3>
        <p>Devices: {len(devices)}</p>
        <p>Connections: {len(connections)}</p>
        <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>

    <svg width="100%" height="100%"></svg>

    <script>
        // Network data
        const nodes = {json.dumps([{
            'id': d['id'],
            'name': d.get('name', 'Unknown'),
            'ip': d.get('ip', 'N/A'),
            'type': d.get('type', 'unknown'),
            'status': d.get('status', 'unknown')
        } for d in devices])};

        const links = {json.dumps([{
            'source': c['source'],
            'target': c['target'],
            'port': c['local_port']
        } for c in connections])};

        // Create SVG
        const svg = d3.select("svg");
        const width = window.innerWidth;
        const height = window.innerHeight;

        svg.attr("width", width).attr("height", height);

        // Create simulation
        const simulation = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links).id(d => d.id).distance(100))
            .force("charge", d3.forceManyBody().strength(-300))
            .force("center", d3.forceCenter(width / 2, height / 2));

        // Create links
        const link = svg.append("g")
            .attr("class", "links")
            .selectAll("line")
            .data(links)
            .enter().append("line")
            .attr("class", "link");

        // Create nodes
        const node = svg.append("g")
            .attr("class", "nodes")
            .selectAll("circle")
            .data(nodes)
            .enter().append("circle")
            .attr("class", "node")
            .attr("r", d => d.type === 'switch' ? 12 : 8)
            .attr("fill", d => d.status === 'online' ? '#4CAF50' : '#f44336')
            .call(d3.drag()
                .on("start", dragstarted)
                .on("drag", dragged)
                .on("end", dragended));

        // Add labels
        const text = svg.append("g")
            .attr("class", "labels")
            .selectAll("text")
            .data(nodes)
            .enter().append("text")
            .attr("class", "node-text")
            .attr("dy", -15)
            .text(d => d.name.split('-')[0]); // Shorten names

        // Update positions
        simulation.on("tick", () => {{
            link
                .attr("x1", d => d.source.x)
                .attr("y1", d => d.source.y)
                .attr("x2", d => d.target.x)
                .attr("y2", d => d.target.y);

            node
                .attr("cx", d => d.x)
                .attr("cy", d => d.y);

            text
                .attr("x", d => d.x)
                .attr("y", d => d.y);
        }});

        // Drag functions
        function dragstarted(event, d) {{
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        }}

        function dragged(event, d) {{
            d.fx = event.x;
            d.fy = event.y;
        }}

        function dragended(event, d) {{
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        }}

        // Handle window resize
        window.addEventListener('resize', () => {{
            const newWidth = window.innerWidth;
            const newHeight = window.innerHeight;
            svg.attr("width", newWidth).attr("height", newHeight);
            simulation.force("center", d3.forceCenter(newWidth / 2, newHeight / 2));
            simulation.restart();
        }});
    </script>
</body>
</html>"""

        # Write to file
        output_file = f'{site_name.replace(" ", "_")}_visual_map.html'
        os.makedirs('generated_maps', exist_ok=True)
        output_path = f'generated_maps/{output_file}'
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        return {
            "status": "success",
            "message": f"Visual map generated for site '{site_name}' with {len(devices)} devices",
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
    result = generate_visual_map(site_name)
    print(json.dumps(result, indent=2))