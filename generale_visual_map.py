#!/usr/bin/env python3
"""
Visual Map Generator

Generates a self-contained HTML file showing an interactive topology graph.

Used by Backend.py via /api/generate_visual_map.
"""

from __future__ import annotations

import json
import os
import glob
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional


def _load_database(db_path: str) -> Dict[str, Any]:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT json FROM json_store WHERE name = 'devices'")
    row = cur.fetchone()
    conn.close()
    if not row:
        raise FileNotFoundError("No devices data in sqlite database")
    return json.loads(row[0])


def _simple_positions(nodes: List[Dict[str, Any]], spacing: float) -> Dict[str, Dict[str, float]]:
    """
    Assign deterministic positions without external dependencies.

    Uses a circular layout; falls back to a grid if math cos/sin is unavailable.
    """
    try:
        import math
        count = max(len(nodes), 1)
        radius = max(320, 70 * count) * spacing
        positions: Dict[str, Dict[str, float]] = {}
        for idx, node in enumerate(nodes):
            angle = (2 * math.pi * idx) / count
            positions[node["id"]] = {
                "x": radius + radius * 1.1 * math.cos(angle),
                "y": radius + radius * 1.1 * math.sin(angle),
            }
        return positions
    except Exception:
        # Basic grid fallback
        positions = {}
        cols = max(1, int(len(nodes) ** 0.5))
        gap = 180 * spacing
        for idx, node in enumerate(nodes):
            r, c = divmod(idx, cols)
            positions[node["id"]] = {"x": 80 + c * gap, "y": 80 + r * gap}
        return positions


def generate_visual_map(site_name: str | None = None, spacing: Any = None) -> Dict[str, Any]:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, "cmapp.sqlite3")
    out_dir = os.path.join(base_dir, "generated_maps")
    icons_dir = os.path.join(base_dir, "Static", "icons", "map")
    icon_web_base = "/static/icons/map"
    os.makedirs(out_dir, exist_ok=True)

    def _prune_old_maps(out_dir: str, safe_site: str, keep: int = 10) -> None:
        pattern = os.path.join(out_dir, f"{safe_site}_visual_map_*.html")
        files = glob.glob(pattern)
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old_path in files[keep:]:
            try:
                os.remove(old_path)
            except OSError:
                pass

    try:
        data = _load_database(db_path)
        sites = data.get("sites", [])
        if not sites:
            return {"status": "error", "message": "No sites found in database"}

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
        root_ip = (selected_site.get("root_ip") or "").strip()

        id_map = {d.get("id"): d for d in devices if d.get("id")}
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        connected_ids = set()
        seen_edges = set()
        icon_map = {
            "router": "router.png",
            "switch": "switch.png",
            "firewall": "firewall.png",
            "ap": "ap.png",
            "phone": "host.png",
            "pc": "host.png",
            "host": "host.png",
            "server": "server.png",
            "nvr": "nvr.png",
            "pda": "pda.png",
            "finger": "finger.png",
            "camera": "nvr.png",
            "printer": "host.png",
            "other": "host.png",
            "unknown": "host.png"
        }

        for d in devices:
            did = d.get("id")
            if not did:
                continue
            label = d.get("name") or did
            ip = d.get("ip") or ""
            dtype = d.get("type") or ""
            platform = d.get("platform") or d.get("model") or ""
            title = " | ".join([x for x in [ip, dtype, platform] if x])
            icon_name = icon_map.get(dtype.lower(), icon_map["unknown"])
            icon_path = os.path.join(icons_dir, icon_name)
            icon_url = f"{icon_web_base}/{icon_name}" if os.path.exists(icon_path) else ""
            is_root = bool(root_ip and d.get("ip") == root_ip)
            nodes.append({"id": did, "label": label, "title": title, "icon": icon_url, "type": dtype, "is_root": is_root})
            if d.get("always_show_on_map") or is_root:
                connected_ids.add(did)

            for c in d.get("connections") or []:
                rid = c.get("remote_device")
                if not rid or rid not in id_map:
                    continue
                a, b = sorted([did, rid])
                key = (a, b, c.get("protocol") or "")
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                from_type = (d.get("type") or "").lower()
                to_type = (id_map.get(rid, {}).get("type") or "").lower()
                thick_types = {"router", "switch", "server"}
                edge_width = 8.0 if (from_type in thick_types and to_type in thick_types) else 1.3
                edges.append({
                    "from": did,
                    "to": rid,
                    "label": c.get("local_interface") or "",
                    "protocol": (c.get("protocol") or "").upper(),
                    "width": edge_width,
                })
                connected_ids.add(did)
                connected_ids.add(rid)

        if connected_ids:
            nodes = [n for n in nodes if n["id"] in connected_ids]
        else:
            return {"status": "error", "message": f"No connected devices found for site '{site_name}'"}

        try:
            spacing_val = float(spacing)
        except Exception:
            spacing_val = 1.0
        spacing_val = max(0.3, min(1.4, spacing_val))

        positions = _simple_positions(nodes, spacing_val)

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
    .node-label {{ fill: #e8edf2; font-size: 11px; pointer-events: none; text-shadow: 0 1px 2px rgba(0,0,0,0.6); }}
    .node-circle {{ stroke: #c1ddff; stroke-width: 1; }}
    .node-circle.root {{ stroke: #f59e0b; stroke-width: 4; }}
    .node-circle.selected {{ stroke: #ffffff; stroke-width: 2; }}
    .node-icon {{ pointer-events: none; }}
    .edge {{ stroke: #5c7ea8; opacity: 0.7; }}
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
    const typeColors = {{
      router: '#F9A8A8',
      switch: '#A5D8FF',
      unknown: '#1f8ef1',
      ap: '#FFFFFF',
      firewall: '#D8B4FE',
      nvr: '#A7F3D0',
      finger: '#FDE68A',
      server: '#FBCFE8'
    }};

    const svg = document.getElementById('svg');
    const tooltip = document.getElementById('tooltip');

    // Layout and physics parameters
    const params = {{
      repulsion: {90000} * {spacing_val},
      spring: {0.012} * (1 / {spacing_val}),
      restLength: {320} * {spacing_val},
      damping: 0.88,
      maxStep: 12,
      gravity: {0.0007} * (1 / {spacing_val})
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
      const center = {{ x: 0, y: 0 }};
      let count = 0;
      nodes.forEach(n => {{
        const p = nodePos[n.id];
        if (!p) return;
        center.x += p.x;
        center.y += p.y;
        count += 1;
      }});
      if (count > 0) {{
        center.x /= count;
        center.y /= count;
      }}

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

      // Gravity toward center (keeps islands nearby)
      if (count > 0) {{
        nodes.forEach(n => {{
          const p = nodePos[n.id];
          if (!p) return;
          const dx = center.x - p.x;
          const dy = center.y - p.y;
          velocity[n.id].x += dx * params.gravity;
          velocity[n.id].y += dy * params.gravity;
        }});
      }}

      // Integrate (no hard clamping; auto-fit handles view)
      nodes.forEach(n => {{
        if (draggingId === n.id) return; // don't move while dragging
        const v = velocity[n.id];
        v.x *= params.damping;
        v.y *= params.damping;
        v.x = Math.max(Math.min(v.x, params.maxStep), -params.maxStep);
        v.y = Math.max(Math.min(v.y, params.maxStep), -params.maxStep);
        const p = nodePos[n.id];
        p.x = p.x + v.x;
        p.y = p.y + v.y;
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
        line.setAttribute('stroke-width', e.width || 1.3);
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
        const rootClass = n.is_root ? ' root' : '';
        circle.setAttribute('class', 'node-circle' + rootClass + (n.id === selectedId ? ' selected' : ''));
        const dtype = (n.type || '').toLowerCase();
        const baseColor = typeColors[dtype] || typeColors.unknown;
        circle.setAttribute('fill', n.id === selectedId ? '#f5b642' : baseColor);
        g.appendChild(circle);

        if (n.icon) {{
          const img = document.createElementNS('http://www.w3.org/2000/svg', 'image');
          img.setAttribute('href', n.icon);
          img.setAttribute('x', p.x - 9);
          img.setAttribute('y', p.y - 9);
          img.setAttribute('width', 18);
          img.setAttribute('height', 18);
          img.setAttribute('class', 'node-icon');
          g.appendChild(img);
        }}

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

        _prune_old_maps(out_dir, safe_site)
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
