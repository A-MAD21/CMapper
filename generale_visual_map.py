#!/usr/bin/env python3
"""
Visual Map Generator

Generates a self-contained HTML file showing an interactive topology graph.

Used by Backend.py via /api/generate_visual_map.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


def _load_database(db_path: str, fallback_path: str | None = None) -> Dict[str, Any]:
    target = db_path if os.path.exists(db_path) else fallback_path
    if not target:
        raise FileNotFoundError(f"Database not found at {db_path}")
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


def _simple_positions(nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """
    Assign deterministic positions without external dependencies.

    Uses a circular layout; falls back to a grid if math cos/sin is unavailable.
    """
    try:
        import math
        count = max(len(nodes), 1)
        radius = max(150, 40 * count)
        positions: Dict[str, Dict[str, float]] = {}
        for idx, node in enumerate(nodes):
            angle = (2 * math.pi * idx) / count
            positions[node["id"]] = {
                "x": radius + radius * 0.8 * math.cos(angle),
                "y": radius + radius * 0.8 * math.sin(angle),
            }
        return positions
    except Exception:
        # Basic grid fallback
        positions = {}
        cols = max(1, int(len(nodes) ** 0.5))
        gap = 120
        for idx, node in enumerate(nodes):
            r, c = divmod(idx, cols)
            positions[node["id"]] = {"x": 80 + c * gap, "y": 80 + r * gap}
        return positions


def generate_visual_map(site_name: str | None = None) -> Dict[str, Any]:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, "devices.db")
    fallback_db_path = os.path.join(base_dir, "database.json")
    out_dir = os.path.join(base_dir, "generated_maps")
    os.makedirs(out_dir, exist_ok=True)

    try:
        data = _load_database(db_path, fallback_db_path)
        sites = data.get("sites", [])
        if not sites:
            return {"status": "error", "message": "No sites found in database.json"}

        selected_site: Optional[Dict[str, Any]] = None
        if site_name:
            for s in sites:
                if s.get("name") == site_name:
                    selected_site = s
                    break
            if not selected_site:
                return {"status": "error", "message": f"Site '{site_name}' not found"}
        else:
            selected_site = sites[0]
            site_name = selected_site.get("name") or "default"

        all_devices = data.get("devices", [])
        devices = [d for d in all_devices if d.get("site") == site_name]
        if not devices:
            return {"status": "error", "message": f"No devices found for site '{site_name}'"}

        id_map = {d.get("id"): d for d in devices if d.get("id")}
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        seen_edges = set()

        for d in devices:
            did = d.get("id")
            if not did:
                continue
            label = d.get("name") or did
            ip = d.get("ip") or ""
            dtype = d.get("type") or ""
            platform = d.get("platform") or d.get("model") or ""
            title = " | ".join([x for x in [ip, dtype, platform] if x])
            nodes.append({"id": did, "label": label, "title": title})

            for c in d.get("connections") or []:
                rid = c.get("remote_device")
                if not rid or rid not in id_map:
                    continue
                a, b = sorted([did, rid])
                key = (a, b, c.get("protocol") or "")
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edges.append({
                    "from": did,
                    "to": rid,
                    "label": c.get("local_interface") or "",
                    "protocol": (c.get("protocol") or "").upper(),
                })

        positions = _simple_positions(nodes)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_site = "".join(ch for ch in (site_name or "site") if ch.isalnum() or ch in ("-", "_")).strip() or "site"
        filename = f"{safe_site}_visual_map_{timestamp}.html"
        out_path = os.path.join(out_dir, filename)

        # Render an interactive SVG-based map with a simple built-in force layout (no external dependencies).
        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>CMapper - {site_name} visual map</title>
  <style>
    html, body {{ height: 100%; margin: 0; font-family: Arial, sans-serif; background: #0c1b2a; color: #e8edf2; }}
    #topbar {{ padding: 12px 16px; border-bottom: 1px solid #1e3147; background: #13263b; }}
    #network {{ height: calc(100% - 54px); background: radial-gradient(circle at 20% 20%, #123, #0b1826); position: relative; overflow: hidden; }}
    .muted {{ color: #9fb3c8; font-size: 13px; }}
    .node-label {{ fill: #e8edf2; font-size: 12px; pointer-events: none; }}
    .node-circle {{ fill: #1f8ef1; stroke: #c1ddff; stroke-width: 1; }}
    .node-circle.selected {{ fill: #f5b642; stroke: #ffffff; stroke-width: 2; }}
    .edge {{ stroke: #5c7ea8; stroke-width: 1.3; opacity: 0.7; }}
    .tooltip {{
      position: absolute;
      background: rgba(10, 20, 35, 0.9);
      color: #e8edf2;
      padding: 6px 10px;
      border: 1px solid #234;
      border-radius: 6px;
      pointer-events: none;
      font-size: 12px;
      display: none;
    }}
  </style>
</head>
<body>
  <div id="topbar">
    <div><b>Site:</b> {site_name} <span class="muted">({len(devices)} devices, {len(edges)} links)</span></div>
    <div class="muted">Generated {datetime.now().isoformat(timespec="seconds")}</div>
  </div>
  <div id="network">
    <svg id="svg" width="100%" height="100%"></svg>
    <div id="tooltip" class="tooltip"></div>
  </div>
  <script>
    const nodes = {json.dumps(nodes)};
    const edges = {json.dumps(edges)};
    const basePositions = {json.dumps(positions)};

    const svg = document.getElementById('svg');
    const tooltip = document.getElementById('tooltip');

    // Layout and physics parameters
    const params = {{
      repulsion: 18000,
      spring: 0.015,
      restLength: 180,
      damping: 0.85,
      maxStep: 12
    }};

    let nodePos = Object.fromEntries(nodes.map(n => [n.id, {{ ...basePositions[n.id] }}]));
    let velocity = Object.fromEntries(nodes.map(n => [n.id, {{ x: 0, y: 0 }}]));
    let draggingId = null;
    let selectedId = null;
    let panning = false;
    let lastPan = null;
    const transform = {{ x: 0, y: 0, scale: 1 }};

    function resize() {{
      const rect = svg.getBoundingClientRect();
      svg.setAttribute('viewBox', `0 0 ${{rect.width}} ${{rect.height}}`);
      // Center initial positions
      Object.values(nodePos).forEach(p => {{
        if (!p._shifted) {{
          p.x += rect.width / 2;
          p.y += rect.height / 2;
          p._shifted = true;
        }}
      }});
    }}

    function applyForces() {{
      // Repulsion
      for (let i = 0; i < nodes.length; i++) {{
        for (let j = i + 1; j < nodes.length; j++) {{
          const a = nodePos[nodes[i].id];
          const b = nodePos[nodes[j].id];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          const dist2 = Math.max(dx*dx + dy*dy, 40); // avoid div/0
          const force = params.repulsion / dist2;
          const invDist = 1 / Math.sqrt(dist2);
          dx *= invDist; dy *= invDist;
          velocity[nodes[i].id].x += dx * force;
          velocity[nodes[i].id].y += dy * force;
          velocity[nodes[j].id].x -= dx * force;
          velocity[nodes[j].id].y -= dy * force;
        }}
      }}

      // Springs
      edges.forEach(e => {{
        const a = nodePos[e.from];
        const b = nodePos[e.to];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const dist = Math.max(Math.sqrt(dx*dx + dy*dy), 1);
        const force = params.spring * (dist - params.restLength);
        const normX = dx / dist;
        const normY = dy / dist;
        velocity[e.from].x += force * normX;
        velocity[e.from].y += force * normY;
        velocity[e.to].x -= force * normX;
        velocity[e.to].y -= force * normY;
      }});

      // Integrate
      const rect = svg.getBoundingClientRect();
      const minX = 30, minY = 30, maxX = rect.width - 30, maxY = rect.height - 30;

      nodes.forEach(n => {{
        if (draggingId === n.id) return; // don't move while dragging
        const v = velocity[n.id];
        v.x *= params.damping;
        v.y *= params.damping;
        v.x = Math.max(Math.min(v.x, params.maxStep), -params.maxStep);
        v.y = Math.max(Math.min(v.y, params.maxStep), -params.maxStep);
        const p = nodePos[n.id];
        p.x = Math.min(Math.max(minX, p.x + v.x), maxX);
        p.y = Math.min(Math.max(minY, p.y + v.y), maxY);
      }});
    }}

    function draw() {{
      svg.innerHTML = '';
      const root = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      root.setAttribute('transform', `translate(${{transform.x}}, ${{transform.y}}) scale(${{transform.scale}})`);

      // Draw edges
      edges.forEach(e => {{
        const a = nodePos[e.from];
        const b = nodePos[e.to];
        if (!a || !b) return;
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', a.x);
        line.setAttribute('y1', a.y);
        line.setAttribute('x2', b.x);
        line.setAttribute('y2', b.y);
        line.setAttribute('class', 'edge');
        root.appendChild(line);
      }});

      // Draw nodes
      nodes.forEach(n => {{
        const p = nodePos[n.id];
        if (!p) return;

        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('cursor', 'pointer');
        g.addEventListener('mousemove', (evt) => {{
          tooltip.style.display = 'block';
          tooltip.style.left = `${{evt.clientX + 10}}px`;
          tooltip.style.top = `${{evt.clientY + 10}}px`;
          tooltip.innerHTML = `<b>${{n.label}}</b><br/>${{n.title || 'No details'}}`;
        }});
        g.addEventListener('mouseleave', () => {{
          tooltip.style.display = 'none';
        }});
        g.addEventListener('mousedown', (evt) => {{
          selectedId = n.id;
          if (window.parent && window.parent !== window) {{
            window.parent.postMessage({{ type: 'cmapp:select', deviceId: n.id }}, '*');
          }}
          draggingId = n.id;
          tooltip.style.display = 'none';
          evt.preventDefault();
          evt.stopPropagation();
        }});

        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('cx', p.x);
        circle.setAttribute('cy', p.y);
        circle.setAttribute('r', 14);
        circle.setAttribute('class', 'node-circle' + (n.id === selectedId ? ' selected' : ''));
        g.appendChild(circle);

        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', p.x);
        text.setAttribute('y', p.y + 26);
        text.setAttribute('class', 'node-label');
        text.setAttribute('text-anchor', 'middle');
        text.textContent = n.label;
        g.appendChild(text);

        root.appendChild(g);
      }});

      svg.appendChild(root);
    }}

    function onMouseMove(evt) {{
      if (draggingId) {{
        const p = nodePos[draggingId];
        const world = toWorld(evt);
        p.x = world.x;
        p.y = world.y;
        velocity[draggingId].x = 0;
        velocity[draggingId].y = 0;
      }} else if (panning && lastPan) {{
        transform.x += evt.clientX - lastPan.x;
        transform.y += evt.clientY - lastPan.y;
        lastPan = {{ x: evt.clientX, y: evt.clientY }};
      }}
    }}
    function onMouseUp() {{ draggingId = null; panning = false; }}

    // Zoom/pan controls
    svg.addEventListener('wheel', (evt) => {{
      evt.preventDefault();
      const scaleDelta = evt.deltaY < 0 ? 1.1 : 0.9;
      const mouse = toWorld(evt);
      transform.scale = Math.min(3, Math.max(0.4, transform.scale * scaleDelta));
      transform.x = evt.clientX - mouse.x * transform.scale;
      transform.y = evt.clientY - mouse.y * transform.scale;
    }}, {{ passive: false }});

    svg.addEventListener('mousedown', (evt) => {{
      if (evt.target.tagName === 'circle' || evt.target.tagName === 'text') return;
      panning = true;
      lastPan = {{ x: evt.clientX, y: evt.clientY }};
    }});

    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    window.addEventListener('message', (evt) => {{
      const data = evt.data || {{}};
      if (data.type === 'cmapp:select' && data.deviceId) {{
        selectedId = data.deviceId;
      }}
    }});

    resize();
    function loop() {{
      applyForces();
      draw();
      requestAnimationFrame(loop);
    }}
    loop();

    function toWorld(evt) {{
      return {{
        x: (evt.clientX - transform.x) / transform.scale,
        y: (evt.clientY - transform.y) / transform.scale
      }};
    }}
  </script>
</body>
</html>
"""

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)

        return {
            "status": "success",
            "message": "Visual map generated successfully",
            "map_file": filename,
            "map_url": f"/generated_maps/{filename}",
            "site_name": site_name,
            "device_count": len(devices),
            "connection_count": len(edges),
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import sys

    site = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(generate_visual_map(site), indent=2))
