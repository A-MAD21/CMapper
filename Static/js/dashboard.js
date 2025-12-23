/**
 * Network Discovery Platform - REAL Frontend
 * Everything works with real data from the backend
 */

class NetworkPlatform {
    constructor() {
        this.currentSite = '';
        this.currentTab = 'dashboard';
        this.activeModuleThreads = new Map();
        this.settings = {};
        this.modules = [];
        
        // ADD MAP-SPECIFIC PROPERTIES
        this.mapLoaded = false;
        this.currentMapSite = '';
        
        // Initialize
        this.initEventListeners();
        this.loadSettings();
        this.loadData();
        
        // Start background updates
        this.startBackgroundUpdates();
    }

    // ==================== INITIALIZATION ====================
        // ==================== MAP TAB METHODS ====================
        

    updateMapTab() {
        const siteSelect = document.getElementById('mapSiteSelect');
        if (!siteSelect) return;
        
        // Store current selection
        const currentValue = siteSelect.value;
        
        // Clear and repopulate
        siteSelect.innerHTML = '<option value="">Select Site</option>';
        
        if (this.sites && this.sites.length > 0) {
            this.sites.forEach(site => {
                const option = document.createElement('option');
                option.value = site.name;
                option.textContent = site.name;
                
                // Auto-select current site
                if (site.name === this.currentSite) {
                    option.selected = true;
                }
                
                siteSelect.appendChild(option);
            });
        }
        
        // Restore selection if it exists
        if (currentValue) {
            const optionExists = Array.from(siteSelect.options).some(opt => opt.value === currentValue);
            if (optionExists) {
                siteSelect.value = currentValue;
            }
        }
        
        // Update Show Map button
        this.updateShowMapButton();
    }

    updateShowMapButton() {
        const siteSelect = document.getElementById('mapSiteSelect');
        const showMapBtn = document.getElementById('showMapBtn');
        
        if (!siteSelect || !showMapBtn) return;
        
        showMapBtn.disabled = !siteSelect.value;
    }

    async loadMapForSite(siteName) {
        const mapContainer = document.getElementById('mapContainer');
        const mapFrame = document.getElementById('mapFrame');
        const noMapMessage = document.getElementById('noMapMessage');
        const showMapBtn = document.getElementById('showMapBtn');
        
        if (!mapContainer || !mapFrame || !noMapMessage || !showMapBtn) {
            console.error('Map elements not found');
            return;
        }
        
        // Show loading state
        showMapBtn.innerHTML = '<i data-feather="loader"></i> Loading...';
        showMapBtn.disabled = true;
        feather.replace();
        
        // Show map container
        mapContainer.style.display = 'block';
        noMapMessage.style.display = 'none';
        mapFrame.style.display = 'none';
        
        // Save current map site
        this.currentMapSite = siteName;
        
        try {
            // Try to load the map
            const response = await fetch(`/api/map/${encodeURIComponent(siteName)}`);
            
            if (response.ok) {
                const data = await response.json();
                
                if (data.map_url) {
                    // Map exists
                    mapFrame.src = data.map_url;
                    mapFrame.style.display = 'block';
                    noMapMessage.style.display = 'none';
                    this.mapLoaded = true;
                } else {
                    throw new Error('No map URL in response');
                }
            } else {
                // No map available
                this.showNoMapMessage(siteName);
                this.mapLoaded = false;
            }
        } catch (error) {
            console.error('Error loading map:', error);
            this.showNoMapMessage(siteName);
            this.mapLoaded = false;
        } finally {
            // Reset button
            showMapBtn.innerHTML = '<i data-feather="eye"></i> Show Map';
            showMapBtn.disabled = false;
            feather.replace();
        }
    }

    showNoMapMessage(siteName) {
        const mapFrame = document.getElementById('mapFrame');
        const noMapMessage = document.getElementById('noMapMessage');
        
        mapFrame.style.display = 'none';
        noMapMessage.style.display = 'block';
        noMapMessage.innerHTML = `
            <i data-feather="map" style="width: 48px; height: 48px; margin-bottom: 16px;"></i>
            <h3 style="margin-bottom: 8px;">No Map Generated</h3>
            <p style="color: var(--text-secondary); margin-bottom: 16px;">
                Run topology discovery for "${siteName}" first to generate a map
            </p>
            <button class="btn btn-primary" onclick="platform.switchTab('topology')">
                <i data-feather="share-2"></i>
                Go to Topology
            </button>
        `;
        feather.replace();
    }
    
        // ==================== INITIALIZATION ====================

    initEventListeners() {
        // Tab navigation
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                const tab = e.currentTarget.dataset.tab;
                this.switchTab(tab);
            });
        });

        // Refresh button
        document.getElementById('refreshBtn').addEventListener('click', () => {
            this.loadData();
        });

        // Add Site buttons
        document.getElementById('addSiteBtn').addEventListener('click', () => {
            this.showAddSiteModal();
        });
        document.getElementById('addSiteBtn2').addEventListener('click', () => {
            this.showAddSiteModal();
        });
        document.getElementById('saveSiteBtn').addEventListener('click', () => {
            this.saveSite();
        });

        // Site selection
        document.getElementById('deviceSiteFilter').addEventListener('change', (e) => {
            this.loadDevices(e.target.value);
        });
        document.getElementById('moduleSiteSelect').addEventListener('change', (e) => {
            this.currentSite = e.target.value;
            this.updateCurrentSiteDisplay();
        });

        // Generate map button
document.getElementById('generateMapBtn')?.addEventListener('click', function() {
    const siteName = document.getElementById('mapSiteSelect').value;
    if (!siteName) {
        platform.showError('Please select a site first');
        return;
    }
    
    const btn = this;
    const originalText = btn.innerHTML;
    
    btn.innerHTML = '<i data-feather="loader"></i> Generating...';
    btn.disabled = true;
    feather.replace();
    
    fetch('/api/generate_text_map', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ site_name: siteName })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            platform.showMessage(`Text map generated for ${siteName} with ${data.device_count} devices!`);
            // Auto-load the new map
            platform.loadMapForSite(siteName);
        } else {
            platform.showError(data.error || 'Failed to generate map');
        }
    })
    .catch(err => {
        platform.showError('Error generating map');
    })
    .finally(() => {
        btn.innerHTML = originalText;
        btn.disabled = false;
        feather.replace();
    });
});

        // Modal close buttons
        document.querySelectorAll('.close-modal').forEach(btn => {
            btn.addEventListener('click', () => {
                this.closeAllModals();
            });
        });
// Show text map
document.getElementById('showTextMapBtn')?.addEventListener('click', async () => {
    const siteName = document.getElementById('mapSiteSelect').value;
    if (siteName) {
        const btn = document.getElementById('showTextMapBtn');
        const originalText = btn.innerHTML;
        
        btn.innerHTML = '<i data-feather="loader"></i> Generating...';
        btn.disabled = true;
        feather.replace();
        
        try {
            // Generate text map for selected site
            const response = await fetch('/api/generate_text_map', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ site_name: siteName })
            });
            
            if (response.ok) {
                const result = await response.json();
                // Load the generated text map
                document.getElementById('mapFrame').src = result.map_url;
                document.getElementById('mapContainer').style.display = 'block';
                document.getElementById('noMapMessage').style.display = 'none';
                document.getElementById('mapFrame').style.display = 'block';
                platform.currentSite = siteName;
                platform.updateCurrentSiteDisplay();
            } else {
                const error = await response.json();
                platform.showError(error.error || 'Failed to generate text map');
            }
            
        } catch (error) {
            console.error('Error generating text map:', error);
            platform.showError('Error generating text map');
        } finally {
            btn.innerHTML = '<i data-feather="list"></i> Text Map';
            btn.disabled = false;
            feather.replace();
        }
    }
});

// Show visual map  
document.getElementById('showVisualMapBtn')?.addEventListener('click', async () => {
    const siteName = document.getElementById('mapSiteSelect').value;
    if (siteName) {
        const btn = document.getElementById('showVisualMapBtn');
        const originalText = btn.innerHTML;
        
        btn.innerHTML = '<i data-feather="loader"></i> Generating...';
        btn.disabled = true;
        feather.replace();
        
        try {
            // Generate visual map for selected site
            const response = await fetch('/api/generate_visual_map', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ site_name: siteName })
            });
            
            if (response.ok) {
                const result = await response.json();
                // Load the generated visual map
                document.getElementById('mapFrame').src = result.map_url;
                document.getElementById('mapContainer').style.display = 'block';
                document.getElementById('noMapMessage').style.display = 'none';
                document.getElementById('mapFrame').style.display = 'block';
                platform.showMessage(`Visual map generated for ${siteName}!`);
            } else {
                throw new Error('Failed to generate visual map');
            }
        } catch (error) {
            console.error('Error generating visual map:', error);
            platform.showMessage('Error generating visual map', 'error');
        } finally {
            btn.innerHTML = originalText;
            btn.disabled = false;
            feather.replace();
        }
    }
});

// Generate both maps
document.getElementById('generateMapBtn')?.addEventListener('click', async () => {
    const siteName = document.getElementById('mapSiteSelect').value;
    if (!siteName) {
        platform.showMessage('Please select a site first', 'warning');
        return;
    }
    
    const btn = document.getElementById('generateMapBtn');
    const originalText = btn.innerHTML;
    
    btn.innerHTML = '<i data-feather="loader"></i> Generating...';
    btn.disabled = true;
    feather.replace();
    
    try {
        // Generate text map for selected site
        const textResponse = await fetch('/api/generate_text_map', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ site_name: siteName })
        });
        
        // Generate visual map for selected site
        const visualResponse = await fetch('/api/generate_visual_map', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ site_name: siteName })
        });
        
        if (textResponse.ok && visualResponse.ok) {
            platform.showMessage(`Maps generated for ${siteName}!`);
        } else {
            throw new Error('Failed to generate one or more maps');
        }
    } catch (error) {
        console.error('Error generating maps:', error);
        platform.showMessage('Error generating maps', 'error');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
        feather.replace();
    }
});
        // Edit Device
        document.getElementById('updateDeviceBtn').addEventListener('click', () => {
            this.updateDevice();
        });

        // Settings
        document.getElementById('saveSettingsBtn').addEventListener('click', () => {
            this.saveSettings();
        });

        // ==================== MAP TAB EVENT LISTENERS ====================
        
        // Map site selection
        document.getElementById('mapSiteSelect')?.addEventListener('change', (e) => {
            this.updateShowMapButton();
        });
        
        // Show Map button
        document.getElementById('showMapBtn')?.addEventListener('click', async () => {
            const siteName = document.getElementById('mapSiteSelect').value;
            if (siteName) {
                const btn = document.getElementById('showMapBtn');
                const originalText = btn.innerHTML;
                
                btn.innerHTML = '<i data-feather="loader"></i> Loading...';
                btn.disabled = true;
                feather.replace();
                
                try {
                    // Try to load existing visual map first
                    const checkResponse = await fetch(`/api/map/${encodeURIComponent(siteName)}`);
                    
                    if (checkResponse.ok) {
                        const data = await checkResponse.json();
                        if (data.map_url && data.map_url.includes('_visual_map.html')) {
                            // Visual map exists, load it
                            document.getElementById('mapFrame').src = data.map_url;
                            document.getElementById('mapContainer').style.display = 'block';
                            document.getElementById('noMapMessage').style.display = 'none';
                            document.getElementById('mapFrame').style.display = 'block';
                            platform.currentSite = siteName;
                            platform.updateCurrentSiteDisplay();
                            return;
                        }
                    }
                    
                    // If no visual map exists, generate one
                    const genResponse = await fetch('/api/generate_visual_map', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ site_name: siteName })
                    });
                    
                    if (genResponse.ok) {
                        const result = await genResponse.json();
                        // Load the generated visual map
                        document.getElementById('mapFrame').src = result.map_url;
                        document.getElementById('mapContainer').style.display = 'block';
                        document.getElementById('noMapMessage').style.display = 'none';
                        document.getElementById('mapFrame').style.display = 'block';
                        platform.currentSite = siteName;
                        platform.updateCurrentSiteDisplay();
                        platform.showMessage(`Visual map loaded for ${siteName}!`);
                    } else {
                        throw new Error('Failed to generate visual map');
                    }
                } catch (error) {
                    console.error('Error loading/generating map:', error);
                    platform.showMessage('Error loading map', 'error');
                } finally {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                    feather.replace();
                }
            }
        });
        
        // Refresh map button
        document.getElementById('refreshMapBtn')?.addEventListener('click', () => {
            const siteName = document.getElementById('mapSiteSelect').value;
            if (siteName) {
                this.loadMapForSite(siteName);
            }
        });
        
        // Fullscreen button
        document.getElementById('fullscreenBtn')?.addEventListener('click', () => {
            const mapFrame = document.getElementById('mapFrame');
            if (mapFrame.src) {
                window.open(mapFrame.src, '_blank');
            }
        });
    }

    // ==================== DATA LOADING ====================

    async loadData() {
    try {
        this.showLoading(true);
        
        // Load all data in parallel
        const [sites, devices, stats, modules] = await Promise.all([
            this.fetchData('/api/sites'),
            this.fetchData('/api/devices'),
            this.fetchData('/api/stats'),
            this.fetchData('/api/modules')
        ]);
        
        // Update UI
        this.sites = sites || [];
        this.devices = devices || [];
        this.modules = modules || [];
        this.stats = stats || {};
        
        this.updateDashboard();
        this.updateSitesTab();
        this.updateDevicesTab();
        this.updateTopologyTab();
        this.updateSettingsTab();
        this.updateCurrentSiteDisplay();
        this.updateTimeDisplay();
        
        // ADD THIS LINE:
        this.updateMapTab(); // Update map dropdown
        
    } catch (error) {
        console.error('Error loading data:', error);
        this.showError('Failed to load data');
    } finally {
        this.showLoading(false);
    }
}

    async fetchData(endpoint) {
        try {
            const response = await fetch(endpoint);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error(`Error fetching ${endpoint}:`, error);
            return null;
        }
    }

    async loadSettings() {
        try {
            const settings = await this.fetchData('/api/settings');
            if (settings) {
                this.settings = settings;
                this.applySettings();
            }
        } catch (error) {
            console.error('Error loading settings:', error);
        }
    }

    // ==================== UI UPDATES ====================

    updateDashboard() {
        // Update stats
        const statsGrid = document.getElementById('statsGrid');
        if (this.stats) {
            statsGrid.innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">Total Sites</div>
                    <div class="stat-value">${this.stats.total_sites || 0}</div>
                    <div class="stat-trend"></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total Devices</div>
                    <div class="stat-value">${this.stats.total_devices || 0}</div>
                    <div class="stat-trend"></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Online Devices</div>
                    <div class="stat-value">${this.stats.online_devices || 0}</div>
                    <div class="stat-trend positive">${this.stats.online_devices || 0} online</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Last Modified</div>
                    <div class="stat-value" style="font-size: 18px;">${this.formatTime(this.stats.last_modified)}</div>
                    <div class="stat-trend"></div>
                </div>
            `;
        }

        // Update sites table
        const sitesBody = document.getElementById('sitesTableBody');
        if (this.sites && this.sites.length > 0) {
            sitesBody.innerHTML = this.sites.map(site => {
                const siteDevices = this.devices.filter(d => d.site === site.name).length;
                return `
                    <tr>
                        <td>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <i data-feather="map-pin" style="width: 16px; height: 16px;"></i>
                                <strong>${site.name}</strong>
                            </div>
                        </td>
                        <td>${site.root_ip || 'N/A'}</td>
                        <td>${siteDevices} devices</td>
                        <td>${site.last_scan ? this.formatTime(site.last_scan) : 'Never'}</td>
                        <td>
                            <span class="status-badge ${site.locked ? 'status-offline' : 'status-online'}">
                                ${site.locked ? 'Locked' : 'Active'}
                            </span>
                        </td>
                        <td>
                            <div class="action-buttons">
                                <button class="btn-icon" title="Select Site" onclick="platform.selectSite('${site.name}')">
                                    <i data-feather="check-circle"></i>
                                </button>
                                <button class="btn-icon" title="Edit Site" onclick="platform.editSite('${site.id}')">
                                    <i data-feather="edit"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        } else {
            sitesBody.innerHTML = `
                <tr>
                    <td colspan="6" class="empty-state">
                        <div style="padding: 32px; text-align: center;">
                            <i data-feather="map-pin" style="width: 48px; height: 48px;"></i>
                            <h3 style="margin: 16px 0 8px;">No Sites Configured</h3>
                            <p style="color: var(--text-secondary); margin-bottom: 16px;">
                                Add your first site to get started
                            </p>
                            <button class="btn btn-primary" onclick="platform.showAddSiteModal()">
                                <i data-feather="plus"></i>
                                Add Site
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }
        
        feather.replace();
    }

    updateSitesTab() {
        const sitesBody = document.getElementById('sitesManagementBody');
        if (this.sites && this.sites.length > 0) {
            sitesBody.innerHTML = this.sites.map(site => {
                const siteDevices = this.devices.filter(d => d.site === site.name).length;
                return `
                    <tr>
                        <td>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <i data-feather="map-pin"></i>
                                <strong>${site.name}</strong>
                            </div>
                        </td>
                        <td>${site.root_ip || 'N/A'}</td>
                        <td>${this.formatTime(site.created)}</td>
                        <td>${siteDevices} devices</td>
                        <td>${site.last_scan ? this.formatTime(site.last_scan) : 'Never'}</td>
                        <td>
                            <span class="status-badge ${site.locked ? 'status-offline' : 'status-online'}">
                                ${site.locked ? 'Yes' : 'No'}
                            </span>
                        </td>
                        <td>
                            <div class="action-buttons">
                                <button class="btn-icon" title="Select Site" onclick="platform.selectSite('${site.name}')">
                                    <i data-feather="check-circle"></i>
                                </button>
                                <button class="btn-icon" title="Edit Site" onclick="platform.editSite('${site.id}')">
                                    <i data-feather="edit"></i>
                                </button>
                                <button class="btn-icon" title="Delete Site" onclick="platform.deleteSite('${site.id}', '${site.name}')">
                                    <i data-feather="trash-2"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        } else {
            sitesBody.innerHTML = `
                <tr>
                    <td colspan="7" class="empty-state">
                        <div style="padding: 32px; text-align: center;">
                            <i data-feather="map-pin" style="width: 48px; height: 48px;"></i>
                            <h3 style="margin: 16px 0 8px;">No Sites Configured</h3>
                            <p style="color: var(--text-secondary); margin-bottom: 16px;">
                                Add your first site to get started
                            </p>
                            <button class="btn btn-primary" onclick="platform.showAddSiteModal()">
                                <i data-feather="plus"></i>
                                Add Site
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }
        
        feather.replace();
    }

    updateDevicesTab() {
        // Update site filter dropdown
        const siteFilter = document.getElementById('deviceSiteFilter');
        const currentValue = siteFilter.value;
        
        siteFilter.innerHTML = '<option value="">All Sites</option>' +
            this.sites.map(site => 
                `<option value="${site.name}" ${site.name === currentValue ? 'selected' : ''}>${site.name}</option>`
            ).join('');
        
        // Filter devices
        const filterSite = siteFilter.value;
        const filteredDevices = filterSite 
            ? this.devices.filter(d => d.site === filterSite)
            : this.devices;
        
        // Update devices table
        const devicesBody = document.getElementById('devicesTableBody');
        if (filteredDevices.length > 0) {
            devicesBody.innerHTML = filteredDevices.map(device => {
                return `
                    <tr>
                        <td>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <i data-feather="server" style="width: 16px; height: 16px;"></i>
                                <strong>${device.name}</strong>
                                ${device.locked ? '<i data-feather="lock" style="width: 12px; height: 12px; color: var(--warning);"></i>' : ''}
                            </div>
                        </td>
                        <td>${device.ip || 'N/A'}</td>
                        <td>${device.site || 'N/A'}</td>
                        <td>
                            <span class="status-badge" style="background: rgba(59, 130, 246, 0.1); color: var(--info);">
                                ${device.type || 'unknown'}
                            </span>
                        </td>
                        <td>
                            <span class="status-badge status-${device.status || 'unknown'}">
                                ${device.status || 'unknown'}
                            </span>
                        </td>
                        <td>${device.discovered_at ? this.formatTime(device.discovered_at) : 'N/A'}</td>
                        <td>${device.last_seen ? this.formatTime(device.last_seen) : 'Never'}</td>
                        <td>
                            <div class="action-buttons">
                                <button class="btn-icon" title="Edit Device" onclick="platform.showEditDeviceModal('${device.id}')">
                                    <i data-feather="edit"></i>
                                </button>
                                <button class="btn-icon" title="Delete Device" onclick="platform.deleteDevice('${device.id}')">
                                    <i data-feather="trash-2"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        } else {
            const message = filterSite 
                ? `No devices in site "${filterSite}"`
                : 'No devices found';
                
            devicesBody.innerHTML = `
                <tr>
                    <td colspan="8" class="empty-state">
                        <div style="padding: 32px; text-align: center;">
                            <i data-feather="server" style="width: 48px; height: 48px;"></i>
                            <h3 style="margin: 16px 0 8px;">${message}</h3>
                            ${!filterSite ? '<p style="color: var(--text-secondary); margin-bottom: 16px;">Use discovery modules to find devices</p>' : ''}
                        </div>
                    </td>
                </tr>
            `;
        }
        
        feather.replace();
    }

    updateTopologyTab() {
        // Update site selector
        const siteSelect = document.getElementById('moduleSiteSelect');
        siteSelect.innerHTML = '<option value="">Select Site</option>' +
            this.sites.map(site => 
                `<option value="${site.name}" ${site.name === this.currentSite ? 'selected' : ''}>${site.name}</option>`
            ).join('');
        
        // Update modules grid
        const modulesGrid = document.getElementById('modulesGrid');
        if (this.modules && this.modules.length > 0) {
            modulesGrid.innerHTML = this.modules.map(module => {
                return `
                    <div class="module-card">
                        <div class="module-header">
                            <i data-feather="box"></i>
                            <h3>${module.name}</h3>
                        </div>
                        <div class="module-description">
                            ${module.description || 'No description available'}
                        </div>
                        <div class="module-actions">
                            <button class="btn btn-primary" onclick="platform.runModule('${module.id}')">
                                <i data-feather="play"></i>
                                Run Module
                            </button>
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            modulesGrid.innerHTML = `
                <div class="empty-state" style="grid-column: 1 / -1;">
                    <i data-feather="box" style="width: 48px; height: 48px;"></i>
                    <h3 style="margin: 16px 0 8px;">No Modules Available</h3>
                    <p style="color: var(--text-secondary); margin-bottom: 16px;">
                        Add modules to the modules/ directory
                    </p>
                </div>
            `;
        }
        
        // Update active jobs
        this.updateModuleJobs();
        
        feather.replace();
    }

    updateSettingsTab() {
        // Update default site dropdown
        const defaultSiteSelect = document.getElementById('defaultSite');
        defaultSiteSelect.innerHTML = '<option value="">No default site</option>' +
            this.sites.map(site => 
                `<option value="${site.name}" ${this.settings.default_site === site.name ? 'selected' : ''}>${site.name}</option>`
            ).join('');
        
        // Update other settings
        document.getElementById('backupPath').value = this.settings.backup_path || './backups';
        document.getElementById('scanDepth').value = this.settings.default_scan_depth || 3;
        document.getElementById('autoRefresh').checked = this.settings.auto_refresh || false;
        document.getElementById('refreshInterval').value = this.settings.refresh_interval || 30;
    }

    updateCurrentSiteDisplay() {
        const display = document.getElementById('currentSiteDisplay');
        if (this.currentSite) {
            display.textContent = this.currentSite;
            display.style.color = 'var(--text-primary)';
        } else {
            display.textContent = 'No site selected';
            display.style.color = 'var(--text-secondary)';
        }
    }

    updateTimeDisplay() {
        const now = new Date();
        const timeString = now.toLocaleTimeString('en-US', { 
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
        document.getElementById('lastUpdateTime').textContent = timeString;
    }

    // ==================== MODULE SYSTEM ====================

    async runModule(moduleId) {
        if (!this.currentSite) {
            this.showError('Please select a site first');
            return;
        }

        const module = this.modules.find(m => m.id === moduleId);
        if (!module) {
            this.showError('Module not found');
            return;
        }

        // Show module form
        this.showModuleForm(module);
    }

    showModuleForm(module) {
        const modal = document.getElementById('moduleRunnerModal');
        const title = document.getElementById('moduleModalTitle');
        const formContainer = document.getElementById('moduleFormContainer');
        const statusDisplay = document.getElementById('moduleStatusDisplay');
        
        // Reset form
        statusDisplay.style.display = 'none';
        
        // Set title
        title.textContent = `Run: ${module.name}`;
        
        // Build form from module inputs
        // Build form from module inputs
let formHTML = '';

if (module.inputs && module.inputs.length > 0) {
    module.inputs.forEach(input => {
        // Skip site field - we use the selected site
        if (input.name === 'site') {
            return;
        }
        
        if (input.type === 'select') {
            formHTML += `
                <div class="form-group">
                    <label for="module_${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                    <select id="module_${input.name}" ${input.required ? 'required' : ''}>
                        ${input.options.map(opt => 
                            `<option value="${opt}" ${opt === input.default ? 'selected' : ''}>${opt}</option>`
                        ).join('')}
                    </select>
                </div>
            `;
        } else {
            const inputType = input.type === 'credential' ? 'password' : 'text';
            formHTML += `
                <div class="form-group">
                    <label for="module_${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                    <input type="${inputType}" 
                           id="module_${input.name}" 
                           placeholder="${input.placeholder || ''}"
                           ${input.required ? 'required' : ''}
                           value="${input.default || ''}">
                </div>
            `;
        }
    });
} else {
    formHTML = '<p>This module has no configurable parameters.</p>';
}

// Add site as hidden field (auto-filled from selection)
formHTML += `
    <input type="hidden" id="module_site_name" value="${this.currentSite}">
    <div class="form-group">
        <label>Site</label>
        <div style="padding: 10px 14px; background: rgba(255,255,255,0.05); border-radius: 12px; border: 1px solid var(--border-color);">
            ${this.currentSite}
        </div>
    </div>
`;
        // Add site field (hidden, auto-filled)
        formHTML += `<input type="hidden" id="module_site" value="${this.currentSite}">`;
        
        formContainer.innerHTML = formHTML;
        
        // Show modal
        modal.classList.add('active');
        
        // Set up start button
        const startBtn = document.getElementById('startModuleBtn');
        startBtn.onclick = () => this.startModule(module);
    }

    async startModule(module) {
        const formContainer = document.getElementById('moduleFormContainer');
        const statusDisplay = document.getElementById('moduleStatusDisplay');
        const startBtn = document.getElementById('startModuleBtn');
        
        // Validate form
        const inputs = {};
        let isValid = true;
        
        module.inputs.forEach(input => {
            const element = document.getElementById(`module_${input.name}`);
            if (element) {
                const value = element.type === 'checkbox' ? element.checked : element.value;
                if (input.required && !value) {
                    isValid = false;
                    element.style.borderColor = 'var(--error)';
                } else {
                    inputs[input.name] = value;
                    element.style.borderColor = '';
                }
            }
        });
        
        if (!isValid) {
            this.showError('Please fill all required fields');
            return;
        }
        
        // Prepare config
        const config = {
            site_name: this.currentSite,
            parameters: inputs
        };
        
        // Show status display
        statusDisplay.style.display = 'block';
        statusDisplay.querySelector('.status-message').textContent = 'Starting module...';
        statusDisplay.querySelector('.progress-fill').style.width = '5%';
        startBtn.disabled = true;
        
        try {
            // Start module
            const response = await fetch(`/api/modules/${module.id}/run`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
            
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            
            const result = await response.json();
            
            if (result.thread_id) {
                // Track this module thread
                this.activeModuleThreads.set(result.thread_id, {
                    module: module,
                    startTime: new Date(),
                    interval: setInterval(() => {
                        this.updateModuleStatus(result.thread_id);
                    }, 1000)
                });
                
                // Update UI
                statusDisplay.querySelector('.status-message').textContent = 'Module running...';
                this.updateModuleJobs();
            } else {
                throw new Error(result.error || 'Failed to start module');
            }
            
        } catch (error) {
            console.error('Error starting module:', error);
            statusDisplay.querySelector('.status-message').textContent = `Error: ${error.message}`;
            statusDisplay.querySelector('.progress-fill').style.width = '0%';
            startBtn.disabled = false;
        }
    }

    async updateModuleStatus(threadId) {
        try {
            const response = await fetch(`/api/modules/status/${threadId}`);
            if (!response.ok) return;
            
            const status = await response.json();
            const statusDisplay = document.getElementById('moduleStatusDisplay');
            
            if (statusDisplay.style.display === 'block') {
                if (status.progress) {
                    statusDisplay.querySelector('.progress-fill').style.width = `${status.progress}%`;
                }
                
                if (status.status === 'completed' || status.status === 'failed' || status.status === 'error') {
                    // Module finished
                    statusDisplay.querySelector('.status-message').textContent = 
                        status.status === 'completed' ? 'Module completed successfully' : 
                        status.status === 'failed' ? 'Module failed' : 'Module error';
                    
                    // Clean up
                    const threadInfo = this.activeModuleThreads.get(threadId);
                    if (threadInfo && threadInfo.interval) {
                        clearInterval(threadInfo.interval);
                    }
                    this.activeModuleThreads.delete(threadId);
                    
                    // Enable start button
                    document.getElementById('startModuleBtn').disabled = false;
                    
                    // Reload data after delay
                    setTimeout(() => {
                        this.loadData();
                        this.updateModuleJobs();
                    }, 1000);
                } else if (status.status === 'running') {
                    // Calculate duration
                    const threadInfo = this.activeModuleThreads.get(threadId);
                    if (threadInfo) {
                        const duration = Math.floor((new Date() - threadInfo.startTime) / 1000);
                        statusDisplay.querySelector('.status-details').textContent = 
                            `Running for ${duration}s`;
                    }
                }
            }
            
            // Update jobs table
            this.updateModuleJobs();
            
        } catch (error) {
            console.error('Error updating module status:', error);
        }
    }

    updateModuleJobs() {
        const jobsBody = document.getElementById('moduleJobsBody');
        const threads = Array.from(this.activeModuleThreads.entries());
        
        if (threads.length > 0) {
            jobsBody.innerHTML = threads.map(([threadId, threadInfo]) => {
                const duration = Math.floor((new Date() - threadInfo.startTime) / 1000);
                return `
                    <tr>
                        <td>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <i data-feather="box"></i>
                                ${threadInfo.module.name}
                            </div>
                        </td>
                        <td>
                            <span class="status-badge status-online">
                                Running
                            </span>
                        </td>
                        <td>
                            <div class="progress-bar">
                                <div class="progress-fill" style="width: 50%"></div>
                            </div>
                        </td>
                        <td>${this.formatTime(threadInfo.startTime.toISOString())}</td>
                        <td>${duration}s</td>
                        <td>
                            <button class="btn-icon" title="Cancel" onclick="platform.cancelModule('${threadId}')">
                                <i data-feather="x-circle"></i>
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
        } else {
            jobsBody.innerHTML = `
                <tr>
                    <td colspan="6" class="empty-state">
                        <div style="padding: 16px; text-align: center;">
                            <i data-feather="clock" style="width: 24px; height: 24px;"></i>
                            <p style="color: var(--text-secondary); margin: 8px 0;">No active module jobs</p>
                        </div>
                    </td>
                </tr>
            `;
        }
        
        feather.replace();
    }

    async cancelModule(threadId) {
        // For now, just remove from tracking
        const threadInfo = this.activeModuleThreads.get(threadId);
        if (threadInfo && threadInfo.interval) {
            clearInterval(threadInfo.interval);
        }
        this.activeModuleThreads.delete(threadId);
        this.updateModuleJobs();
        
        // Note: In a real implementation, we would send a cancel signal to the backend
        this.showMessage('Module cancellation requested');
    }

    // ==================== SITE MANAGEMENT ====================

    showAddSiteModal() {
        document.getElementById('addSiteModal').classList.add('active');
        // Clear form
        document.getElementById('siteName').value = '';
        document.getElementById('siteRootIP').value = '';
        document.getElementById('siteNotes').value = '';
    }

    async saveSite() {
        const name = document.getElementById('siteName').value.trim();
        const rootIP = document.getElementById('siteRootIP').value.trim();
        const notes = document.getElementById('siteNotes').value.trim();
        
        if (!name || !rootIP) {
            this.showError('Site name and root IP are required');
            return;
        }
        
        try {
            const response = await fetch('/api/sites', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    root_ip: rootIP,
                    notes: notes
                })
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to add site');
            }
            
            this.closeAllModals();
            this.showMessage(`Site "${name}" added successfully`);
            this.loadData();
            
            // Auto-select the new site
            this.currentSite = name;
            this.updateCurrentSiteDisplay();
            
        } catch (error) {
            console.error('Error adding site:', error);
            this.showError(error.message);
        }
    }

selectSite(siteName) {
    this.currentSite = siteName;
    this.updateCurrentSiteDisplay();
    this.showMessage(`Selected site: ${siteName}`);
    
    // Switch to devices tab
    this.switchTab('devices');
    
    // ADD THIS: Update map dropdown
    this.updateMapTab();
}

    async editSite(siteId) {
        const site = this.sites.find(s => s.id === siteId);
        if (!site) {
            this.showError('Site not found');
            return;
        }
        
        // For now, just show a simple edit
        const newName = prompt('Enter new site name:', site.name);
        if (newName && newName !== site.name) {
            try {
                const response = await fetch(`/api/sites/${siteId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: newName })
                });
                
                if (response.ok) {
                    this.showMessage(`Site renamed to "${newName}"`);
                    this.loadData();
                    
                    // Update current site if it was the renamed one
                    if (this.currentSite === site.name) {
                        this.currentSite = newName;
                        this.updateCurrentSiteDisplay();
                    }
                }
            } catch (error) {
                this.showError('Failed to update site');
            }
        }
    }

    async deleteSite(siteId, siteName) {
        if (!confirm(`Delete site "${siteName}" and all its devices?`)) {
            return;
        }
        
        try {
            const response = await fetch(`/api/sites/${siteId}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                this.showMessage(`Site "${siteName}" deleted`);
                this.loadData();
                
                // Clear current site if it was deleted
                if (this.currentSite === siteName) {
                    this.currentSite = '';
                    this.updateCurrentSiteDisplay();
                }
            }
        } catch (error) {
            this.showError('Failed to delete site');
        }
    }

    // ==================== DEVICE MANAGEMENT ====================

    async showEditDeviceModal(deviceId) {
        const device = this.devices.find(d => d.id === deviceId);
        if (!device) {
            this.showError('Device not found');
            return;
        }
        
        // Fill form
        document.getElementById('editDeviceName').value = device.name || '';
        document.getElementById('editDeviceIP').value = device.ip || '';
        document.getElementById('editDeviceType').value = device.type || 'router';
        document.getElementById('editDeviceStatus').value = device.status || 'unknown';
        document.getElementById('editDeviceNotes').value = device.notes || '';
        document.getElementById('editDeviceLocked').checked = device.locked || false;
        
        // Store device ID
        document.getElementById('editDeviceModal').dataset.deviceId = deviceId;
        
        // Show modal
        document.getElementById('editDeviceModal').classList.add('active');
    }

    async updateDevice() {
        const deviceId = document.getElementById('editDeviceModal').dataset.deviceId;
        const device = this.devices.find(d => d.id === deviceId);
        
        if (!device) {
            this.showError('Device not found');
            return;
        }
        
        const updates = {
            name: document.getElementById('editDeviceName').value.trim(),
            ip: document.getElementById('editDeviceIP').value.trim(),
            type: document.getElementById('editDeviceType').value,
            status: document.getElementById('editDeviceStatus').value,
            notes: document.getElementById('editDeviceNotes').value.trim(),
            locked: document.getElementById('editDeviceLocked').checked
        };
        
        if (!updates.name || !updates.ip) {
            this.showError('Device name and IP are required');
            return;
        }
        
        try {
            const response = await fetch(`/api/devices/${deviceId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });
            
            if (!response.ok) throw new Error('Failed to update device');
            
            this.closeAllModals();
            this.showMessage('Device updated successfully');
            this.loadData();
            
        } catch (error) {
            console.error('Error updating device:', error);
            this.showError('Failed to update device');
        }
    }

    async deleteDevice(deviceId) {
        if (!confirm('Delete this device?')) {
            return;
        }
        
        try {
            const response = await fetch(`/api/devices/${deviceId}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                this.showMessage('Device deleted');
                this.loadData();
            }
        } catch (error) {
            this.showError('Failed to delete device');
        }
    }

    // ==================== SETTINGS ====================

    applySettings() {
        // Apply auto-refresh if enabled
        if (this.settings.auto_refresh && this.settings.refresh_interval) {
            if (this.refreshInterval) clearInterval(this.refreshInterval);
            this.refreshInterval = setInterval(() => {
                this.loadData();
            }, this.settings.refresh_interval * 1000);
        }
        
        // Apply default site
        if (this.settings.default_site && !this.currentSite) {
            this.currentSite = this.settings.default_site;
            this.updateCurrentSiteDisplay();
        }
    }

    async saveSettings() {
        const settings = {
            default_site: document.getElementById('defaultSite').value,
            backup_path: document.getElementById('backupPath').value.trim(),
            default_scan_depth: parseInt(document.getElementById('scanDepth').value) || 3,
            auto_refresh: document.getElementById('autoRefresh').checked,
            refresh_interval: parseInt(document.getElementById('refreshInterval').value) || 30
        };
        
        try {
            const response = await fetch('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            
            if (!response.ok) throw new Error('Failed to save settings');
            
            this.settings = settings;
            this.applySettings();
            this.showMessage('Settings saved successfully');
            
        } catch (error) {
            console.error('Error saving settings:', error);
            this.showError('Failed to save settings');
        }
    }

    // ==================== UTILITIES ====================

    switchTab(tabName) {
    // Update active tab in sidebar
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
        if (item.dataset.tab === tabName) {
            item.classList.add('active');
        }
    });
    
    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });
    
    const activeTab = document.getElementById(`${tabName}Tab`);
    if (activeTab) {
        activeTab.classList.add('active');
        document.getElementById('pageTitle').textContent = 
            tabName.charAt(0).toUpperCase() + tabName.slice(1);
    }
    
    this.currentTab = tabName;
    
    // ADD THIS: Update map tab when switched to it
    if (tabName === 'map') {
        this.updateMapTab();
    }
}



    closeAllModals() {
        document.querySelectorAll('.modal').forEach(modal => {
            modal.classList.remove('active');
        });
    }

    showLoading(show) {
        const mainContent = document.querySelector('.main-content');
        if (show) {
            mainContent.classList.add('loading');
        } else {
            mainContent.classList.remove('loading');
        }
    }

    showError(message) {
        this.showMessage(message, 'error');
    }

    showMessage(message, type = 'success') {
        // Simple notification
        alert(`${type === 'error' ? 'Error: ' : ''}${message}`);
    }

    formatTime(isoString) {
        if (!isoString) return 'Never';
        
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        
        const diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return `${diffHours}h ago`;
        
        return date.toLocaleDateString();
    }

    startBackgroundUpdates() {
        // Check module status every 2 seconds
        setInterval(() => {
            if (this.activeModuleThreads.size > 0) {
                this.updateModuleJobs();
            }
        }, 2000);
        
        // Auto-refresh if enabled
        if (this.settings.auto_refresh) {
            setInterval(() => {
                if (this.currentTab === 'dashboard' || this.currentTab === 'devices') {
                    this.loadData();
                }
            }, 30000);
        }
    }
}

// Global instance
let platform;

function initializeDashboard() {
    platform = new NetworkPlatform();
}

// Make platform accessible globally for onclick handlers
window.platform = platform;