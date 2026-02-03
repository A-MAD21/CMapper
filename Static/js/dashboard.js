/**
 * Network Discovery Platform - REAL Frontend
 * Everything works with real data from the backend
 */

const replaceIcons = () => {
    if (window.feather && typeof feather.replace === 'function') {
        feather.replace();
    }
};

class NetworkPlatform {
    constructor() {
        this.currentSite = '';
        this.currentTab = 'dashboard';
        this.activeModuleThreads = new Map();
        this.settings = {};
        this.modules = [];
        this.authenticated = false;
        this.authRequired = false;
        this.backgroundUpdatesStarted = false;
        this.currentUser = '';
        this.currentUserRole = '';
        this.allowedSites = [];
        this.users = [];
        this.monitoringData = null;
        this.monitoringInterval = null;
        this.monitoringSiteName = '';
        this.monitoringSelectedDeviceId = '';
        this.monitoringResizeBound = false;
        this.monitoringLayout = null;
        this.monitoringLayoutSaveTimer = null;
        this.selectedDeviceIds = new Set();
        this.sortState = {
            dashboardSites: { key: 'name', dir: 'asc' },
            sites: { key: 'name', dir: 'asc' },
            devices: { key: 'name', dir: 'asc' }
        };
        this.ouiRangesText = '';
        
        // ADD MAP-SPECIFIC PROPERTIES
        this.mapLoaded = false;
        this.currentMapSite = '';
        this.mapSelectedDeviceId = '';
        
        // Initialize
        this.initEventListeners();
        this.initAuth();

        // Map selection messages from iframe
        window.addEventListener('message', (event) => {
            const data = event.data || {};
            if (data.type === 'cmapp:select' && data.deviceId) {
                this.setMapSelection(data.deviceId);
            }
        });
    }

    // ==================== INITIALIZATION ====================
    async initAuth() {
        try {
            const response = await fetch('/api/auth/me');
            if (!response.ok) {
                throw new Error('Failed to check auth status');
            }
            const status = await response.json();
            this.authRequired = !!status.auth_required;
            this.authenticated = !!status.authenticated;
            this.currentUser = status.user || '';
            this.currentUserRole = status.role || '';
            this.allowedSites = status.allowed_sites || [];
            if (this.authenticated || !this.authRequired) {
                this.hideAuthOverlay();
                this.loadSettings();
                if (this.currentUserRole === 'admin') {
                    await this.loadOuiRanges();
                }
                await this.loadData();
                if (this.currentUserRole === 'admin') {
                    await this.loadUsers();
                }
                this.startBackgroundUpdates();
            } else {
                this.showAuthOverlay();
            }
        } catch (error) {
            console.error('Error initializing auth:', error);
            this.showAuthOverlay();
        }
    }

    showAuthOverlay() {
        const overlay = document.getElementById('authOverlay');
        if (overlay) {
            overlay.style.display = 'flex';
            const userInput = document.getElementById('loginUsername');
            if (userInput) {
                userInput.focus();
            }
        }
    }

    hideAuthOverlay() {
        const overlay = document.getElementById('authOverlay');
        if (overlay) {
            overlay.style.display = 'none';
        }
    }

    setAuthMessage(message, isError = false) {
        const el = document.getElementById('loginMessage');
        if (!el) return;
        el.textContent = message || '';
        el.style.color = isError ? 'var(--danger-color)' : 'var(--text-secondary)';
    }

    async handleLogin() {
        const username = document.getElementById('loginUsername')?.value.trim() || '';
        const password = document.getElementById('loginPassword')?.value || '';
        if (!username || !password) {
            this.setAuthMessage('Enter username and password.', true);
            return;
        }
        this.setAuthMessage('Signing in...');
        try {
            const response = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });
            const data = await response.json();
            if (!response.ok) {
                this.setAuthMessage(data.error || 'Login failed.', true);
                return;
            }
            this.authenticated = true;
            this.currentUser = data.user || '';
            this.currentUserRole = data.role || '';
            this.allowedSites = data.allowed_sites || [];
            this.hideAuthOverlay();
            this.setAuthMessage('');
            this.loadSettings();
            if (this.currentUserRole === 'admin') {
                await this.loadOuiRanges();
            }
            await this.loadData();
            if (this.currentUserRole === 'admin') {
                await this.loadUsers();
            }
            this.startBackgroundUpdates();
        } catch (error) {
            console.error('Login error:', error);
            this.setAuthMessage('Login failed.', true);
        }
    }

    async handleLogout() {
        try {
            await fetch('/api/auth/logout', { method: 'POST' });
        } catch (error) {
            console.error('Logout error:', error);
        }
        this.authenticated = false;
        this.currentUser = '';
        this.currentUserRole = '';
        this.allowedSites = [];
        if (this.authRequired) {
            this.showAuthOverlay();
            window.location.reload();
        }
    }

    async handleAuthSetup() {
        const username = document.getElementById('authUsername')?.value.trim() || '';
        const password = document.getElementById('authPassword')?.value || '';
        const enabled = document.getElementById('authEnabled')?.checked ?? true;
        if (!username || !password) {
            this.showError('Username and password are required');
            return;
        }
        try {
            const response = await fetch('/api/auth/setup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password, enabled })
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to save login');
                return;
            }
            this.authRequired = !!data.auth_required;
            this.authenticated = true;
            this.currentUser = data.user || '';
            this.currentUserRole = data.role || '';
            this.allowedSites = data.allowed_sites || [];
            this.showMessage('Login saved successfully');
            this.hideAuthOverlay();
            this.loadSettings();
            if (this.currentUserRole === 'admin') {
                await this.loadOuiRanges();
            }
        } catch (error) {
            console.error('Auth setup error:', error);
            this.showError('Failed to save login');
        }
    }

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
        this.updateMapControls();
    }

    updateMonitoringSelection() {
        const label = document.getElementById('monitoringSelectedLabel');
        const toggleBtn = document.getElementById('monitoringToggleBtn');
        const rulesBtn = document.getElementById('monitoringRulesBtn');
        const device = (this.monitoringData?.devices || []).find(d => d.id === this.monitoringSelectedDeviceId);
        if (device && label) {
            const ip = device.ip ? ` (${device.ip})` : '';
            label.textContent = `${device.name || device.id}${ip}`;
            if (toggleBtn) {
                toggleBtn.disabled = this.currentUserRole === 'guest';
                toggleBtn.classList.toggle('state-on', !!device.enabled);
                toggleBtn.classList.toggle('state-off', !device.enabled);
            }
            if (rulesBtn) rulesBtn.disabled = this.currentUserRole === 'guest';
        } else {
            this.monitoringSelectedDeviceId = '';
            if (label) label.textContent = 'None';
            if (toggleBtn) {
                toggleBtn.disabled = true;
                toggleBtn.classList.remove('state-on', 'state-off');
            }
            if (rulesBtn) rulesBtn.disabled = true;
        }
    }

    updateMonitoringTab() {
        const siteSelect = document.getElementById('monitoringSiteSelect');
        const showBtn = document.getElementById('showMonitoringBtn');
        const rulesBtn = document.getElementById('monitoringRulesBtn');
        if (!siteSelect || !showBtn) return;
        const currentValue = siteSelect.value;
        siteSelect.innerHTML = '<option value="">Select Site</option>';
        if (this.sites && this.sites.length > 0) {
            this.sites.forEach(site => {
                const option = document.createElement('option');
                option.value = site.name;
                option.textContent = site.name;
                if (site.name === this.currentSite) {
                    option.selected = true;
                }
                siteSelect.appendChild(option);
            });
        }
        if (currentValue) {
            const exists = Array.from(siteSelect.options).some(opt => opt.value === currentValue);
            if (exists) {
                siteSelect.value = currentValue;
            }
        }
        showBtn.disabled = !siteSelect.value;
        if (rulesBtn) {
            rulesBtn.disabled = this.currentUserRole === 'guest';
        }
        this.updateMonitoringSelection();
    }

    updateMapControls() {
        const isGuest = this.currentUserRole === 'guest';
        const textBtn = document.getElementById('showTextMapBtn');
        const visualBtn = document.getElementById('showVisualMapBtn');
        const genBtn = document.getElementById('generateMapBtn');
        if (textBtn) textBtn.disabled = isGuest;
        if (visualBtn) visualBtn.disabled = isGuest;
        if (genBtn) genBtn.disabled = isGuest;
    }

    updateShowMapButton() {
        const siteSelect = document.getElementById('mapSiteSelect');
        const showMapBtn = document.getElementById('showMapBtn');
        
        if (!siteSelect || !showMapBtn) return;
        
        showMapBtn.disabled = !siteSelect.value;
    }

    setMapSelection(deviceId) {
        this.mapSelectedDeviceId = deviceId || '';
        const label = document.getElementById('mapSelectedLabel');
        const editBtn = document.getElementById('mapEditBtn');
        const removeBtn = document.getElementById('mapRemoveBtn');

        const device = (this.devices || []).find(d => d.id === this.mapSelectedDeviceId);
        if (!device) {
            this.mapSelectedDeviceId = '';
        }

        if (this.mapSelectedDeviceId && device) {
            const ip = device.ip ? ` (${device.ip})` : '';
            label.textContent = `${device.name}${ip}`;
            editBtn.disabled = false;
            removeBtn.disabled = false;
            if (device.site && device.site !== this.currentSite) {
                this.currentSite = device.site;
                this.updateCurrentSiteDisplay();
                this.updateMapTab();
            }
        } else {
            label.textContent = 'None';
            editBtn.disabled = true;
            removeBtn.disabled = true;
        }
    }

    syncMapSelection() {
        if (!this.mapSelectedDeviceId) {
            this.setMapSelection('');
            return;
        }
        const exists = (this.devices || []).some(d => d.id === this.mapSelectedDeviceId);
        if (!exists) {
            this.setMapSelection('');
        }
    }

    openModuleById(moduleId, prefill = {}) {
        const module = (this.modules || []).find(m => m.id === moduleId);
        if (!module) {
            this.showError(`Module '${moduleId}' not found`);
            return;
        }
        this.showModuleForm(module, prefill);
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
        replaceIcons();
        
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
            replaceIcons();
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
        replaceIcons();
    }
    
        // ==================== INITIALIZATION ====================

    initEventListeners() {
        // Auth controls
        document.getElementById('loginBtn')?.addEventListener('click', () => {
            this.handleLogin();
        });
        document.getElementById('logoutBtn')?.addEventListener('click', () => {
            this.handleLogout();
        });
        document.getElementById('logoutBtnGlobal')?.addEventListener('click', () => {
            this.handleLogout();
        });
        document.getElementById('saveAuthBtn')?.addEventListener('click', () => {
            this.handleAuthSetup();
        });
        document.getElementById('addUserBtn')?.addEventListener('click', () => {
            this.addUser();
        });
        document.getElementById('changePasswordBtn')?.addEventListener('click', () => {
            this.changePassword();
        });

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
    replaceIcons();
    
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
        replaceIcons();
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
        replaceIcons();
        
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
            replaceIcons();
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
        replaceIcons();
        
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
            replaceIcons();
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
    replaceIcons();
    
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
        replaceIcons();
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

        document.getElementById('addConnectionRowBtn')?.addEventListener('click', () => {
            this.addConnectionRow();
        });

        // ==================== MAP TAB EVENT LISTENERS ====================
        
        // Map site selection
        document.getElementById('mapSiteSelect')?.addEventListener('change', (e) => {
            this.updateShowMapButton();
        });
        document.getElementById('monitoringSiteSelect')?.addEventListener('change', (e) => {
            const showBtn = document.getElementById('showMonitoringBtn');
            if (showBtn) {
                showBtn.disabled = !e.target.value;
            }
        });

        document.getElementById('mapAddBtn')?.addEventListener('click', () => {
            if (!this.currentSite) {
                this.showError('Select a site first');
                return;
            }
            this.openModuleById('add_device_manual');
        });

        document.getElementById('mapEditBtn')?.addEventListener('click', () => {
            if (!this.mapSelectedDeviceId) {
                this.showError('Select a node first');
                return;
            }
            this.showEditDeviceModal(this.mapSelectedDeviceId);
        });

        document.getElementById('mapRemoveBtn')?.addEventListener('click', () => {
            if (!this.mapSelectedDeviceId) {
                this.showError('Select a node first');
                return;
            }
            this.openModuleById('remove_device', {
                device_id: this.mapSelectedDeviceId,
                keep_dependents: true
            });
        });
        
        // Show Map button
        document.getElementById('showMapBtn')?.addEventListener('click', async () => {
            const siteName = document.getElementById('mapSiteSelect').value;
            if (siteName) {
                const btn = document.getElementById('showMapBtn');
                const originalText = btn.innerHTML;
                
                btn.innerHTML = '<i data-feather="loader"></i> Loading...';
                btn.disabled = true;
                replaceIcons();
                
                try {
                    // Try to load existing visual map first
                    const checkResponse = await fetch(`/api/map/${encodeURIComponent(siteName)}`);
                    
                    if (checkResponse.ok) {
                        const data = await checkResponse.json();
                        if (data.map_url) {
                            document.getElementById('mapFrame').src = data.map_url;
                            document.getElementById('mapContainer').style.display = 'block';
                            document.getElementById('noMapMessage').style.display = 'none';
                            document.getElementById('mapFrame').style.display = 'block';
                            platform.currentSite = siteName;
                            platform.updateCurrentSiteDisplay();
                            return;
                        }
                    }

                    if (this.currentUserRole === 'guest') {
                        this.showNoMapMessage(siteName);
                        return;
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
                    replaceIcons();
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

        // Monitoring actions
        document.getElementById('showMonitoringBtn')?.addEventListener('click', () => {
            const siteName = document.getElementById('monitoringSiteSelect').value;
            if (siteName) {
                this.loadMonitoringForSite(siteName);
            }
        });
        document.getElementById('monitoringRulesBtn')?.addEventListener('click', () => {
            const siteName = document.getElementById('monitoringSiteSelect').value;
            if (!siteName) {
                this.showError('Select a site first');
                return;
            }
            if (!this.monitoringSelectedDeviceId) {
                this.showError('Select a node first');
                return;
            }
            this.openMonitoringRules(siteName, this.monitoringSelectedDeviceId);
        });
        document.getElementById('monitoringToggleBtn')?.addEventListener('click', () => {
            const siteName = document.getElementById('monitoringSiteSelect').value;
            if (!siteName || !this.monitoringSelectedDeviceId) {
                return;
            }
            this.toggleMonitoringDevice(siteName, this.monitoringSelectedDeviceId);
        });
        
        document.getElementById('exportDataBtn')?.addEventListener('click', () => {
            this.exportData();
        });
        document.getElementById('importDataBtn')?.addEventListener('click', () => {
            this.importData();
        });
        document.getElementById('deleteSelectedDevicesBtn')?.addEventListener('click', () => {
            this.deleteSelectedDevices();
        });
        document.getElementById('devicesSelectAll')?.addEventListener('change', (event) => {
            this.toggleSelectAllDevices(event.target.checked);
        });
        document.getElementById('saveOuiRangesBtn')?.addEventListener('click', () => {
            this.saveOuiRanges();
        });
        document.getElementById('reloadOuiRangesBtn')?.addEventListener('click', () => {
            this.loadOuiRanges();
        });
        document.getElementById('fillOuiFromMacBtn')?.addEventListener('click', () => {
            this.fillOuiFromMac();
        });
        document.getElementById('addOuiRangeBtn')?.addEventListener('click', () => {
            this.addOuiRangeFromModal();
        });
        document.getElementById('saveMonitoringRulesBtn')?.addEventListener('click', () => {
            const siteName = document.getElementById('monitoringSiteSelect').value;
            if (siteName) {
                this.saveMonitoringRules(siteName);
            }
        });
        document.getElementById('saveOuiBtn')?.addEventListener('click', () => {
            this.saveDeviceOui();
        });

        this.bindSortHeaders();
    }

    // ==================== DATA LOADING ====================

    async loadData() {
        this.showLoading(true);
        this.updateTimeDisplay();
        let settled = 0;
        const finishOne = () => {
            settled += 1;
            if (settled === 1) {
                this.showLoading(false);
            }
        };

        const siteTask = this.fetchData('/api/sites', { timeoutMs: 8000 })
            .then(sites => {
                if (sites) {
                    this.sites = sites;
                    this.updateSitesTab();
                    this.updateSettingsTab();
                    this.updateMapTab();
                    this.updateMonitoringTab();
                }
            })
            .finally(finishOne);

        const deviceTask = this.fetchData('/api/devices', { timeoutMs: 8000 })
            .then(devices => {
                if (devices) {
                    this.devices = devices;
                    this.updateDevicesTab();
                    this.updateMapTab();
                    this.syncMapSelection();
                }
            })
            .finally(finishOne);

        const statsTask = this.fetchData('/api/stats', { timeoutMs: 8000 })
            .then(stats => {
                if (stats) {
                    this.stats = stats;
                    this.updateDashboard();
                }
            })
            .finally(finishOne);

        const modulesTask = this.fetchData('/api/modules', { timeoutMs: 8000 })
            .then(modules => {
                if (modules) {
                    this.modules = modules;
                    this.updateTopologyTab();
                }
            })
            .finally(finishOne);

        Promise.allSettled([siteTask, deviceTask, statsTask, modulesTask])
            .then(() => {
                this.updateCurrentSiteDisplay();
                if (this.currentUserRole === 'admin') {
                    this.loadUsers();
                }
            })
            .finally(() => this.showLoading(false));
    }

    async fetchData(endpoint, options = {}) {
        const timeoutMs = options.timeoutMs ?? 10000;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(endpoint, { signal: controller.signal });
            if (response.status === 401 && this.authRequired) {
                this.authenticated = false;
                this.showAuthOverlay();
                return null;
            }
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            if (error.name === 'AbortError') {
                console.warn(`Request timeout for ${endpoint}`);
                return null;
            }
            console.error(`Error fetching ${endpoint}:`, error);
            return null;
        } finally {
            clearTimeout(timeoutId);
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

    async loadOuiRanges() {
        if (this.currentUserRole !== 'admin') {
            return;
        }
        try {
            const response = await this.fetchData('/api/oui_ranges', { timeoutMs: 10000 });
            if (!response) {
                return;
            }
            this.ouiRangesText = response.content || '';
            const textarea = document.getElementById('ouiRangesText');
            if (textarea) {
                textarea.value = this.ouiRangesText;
            }
        } catch (error) {
            console.error('Error loading OUI ranges:', error);
        }
    }

    async saveOuiRanges() {
        if (this.currentUserRole !== 'admin') {
            return;
        }
        const textarea = document.getElementById('ouiRangesText');
        const content = textarea ? textarea.value : '';
        try {
            const response = await fetch('/api/oui_ranges', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to save OUI ranges');
                return;
            }
            this.ouiRangesText = content;
            this.showMessage('OUI ranges saved');
        } catch (error) {
            console.error('Error saving OUI ranges:', error);
            this.showError('Failed to save OUI ranges');
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
            const sortedSites = this.sortSites(this.sites, 'dashboardSites');
            sitesBody.innerHTML = sortedSites.map(site => {
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
        
        this.applySortIndicators('dashboardSites');
        replaceIcons();
    }

    updateSitesTab() {
        const sitesBody = document.getElementById('sitesManagementBody');
        if (this.sites && this.sites.length > 0) {
            const sortedSites = this.sortSites(this.sites, 'sites');
            sitesBody.innerHTML = sortedSites.map(site => {
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
        
        this.applySortIndicators('sites');
        replaceIcons();
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
        const sortedDevices = this.sortDevices(filteredDevices);
        
        // Update devices table
        const devicesBody = document.getElementById('devicesTableBody');
        if (sortedDevices.length > 0) {
            devicesBody.innerHTML = sortedDevices.map(device => {
                const checked = this.selectedDeviceIds.has(device.id) ? 'checked' : '';
                return `
                    <tr>
                        <td>
                            <input type="checkbox" class="device-select" data-device-id="${device.id}" ${checked}>
                        </td>
                        <td>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <i data-feather="server" style="width: 16px; height: 16px;"></i>
                                <strong>${device.name || device.id}</strong>
                                ${device.locked ? '<i data-feather="lock" style="width: 12px; height: 12px; color: var(--warning);"></i>' : ''}
                            </div>
                        </td>
                        <td>${device.ip || 'N/A'}</td>
                        <td>${device.mac || 'N/A'}</td>
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
                                <button class="btn-icon" title="Edit OUI" onclick="platform.showOuiModal('${device.id}')">
                                    <i data-feather="tag"></i>
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
                    <td colspan="10" class="empty-state">
                        <div style="padding: 32px; text-align: center;">
                            <i data-feather="server" style="width: 48px; height: 48px;"></i>
                            <h3 style="margin: 16px 0 8px;">${message}</h3>
                            ${!filterSite ? '<p style="color: var(--text-secondary); margin-bottom: 16px;">Use discovery modules to find devices</p>' : ''}
                        </div>
                    </td>
                </tr>
            `;
        }

        devicesBody.querySelectorAll('.device-select').forEach(input => {
            input.addEventListener('change', (event) => {
                const id = event.target.dataset.deviceId;
                this.toggleDeviceSelection(id, event.target.checked);
            });
        });
        this.syncSelectAllCheckbox(sortedDevices);
        this.applySortIndicators('devices');

        replaceIcons();
    }

    bindSortHeaders() {
        document.querySelectorAll('th.sortable').forEach(th => {
            th.addEventListener('click', () => {
                const target = th.dataset.sortTarget;
                const key = th.dataset.sortKey;
                if (!target || !key) return;
                const state = this.sortState[target] || { key, dir: 'asc' };
                const nextDir = state.key === key && state.dir === 'asc' ? 'desc' : 'asc';
                this.sortState[target] = { key, dir: nextDir };
                if (target === 'dashboardSites') {
                    this.updateDashboard();
                } else if (target === 'sites') {
                    this.updateSitesTab();
                } else if (target === 'devices') {
                    this.updateDevicesTab();
                }
            });
        });
    }

    applySortIndicators(target) {
        document.querySelectorAll(`th.sortable[data-sort-target="${target}"]`).forEach(th => {
            const state = this.sortState[target];
            if (state && th.dataset.sortKey === state.key) {
                th.dataset.sortDir = state.dir;
            } else {
                th.removeAttribute('data-sort-dir');
            }
        });
    }

    sortSites(sites, target) {
        const state = this.sortState[target] || { key: 'name', dir: 'asc' };
        const dir = state.dir === 'desc' ? -1 : 1;
        return [...sites].sort((a, b) => {
            let valA = '';
            let valB = '';
            if (state.key === 'devices') {
                valA = this.devices.filter(d => d.site === a.name).length;
                valB = this.devices.filter(d => d.site === b.name).length;
            } else if (state.key === 'status') {
                valA = a.locked ? 'locked' : 'active';
                valB = b.locked ? 'locked' : 'active';
            } else {
                valA = a[state.key] ?? '';
                valB = b[state.key] ?? '';
            }
            return this.compareValues(valA, valB) * dir;
        });
    }

    sortDevices(devices) {
        const state = this.sortState.devices || { key: 'name', dir: 'asc' };
        const dir = state.dir === 'desc' ? -1 : 1;
        return [...devices].sort((a, b) => {
            let valA = a[state.key] ?? '';
            let valB = b[state.key] ?? '';
            if (state.key === 'discovered_at' || state.key === 'last_seen') {
                valA = valA ? Date.parse(valA) : 0;
                valB = valB ? Date.parse(valB) : 0;
            }
            return this.compareValues(valA, valB) * dir;
        });
    }

    compareValues(a, b) {
        if (typeof a === 'number' && typeof b === 'number') {
            return a - b;
        }
        return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
    }

    toggleDeviceSelection(deviceId, selected) {
        if (!deviceId) return;
        if (selected) {
            this.selectedDeviceIds.add(deviceId);
        } else {
            this.selectedDeviceIds.delete(deviceId);
        }
        this.updateSelectedDevicesUI();
    }

    toggleSelectAllDevices(checked) {
        const devicesBody = document.getElementById('devicesTableBody');
        if (!devicesBody) return;
        devicesBody.querySelectorAll('.device-select').forEach(input => {
            input.checked = checked;
            const id = input.dataset.deviceId;
            if (checked) {
                this.selectedDeviceIds.add(id);
            } else {
                this.selectedDeviceIds.delete(id);
            }
        });
        this.updateSelectedDevicesUI();
    }

    syncSelectAllCheckbox(currentDevices) {
        const selectAll = document.getElementById('devicesSelectAll');
        if (!selectAll) return;
        if (!currentDevices.length) {
            selectAll.checked = false;
            selectAll.indeterminate = false;
            return;
        }
        const visibleIds = new Set(currentDevices.map(d => d.id));
        const selectedVisible = [...visibleIds].filter(id => this.selectedDeviceIds.has(id)).length;
        selectAll.checked = selectedVisible === visibleIds.size;
        selectAll.indeterminate = selectedVisible > 0 && selectedVisible < visibleIds.size;
        this.updateSelectedDevicesUI();
    }

    updateSelectedDevicesUI() {
        const btn = document.getElementById('deleteSelectedDevicesBtn');
        if (!btn) return;
        const count = this.selectedDeviceIds.size;
        btn.disabled = count === 0;
        btn.innerHTML = `<i data-feather="trash-2"></i> Remove Selected${count ? ` (${count})` : ''}`;
        replaceIcons();
    }

    async deleteSelectedDevices() {
        const ids = Array.from(this.selectedDeviceIds);
        if (!ids.length) return;
        if (!confirm(`Delete ${ids.length} devices?`)) {
            return;
        }
        for (const id of ids) {
            try {
                await fetch(`/api/devices/${id}`, { method: 'DELETE' });
            } catch (error) {
                console.error('Delete failed:', error);
            }
        }
        this.selectedDeviceIds.clear();
        this.updateSelectedDevicesUI();
        this.loadData();
    }

    normalizeMac(mac) {
        if (!mac) return '';
        return mac.trim().replace(/-/g, ':').toUpperCase();
    }

    isUbiquitiDevice(device) {
        const vendor = (device.vendor || '').toLowerCase();
        const platform = (device.platform || '').toLowerCase();
        const dtype = (device.type || '').toLowerCase();
        if (dtype === 'nvr') {
            return false;
        }
        return vendor.includes('ubiquiti') ||
            vendor.includes('apunifi') ||
            platform.includes('ubiquiti') ||
            platform.includes('apunifi') ||
            dtype === 'ap';
    }

    fillOuiFromMac() {
        const macInput = document.getElementById('ouiDeviceMac') || document.getElementById('editDeviceMac');
        const startInput = document.getElementById('editDeviceOuiStart');
        const endInput = document.getElementById('editDeviceOuiEnd');
        if (!macInput || !startInput || !endInput) return;
        const mac = this.normalizeMac(macInput.value);
        const parts = mac.split(':');
        if (parts.length < 3) {
            this.showError('MAC must include at least 3 octets');
            return;
        }
        const prefix = parts.slice(0, 3).join(':');
        startInput.value = `${prefix}:00:00:00`;
        endInput.value = `${prefix}:FF:FF:FF`;
    }

    async addOuiRangeFromModal() {
        const labelInput = document.getElementById('editDeviceOuiLabel');
        const startInput = document.getElementById('editDeviceOuiStart');
        const endInput = document.getElementById('editDeviceOuiEnd');
        if (!labelInput || !startInput || !endInput) return;
        const label = labelInput.value.trim();
        const start = this.normalizeMac(startInput.value);
        const end = this.normalizeMac(endInput.value);
        if (!label || !start || !end) {
            this.showError('OUI label and range are required');
            return;
        }
        try {
            const response = await fetch('/api/oui_ranges');
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to load OUI ranges');
                return;
            }
            const current = data.content || '';
            const line = `${start}-${end}=${label}`;
            if (current.toLowerCase().includes(line.toLowerCase())) {
                this.showMessage('OUI range already exists');
                return;
            }
            const content = current ? `${current.trim()}\n${line}\n` : `${line}\n`;
            const saveResp = await fetch('/api/oui_ranges', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });
            const saveData = await saveResp.json();
            if (!saveResp.ok) {
                this.showError(saveData.error || 'Failed to save OUI range');
                return;
            }
            this.showMessage('OUI range added');
            this.ouiRangesText = content;
        } catch (error) {
            console.error('Add OUI range error:', error);
            this.showError('Failed to add OUI range');
        }
    }

    showOuiModal(deviceId) {
        const device = this.devices.find(d => d.id === deviceId);
        if (!device) {
            this.showError('Device not found');
            return;
        }
        const modal = document.getElementById('editOuiModal');
        if (!modal) return;
        document.getElementById('ouiDeviceMac').value = device.mac || '';
        document.getElementById('editDeviceOuiLabel').value = device.oui_label || device.vendor || '';
        document.getElementById('editDeviceOuiStart').value = device.oui_range_start || '';
        document.getElementById('editDeviceOuiEnd').value = device.oui_range_end || '';
        modal.dataset.deviceId = deviceId;
        const addOuiBtn = document.getElementById('addOuiRangeBtn');
        if (addOuiBtn) {
            addOuiBtn.disabled = this.currentUserRole !== 'admin';
        }
        modal.classList.add('active');
    }

    async saveDeviceOui() {
        const modal = document.getElementById('editOuiModal');
        if (!modal) return;
        const deviceId = modal.dataset.deviceId;
        if (!deviceId) return;
        const updates = {
            oui_label: document.getElementById('editDeviceOuiLabel').value.trim(),
            oui_range_start: document.getElementById('editDeviceOuiStart').value.trim(),
            oui_range_end: document.getElementById('editDeviceOuiEnd').value.trim()
        };
        try {
            const response = await fetch(`/api/devices/${deviceId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });
            if (!response.ok) {
                throw new Error('Failed to update OUI');
            }
            this.closeAllModals();
            this.showMessage('OUI updated');
            this.loadData();
        } catch (error) {
            console.error('OUI update error:', error);
            this.showError('Failed to update OUI');
        }
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
        
        replaceIcons();
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
        const authEnabled = document.getElementById('authEnabled');
        if (authEnabled) {
            authEnabled.checked = !!(this.settings.auth && this.settings.auth.enabled);
        }
        const usersSection = document.getElementById('usersSection');
        if (usersSection) {
            usersSection.style.display = this.currentUserRole === 'admin' ? 'block' : 'none';
        }
        const adminAuthSection = document.getElementById('adminAuthSection');
        if (adminAuthSection) {
            adminAuthSection.style.display = this.currentUserRole === 'admin' ? 'block' : 'none';
        }
        const dataTransferSection = document.getElementById('dataTransferSection');
        if (dataTransferSection) {
            dataTransferSection.style.display = this.currentUserRole === 'admin' ? 'block' : 'none';
        }
        const ouiRangesSection = document.getElementById('ouiRangesSection');
        if (ouiRangesSection) {
            ouiRangesSection.style.display = this.currentUserRole === 'admin' ? 'block' : 'none';
            const textarea = document.getElementById('ouiRangesText');
            if (textarea && this.ouiRangesText) {
                textarea.value = this.ouiRangesText;
            }
        }
        if (this.currentUserRole === 'admin') {
            const addSites = document.getElementById('addUserSites');
            if (addSites) {
                this.renderSiteMultiSelect(addSites, [], (this.sites || []).map(site => site.name));
            }
        }
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

    showModuleForm(module, prefill = {}) {
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
        } else if (input.type === 'device_select') {
            const siteDevices = (this.devices || []).filter(d => !this.currentSite || d.site === this.currentSite);
            const options = siteDevices.map(d => {
                const ip = d.ip ? ` (${d.ip})` : '';
                const dtype = d.type ? ` [${d.type}]` : '';
                return `<option value="${d.id}">${d.name}${ip}${dtype}</option>`;
            }).join('');
            formHTML += `
                <div class="form-group">
                    <label for="module_${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                    <select id="module_${input.name}" ${input.required ? 'required' : ''}>
                        <option value="">Select device</option>
                        ${options}
                    </select>
                </div>
            `;
        } else if (input.type === 'device_table') {
            const siteDevices = (this.devices || []).filter(d => !this.currentSite || d.site === this.currentSite);
            const rows = siteDevices.map(d => {
                const checked = module.id === 'ubiquiti_cdp_reader' && this.isUbiquitiDevice(d) ? 'checked' : '';
                const vendor = d.vendor || d.platform || '';
                return `
                    <tr>
                        <td><input type="checkbox" class="module-device-select" data-device-id="${d.id}" ${checked}></td>
                        <td>${d.name || d.id}</td>
                        <td>${d.ip || ''}</td>
                        <td>${vendor}</td>
                        <td>${d.type || ''}</td>
                    </tr>
                `;
            }).join('');
            formHTML += `
                <div class="form-group">
                    <label>${input.label} ${input.required ? '*' : ''}</label>
                    <div class="table-container" style="padding: 0;">
                        <table class="data-table" style="min-width: 520px;">
                            <thead>
                                <tr>
                                    <th style="width: 36px;"></th>
                                    <th>Name</th>
                                    <th>IP</th>
                                    <th>Vendor</th>
                                    <th>Type</th>
                                </tr>
                            </thead>
                            <tbody id="module_${input.name}_table">
                                ${rows || '<tr><td colspan="5">No devices in this site.</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                    <div class="form-group" style="margin-top: 12px; display: grid; grid-template-columns: 1fr 1fr auto; gap: 8px;">
                        <input type="text" id="module_${input.name}_manual_name" placeholder="Device name">
                        <input type="text" id="module_${input.name}_manual_ip" placeholder="IP address">
                        <button class="btn btn-secondary" type="button" id="module_${input.name}_manual_add">Add</button>
                    </div>
                </div>
            `;
        } else if (input.type === 'checkbox') {
            formHTML += `
                <div class="form-group">
                    <label class="checkbox-label">
                        <input type="checkbox"
                               id="module_${input.name}"
                               ${input.default ? 'checked' : ''}>
                        <span>${input.label}</span>
                    </label>
                </div>
            `;
        } else if (input.type === 'textarea') {
            formHTML += `
                <div class="form-group">
                    <label for="module_${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                    <textarea id="module_${input.name}"
                              placeholder="${input.placeholder || ''}"
                              ${input.required ? 'required' : ''}
                              rows="4">${input.default || ''}</textarea>
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
          Object.entries(prefill || {}).forEach(([key, value]) => {
              const element = document.getElementById(`module_${key}`);
              if (!element) {
                  return;
              }
              if (element.type === 'checkbox') {
                  element.checked = Boolean(value);
              } else {
                  element.value = value;
              }
          });

        if (module.inputs && module.inputs.length > 0) {
            module.inputs.forEach(input => {
                if (input.type !== 'device_table') {
                    return;
                }
                const addBtn = document.getElementById(`module_${input.name}_manual_add`);
                const nameInput = document.getElementById(`module_${input.name}_manual_name`);
                const ipInput = document.getElementById(`module_${input.name}_manual_ip`);
                const tableBody = document.getElementById(`module_${input.name}_table`);
                if (!addBtn || !nameInput || !ipInput || !tableBody) {
                    return;
                }
                addBtn.addEventListener('click', () => {
                    const name = nameInput.value.trim();
                    const ip = ipInput.value.trim();
                    if (!name || !ip) {
                        this.showError('Manual device name and IP are required');
                        return;
                    }
                    const row = document.createElement('tr');
                    row.dataset.manual = 'true';
                    row.dataset.name = name;
                    row.dataset.ip = ip;
                    row.innerHTML = `
                        <td><input type="checkbox" class="module-device-select" checked></td>
                        <td>${name}</td>
                        <td>${ip}</td>
                        <td>manual</td>
                        <td><button type="button" class="btn btn-secondary">Remove</button></td>
                    `;
                    row.querySelector('button')?.addEventListener('click', () => {
                        row.remove();
                    });
                    tableBody.appendChild(row);
                    nameInput.value = '';
                    ipInput.value = '';
                });
            });
        }
        
        // Show modal
        modal.classList.add('active');
        const logWrap = document.getElementById('moduleLogOutput');
        if (logWrap) {
            logWrap.style.display = 'none';
            const textarea = logWrap.querySelector('textarea');
            if (textarea) {
                textarea.value = '';
            }
        }
        
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
            if (input.type === 'device_table') {
                const tableBody = document.getElementById(`module_${input.name}_table`);
                if (!tableBody) {
                    return;
                }
                const deviceIds = [];
                const manualDevices = [];
                tableBody.querySelectorAll('tr').forEach(row => {
                    const checkbox = row.querySelector('.module-device-select');
                    if (!checkbox || !checkbox.checked) {
                        return;
                    }
                    if (row.dataset.manual === 'true') {
                        manualDevices.push({
                            name: row.dataset.name || '',
                            ip: row.dataset.ip || ''
                        });
                    } else {
                        const id = checkbox.dataset.deviceId;
                        if (id) {
                            deviceIds.push(id);
                        }
                    }
                });
                if (input.required && deviceIds.length === 0 && manualDevices.length === 0) {
                    isValid = false;
                } else {
                    inputs[input.name] = { device_ids: deviceIds, manual_devices: manualDevices };
                }
                return;
            }
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
            if (statusDisplay) {
                statusDisplay.dataset.logThread = threadId;
            }
            
            if (statusDisplay.style.display === 'block') {
                if (status.progress) {
                    statusDisplay.querySelector('.progress-fill').style.width = `${status.progress}%`;
                }
                
                if (status.status === 'completed' || status.status === 'failed' || status.status === 'error') {
                    this.updateModuleLog(threadId, true);
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
            this.updateModuleLog(threadId, false);
            
        } catch (error) {
            console.error('Error updating module status:', error);
        }
    }

    async updateModuleLog(threadId, deleteAfter) {
        const logWrap = document.getElementById('moduleLogOutput');
        if (!logWrap) return;
        const textarea = logWrap.querySelector('textarea');
        if (!textarea) return;
        const statusDisplay = document.getElementById('moduleStatusDisplay');
        const threadInfo = this.activeModuleThreads.get(threadId);
        if (!threadInfo && (!statusDisplay || statusDisplay.dataset.logThread !== threadId)) {
            logWrap.style.display = 'none';
            return;
        }
        try {
            const query = deleteAfter ? '?delete=1' : '';
            const response = await fetch(`/api/modules/log/${threadId}${query}`);
            const data = await response.json();
            if (!response.ok) {
                logWrap.style.display = 'none';
                return;
            }
            const lines = data.lines || [];
            if (!lines.length) {
                logWrap.style.display = 'none';
                return;
            }
            logWrap.style.display = 'block';
            textarea.value = lines.join('\n');
            textarea.scrollTop = textarea.scrollHeight;
        } catch (error) {
            logWrap.style.display = 'none';
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
        
        replaceIcons();
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
        document.getElementById('editDeviceVlan').value = device.vlan || '';
        document.getElementById('editDeviceMac').value = device.mac || '';
        document.getElementById('editDeviceType').value = device.type || 'router';
        document.getElementById('editDeviceStatus').value = device.status || 'unknown';
        document.getElementById('editDeviceNotes').value = device.notes || '';
        document.getElementById('editDeviceLocked').checked = device.locked || false;
        document.getElementById('editDeviceAlwaysShowMap').checked = device.always_show_on_map || false;
        document.getElementById('editDeviceOS').value = device.os || '';
        document.getElementById('editDeviceVendor').value = device.vendor || '';
        document.getElementById('editDevicePlatform').value = device.platform || device.model || '';
        document.getElementById('editCreateMissingNodes').checked = true;
        this.renderConnectionRows(device);
        
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
            vlan: document.getElementById('editDeviceVlan').value.trim(),
            mac: document.getElementById('editDeviceMac').value.trim(),
            type: document.getElementById('editDeviceType').value,
            os: document.getElementById('editDeviceOS').value.trim(),
            vendor: document.getElementById('editDeviceVendor').value.trim(),
            platform: document.getElementById('editDevicePlatform').value.trim(),
            status: document.getElementById('editDeviceStatus').value,
            notes: document.getElementById('editDeviceNotes').value.trim(),
            locked: document.getElementById('editDeviceLocked').checked,
            always_show_on_map: document.getElementById('editDeviceAlwaysShowMap').checked,
            connections_list: this.collectConnectionRows(),
            create_missing_nodes: document.getElementById('editCreateMissingNodes').checked
        };
        
        if (!updates.name) {
            this.showError('Device name is required');
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

    renderConnectionRows(device) {
        const wrap = document.getElementById('editConnectionsWrap');
        if (!wrap) {
            return;
        }
        wrap.innerHTML = '';
        const connections = device.connections || [];
        if (!connections.length) {
            this.addConnectionRow();
            return;
        }
        connections.forEach(conn => {
            this.addConnectionRow({
                local_interface: conn.local_interface || '',
                remote_device: conn.remote_device || '',
                remote_interface: conn.remote_interface || '',
                protocol: conn.protocol || 'manual'
            });
        });
    }

    addConnectionRow(prefill = {}) {
        const wrap = document.getElementById('editConnectionsWrap');
        if (!wrap) {
            return;
        }
        const row = document.createElement('div');
        row.className = 'connection-row';
        const siteDevices = (this.devices || []).filter(d => !this.currentSite || d.site === this.currentSite);
        const options = siteDevices.map(d => {
            const ip = d.ip ? ` (${d.ip})` : '';
            const dtype = d.type ? ` [${d.type}]` : '';
            return `<option value="${d.id}">${d.name}${ip}${dtype}</option>`;
        }).join('');
        row.innerHTML = `
            <input type="text" class="conn-local" placeholder="Local interface" value="${prefill.local_interface || ''}">
            <select class="conn-remote">
                <option value="">Select device</option>
                ${options}
            </select>
            <input type="text" class="conn-remote-intf" placeholder="Remote interface" value="${prefill.remote_interface || ''}">
            <select class="conn-proto">
                ${['manual','cdp','lldp','snmp','other'].map(p => `<option value="${p}">${p}</option>`).join('')}
            </select>
            <button type="button" class="btn btn-secondary conn-remove">Remove</button>
        `;
        const remoteSelect = row.querySelector('.conn-remote');
        remoteSelect.value = prefill.remote_device || '';
        const protoSelect = row.querySelector('.conn-proto');
        protoSelect.value = prefill.protocol || 'manual';
        row.querySelector('.conn-remove').addEventListener('click', () => {
            row.remove();
        });
        wrap.appendChild(row);
    }

    collectConnectionRows() {
        const wrap = document.getElementById('editConnectionsWrap');
        if (!wrap) {
            return [];
        }
        const rows = Array.from(wrap.querySelectorAll('.connection-row'));
        return rows.map(row => {
            return {
                local_interface: row.querySelector('.conn-local')?.value.trim() || '',
                remote_device_id: row.querySelector('.conn-remote')?.value || '',
                remote_interface: row.querySelector('.conn-remote-intf')?.value.trim() || '',
                protocol: row.querySelector('.conn-proto')?.value || 'manual'
            };
        }).filter(entry => entry.remote_device_id);
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
                this.selectedDeviceIds.delete(deviceId);
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
            const authEnabled = document.getElementById('authEnabled');
            if (authEnabled && this.currentUserRole === 'admin') {
                await this.updateAuthConfig(authEnabled.checked);
            }
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

    async updateAuthConfig(enabled) {
        try {
            const response = await fetch('/api/auth/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: !!enabled })
            });
            if (!response.ok) {
                throw new Error('Failed to update auth config');
            }
            if (this.settings.auth) {
                this.settings.auth.enabled = !!enabled;
            }
        } catch (error) {
            console.error('Error updating auth config:', error);
        }
    }

    async loadUsers() {
        if (this.currentUserRole !== 'admin') {
            return;
        }
        const users = await this.fetchData('/api/users');
        this.users = users || [];
        this.renderUsers();
    }

    renderUsers() {
        const section = document.getElementById('usersSection');
        const tableBody = document.getElementById('usersTableBody');
        if (!section || !tableBody) {
            return;
        }
        section.style.display = this.currentUserRole === 'admin' ? 'block' : 'none';
        if (this.currentUserRole !== 'admin') {
            return;
        }
        const siteNames = (this.sites || []).map(site => site.name);
        tableBody.innerHTML = this.users.map(user => {
            const allowed = (user.allowed_sites || []).join('|');
            const disabled = user.disabled ? 'checked' : '';
            return `
                <tr data-username="${user.username}">
                    <td>${user.username}</td>
                    <td>
                        <select class="user-role">
                            <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>admin</option>
                            <option value="operator" ${user.role === 'operator' ? 'selected' : ''}>operator</option>
                            <option value="guest" ${user.role === 'guest' ? 'selected' : ''}>guest</option>
                        </select>
                    </td>
                    <td>
                        <div class="multi-select user-sites" data-sites="${allowed}">
                            <button class="btn btn-secondary multi-select-toggle" type="button">
                                Select sites
                                <span>▾</span>
                            </button>
                            <div class="multi-select-panel"></div>
                        </div>
                    </td>
                    <td style="text-align: center;">
                        <input type="checkbox" class="user-disabled" ${disabled}>
                    </td>
                    <td>
                        <input type="password" class="user-new-pass" placeholder="New password">
                    </td>
                    <td style="display: flex; gap: 8px;">
                        <button class="btn btn-primary btn-sm user-save">Save</button>
                        <button class="btn btn-secondary btn-sm user-delete">Remove</button>
                    </td>
                </tr>
            `;
        }).join('');

        tableBody.querySelectorAll('.user-sites').forEach(container => {
            const selected = (container.dataset.sites || '').split('|').filter(Boolean);
            this.renderSiteMultiSelect(container, selected, siteNames);
        });

        tableBody.querySelectorAll('.user-save').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const row = e.target.closest('tr');
                const username = row?.dataset.username;
                if (!username) return;
                const role = row.querySelector('.user-role')?.value || 'guest';
                const allowed_sites = this.collectSelectedSites(row.querySelector('.user-sites'));
                const disabled = row.querySelector('.user-disabled')?.checked || false;
                const password = row.querySelector('.user-new-pass')?.value || '';
                await this.updateUser(username, { role, allowed_sites, disabled, password });
                row.querySelector('.user-new-pass').value = '';
            });
        });

        tableBody.querySelectorAll('.user-delete').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const row = e.target.closest('tr');
                const username = row?.dataset.username;
                if (!username) return;
                if (!confirm(`Remove user "${username}"?`)) {
                    return;
                }
                await this.deleteUser(username);
            });
        });
    }

    async addUser() {
        const username = document.getElementById('addUserName')?.value.trim() || '';
        const password = document.getElementById('addUserPassword')?.value || '';
        const role = document.getElementById('addUserRole')?.value || 'guest';
        const allowed_sites = this.collectSelectedSites(document.getElementById('addUserSites'));
        if (!username || !password) {
            this.showError('Username and password are required');
            return;
        }
        try {
            const response = await fetch('/api/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password, role, allowed_sites })
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to add user');
                return;
            }
            document.getElementById('addUserName').value = '';
            document.getElementById('addUserPassword').value = '';
            const addSites = document.getElementById('addUserSites');
            if (addSites) {
                this.renderSiteMultiSelect(addSites, [], (this.sites || []).map(site => site.name));
            }
            await this.loadUsers();
            this.showMessage('User added');
        } catch (error) {
            console.error('Error adding user:', error);
            this.showError('Failed to add user');
        }
    }

    async updateUser(username, payload) {
        try {
            const response = await fetch(`/api/users/${encodeURIComponent(username)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to update user');
                return;
            }
            await this.loadUsers();
            this.showMessage('User updated');
        } catch (error) {
            console.error('Error updating user:', error);
            this.showError('Failed to update user');
        }
    }

    async deleteUser(username) {
        try {
            const response = await fetch(`/api/users/${encodeURIComponent(username)}`, {
                method: 'DELETE'
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to remove user');
                return;
            }
            await this.loadUsers();
            this.showMessage('User removed');
        } catch (error) {
            console.error('Error removing user:', error);
            this.showError('Failed to remove user');
        }
    }

    async changePassword() {
        const currentPassword = document.getElementById('currentPassword')?.value || '';
        const newPassword = document.getElementById('newPassword')?.value || '';
        if (!currentPassword || !newPassword) {
            this.showError('Current and new password are required');
            return;
        }
        try {
            const response = await fetch('/api/auth/change_password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to change password');
                return;
            }
            document.getElementById('currentPassword').value = '';
            document.getElementById('newPassword').value = '';
            this.showMessage('Password updated');
        } catch (error) {
            console.error('Error changing password:', error);
            this.showError('Failed to change password');
        }
    }

    async loadMonitoringForSite(siteName) {
        const board = document.getElementById('monitoringBoard');
        if (!board) return;
        board.querySelectorAll('.monitoring-zone').forEach(zone => {
            const content = zone.querySelector('.monitoring-zone-content');
            if (content) {
                content.innerHTML = '';
            }
        });
        const data = await this.fetchData(`/api/monitoring/site/${encodeURIComponent(siteName)}`, { timeoutMs: 8000 });
        if (!data) {
            const center = board.querySelector('[data-dock="center"]');
            if (center) {
                center.innerHTML = `
                    <div class="empty-state" style="text-align: center; padding: 24px;">
                        <i data-feather="alert-circle" style="width: 32px; height: 32px; margin-bottom: 12px;"></i>
                        <h3 style="margin-bottom: 8px;">No Data</h3>
                        <p style="color: var(--text-secondary); margin-bottom: 12px;">
                            Run the ping monitor module to populate status.
                        </p>
                    </div>
                `;
                replaceIcons();
            }
            return;
        }
        this.monitoringData = data;
        this.monitoringLayout = data.layout || null;
        this.applyMonitoringLayout();
        this.bindMonitoringLayoutHandlers();
        this.renderMonitoringNodes();
        this.renderMonitoringDeviceList();
        this.renderMonitoringLogs(siteName);
        this.startMonitoringAutoRefresh(siteName);
        this.updateMonitoringSelection();
        this.bindMonitoringResize();
    }

    renderMonitoringNodes() {
        const board = document.getElementById('monitoringBoard');
        if (!board) return;
        const zones = {
            top: board.querySelector('[data-dock="top"] .monitoring-zone-content'),
            left: board.querySelector('[data-dock="left"] .monitoring-zone-content'),
            center: board.querySelector('[data-dock="center"] .monitoring-zone-content'),
            right: board.querySelector('[data-dock="right"] .monitoring-zone-content'),
            bottom: board.querySelector('[data-dock="bottom"] .monitoring-zone-content'),
            none: board.querySelector('[data-dock="none"]'),
        };
        Object.values(zones).forEach(zone => {
            if (zone && zone !== zones.none) {
                zone.innerHTML = '';
            }
        });
        const devices = (this.monitoringData?.devices || []).filter(d => d.placed);
        if (!devices.length) {
            const center = zones.center;
            if (center) {
                center.innerHTML = `
                    <div class="empty-state" style="text-align: center; padding: 24px;">
                        <i data-feather="activity" style="width: 32px; height: 32px; margin-bottom: 12px;"></i>
                        <h3 style="margin-bottom: 8px;">Drop devices here</h3>
                        <p style="color: var(--text-secondary); margin-bottom: 12px;">
                            Drag from the Devices list to build your monitoring layout.
                        </p>
                    </div>
                `;
                replaceIcons();
            }
        }
        devices.forEach(device => {
            const status = device.status || 'unknown';
            const statusClass = status === 'ok' ? '' : status === 'not_ok' ? 'status-bad' : 'status-unknown';
            const loss = device.packet_loss != null ? `${device.packet_loss}% loss` : 'Loss: n/a';
            const latency = device.avg_latency_ms != null ? `${device.avg_latency_ms} ms` : 'Latency: n/a';
            const last = device.last_check ? this.formatTime(device.last_check) : 'Never';
            const enabled = device.enabled ? 'Monitoring: on' : 'Monitoring: off';
            const selected = device.id === this.monitoringSelectedDeviceId ? 'selected' : '';

            const node = document.createElement('div');
            node.className = `monitoring-node ${statusClass} ${selected}`;
            node.dataset.deviceId = device.id;
            node.draggable = true;
            node.title = `${device.name || device.id} | ${loss} | ${latency} | ${enabled} | Last: ${last}`;
            node.textContent = device.name || device.id || 'Unknown';
            node.addEventListener('click', () => {
                this.monitoringSelectedDeviceId = device.id;
                this.renderMonitoringNodes();
                this.updateMonitoringSelection();
            });
            node.addEventListener('dragstart', (event) => {
                event.dataTransfer.setData('text/plain', device.id);
            });

            const dock = (device.dock || 'center').toLowerCase();
            const target = zones[dock] || zones.center;
            if (target) {
                target.appendChild(node);
            }
        });

        board.querySelectorAll('.monitoring-zone').forEach(zone => {
            if (zone.dataset.bound === 'true') {
                return;
            }
            zone.dataset.bound = 'true';
            zone.addEventListener('dragover', (event) => {
                event.preventDefault();
            });
            zone.addEventListener('drop', (event) => {
                event.preventDefault();
                const deviceId = event.dataTransfer.getData('text/plain');
                const dock = zone.dataset.dock;
                if (deviceId && dock) {
                    this.updateMonitoringDock(deviceId, dock);
                }
            });
        });

        this.renderMonitoringLinks();
    }

    applyMonitoringLayout() {
        const board = document.getElementById('monitoringBoard');
        if (!board || !this.monitoringLayout) return;
        const layout = this.monitoringLayout;
        board.style.setProperty('--monitoring-top', `${layout.top || 90}px`);
        board.style.setProperty('--monitoring-bottom', `${layout.bottom || 90}px`);
        board.style.setProperty('--monitoring-left', `${layout.left || 120}px`);
        board.style.setProperty('--monitoring-right', `${layout.right || 120}px`);
        board.querySelectorAll('.monitoring-zone-label').forEach(label => {
            const zone = label.dataset.zone;
            const text = layout.labels?.[zone];
            if (text) {
                label.textContent = text;
            }
        });
    }

    bindMonitoringLayoutHandlers() {
        const board = document.getElementById('monitoringBoard');
        if (!board || board.dataset.layoutBound === 'true') {
            return;
        }
        board.dataset.layoutBound = 'true';
        board.querySelectorAll('.monitoring-zone-label').forEach(label => {
            label.addEventListener('blur', () => {
                const zone = label.dataset.zone;
                if (!zone || !this.monitoringLayout) return;
                const value = (label.textContent || '').trim();
                if (!value) return;
                this.monitoringLayout.labels = this.monitoringLayout.labels || {};
                this.monitoringLayout.labels[zone] = value;
                this.scheduleMonitoringLayoutSave();
            });
        });

    }

    scheduleMonitoringLayoutSave() {
        if (this.monitoringLayoutSaveTimer) {
            clearTimeout(this.monitoringLayoutSaveTimer);
        }
        this.monitoringLayoutSaveTimer = setTimeout(() => {
            this.saveMonitoringLayout();
        }, 500);
    }

    async saveMonitoringLayout() {
        const siteName = document.getElementById('monitoringSiteSelect').value;
        if (!siteName || !this.monitoringLayout) return;
        try {
            await fetch(`/api/monitoring/layout/${encodeURIComponent(siteName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ layout: this.monitoringLayout })
            });
        } catch (error) {
            console.error('Error saving monitoring layout:', error);
        }
    }

    async updateMonitoringDock(deviceId, dock) {
        const siteName = document.getElementById('monitoringSiteSelect').value;
        if (!siteName) return;
        const device = (this.monitoringData?.devices || []).find(d => d.id === deviceId);
        const nextPlaced = dock !== 'none';
        if (device) {
            const sameDock = (device.dock || 'center') === dock;
            if (device.placed === nextPlaced && sameDock) {
                return;
            }
        }
        try {
            const payload = dock === 'none' ? { placed: false } : { placed: true, dock };
            const response = await fetch(`/api/monitoring/device/${encodeURIComponent(siteName)}/${encodeURIComponent(deviceId)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to update dock');
                return;
            }
            const device = (this.monitoringData?.devices || []).find(d => d.id === deviceId);
            if (device) {
                device.dock = data.dock;
                device.placed = data.placed;
            }
            if (this.monitoringSelectedDeviceId === deviceId && dock === 'none') {
                this.monitoringSelectedDeviceId = '';
            }
            this.renderMonitoringNodes();
            this.renderMonitoringDeviceList();
            this.renderMonitoringLinks();
            this.updateMonitoringSelection();
        } catch (error) {
            console.error('Dock update error:', error);
            this.showError('Failed to update dock');
        }
    }

    bindMonitoringResize() {
        if (this.monitoringResizeBound) {
            return;
        }
        this.monitoringResizeBound = true;
        window.addEventListener('resize', () => {
            if (this.currentTab === 'monitoring') {
                this.renderMonitoringLinks();
            }
        });
    }

    renderMonitoringDeviceList() {
        const list = document.getElementById('monitoringDeviceList');
        if (!list) return;
        const siteName = document.getElementById('monitoringSiteSelect').value;
        const siteDevices = (this.devices || []).filter(d => d.site === siteName);
        const monitoringMap = new Map((this.monitoringData?.devices || []).map(d => [d.id, d]));
        const available = siteDevices.filter(device => {
            const entry = monitoringMap.get(device.id);
            return !entry || !entry.placed;
        });

        if (!available.length) {
            list.innerHTML = '<div class="meta">All devices are placed.</div>';
            return;
        }

        list.innerHTML = available.map(device => {
            const ip = device.ip ? ` ${device.ip}` : '';
            const hasLinks = (device.connections || []).length > 0;
            const tag = hasLinks ? '' : '<span class="tag">no links</span>';
            return `
                <div class="monitoring-device-item" draggable="true" data-device-id="${device.id}">
                    <span>${device.name || device.id}</span>
                    <span class="meta">${ip}</span>
                    ${tag}
                </div>
            `;
        }).join('');

        list.querySelectorAll('.monitoring-device-item').forEach(item => {
            item.addEventListener('dragstart', (event) => {
                event.dataTransfer.setData('text/plain', item.dataset.deviceId || '');
            });
        });
    }

    async renderMonitoringLogs(siteName) {
        const list = document.getElementById('monitoringLogList');
        if (!list) return;
        const data = await this.fetchData(`/api/monitoring/logs/${encodeURIComponent(siteName)}`, { timeoutMs: 8000 });
        const lines = data?.lines || [];
        if (!lines.length) {
            list.innerHTML = '<div class="monitoring-log-line">No activity yet.</div>';
            return;
        }
        list.innerHTML = lines.slice(-50).map(line => `<div class="monitoring-log-line">${line}</div>`).join('');
    }

    renderMonitoringLinks() {
        const svg = document.getElementById('monitoringLinks');
        const board = document.getElementById('monitoringBoard');
        if (!svg || !board) return;
        const placed = (this.monitoringData?.devices || []).filter(d => d.placed);
        const placedIds = new Set(placed.map(d => d.id));
        const siteName = document.getElementById('monitoringSiteSelect').value;
        const devices = (this.devices || []).filter(d => d.site === siteName);
        const idToElement = new Map();
        board.querySelectorAll('.monitoring-node').forEach(node => {
            idToElement.set(node.dataset.deviceId, node);
        });

        svg.innerHTML = '';
        const edges = new Set();
        devices.forEach(device => {
            const id = device.id;
            if (!placedIds.has(id)) return;
            (device.connections || []).forEach(conn => {
                const remote = conn.remote_device;
                if (!placedIds.has(remote)) return;
                const a = [id, remote].sort().join('|');
                if (edges.has(a)) return;
                edges.add(a);
                const elA = idToElement.get(id);
                const elB = idToElement.get(remote);
                if (!elA || !elB) return;
                const rectA = elA.getBoundingClientRect();
                const rectB = elB.getBoundingClientRect();
                const rectBoard = board.getBoundingClientRect();
                const x1 = rectA.left - rectBoard.left + rectA.width / 2;
                const y1 = rectA.top - rectBoard.top + rectA.height / 2;
                const x2 = rectB.left - rectBoard.left + rectB.width / 2;
                const y2 = rectB.top - rectBoard.top + rectB.height / 2;
                const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', x1);
                line.setAttribute('y1', y1);
                line.setAttribute('x2', x2);
                line.setAttribute('y2', y2);
                line.setAttribute('stroke', 'rgba(15, 23, 32, 0.25)');
                line.setAttribute('stroke-width', '1.2');
                svg.appendChild(line);
            });
        });
    }

    startMonitoringAutoRefresh(siteName) {
        if (this.monitoringSiteName === siteName && this.monitoringInterval) {
            return;
        }
        if (this.monitoringInterval) {
            clearInterval(this.monitoringInterval);
        }
        this.monitoringSiteName = siteName;
        this.monitoringInterval = setInterval(() => {
            if (this.currentTab === 'monitoring') {
                this.loadMonitoringForSite(siteName);
            }
        }, 5000);
    }

    openMonitoringRules(siteName, deviceId) {
        const modal = document.getElementById('monitoringRulesModal');
        if (!modal) return;
        const device = (this.monitoringData?.devices || []).find(d => d.id === deviceId);
        const rules = device?.rules || [];
        const lossRule = rules.find(rule => rule.type === 'loss');
        const latencyRule = rules.find(rule => rule.type === 'latency');
        document.getElementById('monitorLossThreshold').value = lossRule?.threshold ?? 100;
        document.getElementById('monitorLatencyThreshold').value = latencyRule?.threshold ?? 500;
        modal.classList.add('active');
    }

    async saveMonitoringRules(siteName) {
        if (!this.monitoringSelectedDeviceId) {
            this.showError('Select a node first');
            return;
        }
        const loss = parseInt(document.getElementById('monitorLossThreshold').value, 10);
        const latency = parseInt(document.getElementById('monitorLatencyThreshold').value, 10);
        if ([loss, latency].some(v => Number.isNaN(v))) {
            this.showError('All rule values must be numbers');
            return;
        }
        try {
            const response = await fetch(`/api/monitoring/rules/${encodeURIComponent(siteName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: this.monitoringSelectedDeviceId,
                    rules: [
                        { id: 'loss', type: 'loss', threshold: loss, enabled: true },
                        { id: 'latency', type: 'latency', threshold: latency, enabled: true }
                    ]
                })
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to save rules');
                return;
            }
            const device = (this.monitoringData?.devices || []).find(d => d.id === this.monitoringSelectedDeviceId);
            if (device) {
                device.rules = data.rules;
            }
            this.showMessage('Monitoring rules saved');
            this.closeAllModals();
            this.renderMonitoringNodes();
        } catch (error) {
            console.error('Error saving monitoring rules:', error);
            this.showError('Failed to save rules');
        }
    }


    exportData() {
        if (this.currentUserRole !== 'admin') {
            this.showError('Only admins can export data');
            return;
        }
        window.location.href = '/api/export';
    }

    async importData() {
        if (this.currentUserRole !== 'admin') {
            this.showError('Only admins can import data');
            return;
        }
        const fileInput = document.getElementById('importDataFile');
        const file = fileInput?.files?.[0];
        if (!file) {
            this.showError('Select a ZIP file to import');
            return;
        }
        const formData = new FormData();
        formData.append('file', file);
        try {
            const response = await fetch('/api/import', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Import failed');
                return;
            }
            this.showMessage('Import completed. Reloading...');
            window.location.reload();
        } catch (error) {
            console.error('Import error:', error);
            this.showError('Import failed');
        }
    }


    async toggleMonitoringDevice(siteName, deviceId) {
        const device = (this.monitoringData?.devices || []).find(d => d.id === deviceId);
        if (!device) return;
        const enabled = !device.enabled;
        try {
            const response = await fetch(`/api/monitoring/rules/${encodeURIComponent(siteName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: deviceId,
                    enabled: enabled,
                    rules: device.rules && device.rules.length ? device.rules : [{ id: 'loss', type: 'loss', threshold: 100, enabled: true }]
                })
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to update device');
                return;
            }
            device.enabled = data.enabled;
            device.rules = data.rules;
            this.renderMonitoringNodes();
            this.updateMonitoringSelection();
        } catch (error) {
            console.error('Error updating monitoring device:', error);
            this.showError('Failed to update device');
        }
    }

    renderSiteMultiSelect(container, selectedSites, siteNames) {
        if (!container) return;
        const panel = container.querySelector('.multi-select-panel');
        if (!panel) return;
        const selectedSet = new Set(selectedSites || []);
        const names = siteNames || [];
        if (!names.length) {
            panel.innerHTML = '<div class="multi-select-option">No sites available</div>';
            this.updateMultiSelectLabel(container);
            return;
        }
        const allChecked = selectedSet.has('*');
        const allOption = `
            <label class="multi-select-option">
                <input type="checkbox" value="*" ${allChecked ? 'checked' : ''}>
                <span>All sites</span>
            </label>
        `;
        const siteOptions = names.map(name => {
            const checked = selectedSet.has(name) ? 'checked' : '';
            return `
                <label class="multi-select-option">
                    <input type="checkbox" value="${name}" ${checked}>
                    <span>${name}</span>
                </label>
            `;
        }).join('');
        panel.innerHTML = allOption + siteOptions;
        panel.querySelectorAll('input[type="checkbox"]').forEach(input => {
            input.addEventListener('change', (event) => this.handleMultiSelectChange(container, event.target));
        });
        this.setupMultiSelectToggle(container);
        this.applyAllSitesState(container);
        this.updateMultiSelectLabel(container);
    }

    setupMultiSelectToggle(container) {
        const toggle = container.querySelector('.multi-select-toggle');
        if (!toggle || toggle.dataset.bound) return;
        toggle.dataset.bound = 'true';
        toggle.addEventListener('click', () => {
            container.classList.toggle('open');
        });
        document.addEventListener('click', (event) => {
            if (!container.contains(event.target)) {
                container.classList.remove('open');
            }
        });
    }

    updateMultiSelectLabel(container) {
        const toggle = container.querySelector('.multi-select-toggle');
        if (!toggle) return;
        const selected = this.collectSelectedSites(container);
        if (selected.includes('*')) {
            toggle.firstChild.textContent = 'All sites';
            return;
        }
        const count = selected.length;
        toggle.firstChild.textContent = count ? `Sites (${count})` : 'Select sites';
    }

    collectSelectedSites(container) {
        if (!container) return [];
        const selected = Array.from(container.querySelectorAll('input[type="checkbox"]:checked'))
            .map(input => input.value)
            .filter(Boolean);
        if (selected.includes('*')) {
            return ['*'];
        }
        return selected;
    }

    handleMultiSelectChange(container, target) {
        if (target.value === '*') {
            if (target.checked) {
                container.querySelectorAll('input[type="checkbox"]').forEach(input => {
                    if (input.value !== '*') {
                        input.checked = false;
                    }
                });
            }
        }
        this.applyAllSitesState(container);
        this.updateMultiSelectLabel(container);
    }

    applyAllSitesState(container) {
        const allChecked = container.querySelector('input[value="*"]')?.checked;
        container.querySelectorAll('input[type="checkbox"]').forEach(input => {
            if (input.value !== '*') {
                input.disabled = !!allChecked;
            }
        });
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
    if (tabName === 'monitoring') {
        this.updateMonitoringTab();
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
        if (this.backgroundUpdatesStarted) {
            return;
        }
        this.backgroundUpdatesStarted = true;
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
