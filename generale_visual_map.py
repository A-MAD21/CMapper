import json
import os
import math
from datetime import datetime

def generate_visual_map():
    """Generate a visual network diagram from database.json"""
    
    try:
        # Read database
        with open('database.json', 'r') as f:
            data = json.load(f)
        
        devices = data.get('devices', [])
        if not devices:
            return {"status": "error", "message": "No devices in database"}
        
        # Build device mapping
        device_map = {}
        for device in devices:
            device_map[device['id']] = device
        
        # Build unique connections
        connections = []
        connection_set = set()
        
        for device in devices:
            for conn in device.get('connections', []):
                remote_id = conn['remote_device']
                if remote_id in device_map:
                    # Create unique connection ID
                    conn_id = tuple(sorted([device['id'], remote_id]))
                    if conn_id not in connection_set:
                        connection_set.add(conn_id)
                        connections.append({
                            'source': device['id'],
                            'target': remote_id,
                            'local_port': conn['local_interface'],
                            'protocol': conn['protocol']
                        })
        
        # Count online devices
        online_count = len([d for d in devices if d.get('status') == 'online'])
        
        # Generate visual HTML with D3.js
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Visual Network Map - Roodan</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: system-ui, 'Segoe UI', Inter, sans-serif;
            background: #0f172a;
            color: #e5e7eb;
            overflow: hidden;
        }}
        
        .header {{
            background: #020617;
            border-bottom: 1px solid #1e293b;
            padding: 15px 25px;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 100;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .header h1 {{
            font-size: 18px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .stats {{
            display: flex;
            gap: 15px;
        }}
        
        .stat {{
            background: rgba(255,255,255,0.05);
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 12px;
        }}
        
        .stat-value {{
            color: #D71920;
            font-weight: bold;
            font-size: 14px;
        }}
        
        .container {{
            display: flex;
            height: 100vh;
            padding-top: 70px;
        }}
        
        .visualization {{
            flex: 1;
            background: #020617;
            position: relative;
            overflow: hidden;
        }}
        
        #network-graph {{
            width: 100%;
            height: 100%;
        }}
        
        .sidebar {{
            width: 300px;
            background: #020617;
            border-left: 1px solid #1e293b;
            padding: 20px;
            overflow-y: auto;
        }}
        
        .sidebar-title {{
            font-size: 16px;
            font-weight: bold;
            margin-bottom: 20px;
            color: #e5e7eb;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .device-list {{
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        
        .device-item {{
            background: rgba(255,255,255,0.03);
            border: 1px solid #1e293b;
            border-radius: 10px;
            padding: 12px;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        
        .device-item:hover {{
            border-color: #D71920;
            background: rgba(215, 25, 32, 0.1);
        }}
        
        .device-item.active {{
            border-color: #D71920;
            background: rgba(215, 25, 32, 0.2);
        }}
        
        .device-name {{
            font-weight: bold;
            font-size: 14px;
            color: #e5e7eb;
        }}
        
        .device-ip {{
            font-family: monospace;
            font-size: 11px;
            color: #94a3b8;
            margin-top: 2px;
        }}
        
        .device-status {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 10px;
            font-weight: bold;
            margin-top: 5px;
        }}
        
        .status-online {{ background: rgba(16, 185, 129, 0.2); color: #10B981; }}
        .status-unreachable {{ background: rgba(239, 68, 68, 0.2); color: #EF4444; }}
        
        .controls {{
            position: fixed;
            bottom: 20px;
            right: 20px;
            display: flex;
            gap: 10px;
            z-index: 100;
        }}
        
        .control-btn {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: #020617;
            border: 1px solid #1e293b;
            color: #e5e7eb;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        
        .control-btn:hover {{
            border-color: #D71920;
            background: rgba(215, 25, 32, 0.1);
            transform: scale(1.1);
        }}
        
        .legend {{
            position: fixed;
            top: 80px;
            left: 20px;
            background: rgba(2, 6, 23, 0.9);
            border: 1px solid #1e293b;
            border-radius: 10px;
            padding: 15px;
            font-size: 12px;
            z-index: 100;
        }}
        
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
        }}
        
        .legend-color {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }}
        
        .node-tooltip {{
            position: absolute;
            background: rgba(2, 6, 23, 0.95);
            border: 1px solid #D71920;
            border-radius: 8px;
            padding: 15px;
            pointer-events: none;
            font-size: 12px;
            max-width: 300px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.3);
            z-index: 1000;
            display: none;
        }}
        
        .tooltip-title {{
            font-weight: bold;
            color: #D71920;
            margin-bottom: 5px;
        }}
        
        .tooltip-detail {{
            margin-bottom: 3px;
            color: #94a3b8;
        }}
        
        .connection-label {{
            font-size: 10px;
            font-family: monospace;
            fill: #94a3b8;
            pointer-events: none;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#D71920" stroke-width="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="16"/>
                <line x1="8" y1="12" x2="16" y2="12"/>
            </svg>
            Visual Network Topology
        </h1>
        <div class="stats">
            <div class="stat">
                <div class="stat-value">{len(devices)}</div>
                <div>Devices</div>
            </div>
            <div class="stat">
                <div class="stat-value">{len(connections)}</div>
                <div>Connections</div>
            </div>
            <div class="stat">
                <div class="stat-value">{online_count}</div>
                <div>Online</div>
            </div>
        </div>
    </div>
    
    <div class="container">
        <div class="visualization">
            <svg id="network-graph"></svg>
            
            <div class="legend">
                <div class="legend-item">
                    <div class="legend-color" style="background: #D71920;"></div>
                    <span>Switch</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #2196F3;"></div>
                    <span>Router</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #FF9800;"></div>
                    <span>MikroTik</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #10B981;"></div>
                    <span>Online</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #EF4444;"></div>
                    <span>Offline</span>
                </div>
            </div>
            
            <div class="controls">
                <button class="control-btn" onclick="zoomIn()" title="Zoom In">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="11" cy="11" r="8"/>
                        <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                        <line x1="11" y1="8" x2="11" y2="14"/>
                        <line x1="8" y1="11" x2="14" y2="11"/>
                    </svg>
                </button>
                <button class="control-btn" onclick="zoomOut()" title="Zoom Out">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="11" cy="11" r="8"/>
                        <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                        <line x1="8" y1="11" x2="14" y2="11"/>
                    </svg>
                </button>
                <button class="control-btn" onclick="resetView()" title="Reset View">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M23 4v6h-6M1 20v-6h6"/>
                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                    </svg>
                </button>
                <button class="control-btn" onclick="fitToScreen()" title="Fit to Screen">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/>
                    </svg>
                </button>
            </div>
            
            <div class="node-tooltip" id="node-tooltip"></div>
        </div>
        
        <div class="sidebar">
            <div class="sidebar-title">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                    <line x1="9" y1="3" x2="9" y2="21"/>
                </svg>
                Network Devices
            </div>
            
            <div class="device-list" id="device-list">
                <!-- Devices will be populated by JavaScript -->
            </div>
            
            <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #1e293b;">
                <div style="font-size: 12px; color: #94a3b8; margin-bottom: 10px;">
                    <strong>Instructions:</strong>
                </div>
                <div style="font-size: 11px; color: #64748b; line-height: 1.5;">
                    • Click and drag nodes to rearrange<br>
                    • Hover over nodes for details<br>
                    • Click devices in sidebar to highlight<br>
                    • Use controls to zoom/pan
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Network data
        const devices = {json.dumps(devices, indent=2)};
        const connections = {json.dumps(connections, indent=2)};
        
        // Device mapping
        const deviceMap = {{}};
        devices.forEach(device => {{
            deviceMap[device.id] = device;
        }});
        
        // Get device color
        function getDeviceColor(device) {{
            const platform = (device.platform || '').toLowerCase();
            if (platform.includes('cisco')) {{
                return platform.includes('2911') || platform.includes('router') ? '#2196F3' : '#D71920';
            }} else if (platform.includes('mikrotik')) {{
                return '#FF9800';
            }}
            return '#94a3b8';
        }}
        
        // Get device status color
        function getStatusColor(device) {{
            return device.status === 'online' ? '#10B981' : '#EF4444';
        }}
        
        // Initialize visualization
        function initVisualization() {{
            const width = document.querySelector('.visualization').clientWidth;
            const height = document.querySelector('.visualization').clientHeight;
            
            const svg = d3.select('#network-graph')
                .attr('width', width)
                .attr('height', height);
            
            // Clear previous
            svg.selectAll('*').remove();
            
            // Create main group
            const g = svg.append('g');
            
            // Zoom behavior
            const zoom = d3.zoom()
                .scaleExtent([0.1, 4])
                .on('zoom', (event) => {{
                    g.attr('transform', event.transform);
                }});
            
            svg.call(zoom);
            
            // Create force simulation
            const simulation = d3.forceSimulation(devices)
                .force('link', d3.forceLink(connections)
                    .id(d => d.id)
                    .distance(150))
                .force('charge', d3.forceManyBody().strength(-300))
                .force('center', d3.forceCenter(width / 2, height / 2))
                .force('collision', d3.forceCollide().radius(40));
            
            // Create links
            const link = g.append('g')
                .selectAll('line')
                .data(connections)
                .enter().append('g');
            
            link.append('line')
                .attr('class', 'link')
                .attr('stroke', '#1e293b')
                .attr('stroke-width', 2)
                .attr('stroke-dasharray', d => d.protocol === 'cdp' ? '5,5' : 'none');
            
            // Add connection labels
            link.append('text')
                .attr('class', 'connection-label')
                .text(d => {{
                    const sourceDevice = deviceMap[d.source];
                    const conn = sourceDevice?.connections?.find(c => c.remote_device === d.target);
                    return conn ? conn.local_interface.split('/').pop() || 'port' : '';
                }});
            
            // Create nodes
            const node = g.append('g')
                .selectAll('g')
                .data(devices)
                .enter().append('g')
                .attr('class', 'node')
                .call(d3.drag()
                    .on('start', dragStarted)
                    .on('drag', dragged)
                    .on('end', dragEnded))
                .on('mouseover', showTooltip)
                .on('mouseout', hideTooltip)
                .on('click', highlightDevice);
            
            // Node circles
            node.append('circle')
                .attr('r', 30)
                .attr('fill', d => getDeviceColor(d))
                .attr('stroke', d => getStatusColor(d))
                .attr('stroke-width', 3);
            
            // Device name (shortened)
            node.append('text')
                .attr('text-anchor', 'middle')
                .attr('dy', 4)
                .attr('fill', 'white')
                .attr('font-size', '10px')
                .attr('font-weight', 'bold')
                .text(d => {{
                    const name = d.name || 'Unknown';
                    return name.length > 12 ? name.substring(0, 10) + '...' : name;
                }});
            
            // Status indicator
            node.append('circle')
                .attr('r', 6)
                .attr('cx', 20)
                .attr('cy', -20)
                .attr('fill', d => getStatusColor(d))
                .attr('stroke', '#020617')
                .attr('stroke-width', 2);
            
            // Update positions on tick
            simulation.on('tick', () => {{
                link.select('line')
                    .attr('x1', d => d.source.x)
                    .attr('y1', d => d.source.y)
                    .attr('x2', d => d.target.x)
                    .attr('y2', d => d.target.y);
                
                link.select('text')
                    .attr('x', d => (d.source.x + d.target.x) / 2)
                    .attr('y', d => (d.source.y + d.target.y) / 2 - 10);
                
                node.attr('transform', d => `translate(${{d.x}}, ${{d.y}})`);
            }});
            
            // Drag functions
            function dragStarted(event, d) {{
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }}
            
            function dragged(event, d) {{
                d.fx = event.x;
                d.fy = event.y;
            }}
            
            function dragEnded(event, d) {{
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }}
            
            // Tooltip functions
            function showTooltip(event, d) {{
                const tooltip = document.getElementById('node-tooltip');
                const connections = d.connections || [];
                
                let html = `<div class="tooltip-title">${{d.name}}</div>`;
                html += `<div class="tooltip-detail">IP: <code>${{d.ip}}</code></div>`;
                html += `<div class="tooltip-detail">Platform: ${{d.platform || 'Unknown'}}</div>`;
                html += `<div class="tooltip-detail">Status: <span style="color:${{getStatusColor(d)}}">${{d.status || 'unknown'}}</span></div>`;
                
                if (connections.length > 0) {{
                    html += `<div class="tooltip-detail" style="margin-top: 8px;"><strong>Connections:</strong></div>`;
                    connections.slice(0, 3).forEach(conn => {{
                        const remote = deviceMap[conn.remote_device];
                        if (remote) {{
                            html += `<div class="tooltip-detail">• ${{conn.local_interface}} → ${{remote.name}}</div>`;
                        }}
                    }});
                    if (connections.length > 3) {{
                        html += `<div class="tooltip-detail">... and ${{connections.length - 3}} more</div>`;
                    }}
                }}
                
                tooltip.innerHTML = html;
                tooltip.style.display = 'block';
                tooltip.style.left = (event.pageX + 10) + 'px';
                tooltip.style.top = (event.pageY + 10) + 'px';
            }}
            
            function hideTooltip() {{
                document.getElementById('node-tooltip').style.display = 'none';
            }}
            
            // Highlight device
            function highlightDevice(event, d) {{
                // Highlight node
                node.select('circle').attr('stroke-width', 3);
                d3.select(event.currentTarget).select('circle').attr('stroke-width', 6);
                
                // Highlight connected links
                link.select('line').attr('stroke', '#1e293b').attr('stroke-width', 2);
                connections.forEach(conn => {{
                    if (conn.source.id === d.id || conn.target.id === d.id) {{
                        d3.selectAll(`line[data-source="${{conn.source.id}}"][data-target="${{conn.target.id}}"]`)
                            .attr('stroke', '#D71920')
                            .attr('stroke-width', 3);
                    }}
                }});
                
                // Update sidebar
                document.querySelectorAll('.device-item').forEach(item => {{
                    item.classList.remove('active');
                    if (item.dataset.deviceId === d.id) {{
                        item.classList.add('active');
                        item.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                    }}
                }});
            }}
            
            // Populate sidebar
            populateSidebar();
            
            // Initial fit to screen
            setTimeout(fitToScreen, 100);
        }}
        
        // Populate sidebar device list
        function populateSidebar() {{
            const deviceList = document.getElementById('device-list');
            deviceList.innerHTML = '';
            
            devices.forEach(device => {{
                const div = document.createElement('div');
                div.className = 'device-item';
                div.dataset.deviceId = device.id;
                
                div.innerHTML = `
                    <div class="device-name">${{device.name}}</div>
                    <div class="device-ip">${{device.ip}}</div>
                    <div class="device-status ${{device.status === 'online' ? 'status-online' : 'status-unreachable'}}">
                        ${{device.status || 'unknown'}}
                    </div>
                `;
                
                div.addEventListener('click', () => {{
                    // Find and highlight the corresponding node
                    const nodes = d3.selectAll('.node').nodes();
                    const nodeIndex = devices.findIndex(d => d.id === device.id);
                    if (nodeIndex !== -1 && nodes[nodeIndex]) {{
                        nodes[nodeIndex].dispatchEvent(new MouseEvent('click'));
                    }}
                }});
                
                deviceList.appendChild(div);
            }});
        }}
        
        // Control functions
        function zoomIn() {{
            d3.select('#network-graph').transition().call(
                d3.zoom().scaleBy, 1.2
            );
        }}
        
        function zoomOut() {{
            d3.select('#network-graph').transition().call(
                d3.zoom().scaleBy, 0.8
            );
        }}
        
        function resetView() {{
            d3.select('#network-graph').transition().call(
                d3.zoom().transform,
                d3.zoomIdentity
            );
        }}
        
        function fitToScreen() {{
            const svg = document.getElementById('network-graph');
            const width = svg.clientWidth;
            const height = svg.clientHeight;
            
            // Get bounds of all nodes
            let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
            devices.forEach(node => {{
                if (node.x) {{
                    minX = Math.min(minX, node.x - 40);
                    maxX = Math.max(maxX, node.x + 40);
                    minY = Math.min(minY, node.y - 40);
                    maxY = Math.max(maxY, node.y + 40);
                }}
            }});
            
            const scale = Math.min(
                0.8 * width / (maxX - minX),
                0.8 * height / (maxY - minY)
            );
            
            const transform = d3.zoomIdentity
                .translate(width / 2, height / 2)
                .scale(scale)
                .translate(-(minX + maxX) / 2, -(minY + maxY) / 2);
            
            d3.select('#network-graph').transition().duration(750).call(
                d3.zoom().transform,
                transform
            );
        }}
        
        // Handle window resize
        window.addEventListener('resize', initVisualization);
        
        // Initialize
        document.addEventListener('DOMContentLoaded', initVisualization);
    </script>
    
    <div style="position: fixed; bottom: 10px; left: 10px; font-size: 10px; color: #64748b; z-index: 1000;">
        Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </div>
</body>
</html>
"""
        
        # Write to file
        output_file = 'Roodan_visual_map.html'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return {
            "status": "success",
            "message": f"Visual map generated with {len(devices)} devices",
            "map_file": output_file,
            "map_url": f"/static/maps/{output_file}",
            "device_count": len(devices),
            "connection_count": len(connections)
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == '__main__':
    result = generate_visual_map()
    print(json.dumps(result, indent=2))