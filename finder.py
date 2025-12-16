import os
import glob

# Search in common locations
search_patterns = [
    "*.html",                    # Current directory
    "**/*.html",                 # Any subdirectory
    "**/*_map.html",             # Any map file
    "**/Roodan*",               # Anything starting with Roodan
    "maps/**/*.html",           # In maps folder
    "output/**/*.html",         # In output folder
    "generated/**/*.html",      # In generated folder
    "temp/**/*.html",           # In temp folder
]

print("🔍 Searching for map files...")
print("=" * 60)

found_files = []
for pattern in search_patterns:
    for filepath in glob.glob(pattern, recursive=True):
        if "map" in filepath.lower() or "roodan" in filepath.lower():
            found_files.append(filepath)

if found_files:
    print(f"✅ Found {len(found_files)} map-related files:")
    for file in sorted(set(found_files)):
        size = os.path.getsize(file) if os.path.exists(file) else 0
        print(f"  📄 {file} ({size:,} bytes)")
else:
    print("❌ No map files found anywhere!")

# Also check what your cdp_discovery module produces
print("\n🔍 Checking cdp_discovery module output...")
cdp_module = "modules/cdp_discovery"
if os.path.exists(cdp_module):
    print(f"✅ cdp_discovery module exists at: {cdp_module}")
    
    # Look for any Python files in the module
    py_files = glob.glob(f"{cdp_module}/*.py")
    if py_files:
        print(f"  Found {len(py_files)} Python files:")
        for py in py_files:
            # Check if they mention "map" or "html"
            try:
                with open(py, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'html' in content or 'map' in content:
                        print(f"  📝 {os.path.basename(py)} - mentions map/html")
            except:
                pass
else:
    print(f"❌ cdp_discovery module not found at: {cdp_module}")

print("\n" + "=" * 60)
print("💡 Suggestion: Run the CDP discovery module first!")
print("Go to: Topology tab → Run cdp_discovery module")