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
        this.selectedDeviceIds = new Set();
        this.devicesPage = 1;
        this.devicesPageSize = 50;
        this.devicesPageFilter = '';
        this.devicesNetworkFilter = '';
        this.deviceColumnFilters = {};
        this.moduleLogCache = new Map();
        this.serverModuleJobs = [];
        this.dashboardReports = {};
        this.sortState = {
            dashboardSites: { key: 'name', dir: 'asc' },
            sites: { key: 'name', dir: 'asc' },
            devices: { key: 'name', dir: 'asc' },
            ouiRanges: { key: 'oui', dir: 'asc' }
        };
        this.ouiRangesText = '';
        this.ouiRangesEntries = [];
        this.moduleCredentials = {};
        this.moduleLastParams = {};
        this.moduleCredentialTargets = [
            { id: 'cdp_discovery', label: 'CDP Discovery' },
            { id: 'mikrotik_mac_discovery', label: 'MikroTik MAC Discovery' },
            { id: 'mikrotik_dhcp_backup', label: 'MikroTik DHCP Backup' },
            { id: 'ubiquiti_cdp_reader', label: 'Read CDP (Ubiquiti)' },
            { id: 'uniview_nvr_capture', label: 'Uniview NVR Packet Capture' },
            { id: 'uniview_device_type_check', label: 'Uniview Device Type Check' },
            { id: 'mac_table_search', label: 'MAC Search' },
            { id: 'mac_group_map', label: 'Map Group' }
        ];
        this.schedules = [];
        this.scheduleEditId = null;
        this.scheduleModuleEditRow = null;
        this.showCompletedJobs = false;
        this.agents = [];
        
        // ADD MAP-SPECIFIC PROPERTIES
        this.mapLoaded = false;
        this.currentMapSite = '';
        this.mapSelectedDeviceId = '';
        this.editingSiteId = null;
        this.currentTextMapUrl = '';
        
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
        const siteInput = document.getElementById('mapSiteSelect');
        if (!siteInput) return;

        const fallbackValue = this.currentMapSite || this.currentSite || '';
        if (!siteInput.value && this.isKnownMapSite(fallbackValue)) {
            siteInput.value = fallbackValue;
        }
        this.updateMapSiteOptions(siteInput.value);
        
        // Update Show Map button
        this.updateShowMapButton();
        this.updateMapControls();
        this.renderSiteMapStatusControls();
        this.currentTextMapUrl = '';
        const downloadBtn = document.getElementById('downloadTextMapBtn');
        if (downloadBtn) downloadBtn.disabled = true;
    }

    getMapSiteNames() {
        return (this.sites || [])
            .map(site => site && site.name ? site.name : '')
            .filter(Boolean);
    }

    isKnownMapSite(siteName) {
        if (!siteName) return false;
        return this.getMapSiteNames().includes(siteName);
    }

    getSelectedMapSite() {
        const siteInput = document.getElementById('mapSiteSelect');
        const value = (siteInput?.value || '').trim();
        return this.isKnownMapSite(value) ? value : '';
    }

    getMapActionSite() {
        const siteInput = document.getElementById('mapSiteSelect');
        const value = (siteInput?.value || '').trim();
        if (value) {
            return this.isKnownMapSite(value) ? value : '';
        }
        return this.isKnownMapSite(this.currentSite) ? this.currentSite : '';
    }

    getFirstMapSiteMatch(searchText) {
        const needle = (searchText || '').trim().toLowerCase();
        if (!needle) return '';
        return this.getMapSiteNames().find(name => name.toLowerCase().includes(needle)) || '';
    }

    updateMapSiteOptions(searchText = '') {
        const dropdown = document.getElementById('mapSiteDropdown');
        if (!dropdown) return;

        const needle = (searchText || '').trim().toLowerCase();
        const names = this.getMapSiteNames();
        const filteredNames = needle
            ? names.filter(name => name.toLowerCase().includes(needle))
            : names;

        dropdown.innerHTML = '';
        if (!filteredNames.length) {
            const empty = document.createElement('div');
            empty.className = 'map-site-empty';
            empty.textContent = 'No matching sites';
            dropdown.appendChild(empty);
        } else {
            filteredNames.forEach(name => {
                const option = document.createElement('button');
                option.type = 'button';
                option.className = 'map-site-option';
                option.dataset.site = name;
                option.textContent = name;
                dropdown.appendChild(option);
            });
        }
        this.setMapSiteDropdownOpen(!!this.mapSiteDropdownOpen);
    }

    setMapSiteDropdownOpen(open) {
        this.mapSiteDropdownOpen = !!open;
        const dropdown = document.getElementById('mapSiteDropdown');
        const input = document.getElementById('mapSiteSelect');
        if (dropdown) dropdown.hidden = !this.mapSiteDropdownOpen;
        if (input) input.setAttribute('aria-expanded', this.mapSiteDropdownOpen ? 'true' : 'false');
    }

    selectMapSite(siteName) {
        if (!this.isKnownMapSite(siteName)) return;
        const input = document.getElementById('mapSiteSelect');
        if (input) input.value = siteName;
        this.currentSite = siteName;
        this.updateCurrentSiteDisplay();
        this.updateMapSiteOptions(siteName);
        this.setMapSiteDropdownOpen(false);
        this.updateShowMapButton();
        this.renderSiteMapStatusControls();
    }

    getSiteByName(siteName) {
        if (!siteName) return null;
        return (this.sites || []).find(site => site.name === siteName) || null;
    }

    getDevicesTabSiteName() {
        const select = document.getElementById('deviceSiteFilter');
        return select?.value || '';
    }

    getSiteManagementStatusSiteName() {
        const select = document.getElementById('siteMapStatusSiteSelect');
        const selected = select?.value || '';
        if (this.isKnownMapSite(selected)) return selected;
        return this.isKnownMapSite(this.currentSite) ? this.currentSite : '';
    }

    currentDateTimeLocal() {
        const now = new Date();
        now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
        return now.toISOString().slice(0, 16);
    }

    dateTimeLocalValue(value) {
        if (!value) return '';
        const parsed = new Date(value);
        if (!Number.isNaN(parsed.getTime())) {
            parsed.setMinutes(parsed.getMinutes() - parsed.getTimezoneOffset());
            return parsed.toISOString().slice(0, 16);
        }
        return String(value).slice(0, 16);
    }

    renderSiteMapStatusControls() {
        const configs = [
            { id: 'sitesMapStatusControl', siteName: this.getSiteManagementStatusSiteName() },
            { id: 'devicesMapStatusControl', siteName: this.getDevicesTabSiteName() },
            { id: 'mapMapStatusControl', siteName: this.getMapActionSite() }
        ];

        configs.forEach(({ id, siteName }) => {
            const container = document.getElementById(id);
            if (!container) return;
            const site = this.getSiteByName(siteName);
            const disabled = !site || this.currentUserRole === 'guest';
            const checked = site?.map_reliable ? 'checked' : '';
            const mappedAt = this.dateTimeLocalValue(site?.map_reliable_at || '');
            const label = site ? 'Reliable map' : 'Select site';
            container.innerHTML = `
                <div class="site-map-status-control ${disabled ? 'disabled' : ''}" data-site-status-control="${id}" data-site-id="${site?.id || ''}">
                    <label class="checkbox-label">
                        <input type="checkbox" class="site-map-reliable" ${checked} ${disabled ? 'disabled' : ''}>
                        <span>${label}</span>
                    </label>
                    <input type="datetime-local" class="site-map-reliable-at" value="${mappedAt}" ${disabled ? 'disabled' : ''}>
                    <button class="btn btn-secondary site-map-status-save" type="button" ${disabled ? 'disabled' : ''}>Save</button>
                </div>
            `;
            const checkbox = container.querySelector('.site-map-reliable');
            const dateInput = container.querySelector('.site-map-reliable-at');
            const saveBtn = container.querySelector('.site-map-status-save');
            checkbox?.addEventListener('change', () => {
                if (checkbox.checked && dateInput && !dateInput.value) {
                    dateInput.value = this.currentDateTimeLocal();
                }
            });
            saveBtn?.addEventListener('click', () => {
                this.saveSiteMapStatus(id);
            });
        });
    }

    async saveSiteMapStatus(controlId) {
        const container = document.getElementById(controlId);
        const control = container?.querySelector('[data-site-id]');
        const siteId = control?.dataset.siteId || '';
        if (!siteId) {
            this.showError('Select a site first');
            return;
        }
        const reliable = !!control.querySelector('.site-map-reliable')?.checked;
        const dateInput = control.querySelector('.site-map-reliable-at');
        if (reliable && dateInput && !dateInput.value) {
            dateInput.value = this.currentDateTimeLocal();
        }
        try {
            const response = await fetch(`/api/sites/${siteId}/map_status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    map_reliable: reliable,
                    map_reliable_at: dateInput?.value || ''
                })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                this.showError(data.error || 'Failed to update map status');
                return;
            }
            this.showMessage(reliable ? 'Site marked as reliably mapped' : 'Site marked as not reliably mapped');
            await this.loadData();
        } catch (error) {
            console.error('Failed to update map status:', error);
            this.showError('Failed to update map status');
        }
    }

    updateMapControls() {
        const isGuest = this.currentUserRole === 'guest';
        const textBtn = document.getElementById('showTextMapBtn');
        const visualBtn = document.getElementById('showVisualMapBtn');
        const genBtn = document.getElementById('generateMapBtn');
        const macSearchBtn = document.getElementById('mapMacSearchBtn');
        const mapGroupBtn = document.getElementById('mapGroupBtn');
        if (textBtn) textBtn.disabled = isGuest;
        if (visualBtn) visualBtn.disabled = isGuest;
        if (genBtn) genBtn.disabled = isGuest;
        if (macSearchBtn) macSearchBtn.disabled = isGuest;
        if (mapGroupBtn) mapGroupBtn.disabled = isGuest;
    }

    updateShowMapButton() {
        const siteSelect = document.getElementById('mapSiteSelect');
        const showMapBtn = document.getElementById('showMapBtn');
        
        if (!siteSelect || !showMapBtn) return;
        
        showMapBtn.disabled = !this.isKnownMapSite(siteSelect.value);
    }

    getMapSpacing() {
        const range = document.getElementById('mapSpacingRange');
        const raw = range ? parseFloat(range.value || '1') : 1;
        if (!Number.isFinite(raw)) return 1;
        return Math.min(1.4, Math.max(0.3, raw));
    }

    setMapSelection(deviceId) {
        this.mapSelectedDeviceId = deviceId || '';
        const label = document.getElementById('mapSelectedLabel');
        const editBtn = document.getElementById('mapEditBtn');
        const removeBtn = document.getElementById('mapRemoveBtn');
        const rootBtn = document.getElementById('mapRootBtn');

        const device = (this.devices || []).find(d => d.id === this.mapSelectedDeviceId);
        if (!device) {
            this.mapSelectedDeviceId = '';
        }

        if (this.mapSelectedDeviceId && device) {
            const ip = device.ip ? ` (${device.ip})` : '';
            label.textContent = `${device.name}${ip}`;
            editBtn.disabled = false;
            removeBtn.disabled = false;
            rootBtn.disabled = !device.ip || this.currentUserRole === 'guest';
            if (device.site && device.site !== this.currentSite) {
                this.currentSite = device.site;
                this.updateCurrentSiteDisplay();
                this.updateMapTab();
            }
        } else {
            label.textContent = 'None';
            editBtn.disabled = true;
            removeBtn.disabled = true;
            rootBtn.disabled = true;
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

    async setMapRootFromSelection() {
        if (!this.mapSelectedDeviceId) {
            this.showError('Select a node first');
            return;
        }
        const device = (this.devices || []).find(d => d.id === this.mapSelectedDeviceId);
        if (!device || !device.ip) {
            this.showError('Selected device has no IP address');
            return;
        }
        const siteName = device.site || this.currentSite;
        const site = (this.sites || []).find(s => s.name === siteName);
        if (!site) {
            this.showError('Site not found');
            return;
        }
        try {
            const response = await fetch(`/api/sites/${site.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ root_ip: device.ip })
            });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to update root IP');
            }
            this.showMessage(`L2 root set to ${device.ip} for ${site.name}`);
            this.loadData();
        } catch (error) {
            this.showError(error.message || 'Failed to update root IP');
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
        document.getElementById('refreshBtn')?.addEventListener('click', () => {
            this.loadData();
        });

        // Add Site buttons
        document.getElementById('addSiteBtn')?.addEventListener('click', () => {
            this.showAddSiteModal();
        });
        document.getElementById('addSiteBtn2')?.addEventListener('click', () => {
            this.showAddSiteModal();
        });
        document.getElementById('saveSiteBtn')?.addEventListener('click', () => {
            this.saveSite();
        });
        document.getElementById('siteRootDevice')?.addEventListener('change', (event) => {
            const rootIpInput = document.getElementById('siteRootIP');
            if (!rootIpInput) return;
            const value = event.target.value || '';
            if (value) {
                rootIpInput.value = value;
            }
        });
        document.getElementById('siteRootIP')?.addEventListener('input', (event) => {
            const select = document.getElementById('siteRootDevice');
            if (!select) return;
            const value = event.target.value.trim();
            if (!value) {
                select.value = '';
                return;
            }
            const optionExists = Array.from(select.options).some(opt => opt.value === value);
            if (!optionExists) {
                select.value = '';
            }
        });
        document.getElementById('siteMapReliable')?.addEventListener('change', (event) => {
            const dateInput = document.getElementById('siteMapReliableAt');
            if (event.target.checked && dateInput && !dateInput.value) {
                dateInput.value = this.currentDateTimeLocal();
            }
        });
        document.getElementById('siteMapStatusSiteSelect')?.addEventListener('change', (event) => {
            if (event.target.value) {
                this.currentSite = event.target.value;
                this.updateCurrentSiteDisplay();
            }
            this.renderSiteMapStatusControls();
        });

        // Site selection
        document.getElementById('deviceSiteFilter')?.addEventListener('change', (e) => {
            this.devicesPage = 1;
            this.devicesNetworkFilter = '';
            this.updateDevicesTab();
            this.renderSiteMapStatusControls();
        });
        document.getElementById('deviceNetworkFilter')?.addEventListener('change', (e) => {
            this.devicesPage = 1;
            this.devicesNetworkFilter = e.target.value || '';
            this.updateDevicesTab();
        });
        document.getElementById('moduleSiteSelect')?.addEventListener('change', (e) => {
            this.currentSite = e.target.value;
            this.updateCurrentSiteDisplay();
        });

        // Generate map button
document.getElementById('generateMapBtn')?.addEventListener('click', function() {
    const siteName = platform.getMapActionSite ? platform.getMapActionSite() : document.getElementById('mapSiteSelect').value;
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
            platform.currentTextMapUrl = data.map_url || '';
            const downloadBtn = document.getElementById('downloadTextMapBtn');
            if (downloadBtn) downloadBtn.disabled = !platform.currentTextMapUrl;
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
    const siteName = platform.getMapActionSite ? platform.getMapActionSite() : document.getElementById('mapSiteSelect').value;
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
                platform.currentTextMapUrl = result.map_url || '';
                const downloadBtn = document.getElementById('downloadTextMapBtn');
                if (downloadBtn) downloadBtn.disabled = !platform.currentTextMapUrl;
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

function readErrorMessage(response, fallback) {
    const fallbackMessage = fallback || 'Request failed';
    return response
        .json()
        .then((data) => data.error || data.message || fallbackMessage)
        .catch(() => fallbackMessage);
}

// Show visual map  
document.getElementById('showVisualMapBtn')?.addEventListener('click', async () => {
    const siteName = platform.getMapActionSite ? platform.getMapActionSite() : document.getElementById('mapSiteSelect').value;
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
                body: JSON.stringify({ site_name: siteName, spacing: platform.getMapSpacing() })
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
                const message = await readErrorMessage(response, 'Failed to generate visual map');
                throw new Error(message);
            }
        } catch (error) {
            console.error('Error generating visual map:', error);
            platform.showMessage(error.message || 'Error generating visual map', 'error');
        } finally {
            btn.innerHTML = originalText;
            btn.disabled = false;
            replaceIcons();
        }
    }
});

// Generate both maps
document.getElementById('generateMapBtn')?.addEventListener('click', async () => {
    const siteName = platform.getMapActionSite ? platform.getMapActionSite() : document.getElementById('mapSiteSelect').value;
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
            body: JSON.stringify({ site_name: siteName, spacing: platform.getMapSpacing() })
        });
        
        if (textResponse.ok && visualResponse.ok) {
            platform.showMessage(`Maps generated for ${siteName}!`);
        } else {
            let errorMessage = 'Failed to generate one or more maps';
            if (!textResponse.ok) {
                errorMessage = await readErrorMessage(textResponse, errorMessage);
            } else if (!visualResponse.ok) {
                errorMessage = await readErrorMessage(visualResponse, errorMessage);
            }
            throw new Error(errorMessage);
        }
    } catch (error) {
        console.error('Error generating maps:', error);
        platform.showMessage(error.message || 'Error generating maps', 'error');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
        replaceIcons();
    }
});
        // Edit Device
        document.getElementById('updateDeviceBtn')?.addEventListener('click', () => {
            this.updateDevice();
        });

        // Settings
        document.getElementById('saveSettingsBtn')?.addEventListener('click', () => {
            this.saveSettings();
        });
        document.getElementById('auditLogRefreshBtn')?.addEventListener('click', () => {
            this.auditLogPage = 1;
            this.loadAuditEvents();
        });
        document.getElementById('auditLogDownloadBtn')?.addEventListener('click', () => {
            this.downloadAuditLog();
        });
        document.getElementById('auditLogDay')?.addEventListener('change', () => {
            this.auditLogPage = 1;
            this.loadAuditEvents();
        });
        document.getElementById('auditLogPrevBtn')?.addEventListener('click', () => {
            this.auditLogPage = Math.max(1, (this.auditLogPage || 1) - 1);
            this.loadAuditEvents();
        });
        document.getElementById('auditLogNextBtn')?.addEventListener('click', () => {
            this.auditLogPage = (this.auditLogPage || 1) + 1;
            this.loadAuditEvents();
        });
        document.getElementById('showCompletedJobs')?.addEventListener('change', (event) => {
            this.showCompletedJobs = !!event.target.checked;
            this.updateModuleJobs();
        });
        document.getElementById('moduleCredSaveBtn')?.addEventListener('click', () => {
            this.saveModuleCredential();
        });
        document.getElementById('moduleCredClearBtn')?.addEventListener('click', () => {
            this.clearModuleCredentialForm();
        });

        document.getElementById('addConnectionRowBtn')?.addEventListener('click', () => {
            this.addConnectionRow();
        });

        // ==================== MAP TAB EVENT LISTENERS ====================
        
        // Map site selection
        document.getElementById('mapSiteToggle')?.addEventListener('click', () => {
            const input = document.getElementById('mapSiteSelect');
            this.setMapSiteDropdownOpen(!this.mapSiteDropdownOpen);
            this.updateMapSiteOptions(input?.value || '');
            input?.focus();
        });
        document.getElementById('mapSiteSelect')?.addEventListener('input', (e) => {
            const value = e.target.value || '';
            this.setMapSiteDropdownOpen(true);
            this.updateMapSiteOptions(value);
            if (this.isKnownMapSite(value)) {
                this.currentSite = value;
                this.updateCurrentSiteDisplay();
            }
            this.updateShowMapButton();
            this.renderSiteMapStatusControls();
        });
        document.getElementById('mapSiteSelect')?.addEventListener('focus', (e) => {
            this.setMapSiteDropdownOpen(true);
            this.updateMapSiteOptions(e.target.value || '');
        });
        document.getElementById('mapSiteSelect')?.addEventListener('keydown', (e) => {
            const input = document.getElementById('mapSiteSelect');
            if (!input) return;
            if (e.key === 'Escape') {
                this.setMapSiteDropdownOpen(false);
                return;
            }
            if (e.key !== 'Enter') return;
            const selectedSite = this.isKnownMapSite(input.value)
                ? input.value
                : this.getFirstMapSiteMatch(input.value);
            if (selectedSite) {
                e.preventDefault();
                this.selectMapSite(selectedSite);
            }
        });
        document.getElementById('mapSiteSelect')?.addEventListener('change', (e) => {
            const value = e.target.value || '';
            if (this.isKnownMapSite(value)) {
                this.selectMapSite(value);
            }
            this.updateMapSiteOptions(value);
            this.updateShowMapButton();
            this.renderSiteMapStatusControls();
        });
        document.getElementById('mapSiteDropdown')?.addEventListener('mousedown', (e) => {
            e.preventDefault();
        });
        document.getElementById('mapSiteDropdown')?.addEventListener('click', (e) => {
            const option = e.target.closest('.map-site-option');
            if (!option) return;
            this.selectMapSite(option.dataset.site || '');
        });
        document.addEventListener('click', (e) => {
            const combo = document.getElementById('mapSiteCombo');
            if (combo && !combo.contains(e.target)) {
                this.setMapSiteDropdownOpen(false);
            }
        });

        document.getElementById('mapAddBtn')?.addEventListener('click', () => {
            const siteName = this.getMapActionSite();
            if (!siteName) {
                this.showError('Select a site first');
                return;
            }
            this.currentSite = siteName;
            this.updateCurrentSiteDisplay();
            this.openModuleById('add_device_manual');
        });

        document.getElementById('mapMacSearchBtn')?.addEventListener('click', () => {
            const siteName = this.getMapActionSite();
            if (!siteName) {
                this.showError('Select a site first');
                return;
            }
            this.currentSite = siteName;
            this.updateCurrentSiteDisplay();
            const selected = (this.devices || []).find(device => device.id === this.mapSelectedDeviceId);
            const prefill = selected && selected.mac ? { target_device_id: selected.id } : {};
            this.openModuleById('mac_table_search', prefill);
        });

        document.getElementById('mapGroupBtn')?.addEventListener('click', () => {
            const siteName = this.getMapActionSite();
            if (!siteName) {
                this.showError('Select a site first');
                return;
            }
            this.currentSite = siteName;
            this.updateCurrentSiteDisplay();
            this.openModuleById('mac_group_map');
        });

        document.getElementById('mapEditBtn')?.addEventListener('click', () => {
            if (!this.mapSelectedDeviceId) {
                this.showError('Select a node first');
                return;
            }
            this.showEditDeviceModal(this.mapSelectedDeviceId);
        });

        document.getElementById('mapRootBtn')?.addEventListener('click', () => {
            this.setMapRootFromSelection();
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
            const siteName = this.getMapActionSite();
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
                        body: JSON.stringify({ site_name: siteName, spacing: platform.getMapSpacing() })
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
                        const message = await readErrorMessage(genResponse, 'Failed to generate visual map');
                        throw new Error(message);
                    }
                } catch (error) {
                    console.error('Error loading/generating map:', error);
                    platform.showMessage(error.message || 'Error loading map', 'error');
                } finally {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                    replaceIcons();
                }
            }
        });
        
        // Refresh map button
        document.getElementById('refreshMapBtn')?.addEventListener('click', () => {
            const siteName = this.getMapActionSite();
            if (siteName) {
                this.loadMapForSite(siteName);
            }
        });
        document.getElementById('downloadTextMapBtn')?.addEventListener('click', () => {
            if (this.currentTextMapUrl) {
                window.open(this.currentTextMapUrl, '_blank');
            }
        });
        
        // Fullscreen button
        document.getElementById('fullscreenBtn')?.addEventListener('click', () => {
            const mapFrame = document.getElementById('mapFrame');
            if (mapFrame.src) {
                window.open(mapFrame.src, '_blank');
            }
        });
        const mapSpacingRange = document.getElementById('mapSpacingRange');
        const mapSpacingValue = document.getElementById('mapSpacingValue');
        if (mapSpacingRange && mapSpacingValue) {
            const updateLabel = () => {
                const val = parseFloat(mapSpacingRange.value || '1') || 1;
                mapSpacingValue.textContent = `${val.toFixed(2)}x`;
            };
            mapSpacingRange.addEventListener('input', updateLabel);
            updateLabel();
        }
        document.getElementById('exportDataBtn')?.addEventListener('click', () => {
            this.exportData();
        });
        document.getElementById('importDataBtn')?.addEventListener('click', () => {
            this.importData();
        });
        document.getElementById('deleteSelectedDevicesBtn')?.addEventListener('click', () => {
            this.deleteSelectedDevices();
        });
        document.getElementById('mapSelectedDevicesBtn')?.addEventListener('click', () => {
            this.setSelectedDevicesMapVisibility(true);
        });
        document.getElementById('unmapSelectedDevicesBtn')?.addEventListener('click', () => {
            this.setSelectedDevicesMapVisibility(false);
        });
        document.getElementById('deleteCatchedDevicesBtn')?.addEventListener('click', () => {
            this.runDeleteCatchedDevices();
        });
        document.getElementById('enforceOuiBtn')?.addEventListener('click', () => {
            this.runEnforceOui();
        });
        document.getElementById('mikrotikDiscoveryBtn')?.addEventListener('click', () => {
            this.runMikrotikDiscovery();
        });
        document.getElementById('agentDiscoveryBtn')?.addEventListener('click', () => {
            this.runAgentDiscoveryForSite();
        });
        document.getElementById('domainLookupBtn')?.addEventListener('click', () => {
            this.runDomainLookup();
        });
        document.getElementById('addDeviceModuleBtn')?.addEventListener('click', () => {
            this.runAddDeviceModule();
        });
        document.getElementById('devicesSelectAll')?.addEventListener('change', (event) => {
            this.toggleSelectAllDevices(event.target.checked);
        });
        document.getElementById('devicesPagePrev')?.addEventListener('click', () => {
            this.changeDevicesPage(-1);
        });
        document.getElementById('devicesPageNext')?.addEventListener('click', () => {
            this.changeDevicesPage(1);
        });
        document.getElementById('devicesPageSize')?.addEventListener('change', (event) => {
            const value = parseInt(event.target.value, 10);
            if (!Number.isNaN(value) && value > 0) {
                this.setDevicesPageSize(value);
            }
        });
        document.querySelectorAll('.device-column-search').forEach(input => {
            input.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                this.applyDeviceColumnSearch();
            });
            input.addEventListener('input', () => {
                if (input.value) return;
                const key = input.dataset.deviceFilterKey;
                if (!key || !this.deviceColumnFilters[key]) return;
                delete this.deviceColumnFilters[key];
                this.devicesPage = 1;
                this.updateDevicesTab();
            });
        });
        document.getElementById('saveOuiRangesBtn')?.addEventListener('click', () => {
            this.saveOuiRanges();
        });
        document.getElementById('addOuiRangeRowBtn')?.addEventListener('click', () => {
            this.addOuiRangeTableRow();
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
        document.getElementById('saveOuiBtn')?.addEventListener('click', () => {
            this.saveDeviceOui();
        });
        document.getElementById('exportDevicesBtn')?.addEventListener('click', () => {
            this.openExportDevices();
        });
        document.getElementById('saveScheduleBtn')?.addEventListener('click', () => {
            this.saveSchedule();
        });
        document.getElementById('clearScheduleBtn')?.addEventListener('click', () => {
            this.clearScheduleForm();
        });
        document.getElementById('addScheduleModuleBtn')?.addEventListener('click', () => {
            this.addScheduleModuleRow();
        });
        document.getElementById('scheduleSiteScope')?.addEventListener('change', () => {
            this.updateScheduleScopeUI();
        });
        document.getElementById('scheduleModuleSaveBtn')?.addEventListener('click', () => {
            this.saveScheduleModuleConfig();
        });

        // Agent Manager
        document.getElementById('addAgentBtn')?.addEventListener('click', () => {
            this.openAgentModal();
        });
        document.getElementById('saveAgentBtn')?.addEventListener('click', () => {
            this.saveAgent();
        });
        document.getElementById('pullAgentIdentityBtn')?.addEventListener('click', () => {
            this.pullAgentIdentity();
        });
        document.getElementById('downloadAgentExeBtn')?.addEventListener('click', () => {
            this.downloadAgentExe();
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
                    if (this.currentUserRole === 'admin') {
                        this.renderModuleCredentials();
                    }
                }
            })
            .finally(finishOne);

        const schedulesTask = this.fetchData('/api/schedules', { timeoutMs: 8000 })
            .then(schedules => {
                if (schedules) {
                    this.schedules = schedules;
                    this.renderScheduleList();
                    this.updateScheduleFormSites();
                }
                return this.refreshModuleJobs();
            })
            .finally(finishOne);

        const agentsTask = this.fetchData('/api/agents', { timeoutMs: 8000 })
            .then(agents => {
                if (agents) {
                    this.agents = agents;
                    this.renderAgents();
                }
            })
            .finally(finishOne);

        Promise.allSettled([siteTask, deviceTask, statsTask, modulesTask, schedulesTask, agentsTask])
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
                this.moduleCredentials = settings.module_credentials || {};
                this.moduleLastParams = settings.module_last_params || {};
                this.applySettings();
                if (this.currentUserRole === 'admin') {
                    this.renderModuleCredentials();
                    this.loadAuditLogDays();
                }
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
            this.ouiRangesEntries = this.parseOuiRanges(this.ouiRangesText);
            const textarea = document.getElementById('ouiRangesText');
            if (textarea) {
                textarea.value = this.ouiRangesText;
            }
            this.renderOuiRangesTable();
        } catch (error) {
            console.error('Error loading OUI ranges:', error);
        }
    }

    parseOuiRanges(text) {
        const entries = [];
        if (!text) return entries;
        text.split(/\r?\n/).forEach(line => {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith('#')) return;
            if (!trimmed.includes('=') || !trimmed.includes('-')) return;
            const parts = trimmed.split('=');
            if (parts.length < 2) return;
            const rangePart = parts[0].trim();
            const [start, end] = rangePart.split('-', 2);
            if (!start || !end) return;
            const right = parts.slice(1).join('=').trim();
            let label = right;
            let dtype = '';
            if (right.includes(',')) {
                const tokens = right.split(',').map(t => t.trim()).filter(Boolean);
                label = tokens[0] || '';
                tokens.slice(1).forEach(token => {
                    const lower = token.toLowerCase();
                    if (lower.startsWith('device_type=')) {
                        dtype = token.split('=', 2)[1].trim().toLowerCase();
                    }
                });
            }
            entries.push({ start, end, label, dtype });
        });
        return entries;
    }

    buildOuiRangesText(entries) {
        return entries.map(entry => {
            const base = `${entry.start}-${entry.end}=${entry.label}`;
            return entry.dtype ? `${base},device_type=${entry.dtype}` : base;
        }).join('\n');
    }

    getDeviceTypeOptions(selected = '') {
        const types = ['', 'router', 'switch', 'firewall', 'ap', 'server', 'nvr', 'pda', 'host', 'phone', 'printer', 'pc', 'finger', 'unknown', 'other'];
        return types.map(type => {
            const label = type ? type : 'Select type';
            return `<option value="${this.escapeHtml(type)}" ${type === selected ? 'selected' : ''}>${this.escapeHtml(label)}</option>`;
        }).join('');
    }

    normalizeOuiEntry(entry = {}) {
        const dtype = (entry.dtype || '').trim().toLowerCase();
        return {
            start: (entry.start || '').trim().toUpperCase(),
            end: (entry.end || '').trim().toUpperCase(),
            label: (entry.label || '').trim(),
            dtype: dtype === 'camera' ? 'nvr' : dtype
        };
    }

    ouiSortValue(entry, key) {
        if (key === 'oui') {
            return `${entry.start || ''}-${entry.end || ''}`;
        }
        if (key === 'device_type') {
            return entry.dtype || '';
        }
        return entry.label || '';
    }

    renderOuiRangesTable() {
        const body = document.getElementById('ouiRangesTableBody');
        if (!body) return;
        const state = this.sortState.ouiRanges || { key: 'oui', dir: 'asc' };
        const dir = state.dir === 'desc' ? -1 : 1;
        const entries = (this.ouiRangesEntries || [])
            .map((entry, index) => ({ ...this.normalizeOuiEntry(entry), index }))
            .sort((a, b) => this.compareValues(this.ouiSortValue(a, state.key), this.ouiSortValue(b, state.key)) * dir);

        if (!entries.length) {
            body.innerHTML = `
                <tr>
                    <td colspan="4" class="empty-state">
                        <div style="padding: 24px; text-align: center;">No OUI ranges yet.</div>
                    </td>
                </tr>
            `;
            this.applySortIndicators('ouiRanges');
            return;
        }

        body.innerHTML = entries.map(entry => `
            <tr data-index="${entry.index}">
                <td>
                    <div style="display: grid; grid-template-columns: minmax(150px, 1fr) auto minmax(150px, 1fr); gap: 8px; align-items: center;">
                        <input type="text" class="oui-start" value="${this.escapeHtml(entry.start)}" placeholder="AA:BB:CC:00:00:00">
                        <span style="color: var(--text-secondary);">to</span>
                        <input type="text" class="oui-end" value="${this.escapeHtml(entry.end)}" placeholder="AA:BB:CC:FF:FF:FF">
                    </div>
                </td>
                <td>
                    <input type="text" class="oui-label" value="${this.escapeHtml(entry.label)}" placeholder="Vendor / Name">
                </td>
                <td>
                    <select class="oui-device-type">
                        ${this.getDeviceTypeOptions(entry.dtype)}
                    </select>
                </td>
                <td>
                    <button class="btn btn-secondary btn-sm oui-remove-row" type="button">Remove</button>
                </td>
            </tr>
        `).join('');

        body.querySelectorAll('input, select').forEach(input => {
            input.addEventListener('input', () => this.captureOuiTableRows());
            input.addEventListener('change', () => this.captureOuiTableRows());
        });
        body.querySelectorAll('.oui-remove-row').forEach(button => {
            button.addEventListener('click', (event) => {
                const row = event.target.closest('tr');
                const index = Number(row?.dataset.index);
                if (Number.isInteger(index)) {
                    this.captureOuiTableRows();
                    this.ouiRangesEntries.splice(index, 1);
                    this.syncOuiTextareaFromEntries();
                    this.renderOuiRangesTable();
                }
            });
        });
        this.applySortIndicators('ouiRanges');
    }

    captureOuiTableRows() {
        const body = document.getElementById('ouiRangesTableBody');
        if (!body) return this.ouiRangesEntries || [];
        const byIndex = new Map();
        body.querySelectorAll('tr[data-index]').forEach(row => {
            const index = Number(row.dataset.index);
            byIndex.set(index, this.normalizeOuiEntry({
                start: row.querySelector('.oui-start')?.value || '',
                end: row.querySelector('.oui-end')?.value || '',
                label: row.querySelector('.oui-label')?.value || '',
                dtype: row.querySelector('.oui-device-type')?.value || ''
            }));
        });
        this.ouiRangesEntries = (this.ouiRangesEntries || []).map((entry, index) => byIndex.get(index) || this.normalizeOuiEntry(entry));
        this.syncOuiTextareaFromEntries();
        return this.ouiRangesEntries;
    }

    syncOuiTextareaFromEntries() {
        const content = this.buildOuiRangesText((this.ouiRangesEntries || []).map(entry => this.normalizeOuiEntry(entry)).filter(entry =>
            entry.start || entry.end || entry.label || entry.dtype
        ));
        this.ouiRangesText = content;
        const textarea = document.getElementById('ouiRangesText');
        if (textarea) {
            textarea.value = content;
        }
        return content;
    }

    addOuiRangeTableRow() {
        if (this.currentUserRole !== 'admin') return;
        this.captureOuiTableRows();
        this.ouiRangesEntries.push({ start: '', end: '', label: '', dtype: '' });
        this.renderOuiRangesTable();
    }

    async saveOuiRanges() {
        if (this.currentUserRole !== 'admin') {
            return;
        }
        this.captureOuiTableRows();
        const invalid = (this.ouiRangesEntries || []).find(entry => {
            const normalized = this.normalizeOuiEntry(entry);
            const hasAny = normalized.start || normalized.end || normalized.label || normalized.dtype;
            return hasAny && (!normalized.start || !normalized.end || !normalized.label);
        });
        if (invalid) {
            this.showError('Each OUI row needs start, end, and name');
            return;
        }
        const content = this.syncOuiTextareaFromEntries();
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
            this.ouiRangesEntries = this.parseOuiRanges(content);
            this.renderOuiRangesTable();
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
        const chartsGrid = document.getElementById('dashboardCharts');
        if (this.stats) {
            statsGrid.innerHTML = `
                <div class="stat-card kpi" style="--stat-accent:#38BDF8;">
                    <div class="kpi-icon"><i data-feather="map-pin"></i></div>
                    <div class="stat-label">Total Sites</div>
                    <div class="stat-value">${this.stats.total_sites || 0}</div>
                    <div class="stat-chip">All locations</div>
                </div>
                <div class="stat-card kpi" style="--stat-accent:#10B981;">
                    <div class="kpi-icon"><i data-feather="server"></i></div>
                    <div class="stat-label">Total Devices</div>
                    <div class="stat-value">${this.stats.total_devices || 0}</div>
                    <div class="stat-chip">Inventory size</div>
                </div>
                <div class="stat-card kpi" style="--stat-accent:#F59E0B;">
                    <div class="kpi-icon"><i data-feather="help-circle"></i></div>
                    <div class="stat-label">Unknown Devices</div>
                    <div class="stat-value">${this.stats.unknown_devices || 0}</div>
                    <div class="stat-chip">Needs classification</div>
                </div>
                <div class="stat-card kpi" style="--stat-accent:#EF4444;">
                    <div class="kpi-icon"><i data-feather="map"></i></div>
                    <div class="stat-label">Sites Without Reliable Map</div>
                    <div class="stat-value">${this.stats.unreliable_map_count || 0}</div>
                    <div class="stat-chip">${this.stats.unreliable_map_rate || 0}% of sites</div>
                </div>
            `;
            if (chartsGrid) {
                const staleDays = this.stats.stale_scan_days || 7;
                const onlineMinutes = this.stats.agent_online_minutes || 5;
                const sitesNoRouter = this.stats.sites_no_router || [];
                const staleSites = this.stats.stale_sites || [];
                const unknownRates = this.stats.unknown_rate_sites || [];
                const unreliableMaps = this.stats.unreliable_maps || this.stats.uncompleted_maps || [];
                const catchedSites = this.stats.catched_sites || [];
                const catchedTotal = this.stats.catched_total || 0;
                const pcNoDomainSites = this.stats.pc_no_domain_sites || [];
                const pcNoDomainTotal = this.stats.pc_no_domain_total || 0;

                this.dashboardReports = {
                    sitesNoRouter: {
                        title: 'Sites With Unknown Router',
                        subtitle: 'All sites missing a router type',
                        columns: ['No.', 'Site'],
                        rows: sitesNoRouter.map((site, index) => [index + 1, site])
                    },
                    staleSites: {
                        title: 'No Recent Scans',
                        subtitle: `All sites older than ${staleDays} days`,
                        columns: ['No.', 'Site'],
                        rows: staleSites.map((site, index) => [index + 1, site])
                    },
                    unknownRates: {
                        title: 'Highest Unknown Rate',
                        subtitle: 'All sites sorted by unknown-device rate',
                        columns: ['No.', 'Site', 'Unknown Rate'],
                        rows: unknownRates.map((item, index) => [index + 1, item.site || '', `${Number(item.rate || 0)}%`])
                    },
                    unreliableMaps: {
                        title: 'Sites Without Reliable Map',
                        subtitle: 'All sites not marked as reliably mapped',
                        columns: ['No.', 'Site'],
                        rows: unreliableMaps.map((site, index) => [index + 1, site])
                    },
                    catchedSites: {
                        title: 'Catched IPs',
                        subtitle: 'All sites with Catched devices',
                        columns: ['No.', 'Site', 'Catched Count'],
                        rows: catchedSites.map((item, index) => [index + 1, item.site || '', Number(item.count || 0)])
                    },
                    pcNoDomainSites: {
                        title: 'PCs With No Domain',
                        subtitle: 'All sites with PC/domain gaps',
                        columns: ['No.', 'Site', 'PCs With No Domain'],
                        rows: pcNoDomainSites.map((item, index) => [index + 1, item.site || '', Number(item.count || 0)])
                    }
                };

                const listItems = (items, emptyText) => {
                    if (!items.length) {
                        return `<div style="color: var(--text-secondary); font-size: 12px;">${emptyText}</div>`;
                    }
                    return items.map(item => `<div>${this.escapeHtml(item)}</div>`).join('');
                };

                const barList = (items, key, valueKey, emptyText, color) => {
                    if (!items.length) {
                        return `<div style="color: var(--text-secondary); font-size: 12px;">${emptyText}</div>`;
                    }
                    const maxValue = Math.max(...items.map(item => Number(item[valueKey] || 0)), 1);
                    return `
                        <div class="chart-list">
                            ${items.map(item => {
                                const label = item[key] || '';
                                const value = Number(item[valueKey] || 0);
                                const pct = valueKey === 'rate'
                                    ? Math.min(100, Math.max(0, value))
                                    : Math.min(100, Math.max(0, (value / maxValue) * 100));
                                return `
                                    <div class="chart-row">
                                        <div>${this.escapeHtml(label)}</div>
                                        <div class="chart-bar">
                                            <span style="width:${pct}%; background:${color};"></span>
                                        </div>
                                        <div class="chart-badge">${value}${valueKey === 'rate' ? '%' : ''}</div>
                                    </div>
                                `;
                            }).join('')}
                        </div>
                    `;
                };

                chartsGrid.innerHTML = `
                    <div class="chart-card clickable" data-dashboard-report="sitesNoRouter">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <div class="chart-title">Sites With Unknown Router</div>
                                <div class="chart-subtitle">Sites missing a router type</div>
                            </div>
                            <strong>${sitesNoRouter.length}</strong>
                        </div>
                        <div style="margin-top: 12px; font-size: 12px;">
                            ${listItems(sitesNoRouter.slice(0, 6), 'All sites have a router.')}
                        </div>
                    </div>
                    <div class="chart-card clickable" data-dashboard-report="staleSites">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <div class="chart-title">No Recent Scans</div>
                                <div class="chart-subtitle">Older than ${staleDays} days</div>
                            </div>
                            <strong>${staleSites.length}</strong>
                        </div>
                        <div style="margin-top: 12px; font-size: 12px;">
                            ${listItems(staleSites.slice(0, 6), 'All sites scanned recently.')}
                        </div>
                    </div>
                    <div class="chart-card clickable" data-dashboard-report="unknownRates">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <div class="chart-title">Highest Unknown Rate</div>
                                <div class="chart-subtitle">Top 5 by unknown devices</div>
                            </div>
                            <strong>${unknownRates.length}</strong>
                        </div>
                        ${barList(unknownRates.slice(0, 5), 'site', 'rate', 'No data.', 'linear-gradient(90deg, #F59E0B, #EF4444)')}
                    </div>
                    <div class="chart-card clickable" data-dashboard-report="unreliableMaps">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <div class="chart-title">Sites Without Reliable Map</div>
                                <div class="chart-subtitle">Manual reliable-map status</div>
                            </div>
                            <strong>${unreliableMaps.length}</strong>
                        </div>
                        <div style="margin-top: 12px; font-size: 12px;">
                            ${listItems(unreliableMaps.slice(0, 6), 'All sites are marked reliable.')}
                        </div>
                    </div>
                    <div class="chart-card clickable" data-dashboard-report="catchedSites">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <div class="chart-title">Catched IPs</div>
                                <div class="chart-subtitle">By site (top 5)</div>
                            </div>
                            <strong>${catchedTotal}</strong>
                        </div>
                        ${barList(catchedSites.slice(0, 5).map(item => ({ site: item.site, count: item.count })), 'site', 'count', 'No catched IPs.', 'linear-gradient(90deg, #38BDF8, #10B981)')}
                    </div>
                    <div class="chart-card clickable" data-dashboard-report="pcNoDomainSites">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <div class="chart-title">PCs With No Domain</div>
                                <div class="chart-subtitle">Sites with maximum PC/domain gaps (top 10)</div>
                            </div>
                            <strong>${pcNoDomainTotal}</strong>
                        </div>
                        ${barList(pcNoDomainSites.slice(0, 10).map(item => ({ site: item.site, count: item.count })), 'site', 'count', 'All PCs have domain data.', 'linear-gradient(90deg, #8B5CF6, #38BDF8)')}
                    </div>
                `;
                chartsGrid.querySelectorAll('[data-dashboard-report]').forEach(card => {
                    card.addEventListener('click', () => {
                        this.showDashboardReport(card.dataset.dashboardReport);
                    });
                });
            }
        }

        // Update sites table
        const sitesBody = document.getElementById('sitesTableBody');
        if (this.sites && this.sites.length > 0) {
            const sortedSites = this.sortSites(this.sites, 'dashboardSites');
            sitesBody.innerHTML = sortedSites.map(site => {
                const siteDevices = this.devices.filter(d => d.site === site.name).length;
                const mapStatus = this.siteMapStatusBadge(site);
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
                        <td>${mapStatus}</td>
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
        
        this.applySortIndicators('dashboardSites');
        this.renderSiteMapStatusControls();
        replaceIcons();
    }

    showDashboardReport(reportKey) {
        const report = (this.dashboardReports || {})[reportKey];
        if (!report) return;

        const modal = document.getElementById('dashboardReportModal');
        const title = document.getElementById('dashboardReportTitle');
        const subtitle = document.getElementById('dashboardReportSubtitle');
        const head = document.getElementById('dashboardReportHead');
        const body = document.getElementById('dashboardReportBody');
        if (!modal || !title || !subtitle || !head || !body) return;

        const rows = Array.isArray(report.rows) ? report.rows : [];
        const columns = Array.isArray(report.columns) ? report.columns : [];
        title.textContent = report.title || 'Dashboard Report';
        subtitle.textContent = `${report.subtitle || ''}${rows.length ? ` • ${rows.length} row${rows.length === 1 ? '' : 's'}` : ''}`;
        head.innerHTML = `
            <tr>
                ${columns.map(column => `<th>${this.escapeHtml(column)}</th>`).join('')}
            </tr>
        `;
        body.innerHTML = rows.length
            ? rows.map(row => `
                <tr>
                    ${row.map(value => `<td>${this.escapeHtml(value)}</td>`).join('')}
                </tr>
            `).join('')
            : `<tr><td colspan="${Math.max(columns.length, 1)}" class="empty-state">No records.</td></tr>`;
        modal.classList.add('active');
    }

    updateSitesTab() {
        const statusSelect = document.getElementById('siteMapStatusSiteSelect');
        if (statusSelect) {
            const currentValue = statusSelect.value || this.currentSite || '';
            const siteOptions = (this.sites || []).map(site => (
                `<option value="${site.name}" ${site.name === currentValue ? 'selected' : ''}>${site.name}</option>`
            )).join('');
            statusSelect.innerHTML = '<option value="">Select site</option>' + siteOptions;
        }
        const sitesBody = document.getElementById('sitesManagementBody');
        if (this.sites && this.sites.length > 0) {
            const sortedSites = this.sortSites(this.sites, 'sites');
            sitesBody.innerHTML = sortedSites.map(site => {
                const siteDevices = this.devices.filter(d => d.site === site.name).length;
                const mapStatus = this.siteMapStatusBadge(site);
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
                        <td>${mapStatus}</td>
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
                    <td colspan="8" class="empty-state">
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
        this.renderSiteMapStatusControls();
        replaceIcons();
    }

    ipToNumber(ip) {
        const parts = String(ip || '').trim().split('.');
        if (parts.length !== 4) return null;
        let value = 0;
        for (const part of parts) {
            if (!/^\d+$/.test(part)) return null;
            const octet = Number(part);
            if (octet < 0 || octet > 255) return null;
            value = (value * 256) + octet;
        }
        return value >>> 0;
    }

    parseCidr(cidr) {
        const match = String(cidr || '').trim().match(/^(\d+\.\d+\.\d+\.\d+)\/(\d{1,2})$/);
        if (!match) return null;
        const base = this.ipToNumber(match[1]);
        const prefix = Number(match[2]);
        if (base === null || prefix < 0 || prefix > 32) return null;
        const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
        const network = (base & mask) >>> 0;
        return { cidr: `${this.numberToIp(network)}/${prefix}`, network, mask, prefix };
    }

    numberToIp(value) {
        const n = value >>> 0;
        return [
            (n >>> 24) & 255,
            (n >>> 16) & 255,
            (n >>> 8) & 255,
            n & 255
        ].join('.');
    }

    cidrForIp(ip, prefix = 24) {
        const value = this.ipToNumber(ip);
        if (value === null) return null;
        const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
        return `${this.numberToIp((value & mask) >>> 0)}/${prefix}`;
    }

    ipInCidr(ip, cidrInfo) {
        const value = this.ipToNumber(ip);
        if (value === null || !cidrInfo) return false;
        return ((value & cidrInfo.mask) >>> 0) === cidrInfo.network;
    }

    getConfiguredNetworksForSite(siteName) {
        const networks = [];
        const add = (value) => {
            const parsed = this.parseCidr(value);
            if (parsed && !networks.some(item => item.cidr === parsed.cidr)) {
                networks.push(parsed);
            }
        };
        const site = this.getSiteByName(siteName);
        (site?.active_scan_ranges || []).forEach(add);
        (this.agents || []).forEach(agent => {
            if (!agent || agent.site !== siteName) return;
            add(agent.target_range);
            (agent.target_ranges || []).forEach(add);
            (agent.network_ranges || []).forEach(add);
        });
        networks.sort((a, b) => b.prefix - a.prefix || a.cidr.localeCompare(b.cidr, undefined, { numeric: true }));
        return networks;
    }

    normalizeScanRangeList(values) {
        const items = Array.isArray(values)
            ? values
            : String(values || '').split(/[\n,;]+/);
        const seen = new Set();
        const ranges = [];
        items.forEach(item => {
            const value = String(item || '').trim();
            if (!value || seen.has(value)) return;
            if (!this.parseCidr(value) && !/^\d{1,3}(?:\.\d{1,3}){3}(?:-\d{1,3}(?:\.\d{1,3}){3})?$/.test(value)) {
                return;
            }
            seen.add(value);
            ranges.push(value);
        });
        return ranges;
    }

    renderDeviceActiveScanRanges(siteName, networks = []) {
        const container = document.getElementById('deviceActiveScanRangesControl');
        if (!container) return;
        if (!siteName) {
            container.innerHTML = '';
            return;
        }
        const site = this.getSiteByName(siteName);
        if (!site) {
            container.innerHTML = '';
            return;
        }
        const active = new Set(this.normalizeScanRangeList(site.active_scan_ranges || []));
        const candidateRanges = networks.map(item => item.cidr).filter(Boolean);
        if (!candidateRanges.length) {
            container.innerHTML = '<div class="form-hint">No ranges detected</div>';
            return;
        }
        container.innerHTML = `
            <div class="range-picker">
                <button class="btn btn-secondary btn-sm" type="button" id="activeScanRangesToggle">Active scan ranges (${active.size})</button>
                <div class="range-picker-panel" id="activeScanRangesPanel" hidden>
                    ${candidateRanges.map(range => `
                        <label class="range-item">
                            <input type="checkbox" class="active-scan-range-select" value="${range}" ${active.has(range) ? 'checked' : ''}>
                            <span>${range}</span>
                        </label>
                    `).join('')}
                    <button class="btn btn-primary btn-sm" type="button" id="saveActiveScanRangesBtn">Save Ranges</button>
                </div>
            </div>
        `;
        document.getElementById('activeScanRangesToggle')?.addEventListener('click', () => {
            const panel = document.getElementById('activeScanRangesPanel');
            if (panel) panel.hidden = !panel.hidden;
        });
        document.getElementById('saveActiveScanRangesBtn')?.addEventListener('click', () => {
            const selected = Array.from(document.querySelectorAll('.active-scan-range-select:checked'))
                .map(input => input.value)
                .filter(Boolean);
            const candidateSet = new Set(candidateRanges);
            const preserved = Array.from(active).filter(range => !candidateSet.has(range));
            this.saveSiteActiveScanRanges(site.id, preserved.concat(selected));
        });
    }

    async saveSiteActiveScanRanges(siteId, ranges) {
        const site = (this.sites || []).find(s => s.id === siteId);
        if (!site) {
            this.showError('Site not found');
            return;
        }
        try {
            const response = await fetch(`/api/sites/${siteId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active_scan_ranges: this.normalizeScanRangeList(ranges) })
            });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.error || 'Failed to save active ranges');
            }
            this.showMessage('Active scan ranges saved');
            await this.loadData();
        } catch (error) {
            console.error('Save active ranges error:', error);
            this.showError(error.message || 'Failed to save active ranges');
        }
    }

    getDeviceNetworkOptions(siteDevices, siteName) {
        const configured = this.getConfiguredNetworksForSite(siteName);
        const networkMap = new Map(configured.map(item => [item.cidr, { ...item, count: 0 }]));
        let unknownCount = 0;

        siteDevices.forEach(device => {
            const ip = device.ip || '';
            const value = this.ipToNumber(ip);
            if (value === null) {
                unknownCount += 1;
                return;
            }
            const configuredMatch = configured.find(info => this.ipInCidr(ip, info));
            if (configuredMatch) {
                networkMap.get(configuredMatch.cidr).count += 1;
                return;
            }
            const fallbackCidr = this.cidrForIp(ip, 24);
            const parsed = this.parseCidr(fallbackCidr);
            if (!parsed) {
                unknownCount += 1;
                return;
            }
            if (!networkMap.has(parsed.cidr)) {
                networkMap.set(parsed.cidr, { ...parsed, count: 0 });
            }
            networkMap.get(parsed.cidr).count += 1;
        });

        const networks = Array.from(networkMap.values())
            .filter(item => item.count > 0)
            .sort((a, b) => a.network - b.network || b.prefix - a.prefix);
        return { networks, unknownCount };
    }

    updateDeviceNetworkFilter(siteDevices, siteName) {
        const networkFilter = document.getElementById('deviceNetworkFilter');
        if (!networkFilter) return '';
        if (!siteName) {
            networkFilter.innerHTML = '<option value="">All Networks</option>';
            networkFilter.value = '';
            networkFilter.disabled = true;
            this.devicesNetworkFilter = '';
            return '';
        }

        const { networks, unknownCount } = this.getDeviceNetworkOptions(siteDevices, siteName);
        const currentValue = this.devicesNetworkFilter || networkFilter.value || '';
        const validValues = new Set(['']);
        let html = '<option value="">All Networks</option>';
        networks.forEach(item => {
            validValues.add(item.cidr);
            html += `<option value="${item.cidr}">${item.cidr} (${item.count})</option>`;
        });
        if (unknownCount > 0) {
            validValues.add('__unknown__');
            html += `<option value="__unknown__">Unknown / No IP (${unknownCount})</option>`;
        }
        networkFilter.innerHTML = html;
        networkFilter.disabled = networks.length === 0 && unknownCount === 0;
        const nextValue = validValues.has(currentValue) ? currentValue : '';
        networkFilter.value = nextValue;
        this.devicesNetworkFilter = nextValue;
        this.renderDeviceActiveScanRanges(siteName, networks);
        return nextValue;
    }

    deviceMatchesNetwork(device, networkValue) {
        if (!networkValue) return true;
        const ip = device.ip || '';
        if (networkValue === '__unknown__') {
            return this.ipToNumber(ip) === null;
        }
        return this.ipInCidr(ip, this.parseCidr(networkValue));
    }

    applyDeviceColumnSearch() {
        const filters = {};
        document.querySelectorAll('.device-column-search').forEach(input => {
            const key = input.dataset.deviceFilterKey;
            const value = input.value.trim();
            if (key && value) {
                filters[key] = value.toLowerCase();
            }
        });
        this.deviceColumnFilters = filters;
        this.devicesPage = 1;
        this.updateDevicesTab();
    }

    getDeviceFilterValue(device, key) {
        if (key === 'discovered_at' || key === 'last_seen') {
            if (device[key]) return this.formatTime(device[key]);
            return key === 'last_seen' ? 'Never' : 'N/A';
        }
        return String(device[key] ?? '');
    }

    deviceMatchesColumnFilters(device) {
        const filters = this.deviceColumnFilters || {};
        return Object.entries(filters).every(([key, prefix]) => {
            if (!prefix) return true;
            return this.getDeviceFilterValue(device, key).toLowerCase().startsWith(prefix);
        });
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
        if (filterSite !== this.devicesPageFilter) {
            this.devicesPage = 1;
            this.devicesPageFilter = filterSite;
        }
        const siteDevices = filterSite 
            ? this.devices.filter(d => d.site === filterSite)
            : this.devices;
        const networkValue = this.updateDeviceNetworkFilter(siteDevices, filterSite);
        const filteredDevices = networkValue
            ? siteDevices.filter(device => this.deviceMatchesNetwork(device, networkValue))
            : siteDevices;
        const columnFilteredDevices = filteredDevices.filter(device => this.deviceMatchesColumnFilters(device));
        const rootIpBySite = new Map((this.sites || []).map(site => [site.name, site.root_ip]));
        const sortedDevices = this.sortDevices(columnFilteredDevices);
        const totalDevices = sortedDevices.length;
        const pageSize = this.devicesPageSize || 50;
        const totalPages = Math.max(1, Math.ceil(totalDevices / pageSize));
        if (this.devicesPage > totalPages) {
            this.devicesPage = totalPages;
        }
        const startIndex = (this.devicesPage - 1) * pageSize;
        const pagedDevices = sortedDevices.slice(startIndex, startIndex + pageSize);
        const countLabel = document.getElementById('deviceCountLabel');
        if (countLabel) {
            const count = columnFilteredDevices.length;
            const activeColumnFilters = Object.keys(this.deviceColumnFilters || {}).length;
            const suffix = count === 1 ? 'device' : 'devices';
            const networkScope = networkValue === '__unknown__' ? ' / Unknown' : (networkValue ? ` / ${networkValue}` : '');
            const searchScope = activeColumnFilters ? ` / Search from ${filteredDevices.length}` : '';
            const scope = filterSite ? `in ${filterSite}${networkScope}` : 'total';
            countLabel.textContent = `${count} ${suffix} (${scope}${searchScope})`;
        }
        this.updateDevicesPagination(totalDevices, totalPages);
        
        // Update devices table
        const devicesBody = document.getElementById('devicesTableBody');
        if (pagedDevices.length > 0) {
            devicesBody.innerHTML = pagedDevices.map(device => {
                const checked = this.selectedDeviceIds.has(device.id) ? 'checked' : '';
                const isRoot = device.ip && rootIpBySite.get(device.site) === device.ip;
                const rootBadge = isRoot ? '<span class="status-badge" style="background: rgba(245, 158, 11, 0.15); color: #f59e0b; font-size: 11px;">Root</span>' : '';
                const mapHiddenBadge = device.hide_from_map ? '<span class="status-badge" style="background: rgba(100, 116, 139, 0.15); color: #64748b; font-size: 11px;">Map hidden</span>' : '';
                return `
                    <tr>
                        <td>
                            <input type="checkbox" class="device-select" data-device-id="${device.id}" ${checked}>
                        </td>
                        <td>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <i data-feather="server" style="width: 16px; height: 16px;"></i>
                                <strong>${device.name || device.id}</strong>
                                ${rootBadge}
                                ${mapHiddenBadge}
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
                        <td>${device.domain || 'N/A'}</td>
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
            const networkText = networkValue === '__unknown__' ? 'Unknown / No IP' : networkValue;
            const activeColumnFilters = Object.keys(this.deviceColumnFilters || {}).length;
            const message = activeColumnFilters
                ? 'No devices match the search'
                : filterSite && networkValue
                ? `No devices in "${filterSite}" for ${networkText}`
                : filterSite 
                ? `No devices in site "${filterSite}"`
                : 'No devices found';
                
            devicesBody.innerHTML = `
                <tr>
                    <td colspan="9" class="empty-state">
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
        this.syncSelectAllCheckbox(pagedDevices);
        this.applySortIndicators('devices');
        this.renderSiteMapStatusControls();

        replaceIcons();
    }

    siteMapStatusBadge(site) {
        if (!site || !site.map_reliable) {
            return '<span class="status-badge status-offline">Not reliable</span>';
        }
        const mappedAt = site.map_reliable_at ? ` ${this.formatTime(site.map_reliable_at)}` : '';
        return `<span class="status-badge status-online">Reliable${mappedAt}</span>`;
    }

    updateDevicesPagination(totalDevices, totalPages) {
        const info = document.getElementById('devicesPageInfo');
        if (info) {
            info.textContent = `Page ${this.devicesPage} of ${totalPages} (${totalDevices} devices)`;
        }
        const prevBtn = document.getElementById('devicesPagePrev');
        const nextBtn = document.getElementById('devicesPageNext');
        if (prevBtn) {
            prevBtn.disabled = this.devicesPage <= 1;
        }
        if (nextBtn) {
            nextBtn.disabled = this.devicesPage >= totalPages;
        }
        const sizeSelect = document.getElementById('devicesPageSize');
        if (sizeSelect && String(this.devicesPageSize) !== sizeSelect.value) {
            sizeSelect.value = String(this.devicesPageSize);
        }
    }

    changeDevicesPage(delta) {
        const next = this.devicesPage + delta;
        if (next < 1) return;
        this.devicesPage = next;
        this.updateDevicesTab();
    }

    setDevicesPageSize(size) {
        this.devicesPageSize = size;
        this.devicesPage = 1;
        this.updateDevicesTab();
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
                } else if (target === 'ouiRanges') {
                    this.captureOuiTableRows();
                    this.renderOuiRangesTable();
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
            } else if (state.key === 'map_reliable') {
                valA = `${a.map_reliable ? '1' : '0'}-${a.map_reliable_at || ''}`;
                valB = `${b.map_reliable ? '1' : '0'}-${b.map_reliable_at || ''}`;
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
        const count = this.selectedDeviceIds.size;
        const deleteBtn = document.getElementById('deleteSelectedDevicesBtn');
        const mapBtn = document.getElementById('mapSelectedDevicesBtn');
        const unmapBtn = document.getElementById('unmapSelectedDevicesBtn');
        if (deleteBtn) {
            deleteBtn.disabled = count === 0;
            deleteBtn.innerHTML = `<i data-feather="trash-2"></i> Remove Selected${count ? ` (${count})` : ''}`;
        }
        if (mapBtn) {
            mapBtn.disabled = count === 0;
            mapBtn.innerHTML = `<i data-feather="map-pin"></i> Map Selected${count ? ` (${count})` : ''}`;
        }
        if (unmapBtn) {
            unmapBtn.disabled = count === 0;
            unmapBtn.innerHTML = `<i data-feather="eye-off"></i> Unmap Selected${count ? ` (${count})` : ''}`;
        }
        replaceIcons();
    }

    async setSelectedDevicesMapVisibility(visible) {
        const ids = Array.from(this.selectedDeviceIds);
        if (!ids.length) return;
        const action = visible ? 'show on the map' : 'hide from the map';
        if (!confirm(`${visible ? 'Map' : 'Unmap'} ${ids.length} selected devices?`)) {
            return;
        }
        try {
            const response = await fetch('/api/devices/bulk_map_visibility', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids, visible })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                this.showError(data.error || `Failed to ${action}`);
                return;
            }
            this.selectedDeviceIds.clear();
            this.showMessage(`${data.updated?.length || 0} devices updated`);
            this.loadData();
        } catch (error) {
            console.error('Map visibility update failed:', error);
            this.showError(`Failed to ${action}`);
        }
    }

    async deleteSelectedDevices() {
        const ids = Array.from(this.selectedDeviceIds);
        if (!ids.length) return;
        if (!confirm(`Delete ${ids.length} devices?`)) {
            return;
        }
        const blockRediscovery = confirm('Block these devices from future rediscovery too? Choose Cancel if you only want to delete them for now.');
        const batchSize = 50;
        for (let i = 0; i < ids.length; i += batchSize) {
            const batch = ids.slice(i, i + batchSize);
            try {
                const response = await fetch('/api/devices/bulk_delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ids: batch, block: blockRediscovery })
                });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    this.showError(data.error || 'Failed to delete devices');
                    break;
                }
            } catch (error) {
                console.error('Bulk delete failed:', error);
                this.showError('Failed to delete devices');
                break;
            }
        }
        this.selectedDeviceIds.clear();
        this.updateSelectedDevicesUI();
        this.loadData();
    }

    runDeleteCatchedDevices() {
        const siteFilter = document.getElementById('deviceSiteFilter');
        const siteName = siteFilter ? siteFilter.value : '';
        if (!siteName) {
            this.showError('Select a site first');
            return;
        }
        if (!confirm(`Delete all Catched devices in "${siteName}"?`)) {
            return;
        }
        this.currentSite = siteName;
        this.runModule('delete_catched_devices');
    }

    runEnforceOui() {
        const siteFilter = document.getElementById('deviceSiteFilter');
        const siteName = siteFilter ? siteFilter.value : '';
        if (!siteName) {
            this.showError('Select a site first');
            return;
        }
        if (!confirm(`Run Enforce OUI for "${siteName}"?`)) {
            return;
        }
        this.currentSite = siteName;
        this.runModule('enforce_oui_table');
    }

    runMikrotikDiscovery() {
        const siteFilter = document.getElementById('deviceSiteFilter');
        const siteName = siteFilter ? siteFilter.value : '';
        if (!siteName) {
            this.showError('Select a site first');
            return;
        }
        if (!confirm(`Run MikroTik Discovery for "${siteName}"?`)) {
            return;
        }
        this.currentSite = siteName;
        this.runModule('mikrotik_mac_discovery');
    }

    runDomainLookup() {
        const siteFilter = document.getElementById('deviceSiteFilter');
        const siteName = siteFilter ? siteFilter.value : '';
        if (!siteName) {
            this.showError('Select a site first');
            return;
        }
        this.currentSite = siteName;
        this.runModule('domain_lookup');
    }

    runAddDeviceModule() {
        const siteFilter = document.getElementById('deviceSiteFilter');
        const siteName = siteFilter ? siteFilter.value : '';
        if (!siteName) {
            this.showError('Select a site first');
            return;
        }
        this.currentSite = siteName;
        this.runModule('add_device_manual');
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

    isNvrDevice(device) {
        const vendor = (device.vendor || '').toLowerCase();
        const platform = (device.platform || '').toLowerCase();
        const dtype = (device.type || '').toLowerCase();
        return dtype === 'nvr' ||
            vendor.includes('univiewnvr') ||
            platform.includes('uniview');
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
        const typeInput = document.getElementById('editDeviceOuiType');
        if (!labelInput || !startInput || !endInput) return;
        const label = labelInput.value.trim();
        const start = this.normalizeMac(startInput.value);
        const end = this.normalizeMac(endInput.value);
        const dtype = typeInput ? typeInput.value.trim() : '';
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
            const entries = this.parseOuiRanges(current);
            const existing = entries.find(entry =>
                entry.start.toLowerCase() === start.toLowerCase() &&
                entry.end.toLowerCase() === end.toLowerCase()
            );
            if (existing) {
                existing.label = label;
                existing.dtype = dtype ? dtype.toLowerCase() : '';
            } else {
                entries.push({ start, end, label, dtype: dtype ? dtype.toLowerCase() : '' });
            }
            const content = this.buildOuiRangesText(entries);
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
            this.ouiRangesEntries = this.parseOuiRanges(content);
            this.renderOuiRangesTable();
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
        const typeInput = document.getElementById('editDeviceOuiType');
        if (typeInput) {
            const entries = this.parseOuiRanges(this.ouiRangesText);
            const start = (device.oui_range_start || '').trim().toLowerCase();
            const end = (device.oui_range_end || '').trim().toLowerCase();
            let match = null;
            if (start && end) {
                match = entries.find(entry =>
                    entry.start.toLowerCase() === start && entry.end.toLowerCase() === end
                );
            }
            if (!match) {
                const label = (device.oui_label || device.vendor || '').trim().toLowerCase();
                match = entries.find(entry => entry.label.trim().toLowerCase() === label);
            }
            typeInput.value = match ? (match.dtype || '') : '';
        }
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
        const dtypeInput = document.getElementById('editDeviceOuiType');
        const dtype = dtypeInput ? dtypeInput.value.trim() : '';
        try {
            const response = await fetch(`/api/devices/${deviceId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });
            if (!response.ok) {
                throw new Error('Failed to update OUI');
            }
            if (updates.oui_label && updates.oui_range_start && updates.oui_range_end) {
                const responseRanges = await fetch('/api/oui_ranges');
                const dataRanges = await responseRanges.json();
                if (responseRanges.ok) {
                    const current = dataRanges.content || '';
                    const entries = this.parseOuiRanges(current);
                    const start = updates.oui_range_start.trim();
                    const end = updates.oui_range_end.trim();
                    const existing = entries.find(entry =>
                        entry.start.toLowerCase() === start.toLowerCase() &&
                        entry.end.toLowerCase() === end.toLowerCase()
                    );
                    if (existing) {
                        existing.label = updates.oui_label;
                        existing.dtype = dtype ? dtype.toLowerCase() : '';
                    } else {
                        entries.push({
                            start,
                            end,
                            label: updates.oui_label,
                            dtype: dtype ? dtype.toLowerCase() : ''
                        });
                    }
                    const content = this.buildOuiRangesText(entries);
                    await fetch('/api/oui_ranges', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content })
                    });
                    this.ouiRangesText = content;
                    this.ouiRangesEntries = this.parseOuiRanges(content);
                    this.renderOuiRangesTable();
                }
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
        const agentEligible = new Set(['ubiquiti_cdp_reader', 'uniview_nvr_capture', 'agent_ip_scan']);
        if (this.modules && this.modules.length > 0) {
            modulesGrid.innerHTML = this.modules.map(module => {
                const showAgent = agentEligible.has(module.id);
                const showServer = module.id !== 'agent_ip_scan';
                return `
                    <div class="module-card">
                        <div class="module-header">
                            <i data-feather="box"></i>
                            <h3>${module.name}</h3>
                        </div>
                        <div class="module-description">
                            ${module.description || 'No description available'}
                        </div>
                        <div class="module-actions" style="display:flex; gap:10px; flex-wrap:wrap;">
                            ${showServer ? `
                            <button class="btn btn-primary" onclick="platform.runModule('${module.id}')">
                                <i data-feather="play"></i>
                                Run on Server
                            </button>
                            ` : ''}
                            ${showAgent ? `
                            <button class="btn btn-agent" onclick="platform.runModuleOnAgent('${module.id}')">
                                <i data-feather="cpu"></i>
                                Run on Agent
                            </button>
                            ` : ''}
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
        this.updateScheduleFormSites();
        this.renderScheduleList();
        const scheduleBody = document.getElementById('scheduleModulesBody');
        if (scheduleBody && scheduleBody.children.length === 0) {
            this.addScheduleModuleRow();
        }
        
        replaceIcons();
    }

    updateScheduleFormSites(selectedSites = null) {
        const container = document.getElementById('scheduleSitesSelect');
        if (!container) return;
        const siteNames = (this.sites || []).map(site => site.name);
        const selected = Array.isArray(selectedSites) ? selectedSites : this.collectSelectedSites(container);
        this.renderSiteMultiSelect(container, selected, siteNames);
        this.updateScheduleScopeUI();
    }

    updateScheduleScopeUI() {
        const scopeSelect = document.getElementById('scheduleSiteScope');
        const container = document.getElementById('scheduleSitesSelect');
        if (!scopeSelect || !container) return;
        const mode = scopeSelect.value || 'selected';
        const allMode = mode === 'all';
        container.classList.toggle('is-disabled', allMode);
        const allInput = container.querySelector('input[value="*"]');
        if (allInput) {
            allInput.checked = allMode ? true : false;
        }
        this.applyAllSitesState(container);
        this.updateMultiSelectLabel(container);
    }

    renderScheduleList() {
        const body = document.getElementById('scheduleListBody');
        if (!body) return;
        const schedules = this.schedules || [];
        if (!schedules.length) {
            body.innerHTML = '<tr><td colspan="8">No schedules configured.</td></tr>';
            return;
        }
        body.innerHTML = schedules.map(schedule => {
            const scope = schedule.site_scope || {};
            const rawSites = scope.sites || [];
            const sites = scope.mode === 'all'
                ? 'All sites'
                : (rawSites.length ? rawSites.join(', ') : 'None');
            const moduleLabels = (schedule.modules || []).map(mod => {
                const module = this.modules.find(entry => entry.id === mod.module_id);
                return module ? module.name : mod.module_id;
            });
            const status = schedule.status || (schedule.enabled ? 'idle' : 'disabled');
            const nextRun = schedule.next_run_at ? this.formatDateTime(schedule.next_run_at) : '?';
            const progress = schedule.progress || {};
            const completedSites = progress.completed_sites || 0;
            const totalSites = progress.total_sites || 0;
            const activeSites = progress.active_sites || 0;
            const progressLabel = totalSites ? `${completedSites}/${totalSites} sites` : '?';
            const activeJobs = schedule.active_jobs || [];
            const activeJobsText = activeJobs.slice(0, 3).map(job => {
                const module = this.modules.find(entry => entry.id === job.module_id);
                const name = module ? module.name : job.module_id;
                const site = job.site_name || 'unknown site';
                const jobStatus = job.status || 'running';
                return `${name} for ${site} - ${jobStatus}`;
            }).join('<br>');
            const moreJobs = activeJobs.length > 3 ? `<div class="meta">+${activeJobs.length - 3} more</div>` : '';
            const statusBadgeClass = status === 'running'
                ? 'status-online'
                : status === 'disabled'
                    ? 'status-offline'
                    : 'status-unknown';
            return `
                <tr data-id="${schedule.id}">
                    <td>${schedule.name}</td>
                    <td>${sites}</td>
                    <td>${moduleLabels.join(', ') || 'None'}</td>
                    <td>${schedule.delay_between_modules_sec || 0}s</td>
                    <td>${schedule.repeat_interval_min || 0}m</td>
                    <td>
                        <div>
                            <span class="status-badge ${statusBadgeClass}">
                                ${status}
                            </span>
                        </div>
                        <div class="meta" style="margin-top: 6px;">
                            ${progressLabel}${activeSites ? ` ? ${activeSites} active` : ''}
                        </div>
                        ${activeJobsText ? `<div class="meta" style="margin-top: 6px; line-height: 1.4;">${activeJobsText}${moreJobs}</div>` : ''}
                    </td>
                    <td>${nextRun}</td>
                    <td style="display:flex; gap:8px;">
                        <button class="btn btn-secondary schedule-run" type="button">Run</button>
                        <button class="btn btn-secondary schedule-edit" type="button">Edit</button>
                        <button class="btn btn-secondary schedule-toggle" type="button">${schedule.enabled ? 'Disable' : 'Enable'}</button>
                        <button class="btn btn-secondary schedule-remove" type="button">Delete</button>
                    </td>
                </tr>
            `;
        }).join('');

        body.querySelectorAll('.schedule-run').forEach(btn => {
            btn.addEventListener('click', (event) => {
                const row = event.currentTarget.closest('tr');
                if (row) {
                    this.runScheduleNow(row.dataset.id);
                }
            });
        });
        body.querySelectorAll('.schedule-edit').forEach(btn => {
            btn.addEventListener('click', (event) => {
                const row = event.currentTarget.closest('tr');
                if (row) {
                    this.editSchedule(row.dataset.id);
                }
            });
        });
        body.querySelectorAll('.schedule-toggle').forEach(btn => {
            btn.addEventListener('click', (event) => {
                const row = event.currentTarget.closest('tr');
                if (row) {
                    this.toggleSchedule(row.dataset.id);
                }
            });
        });
        body.querySelectorAll('.schedule-remove').forEach(btn => {
            btn.addEventListener('click', (event) => {
                const row = event.currentTarget.closest('tr');
                if (row) {
                    this.deleteSchedule(row.dataset.id);
                }
            });
        });
    }

    clearScheduleForm() {
        this.scheduleEditId = null;
        this.scheduleModuleEditRow = null;
        const nameInput = document.getElementById('scheduleName');
        const enabledInput = document.getElementById('scheduleEnabled');
        const scopeSelect = document.getElementById('scheduleSiteScope');
        const runMode = document.getElementById('scheduleRunMode');
        const delayInput = document.getElementById('scheduleDelaySeconds');
        const repeatInput = document.getElementById('scheduleRepeatMinutes');
        if (nameInput) nameInput.value = '';
        if (enabledInput) enabledInput.checked = true;
        if (scopeSelect) scopeSelect.value = 'selected';
        if (runMode) runMode.value = 'sequential';
        if (delayInput) delayInput.value = 30;
        if (repeatInput) repeatInput.value = 60;

        const body = document.getElementById('scheduleModulesBody');
        if (body) {
            body.innerHTML = '';
        }
        this.addScheduleModuleRow();
        this.updateScheduleFormSites([]);
    }

    addScheduleModuleRow(prefill = {}) {
        const body = document.getElementById('scheduleModulesBody');
        if (!body) return;
        const row = document.createElement('tr');
        row.innerHTML = `
            <td class="schedule-order"></td>
            <td>
                <select class="schedule-module-select"></select>
            </td>
            <td>
                <select class="schedule-credential-select">
                    <option value="">Manual</option>
                </select>
            </td>
            <td>
                <div style="display:flex; flex-direction: column; gap: 6px;">
                    <button class="btn btn-secondary schedule-configure" type="button">Configure</button>
                    <span class="schedule-config-summary">Not configured</span>
                </div>
            </td>
            <td>
                <button class="btn btn-secondary schedule-move-up" type="button">Up</button>
                <button class="btn btn-secondary schedule-move-down" type="button">Down</button>
                <button class="btn btn-secondary schedule-remove-row" type="button">Remove</button>
            </td>
        `;
        body.appendChild(row);

        const moduleSelect = row.querySelector('.schedule-module-select');
        const credentialSelect = row.querySelector('.schedule-credential-select');
        const summaryEl = row.querySelector('.schedule-config-summary');

        const options = (this.modules || []).map(module => (
            `<option value="${module.id}">${module.name || module.id}</option>`
        )).join('');
        moduleSelect.innerHTML = options || '<option value="">No modules</option>';
        moduleSelect.value = prefill.module_id || moduleSelect.value;

        if (prefill.parameters) {
            row.dataset.parameters = JSON.stringify(prefill.parameters);
        }
        this.updateScheduleRowSummary(row);

        const updateProfiles = () => {
            const moduleId = moduleSelect.value;
            const profiles = this.getRunnableModuleCredentialProfiles(moduleId);
            const options = ['<option value="">Manual</option>']
                .concat(profiles.map(profile => `<option value="${profile.name}">${profile.name}</option>`));
            credentialSelect.innerHTML = options.join('');
            credentialSelect.value = prefill.credential_profile || '';
        };
        updateProfiles();

        moduleSelect.addEventListener('change', () => {
            prefill = {};
            updateProfiles();
            row.dataset.parameters = '';
            this.updateScheduleRowSummary(row);
        });

        row.querySelector('.schedule-configure').addEventListener('click', () => {
            this.openScheduleModuleConfig(row);
        });

        row.querySelector('.schedule-move-up').addEventListener('click', () => {
            const prev = row.previousElementSibling;
            if (prev) {
                row.parentNode.insertBefore(row, prev);
                this.updateScheduleOrder();
            }
        });

        row.querySelector('.schedule-move-down').addEventListener('click', () => {
            const next = row.nextElementSibling;
            if (next) {
                row.parentNode.insertBefore(next, row);
                this.updateScheduleOrder();
            }
        });

        row.querySelector('.schedule-remove-row').addEventListener('click', () => {
            row.remove();
            this.updateScheduleOrder();
        });

        this.updateScheduleOrder();
    }

    updateScheduleOrder() {
        const rows = document.querySelectorAll('#scheduleModulesBody tr');
        rows.forEach((row, index) => {
            const orderCell = row.querySelector('.schedule-order');
            if (orderCell) {
                orderCell.textContent = String(index + 1);
            }
        });
    }

    updateScheduleRowSummary(row) {
        if (!row) return;
        const summaryEl = row.querySelector('.schedule-config-summary');
        if (!summaryEl) return;
        const params = row.dataset.parameters;
        summaryEl.textContent = params ? 'Configured' : 'Not configured';
    }

    getScheduleEditorSite() {
        const scopeMode = document.getElementById('scheduleSiteScope')?.value || 'selected';
        if (scopeMode === 'all') {
            return this.currentSite || (this.sites?.[0]?.name || '');
        }
        const container = document.getElementById('scheduleSitesSelect');
        const selected = this.collectSelectedSites(container).filter(site => site !== '*');
        if (selected.length) {
            return selected[0];
        }
        return this.currentSite || (this.sites?.[0]?.name || '');
    }

    openScheduleModuleConfig(row) {
        const moduleId = row.querySelector('.schedule-module-select')?.value;
        const module = this.modules.find(entry => entry.id === moduleId);
        if (!module) {
            this.showError('Module not found');
            return;
        }
        this.scheduleModuleEditRow = row;
        const siteName = this.getScheduleEditorSite();
        const prefill = row.dataset.parameters ? JSON.parse(row.dataset.parameters) : {};
        const rowCredential = row.querySelector('.schedule-credential-select')?.value || '';
        const mergedPrefill = rowCredential ? { ...prefill, credential_profile: rowCredential } : prefill;
        const modal = document.getElementById('scheduleModuleModal');
        const title = document.getElementById('scheduleModuleTitle');
        const formContainer = document.getElementById('scheduleModuleFormContainer');
        if (title) {
            title.textContent = `Configure: ${module.name || module.id}`;
        }
        this.renderModuleFormInto(formContainer, module, siteName, mergedPrefill, {
            includeCredentialProfiles: true,
            showSiteDisplay: true,
            idPrefix: 'schedule_module_'
        });
        if (!siteName && formContainer) {
            const note = document.createElement('div');
            note.style.marginBottom = '12px';
            note.style.color = 'var(--text-secondary)';
            note.textContent = 'Select a site to preview device lists.';
            formContainer.prepend(note);
        }
        modal.classList.add('active');
    }

    saveScheduleModuleConfig() {
        const row = this.scheduleModuleEditRow;
        if (!row) {
            this.closeAllModals();
            return;
        }
        const moduleId = row.querySelector('.schedule-module-select')?.value;
        const module = this.modules.find(entry => entry.id === moduleId);
        if (!module) {
            this.showError('Module not found');
            return;
        }
        const inputResult = this.collectModuleInputs(module, 'schedule_module_');
        if (!inputResult.isValid) {
            this.showError(inputResult.error || 'Please fill all required fields');
            return;
        }
        row.dataset.parameters = JSON.stringify(inputResult.inputs || {});
        this.updateScheduleRowSummary(row);
        this.scheduleModuleEditRow = null;
        this.closeAllModals();
    }

    collectScheduleForm() {
        const name = document.getElementById('scheduleName')?.value.trim();
        const enabled = document.getElementById('scheduleEnabled')?.checked ?? true;
        const scopeMode = document.getElementById('scheduleSiteScope')?.value || 'selected';
        const siteContainer = document.getElementById('scheduleSitesSelect');
        let selectedSites = this.collectSelectedSites(siteContainer);
        if (scopeMode !== 'all') {
            selectedSites = selectedSites.filter(s => s !== '*');
            if (!selectedSites.length) {
                const fallback = this.currentSite || (this.sites?.[0]?.name || '');
                if (fallback) {
                    selectedSites = [fallback];
                } else {
                    this.showError('Select at least one site for this schedule.');
                    return null;
                }
            }
        }
        const runMode = document.getElementById('scheduleRunMode')?.value || 'sequential';
        const delay = parseInt(document.getElementById('scheduleDelaySeconds')?.value || '0', 10);
        const repeat = parseInt(document.getElementById('scheduleRepeatMinutes')?.value || '0', 10);

        if (!name) {
            this.showError('Schedule name is required');
            return null;
        }
        const modules = [];
        const rows = document.querySelectorAll('#scheduleModulesBody tr');
        for (const row of rows) {
            const moduleId = row.querySelector('.schedule-module-select')?.value;
            const credential = row.querySelector('.schedule-credential-select')?.value || '';
            if (!moduleId) continue;
            let params = {};
            if (row.dataset.parameters) {
                try {
                    params = JSON.parse(row.dataset.parameters);
                } catch (err) {
                    this.showError('Invalid module configuration data.');
                    return null;
                }
            }
            const entry = { module_id: moduleId, parameters: params };
            if (credential) {
                entry.credential_profile = credential;
            }
            modules.push(entry);
        }

        if (!modules.length) {
            this.showError('Add at least one module to the schedule');
            return null;
        }

        return {
            id: this.scheduleEditId || undefined,
            name,
            enabled,
            site_scope: { mode: scopeMode, sites: scopeMode === 'all' ? [] : selectedSites },
            site_run_mode: runMode,
            delay_between_modules_sec: Number.isFinite(delay) ? delay : 0,
            repeat_interval_min: Number.isFinite(repeat) ? repeat : 0,
            modules
        };
    }

    async saveSchedule() {
        const payload = this.collectScheduleForm();
        if (!payload) return;
        const method = this.scheduleEditId ? 'PUT' : 'POST';
        const endpoint = this.scheduleEditId ? `/api/schedules/${this.scheduleEditId}` : '/api/schedules';
        try {
            const response = await fetch(endpoint, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to save schedule');
                return;
            }
            this.showMessage('Schedule saved');
            if (data.enabled) {
                await this.runScheduleNow(data.id, { silent: true });
            }
            await this.refreshSchedules();
            this.clearScheduleForm();
        } catch (error) {
            console.error('Error saving schedule:', error);
            this.showError('Failed to save schedule');
        }
    }

    async refreshSchedules() {
        const schedules = await this.fetchData('/api/schedules', { timeoutMs: 8000 });
        if (schedules) {
            this.schedules = schedules;
            this.renderScheduleList();
        }
        await this.refreshModuleJobs();
    }

    async refreshModuleJobs() {
        const data = await this.fetchData('/api/modules/status', { timeoutMs: 8000 });
        if (data && Array.isArray(data.running_jobs)) {
            this.serverModuleJobs = data.running_jobs;
        }
        this.updateModuleJobs();
    }

    editSchedule(scheduleId) {
        const schedule = (this.schedules || []).find(entry => entry.id === scheduleId);
        if (!schedule) {
            this.showError('Schedule not found');
            return;
        }
        this.scheduleEditId = scheduleId;
        document.getElementById('scheduleName').value = schedule.name || '';
        document.getElementById('scheduleEnabled').checked = !!schedule.enabled;
        document.getElementById('scheduleSiteScope').value = (schedule.site_scope?.mode || 'selected');
        document.getElementById('scheduleRunMode').value = schedule.site_run_mode || 'sequential';
        document.getElementById('scheduleDelaySeconds').value = schedule.delay_between_modules_sec ?? 0;
        document.getElementById('scheduleRepeatMinutes').value = schedule.repeat_interval_min ?? 0;

        const body = document.getElementById('scheduleModulesBody');
        body.innerHTML = '';
        (schedule.modules || []).forEach(entry => this.addScheduleModuleRow(entry));
        if (!(schedule.modules || []).length) {
            this.addScheduleModuleRow();
        }
        this.updateScheduleFormSites(schedule.site_scope?.mode === 'all' ? ['*'] : (schedule.site_scope?.sites || []));
    }

    async deleteSchedule(scheduleId) {
        if (!confirm('Delete this schedule?')) return;
        try {
            const response = await fetch(`/api/schedules/${scheduleId}`, { method: 'DELETE' });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to delete schedule');
                return;
            }
            this.showMessage('Schedule deleted');
            await this.refreshSchedules();
        } catch (error) {
            console.error('Error deleting schedule:', error);
            this.showError('Failed to delete schedule');
        }
    }

    async runScheduleNow(scheduleId, options = {}) {
        try {
            const response = await fetch(`/api/schedules/${scheduleId}/run_now`, { method: 'POST' });
            const data = await response.json();
            if (!response.ok) {
                const details = Array.isArray(data.details) ? data.details.join('; ') : '';
                const message = details ? `${data.error || 'Failed to run schedule'}: ${details}` : (data.error || 'Failed to run schedule');
                this.showError(message);
                return;
            }
            if (!options.silent) {
                this.showMessage('Schedule queued');
            }
            await this.refreshSchedules();
        } catch (error) {
            console.error('Error running schedule:', error);
            if (!options.silent) {
                this.showError('Failed to run schedule');
            }
        }
    }

    async toggleSchedule(scheduleId) {
        const schedule = (this.schedules || []).find(entry => entry.id === scheduleId);
        if (!schedule) return;
        const payload = { ...schedule, enabled: !schedule.enabled };
        try {
            const response = await fetch(`/api/schedules/${scheduleId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to update schedule');
                return;
            }
            await this.refreshSchedules();
        } catch (error) {
            console.error('Error updating schedule:', error);
            this.showError('Failed to update schedule');
        }
    }

    formatDateTime(isoString) {
        if (!isoString) return '—';
        const date = new Date(isoString);
        if (Number.isNaN(date.getTime())) return isoString;
        return date.toLocaleString();
    }

    openExportDevices() {
        const siteSelect = document.getElementById('deviceSiteFilter');
        const selectedSite = siteSelect ? siteSelect.value : '';
        this.currentSite = selectedSite;
        this.updateCurrentSiteDisplay();
        this.runModule('export_devices', {
            site_scope: selectedSite ? 'current' : 'all',
            selected_only: this.selectedDeviceIds.size > 0
        });
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
        const moduleMax = document.getElementById('moduleMaxConcurrent');
        if (moduleMax) {
            moduleMax.value = this.settings.module_max_concurrent || 2;
            moduleMax.disabled = this.currentUserRole !== 'admin';
        }
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
        const auditLogsSection = document.getElementById('auditLogsSection');
        if (auditLogsSection) {
            auditLogsSection.style.display = this.currentUserRole === 'admin' ? 'block' : 'none';
        }
        const ouiRangesSection = document.getElementById('ouiRangesSection');
        if (ouiRangesSection) {
            ouiRangesSection.style.display = this.currentUserRole === 'admin' ? 'block' : 'none';
            const textarea = document.getElementById('ouiRangesText');
            if (textarea && this.ouiRangesText) {
                textarea.value = this.ouiRangesText;
            }
            if (this.currentUserRole === 'admin') {
                this.renderOuiRangesTable();
            }
        }
        const moduleCredsSection = document.getElementById('moduleCredentialsSection');
        if (moduleCredsSection) {
            moduleCredsSection.style.display = this.currentUserRole === 'admin' ? 'block' : 'none';
            if (this.currentUserRole === 'admin') {
                this.renderModuleCredentials();
            }
        }
        if (this.currentUserRole === 'admin') {
            const addSites = document.getElementById('addUserSites');
            if (addSites) {
                this.renderSiteMultiSelect(addSites, [], (this.sites || []).map(site => site.name));
            }
        }
    }

    async loadAuditLogDays() {
        if (this.currentUserRole !== 'admin') return;
        const select = document.getElementById('auditLogDay');
        if (!select) return;
        const data = await this.fetchData('/api/audit/logs', { timeoutMs: 8000 });
        const logs = data?.logs || [];
        select.innerHTML = logs.length
            ? logs.map(log => `<option value="${this.escapeHtml(log.day)}">${this.escapeHtml(log.day)} (${this.formatBytes(log.size || 0)})</option>`).join('')
            : '<option value="">No logs yet</option>';
        const meta = document.getElementById('auditLogsMeta');
        if (meta) {
            meta.textContent = `Audit logs are kept for ${data?.retention_days || 14} days.`;
        }
        if (logs.length) {
            await this.loadAuditEvents();
        } else {
            this.renderAuditEvents([]);
        }
    }

    async loadAuditEvents() {
        if (this.currentUserRole !== 'admin') return;
        const day = document.getElementById('auditLogDay')?.value || '';
        if (!day) {
            this.renderAuditEvents([]);
            this.updateAuditPagination({ page: 1, pages: 1, has_prev: false, has_next: false });
            return;
        }
        const eventFilter = document.getElementById('auditLogEvent')?.value.trim() || '';
        const search = document.getElementById('auditLogSearch')?.value.trim() || '';
        const page = Math.max(1, this.auditLogPage || 1);
        const params = new URLSearchParams({ limit: '50', page: String(page) });
        if (eventFilter) params.set('event', eventFilter);
        if (search) params.set('q', search);
        const data = await this.fetchData(`/api/audit/logs/${encodeURIComponent(day)}?${params.toString()}`, { timeoutMs: 10000 });
        if (data?.page) {
            this.auditLogPage = data.page;
        }
        this.renderAuditEvents(data?.events || []);
        const meta = document.getElementById('auditLogsMeta');
        if (meta && data) {
            meta.textContent = `Showing ${data.count || 0} of ${data.total || 0} event(s) from ${day}. Raw files are retained for 14 days.`;
        }
        this.updateAuditPagination(data);
    }

    renderAuditEvents(events) {
        const body = document.getElementById('auditLogsTableBody');
        if (!body) return;
        if (!events.length) {
            body.innerHTML = '<tr><td colspan="5">No audit events</td></tr>';
            return;
        }
        body.innerHTML = events.map(event => {
            const details = { ...event };
            ['ts', 'event', 'actor', 'role', 'client_ip', 'user_agent'].forEach(key => delete details[key]);
            const detailText = JSON.stringify(details);
            return `
                <tr>
                    <td>${this.escapeHtml(this.formatDateTime(event.ts))}</td>
                    <td>${this.escapeHtml(event.event || '')}</td>
                    <td>${this.escapeHtml(event.actor || '')}</td>
                    <td>${this.escapeHtml(event.client_ip || '')}</td>
                    <td><code style="white-space: pre-wrap;">${this.escapeHtml(detailText)}</code></td>
                </tr>
            `;
        }).join('');
    }

    updateAuditPagination(data) {
        const pageInfo = document.getElementById('auditLogPageInfo');
        const prevBtn = document.getElementById('auditLogPrevBtn');
        const nextBtn = document.getElementById('auditLogNextBtn');
        const page = data?.page || 1;
        const pages = data?.pages || 1;
        if (pageInfo) {
            pageInfo.textContent = `Page ${page} / ${pages}`;
        }
        if (prevBtn) {
            prevBtn.disabled = !data?.has_prev;
        }
        if (nextBtn) {
            nextBtn.disabled = !data?.has_next;
        }
    }

    downloadAuditLog() {
        if (this.currentUserRole !== 'admin') return;
        const day = document.getElementById('auditLogDay')?.value || '';
        if (!day) {
            this.showError('No audit log selected');
            return;
        }
        window.location.href = `/api/audit/logs/${encodeURIComponent(day)}/download`;
    }

    formatBytes(value) {
        const bytes = Number(value) || 0;
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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

    async runModule(moduleId, prefill = {}) {
        if (!this.currentSite && moduleId !== 'export_devices') {
            this.showError('Please select a site first');
            return;
        }

        const module = this.modules.find(m => m.id === moduleId);
        if (!module) {
            this.showError('Module not found');
            return;
        }

        // Show module form
        this.showModuleForm(module, prefill);
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
        const savedPrefill = this.getModuleLastParams(module.id, this.currentSite);
        const mergedPrefill = { ...savedPrefill, ...prefill };
        this.renderModuleFormInto(formContainer, module, this.currentSite, mergedPrefill, {
            includeCredentialProfiles: true,
            showSiteDisplay: true,
            idPrefix: 'module_'
        });

        const credentialSelect = document.getElementById('module_credential_profile');
        if (credentialSelect) {
            credentialSelect.addEventListener('change', () => {
                const selected = credentialSelect.value;
                if (!selected) {
                    return;
                }
                const profile = this.getModuleCredentialProfiles(module.id).find(entry => entry.name === selected);
                if (!profile) {
                    return;
                }
                const usernameInput = document.getElementById('module_username');
                const passwordInput = document.getElementById('module_password');
                if (usernameInput) {
                    usernameInput.value = profile.username || '';
                }
                if (passwordInput) {
                    passwordInput.value = profile.password || '';
                }
            });
        }

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

    getModuleLastParams(moduleId, siteName) {
        const byModule = this.moduleLastParams?.[moduleId] || {};
        if (siteName && byModule[siteName]) {
            return byModule[siteName];
        }
        return byModule['*'] || {};
    }

    renderModuleFormInto(container, module, siteName, prefill = {}, options = {}) {
        if (!container) return;
        const includeCredentialProfiles = !!options.includeCredentialProfiles;
        const showSiteDisplay = !!options.showSiteDisplay;
        const idPrefix = options.idPrefix || 'module_';
        const formHTML = this.buildModuleFormHTML(module, siteName, includeCredentialProfiles, showSiteDisplay, idPrefix);
        container.innerHTML = formHTML;
        this.applyPrefillValues(prefill, idPrefix);
        this.bindManualDeviceButtons(module, idPrefix);
    }

    buildModuleFormHTML(module, siteName, includeCredentialProfiles, showSiteDisplay, idPrefix) {
        let formHTML = '';
        if (includeCredentialProfiles) {
            const supportsCredentialProfiles = this.moduleCredentialTargets.some(target => target.id === module.id);
            if (supportsCredentialProfiles) {
                const profiles = this.getModuleCredentialProfiles(module.id);
                const options = profiles.map(profile => (
                    `<option value="${profile.name}">${profile.name} (${profile.username || 'user'})</option>`
                )).join('');
                formHTML += `
                    <div class="form-group">
                        <label for="${idPrefix}credential_profile">Credential Profile</label>
                        <select id="${idPrefix}credential_profile">
                            <option value="">Manual</option>
                            ${options}
                        </select>
                    </div>
                `;
            }
        }

        if (module.inputs && module.inputs.length > 0) {
            module.inputs.forEach(input => {
                if (input.name === 'site') {
                    return;
                }
                if (input.type === 'select') {
                    formHTML += `
                        <div class="form-group">
                            <label for="${idPrefix}${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                            <select id="${idPrefix}${input.name}" ${input.required ? 'required' : ''}>
                                ${(input.options || []).map(opt =>
                                    `<option value="${opt}" ${opt === input.default ? 'selected' : ''}>${opt}</option>`
                                ).join('')}
                            </select>
                        </div>
                    `;
                } else if (input.type === 'multi_select') {
                    const defaultValues = Array.isArray(input.default) ? input.default : (input.default ? [input.default] : []);
                    formHTML += `
                        <div class="form-group">
                            <label for="${idPrefix}${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                            <select id="${idPrefix}${input.name}" multiple size="${Math.min(Math.max((input.options || []).length, 4), 8)}" ${input.required ? 'required' : ''}>
                                ${(input.options || []).map(opt =>
                                    `<option value="${this.escapeHtml(opt)}" ${defaultValues.includes(opt) ? 'selected' : ''}>${this.escapeHtml(opt)}</option>`
                                ).join('')}
                            </select>
                        </div>
                    `;
                } else if (input.type === 'device_select') {
                    const siteDevices = this.getDevicesForSite(siteName, input);
                    const options = siteDevices.map(d => {
                        const ip = d.ip ? ` (${d.ip})` : '';
                        const dtype = d.type ? ` [${d.type}]` : '';
                        const mac = input.show_mac && d.mac ? ` - ${d.mac}` : '';
                        return `<option value="${d.id}">${d.name}${ip}${dtype}${mac}</option>`;
                    }).join('');
                    formHTML += `
                        <div class="form-group">
                            <label for="${idPrefix}${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                            <select id="${idPrefix}${input.name}" ${input.required ? 'required' : ''}>
                                <option value="">Select device</option>
                                ${options}
                            </select>
                        </div>
                    `;
                } else if (input.type === 'device_table') {
                    const siteDevices = this.getDevicesForSite(siteName);
                    let autoselect = false;
                    const rows = siteDevices.map(d => {
                        let checked = '';
                        if (module.id === 'ubiquiti_cdp_reader' && this.isUbiquitiDevice(d)) {
                            checked = 'checked';
                            autoselect = true;
                        }
                        if ((module.id === 'uniview_nvr_capture' || module.id === 'uniview_device_type_check') && this.isNvrDevice(d)) {
                            checked = 'checked';
                            autoselect = true;
                        }
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
                                    <tbody id="${idPrefix}${input.name}_table">
                                        ${rows || '<tr><td colspan="5">No devices in this site.</td></tr>'}
                                    </tbody>
                                </table>
                            </div>
                            <div class="form-group" style="margin-top: 12px; display: grid; grid-template-columns: 1fr 1fr auto; gap: 8px;">
                                <input type="text" id="${idPrefix}${input.name}_manual_name" placeholder="Device name">
                                <input type="text" id="${idPrefix}${input.name}_manual_ip" placeholder="IP address">
                                <button class="btn btn-secondary" type="button" id="${idPrefix}${input.name}_manual_add">Add</button>
                            </div>
                        </div>
                    `;
                    if (autoselect) {
                        formHTML += `<input type="hidden" id="${idPrefix}${input.name}_autoselect" value="1">`;
                    }
                } else if (input.type === 'checkbox') {
                    const infoText = input.info || input.description || '';
                    const infoIcon = infoText
                        ? `<span class="info-icon" title="${this.escapeHtml(infoText)}">i</span>`
                        : '';
                    formHTML += `
                        <div class="form-group">
                            <label class="checkbox-label">
                                <input type="checkbox"
                                       id="${idPrefix}${input.name}"
                                       ${input.default ? 'checked' : ''}>
                                <span>${input.label}</span>
                                ${infoIcon}
                            </label>
                        </div>
                    `;
                } else if (input.type === 'textarea') {
                    formHTML += `
                        <div class="form-group">
                            <label for="${idPrefix}${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                            <textarea id="${idPrefix}${input.name}"
                                      placeholder="${input.placeholder || ''}"
                                      ${input.required ? 'required' : ''}
                                      rows="4">${input.default || ''}</textarea>
                        </div>
                    `;
                } else if (input.type === 'info') {
                    formHTML += `
                        <div class="form-group">
                            <label>${this.escapeHtml(input.label || 'Note')}</label>
                            <div style="padding: 10px 12px; border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-secondary); background: rgba(255,255,255,0.04);">
                                ${this.escapeHtml(input.description || '')}
                            </div>
                        </div>
                    `;
                } else {
                    const inputType = (input.type === 'credential' || input.type === 'password') ? 'password' : 'text';
                    formHTML += `
                        <div class="form-group">
                            <label for="${idPrefix}${input.name}">${input.label} ${input.required ? '*' : ''}</label>
                            <input type="${inputType}" 
                                   id="${idPrefix}${input.name}" 
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

        if (showSiteDisplay) {
            const siteLabel = siteName || 'No site selected';
            formHTML += `
                <input type="hidden" id="${idPrefix}site_name" value="${siteName || ''}">
                <div class="form-group">
                    <label>Site</label>
                    <div style="padding: 10px 14px; background: rgba(255,255,255,0.05); border-radius: 12px; border: 1px solid var(--border-color);">
                        ${siteLabel}
                    </div>
                </div>
            `;
        }
        return formHTML;
    }

    escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    bindManualDeviceButtons(module, idPrefix) {
        if (module.inputs && module.inputs.length > 0) {
            module.inputs.forEach(input => {
                if (input.type !== 'device_table') {
                    return;
                }
                const addBtn = document.getElementById(`${idPrefix}${input.name}_manual_add`);
                const nameInput = document.getElementById(`${idPrefix}${input.name}_manual_name`);
                const ipInput = document.getElementById(`${idPrefix}${input.name}_manual_ip`);
                const tableBody = document.getElementById(`${idPrefix}${input.name}_table`);
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
    }

    applyPrefillValues(prefill, idPrefix) {
        Object.entries(prefill || {}).forEach(([key, value]) => {
            const element = document.getElementById(`${idPrefix}${key}`);
            if (!element) {
                return;
            }
            if (element.type === 'checkbox') {
                element.checked = Boolean(value);
            } else if (element.multiple && Array.isArray(value)) {
                Array.from(element.options).forEach(option => {
                    option.selected = value.includes(option.value);
                });
            } else {
                element.value = value;
            }
        });
    }

    getDevicesForSite(siteName, input = null) {
        if (!siteName) {
            return [];
        }
        let devices = (this.devices || []).filter(d => d.site === siteName);
        if (!input) {
            return devices;
        }
        const typeFilters = Array.isArray(input.device_types) ? input.device_types.map(v => String(v).toLowerCase()) : [];
        const nameContains = Array.isArray(input.device_name_contains) ? input.device_name_contains.map(v => String(v).toLowerCase()) : [];
        if (!typeFilters.length && !nameContains.length) {
            return devices;
        }
        return devices.filter(d => {
            const type = String(d.type || '').toLowerCase();
            const name = String(d.name || '').toLowerCase();
            const vendor = String(d.vendor || '').toLowerCase();
            if (typeFilters.length && !typeFilters.includes(type)) {
                return false;
            }
            if (nameContains.length) {
                const match = nameContains.some(token => token && (name.includes(token) || vendor.includes(token)));
                if (!match) {
                    return false;
                }
            }
            return true;
        });
    }

    async startModule(module) {
        const formContainer = document.getElementById('moduleFormContainer');
        const statusDisplay = document.getElementById('moduleStatusDisplay');
        const startBtn = document.getElementById('startModuleBtn');
        
        // Validate form
        const inputResult = this.collectModuleInputs(module, 'module_');
        const inputs = inputResult.inputs;
        const isValid = inputResult.isValid;
        if (inputResult.error) {
            this.showError(inputResult.error);
            return;
        }
        
        if (!isValid) {
            this.showError('Please fill all required fields');
            return;
        }
        
        // Prepare config
        const config = {
            site_name: this.currentSite,
            parameters: inputs
        };
        if (module.id === 'export_devices') {
            config.parameters.selected_device_ids = Array.from(this.selectedDeviceIds || []);
        }
        
        // Show status display
        statusDisplay.style.display = 'block';
        statusDisplay.querySelector('.status-message').textContent = 'Starting module...';
        statusDisplay.querySelector('.progress-fill').style.width = '5%';
        const logWrap = document.getElementById('moduleLogOutput');
        if (logWrap) {
            logWrap.style.display = 'block';
            const textarea = logWrap.querySelector('textarea');
            if (textarea) {
                textarea.value = '';
            }
        }
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
                this.moduleLogCache.delete(result.thread_id);
                if (statusDisplay) {
                    statusDisplay.dataset.logThread = result.thread_id;
                }
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
                this.updateModuleLog(result.thread_id, false);
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
            const terminalStates = ['completed', 'failed', 'error', 'timeout'];
            const isTerminal = terminalStates.includes(status.status);
            const statusDisplay = document.getElementById('moduleStatusDisplay');
            if (statusDisplay) {
                statusDisplay.dataset.logThread = threadId;
            }
            
            if (statusDisplay.style.display === 'block') {
                if (status.progress) {
                    statusDisplay.querySelector('.progress-fill').style.width = `${status.progress}%`;
                }
                
                if (isTerminal) {
                    this.updateModuleLog(threadId, false);
                    setTimeout(() => {
                        this.updateModuleLog(threadId, true);
                    }, 500);
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
                    
                    this.renderModuleDownload(status);
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
            if (!isTerminal) {
                this.updateModuleLog(threadId, false);
            } else {
                setTimeout(() => {
                    this.updateModuleLog(threadId, true);
                }, 500);
            }
            this.renderModuleDownload(status);
            
        } catch (error) {
            console.error('Error updating module status:', error);
        }
    }

    renderModuleDownload(status) {
        const statusDisplay = document.getElementById('moduleStatusDisplay');
        if (!statusDisplay) return;
        const details = statusDisplay.querySelector('.status-details');
        if (!details) return;
        const exportInfo = status?.output?.export;
        if (!exportInfo || !exportInfo.content_base64) {
            return;
        }
        const filename = exportInfo.filename || 'devices_export.csv';
        const buttonId = 'moduleExportDownloadBtn';
        if (document.getElementById(buttonId)) {
            return;
        }
        const btn = document.createElement('button');
        btn.className = 'btn btn-secondary';
        btn.id = buttonId;
        btn.type = 'button';
        btn.textContent = 'Download CSV';
        btn.addEventListener('click', () => {
            try {
                const bytes = Uint8Array.from(atob(exportInfo.content_base64), c => c.charCodeAt(0));
                const blob = new Blob([bytes], { type: 'text/csv;charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            } catch (err) {
                console.error('Download failed', err);
                this.showError('Failed to download CSV');
            }
        });
        details.appendChild(btn);
    }

    collectModuleInputs(module, idPrefix) {
        const inputs = {};
        let isValid = true;
        let error = '';

        const credentialSelect = document.getElementById(`${idPrefix}credential_profile`);
        const credentialProfile = credentialSelect && credentialSelect.value ? credentialSelect.value : '';
        const credentialFields = new Set(['username', 'password']);

        (module.inputs || []).forEach(input => {
            if (input.name === 'site') {
                return;
            }
            if (input.type === 'device_table') {
                const tableBody = document.getElementById(`${idPrefix}${input.name}_table`);
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
                const autoMarker = document.getElementById(`${idPrefix}${input.name}_autoselect`);
                if (input.required && deviceIds.length === 0 && manualDevices.length === 0) {
                    if (autoMarker || (module && (
                        module.id === 'ubiquiti_cdp_reader'
                        || module.id === 'uniview_nvr_capture'
                        || module.id === 'uniview_device_type_check'
                    ))) {
                        inputs[input.name] = { device_ids: '__AUTO__', manual_devices: [] };
                    } else {
                        isValid = false;
                    }
                } else {
                    inputs[input.name] = { device_ids: deviceIds, manual_devices: manualDevices };
                }
                return;
            }
            const element = document.getElementById(`${idPrefix}${input.name}`);
            if (element) {
                const value = element.type === 'checkbox'
                    ? element.checked
                    : (element.multiple ? Array.from(element.selectedOptions).map(option => option.value) : element.value);
                if (input.required && !value && credentialProfile && credentialFields.has(input.name)) {
                    element.style.borderColor = '';
                    return;
                }
                if (input.required && (!value || (Array.isArray(value) && value.length === 0))) {
                    isValid = false;
                    element.style.borderColor = 'var(--error)';
                } else {
                    if (!(credentialProfile && credentialFields.has(input.name) && !value)) {
                        inputs[input.name] = value;
                    }
                    element.style.borderColor = '';
                }
            }
        });

        if (credentialProfile) {
            inputs.credential_profile = credentialProfile;
        }

        if (!isValid && !error) {
            error = 'Please fill all required fields';
        }

        return { inputs, isValid, error };
    }

    async updateModuleLog(threadId, deleteAfter) {
        const logWrap = document.getElementById('moduleLogOutput');
        if (!logWrap) return;
        const textarea = logWrap.querySelector('textarea');
        if (!textarea) return;
        const statusDisplay = document.getElementById('moduleStatusDisplay');
        const threadInfo = this.activeModuleThreads.get(threadId);
        if (!deleteAfter && !threadInfo && (!statusDisplay || statusDisplay.dataset.logThread !== threadId)) {
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
                const cached = this.moduleLogCache.get(threadId);
                if (cached && cached.length) {
                    logWrap.style.display = 'block';
                    textarea.value = cached.join('\n');
                    textarea.scrollTop = textarea.scrollHeight;
                } else {
                    logWrap.style.display = 'block';
                    textarea.value = 'No log output yet.';
                }
                return;
            }
            logWrap.style.display = 'block';
            textarea.value = lines.join('\n');
            textarea.scrollTop = textarea.scrollHeight;
            this.moduleLogCache.set(threadId, lines);
        } catch (error) {
            logWrap.style.display = 'none';
        }
    }

    updateModuleJobs() {
        const jobsBody = document.getElementById('moduleJobsBody');
        const serverJobs = Array.isArray(this.serverModuleJobs) ? this.serverModuleJobs : [];
        const useServerJobs = serverJobs.length > 0;
        const threads = Array.from(this.activeModuleThreads.entries());
        const summary = document.getElementById('moduleJobsSummary');
        
        if (useServerJobs || threads.length > 0) {
            let jobs = useServerJobs ? serverJobs.slice() : [];
            if (useServerJobs && !this.showCompletedJobs) {
                jobs = jobs.filter(job => {
                    const status = (job.status || '').toLowerCase();
                    if (status === 'completed') {
                        const output = job.output || {};
                        return output && output.status === 'error';
                    }
                    return true;
                });
            }
            const totalJobs = jobs.length;
            const maxRows = 50;
            const clipped = jobs.slice(0, maxRows);
            if (summary) {
                summary.textContent = `${totalJobs} job${totalJobs === 1 ? '' : 's'}`;
            }
            const rows = useServerJobs
                ? clipped.map(job => {
                    const module = (this.modules || []).find(m => m.id === job.module_id);
                    const moduleName = module ? module.name : job.module_id;
                    const status = job.status || 'running';
                    const output = job.output || {};
                    const errorMessage = (output && output.status === 'error') ? (output.message || output.error || '') : '';
                    let statusText = status;
                    if (errorMessage) {
                        const msg = String(errorMessage).toLowerCase();
                        if (msg.includes('ssh') || msg.includes('login') || msg.includes('username') || msg.includes('password')) {
                            statusText = 'connecting failed';
                        } else {
                            statusText = 'failed';
                        }
                    }
                    const progress = typeof job.progress === 'number' ? job.progress : 0;
                    const startText = job.start_time || job.queued_at;
                    const startTime = startText ? new Date(startText) : null;
                    const duration = startTime ? Math.floor((new Date() - startTime) / 1000) : 0;
                    const siteLabel = job.site_name ? ` • ${job.site_name}` : '';
                    const scheduleLabel = job.schedule_name ? ` • ${job.schedule_name}` : '';
                    const statusClass = (errorMessage || status === 'failed')
                        ? 'status-offline'
                        : status === 'running'
                        ? 'status-online'
                        : status === 'queued'
                            ? 'status-unknown'
                            : 'status-offline';
                    return `
                        <tr>
                            <td>
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <i data-feather="box"></i>
                                    <div>
                                        <div>${moduleName}</div>
                                        <div class="meta">${siteLabel}${scheduleLabel}</div>
                                        ${errorMessage ? `<div class="meta" style="color: var(--danger-color);">${this.escapeHtml(errorMessage)}</div>` : ''}
                                    </div>
                                </div>
                            </td>
                            <td>
                                <span class="status-badge ${statusClass}">
                                    ${statusText}
                                </span>
                            </td>
                            <td>
                                <div class="progress-bar">
                                    <div class="progress-fill" style="width: ${Math.min(100, Math.max(0, progress))}%"></div>
                                </div>
                            </td>
                            <td>${startTime ? this.formatTime(startTime.toISOString()) : '—'}</td>
                            <td>${duration}s</td>
                            <td>
                                <button class="btn-icon" title="Cancel" disabled>
                                    <i data-feather="x-circle"></i>
                                </button>
                            </td>
                        </tr>
                    `;
                }).concat(totalJobs > maxRows ? [`
                        <tr>
                            <td colspan="6" class="empty-state">
                                <div style="padding: 12px; text-align: center; color: var(--text-secondary);">
                                    Showing first ${maxRows} of ${totalJobs} jobs
                                </div>
                            </td>
                        </tr>
                    `] : [])
                : threads.map(([threadId, threadInfo]) => {
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
                });
            jobsBody.innerHTML = rows.join('');
        } else {
            if (summary) {
                summary.textContent = '0 jobs';
            }
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
        this.editingSiteId = null;
        const title = document.getElementById('siteModalTitle');
        const saveBtn = document.getElementById('saveSiteBtn');
        if (title) title.textContent = 'Add New Site';
        if (saveBtn) saveBtn.textContent = 'Add Site';
        const siteIdInput = document.getElementById('siteId');
        if (siteIdInput) siteIdInput.value = '';
        document.getElementById('siteName').value = '';
        document.getElementById('siteRootIP').value = '';
        document.getElementById('siteMapReliable').checked = false;
        document.getElementById('siteMapReliableAt').value = '';
        document.getElementById('siteActiveScanRanges').value = '';
        document.getElementById('siteNotes').value = '';
        this.populateSiteRootDeviceOptions('', '');
    }

    async saveSite() {
        const siteId = document.getElementById('siteId')?.value || '';
        const name = document.getElementById('siteName').value.trim();
        const rootIP = document.getElementById('siteRootIP').value.trim();
        const mapReliable = document.getElementById('siteMapReliable').checked;
        const mapReliableAtInput = document.getElementById('siteMapReliableAt');
        if (mapReliable && mapReliableAtInput && !mapReliableAtInput.value) {
            mapReliableAtInput.value = this.currentDateTimeLocal();
        }
        const mapReliableAt = mapReliableAtInput?.value || '';
        const activeScanRanges = this.normalizeScanRangeList(document.getElementById('siteActiveScanRanges')?.value || '');
        const notes = document.getElementById('siteNotes').value.trim();
        
        if (!name || !rootIP) {
            this.showError('Site name and root IP are required');
            return;
        }
        
        try {
            const isEdit = !!siteId;
            const url = isEdit ? `/api/sites/${siteId}` : '/api/sites';
            const method = isEdit ? 'PUT' : 'POST';
            const response = await fetch(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    root_ip: rootIP,
                    map_reliable: mapReliable,
                    map_reliable_at: mapReliableAt,
                    active_scan_ranges: activeScanRanges,
                    notes: notes
                })
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || (isEdit ? 'Failed to update site' : 'Failed to add site'));
            }
            
            this.closeAllModals();
            this.showMessage(isEdit ? `Site "${name}" updated successfully` : `Site "${name}" added successfully`);
            this.loadData();
            
            // Auto-select the new site
            this.currentSite = name;
            this.updateCurrentSiteDisplay();
            
        } catch (error) {
            console.error('Error adding site:', error);
            this.showError(error.message);
        }
    }

    populateSiteRootDeviceOptions(siteName, currentRootIp) {
        const select = document.getElementById('siteRootDevice');
        if (!select) return;
        const devices = (this.devices || []).filter(d => d.site === siteName && d.ip);
        const options = devices
            .map(d => ({ ip: d.ip, label: `${d.ip} (${d.name || d.id || 'Device'})` }))
            .sort((a, b) => a.ip.localeCompare(b.ip));
        const current = currentRootIp || '';
        select.innerHTML = '<option value="">Manual entry</option>' + options.map(opt => (
            `<option value="${opt.ip}" ${opt.ip === current ? 'selected' : ''}>${opt.label}</option>`
        )).join('');
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

        const title = document.getElementById('siteModalTitle');
        const saveBtn = document.getElementById('saveSiteBtn');
        if (title) title.textContent = 'Edit Site';
        if (saveBtn) saveBtn.textContent = 'Save Changes';
        this.editingSiteId = siteId;
        const siteIdInput = document.getElementById('siteId');
        if (siteIdInput) siteIdInput.value = siteId;
        document.getElementById('siteName').value = site.name || '';
        document.getElementById('siteRootIP').value = site.root_ip || '';
        document.getElementById('siteMapReliable').checked = !!site.map_reliable;
        document.getElementById('siteMapReliableAt').value = this.dateTimeLocalValue(site.map_reliable_at || '');
        document.getElementById('siteActiveScanRanges').value = this.normalizeScanRangeList(site.active_scan_ranges || []).join('\n');
        document.getElementById('siteNotes').value = site.notes || '';
        this.populateSiteRootDeviceOptions(site.name, site.root_ip || '');
        document.getElementById('addSiteModal').classList.add('active');
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
        document.getElementById('editDeviceDomain').value = device.domain || '';
        document.getElementById('editDeviceDomainName').value = device.domain_name || '';
        document.getElementById('editDeviceMac').value = device.mac || '';
        document.getElementById('editDeviceType').value = device.type || 'router';
        document.getElementById('editDeviceNotes').value = device.notes || '';
        document.getElementById('editDeviceLocked').checked = device.locked || false;
        document.getElementById('editDeviceAlwaysShowMap').checked = device.always_show_on_map || false;
        document.getElementById('editDeviceHideFromMap').checked = device.hide_from_map || false;
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
            domain: document.getElementById('editDeviceDomain').value.trim(),
            domain_name: document.getElementById('editDeviceDomainName').value.trim(),
            mac: document.getElementById('editDeviceMac').value.trim(),
            type: document.getElementById('editDeviceType').value,
            os: document.getElementById('editDeviceOS').value.trim(),
            vendor: document.getElementById('editDeviceVendor').value.trim(),
            platform: document.getElementById('editDevicePlatform').value.trim(),
            notes: document.getElementById('editDeviceNotes').value.trim(),
            locked: document.getElementById('editDeviceLocked').checked,
            always_show_on_map: document.getElementById('editDeviceAlwaysShowMap').checked,
            hide_from_map: document.getElementById('editDeviceHideFromMap').checked,
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
        const blockRediscovery = confirm('Block this device from future rediscovery too? Choose Cancel if you only want to delete it for now.');
        
        try {
            const response = await fetch(`/api/devices/${deviceId}?block=${blockRediscovery ? '1' : '0'}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                this.selectedDeviceIds.delete(deviceId);
                this.showMessage(blockRediscovery ? 'Device deleted and blocked from rediscovery' : 'Device deleted');
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

        const agentUrlInput = document.getElementById('agentServerUrl');
        if (agentUrlInput && this.settings.agent_server_url) {
            agentUrlInput.value = this.settings.agent_server_url;
        }
        const staleInput = document.getElementById('staleScanDays');
        if (staleInput && this.settings.stale_scan_days) {
            staleInput.value = this.settings.stale_scan_days;
        }
        const agentOnlineInput = document.getElementById('agentOnlineMinutes');
        if (agentOnlineInput && this.settings.agent_online_minutes) {
            agentOnlineInput.value = this.settings.agent_online_minutes;
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
        const agentUrlInput = document.getElementById('agentServerUrl');
        if (agentUrlInput) {
            settings.agent_server_url = agentUrlInput.value.trim();
        }
        const staleInput = document.getElementById('staleScanDays');
        if (staleInput) {
            settings.stale_scan_days = parseInt(staleInput.value) || 7;
        }
        const agentOnlineInput = document.getElementById('agentOnlineMinutes');
        if (agentOnlineInput) {
            settings.agent_online_minutes = parseInt(agentOnlineInput.value) || 5;
        }
        if (this.currentUserRole === 'admin') {
            settings.module_credentials = this.moduleCredentials || {};
            const moduleMax = document.getElementById('moduleMaxConcurrent');
            if (moduleMax) {
                settings.module_max_concurrent = parseInt(moduleMax.value) || 2;
            }
        }
        
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
            
            this.settings = { ...this.settings, ...settings };
            this.applySettings();
            this.showMessage('Settings saved successfully');
            
        } catch (error) {
            console.error('Error saving settings:', error);
            this.showError('Failed to save settings');
        }
    }

    getModuleCredentialProfiles(moduleId) {
        if (!moduleId) {
            return [];
        }
        const profiles = [...(((this.moduleCredentials || {})[moduleId]) || [])];
        const inheritedModules = [];
        if (moduleId === 'mac_table_search' || moduleId === 'mac_group_map') {
            inheritedModules.push('cdp_discovery');
        }
        if (moduleId === 'mikrotik_dhcp_backup') {
            inheritedModules.push('mikrotik_mac_discovery');
        }
        const knownNames = new Set(profiles.map(profile => profile.name));
        inheritedModules.forEach(sourceModule => {
            (((this.moduleCredentials || {})[sourceModule]) || []).forEach(profile => {
                if (!knownNames.has(profile.name)) {
                    profiles.push(profile);
                    knownNames.add(profile.name);
                }
            });
        });
        return profiles;
    }

    renderModuleCredentials() {
        const moduleSelect = document.getElementById('moduleCredModule');
        const tableBody = document.getElementById('moduleCredsTableBody');
        if (!moduleSelect || !tableBody) {
            return;
        }
        const moduleOptions = (this.modules || []).map(module => ({
            id: module.id,
            label: module.name || module.id
        }));
        const dropdownModules = moduleOptions.length ? moduleOptions : this.moduleCredentialTargets;
        moduleSelect.innerHTML = dropdownModules.map(target => (
            `<option value="${target.id}">${target.label}</option>`
        )).join('');
        if (dropdownModules.length) {
            moduleSelect.value = dropdownModules[0].id;
        }

        const rows = [];
        dropdownModules.forEach(target => {
            const profiles = this.getModuleCredentialProfiles(target.id);
            profiles.forEach(profile => {
                rows.push(`
                    <tr data-module="${target.id}" data-name="${profile.name}">
                        <td>${target.label}</td>
                        <td>${profile.name}</td>
                        <td>${profile.username || ''}</td>
                        <td style="display: flex; gap: 8px;">
                            <button class="btn btn-secondary module-cred-edit" type="button">Edit</button>
                            <button class="btn btn-secondary module-cred-remove" type="button">Remove</button>
                        </td>
                    </tr>
                `);
            });
        });
        tableBody.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="4">No saved credentials.</td></tr>';

        tableBody.querySelectorAll('.module-cred-edit').forEach(button => {
            button.addEventListener('click', (event) => {
                const row = event.currentTarget.closest('tr');
                if (!row) {
                    return;
                }
                const moduleId = row.dataset.module;
                const profileName = row.dataset.name;
                const profile = this.getModuleCredentialProfiles(moduleId).find(p => p.name === profileName);
                if (profile) {
                    this.fillModuleCredentialForm(moduleId, profile);
                }
            });
        });
        tableBody.querySelectorAll('.module-cred-remove').forEach(button => {
            button.addEventListener('click', (event) => {
                const row = event.currentTarget.closest('tr');
                if (!row) {
                    return;
                }
                const moduleId = row.dataset.module;
                const profileName = row.dataset.name;
                if (confirm(`Remove credential profile "${profileName}"?`)) {
                    this.removeModuleCredential(moduleId, profileName);
                }
            });
        });
    }

    fillModuleCredentialForm(moduleId, profile) {
        const moduleSelect = document.getElementById('moduleCredModule');
        const nameInput = document.getElementById('moduleCredName');
        const userInput = document.getElementById('moduleCredUsername');
        const passInput = document.getElementById('moduleCredPassword');
        if (moduleSelect) moduleSelect.value = moduleId || '';
        if (nameInput) nameInput.value = profile.name || '';
        if (userInput) userInput.value = profile.username || '';
        if (passInput) passInput.value = profile.password || '';
    }

    clearModuleCredentialForm() {
        const nameInput = document.getElementById('moduleCredName');
        const userInput = document.getElementById('moduleCredUsername');
        const passInput = document.getElementById('moduleCredPassword');
        if (nameInput) nameInput.value = '';
        if (userInput) userInput.value = '';
        if (passInput) passInput.value = '';
    }

    saveModuleCredential() {
        const moduleSelect = document.getElementById('moduleCredModule');
        const nameInput = document.getElementById('moduleCredName');
        const userInput = document.getElementById('moduleCredUsername');
        const passInput = document.getElementById('moduleCredPassword');
        if (!moduleSelect || !nameInput || !userInput || !passInput) {
            return;
        }
        const moduleId = moduleSelect.value;
        const name = nameInput.value.trim();
        const username = userInput.value.trim();
        const password = passInput.value || '';
        if (!moduleId || !name || !username || !password) {
            this.showError('Module, profile name, username, and password are required');
            return;
        }
        const profiles = this.getModuleCredentialProfiles(moduleId);
        const existingIndex = profiles.findIndex(profile => profile.name === name);
        const entry = { name, username, password };
        if (existingIndex >= 0) {
            profiles[existingIndex] = entry;
        } else {
            profiles.push(entry);
        }
        this.moduleCredentials[moduleId] = profiles;
        this.renderModuleCredentials();
        this.showMessage(`Saved credential profile "${name}"`);
        this.persistModuleCredentials();
    }

    removeModuleCredential(moduleId, profileName) {
        if (!moduleId || !profileName) {
            return;
        }
        const profiles = this.getModuleCredentialProfiles(moduleId).filter(profile => profile.name !== profileName);
        this.moduleCredentials[moduleId] = profiles;
        this.renderModuleCredentials();
        this.persistModuleCredentials();
    }

    async persistModuleCredentials() {
        if (this.currentUserRole !== 'admin') {
            return;
        }
        try {
            const response = await fetch('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ module_credentials: this.moduleCredentials || {} })
            });
            if (!response.ok) {
                throw new Error('Failed to save module credentials');
            }
            const updated = await response.json();
            if (updated && updated.module_credentials) {
                this.moduleCredentials = updated.module_credentials;
            }
        } catch (error) {
            console.error('Error saving module credentials:', error);
            this.showError('Failed to save module credentials');
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
        toggle.addEventListener('click', (event) => {
            event.stopPropagation();
            container.classList.toggle('open');
        });
        const panel = container.querySelector('.multi-select-panel');
        if (panel) {
            panel.addEventListener('click', (event) => {
                event.stopPropagation();
            });
        }
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

    async runAgentDiscoveryForSite() {
        const siteFilter = document.getElementById('deviceSiteFilter')?.value || '';
        const siteName = siteFilter || this.currentSite || '';
        if (!siteName) {
            this.showError('Select a site first.');
            return;
        }
        const agent = this.getAgentForSite(siteName);
        if (!agent) {
            this.showError('No agent available for this site.');
            return;
        }
        if (!this.isAgentReady(agent)) {
            this.showError(`Agent is busy (${agent.last_state || 'running'}). Wait until it finishes.`);
            return;
        }
        try {
            const response = await fetch(`/api/agents/${agent.id}/trigger`, { method: 'POST' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                this.showError(data.error || 'Failed to trigger agent');
                return;
            }
            this.showMessage(`Agent run requested for ${siteName}`);
        } catch (error) {
            console.error('Error triggering agent:', error);
            this.showError('Failed to trigger agent');
        }
    }

    async runModuleOnAgent(moduleId) {
        const siteSelect = document.getElementById('moduleSiteSelect');
        const siteName = siteSelect?.value || this.currentSite || '';
        if (!siteName) {
            this.showError('Select a site first.');
            return;
        }
        const agent = this.getAgentForSite(siteName);
        if (!agent) {
            this.showError('No agent available for this site.');
            return;
        }
        if (!this.isAgentReady(agent)) {
            this.showError(`Agent is busy (${agent.last_state || 'running'}). Wait until it finishes.`);
            return;
        }
        const module = (this.modules || []).find(m => m.id === moduleId);
        if (!module) {
            this.showError('Module not found');
            return;
        }
        this.showModuleFormOnAgent(module);
    }

    getAgentForSite(siteName) {
        const agents = this.agents || [];
        return agents.find(a => (a.site || '') === siteName && a.enabled !== false);
    }

    isAgentReady(agent) {
        const state = (agent?.last_state || '').toLowerCase();
        if (!state) return true;
        return state === 'done' || state === 'idle' || state.startsWith('module:');
    }

    showModuleFormOnAgent(module, prefill = {}) {
        const modal = document.getElementById('moduleRunnerModal');
        const title = document.getElementById('moduleModalTitle');
        const formContainer = document.getElementById('moduleFormContainer');
        const statusDisplay = document.getElementById('moduleStatusDisplay');
        const startBtn = document.getElementById('startModuleBtn');

        statusDisplay.style.display = 'none';
        title.textContent = `Run on Agent: ${module.name}`;
        const savedPrefill = this.getModuleLastParams(module.id, this.currentSite);
        const mergedPrefill = { ...savedPrefill, ...prefill };
        this.renderModuleFormInto(formContainer, module, this.currentSite, mergedPrefill, {
            includeCredentialProfiles: true,
            showSiteDisplay: true,
            idPrefix: 'module_'
        });
        this.bindManualDeviceButtons(module, 'module_');
        this.applyPrefillValues(mergedPrefill, 'module_');

        const credentialSelect = document.getElementById('module_credential_profile');
        if (credentialSelect) {
            credentialSelect.addEventListener('change', () => {
                const selected = credentialSelect.value;
                if (!selected) {
                    return;
                }
                const profile = this.getModuleCredentialProfiles(module.id).find(entry => entry.name === selected);
                if (!profile) {
                    return;
                }
                const usernameInput = document.getElementById('module_username');
                const passwordInput = document.getElementById('module_password');
                if (usernameInput) {
                    usernameInput.value = profile.username || '';
                }
                if (passwordInput) {
                    passwordInput.value = profile.password || '';
                }
            });
        }

        modal.classList.add('active');
        const logWrap = document.getElementById('moduleLogOutput');
        if (logWrap) {
            logWrap.style.display = 'none';
            const textarea = logWrap.querySelector('textarea');
            if (textarea) {
                textarea.value = '';
            }
        }
        startBtn.disabled = false;
        startBtn.onclick = () => this.startAgentModule(module);
    }

    buildAgentTargetsFromInputs(module, inputs, siteName) {
        let targets = [];
        const inputDefs = module.inputs || [];
        inputDefs.forEach(input => {
            if (input.type !== 'device_table') {
                return;
            }
            const value = inputs[input.name] || {};
            let deviceIds = value.device_ids || [];
            if (deviceIds === '__AUTO__') {
                deviceIds = this.getDevicesForSite(siteName, input).map(d => d.id);
            }
            if (Array.isArray(deviceIds)) {
                deviceIds.forEach(id => {
                    const dev = (this.devices || []).find(d => d.id === id);
                    if (dev && dev.ip) {
                        targets.push({ ip: dev.ip, name: dev.name || dev.ip, mac: dev.mac || '' });
                    }
                });
            }
            const manual = value.manual_devices || [];
            if (Array.isArray(manual)) {
                manual.forEach(entry => {
                    if (entry && entry.ip) {
                        targets.push({ ip: entry.ip, name: entry.name || entry.ip, mac: entry.mac || '' });
                    }
                });
            }
        });
        return targets;
    }

    async startAgentModule(module) {
        const inputResult = this.collectModuleInputs(module, 'module_');
        if (inputResult.error) {
            this.showError(inputResult.error);
            return;
        }
        if (!inputResult.isValid) {
            this.showError('Please fill all required fields');
            return;
        }
        const siteName = this.currentSite;
        const agent = this.getAgentForSite(siteName);
        if (!agent) {
            this.showError('No agent available for this site.');
            return;
        }
        if (!this.isAgentReady(agent)) {
            this.showError(`Agent is busy (${agent.last_state || 'running'}). Wait until it finishes.`);
            return;
        }

        const inputs = inputResult.inputs || {};
        const targets = this.buildAgentTargetsFromInputs(module, inputs, siteName);
        const params = { ...inputs };
        (module.inputs || []).forEach(input => {
            if (input.type === 'device_table') {
                delete params[input.name];
            }
        });
        delete params.credential_profile;
        if (module.id === 'ubiquiti_cdp_reader') {
            params.capture_seconds = parseInt(params.capture_seconds || 60, 10);
            if (!params.packet_size) {
                params.packet_size = parseInt(params.batch_size || 1500, 10);
            }
            if (!params.interface) {
                params.interface = 'eth0';
            }
        }
        if (module.id === 'uniview_nvr_capture') {
            if (!params.ip_mode) {
                params.ip_mode = 'filter';
            }
        }
        if ((module.id === 'ubiquiti_cdp_reader' || module.id === 'uniview_nvr_capture') && targets.length === 0) {
            this.showError('No target devices selected');
            return;
        }

        const statusDisplay = document.getElementById('moduleStatusDisplay');
        const startBtn = document.getElementById('startModuleBtn');
        statusDisplay.style.display = 'block';
        statusDisplay.querySelector('.status-message').textContent = 'Sending to agent...';
        statusDisplay.querySelector('.progress-fill').style.width = '20%';
        startBtn.disabled = true;

        try {
            const response = await fetch(`/api/agents/${agent.id}/trigger`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    module_id: module.id,
                    targets,
                    params
                })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                this.showError(data.error || 'Failed to trigger agent');
                statusDisplay.querySelector('.status-message').textContent = 'Failed to send to agent';
                statusDisplay.querySelector('.progress-fill').style.width = '0%';
                startBtn.disabled = false;
                return;
            }
            statusDisplay.querySelector('.status-message').textContent = 'Agent run queued';
            statusDisplay.querySelector('.progress-fill').style.width = '100%';
            this.showMessage(`Agent run requested for ${siteName}`);
        } catch (error) {
            console.error('Error triggering agent:', error);
            this.showError('Failed to trigger agent');
            statusDisplay.querySelector('.status-message').textContent = 'Failed to send to agent';
            statusDisplay.querySelector('.progress-fill').style.width = '0%';
        } finally {
            startBtn.disabled = false;
        }
    }

    getModuleCredentialProfiles(moduleId) {
        if (!moduleId) {
            return [];
        }
        const profiles = [...(((this.moduleCredentials || {})[moduleId]) || [])];
        const inheritedModules = [];
        if (moduleId === 'mac_table_search' || moduleId === 'mac_group_map') {
            inheritedModules.push('cdp_discovery');
        }
        if (moduleId === 'mikrotik_dhcp_backup') {
            inheritedModules.push('mikrotik_mac_discovery');
        }
        const knownNames = new Set(profiles.map(profile => profile.name));
        inheritedModules.forEach(sourceModule => {
            (((this.moduleCredentials || {})[sourceModule]) || []).forEach(profile => {
                if (!knownNames.has(profile.name)) {
                    profiles.push(profile);
                    knownNames.add(profile.name);
                }
            });
        });
        return profiles;
    }

    getRunnableModuleCredentialProfiles(moduleId) {
        return this.getModuleCredentialProfiles(moduleId);
    }

    // ==================== AGENTS ====================
    renderAgents() {
        const body = document.getElementById('agentsTableBody');
        if (!body) return;
        const rows = [];
        (this.agents || []).forEach(agent => {
            const mode = [
                agent.allow_interval ? 'interval' : null,
                agent.allow_on_demand ? 'on-demand' : null
            ].filter(Boolean).join(' + ') || 'disabled';
            const isPending = !!agent.run_now;
            let status = agent.enabled ? (isPending ? 'pending' : 'idle') : 'disabled';
            if (agent.agent_status === 'online') {
                status = 'online';
            } else if (agent.agent_status === 'offline') {
                status = agent.enabled ? 'offline' : 'disabled';
            }
            if (agent.last_result === 'success') {
                status = 'success';
            } else if (agent.last_result === 'identity_mismatch') {
                status = 'identity mismatch';
            }
            const lastScan = agent.last_scan_at ? this.formatDateTime(agent.last_scan_at) : '--';
            const queued = Array.isArray(agent.queued_modules) ? agent.queued_modules : [];
            const queueLabel = queued.length ? `${queued.length} pending` : 'empty';
            const stateLabel = agent.last_state ? ` (${agent.last_state})` : '';
            const agentHost = [
                agent.device_name || '',
                agent.device_ip || '',
                agent.device_mac || ''
            ].filter(Boolean).join(' | ') || '--';
            rows.push(`
                <tr>
                    <td>${this.escapeHtml(agent.name || '')}</td>
                    <td>${this.escapeHtml(agent.site || '')}</td>
                    <td>${this.escapeHtml(agent.target_range || '')}</td>
                    <td>${this.escapeHtml(agentHost)}</td>
                    <td>${mode}</td>
                    <td>${agent.interval_min ?? 0}</td>
                    <td>${status}${stateLabel}</td>
                    <td>${queueLabel}</td>
                    <td>${lastScan}</td>
                    <td>
                        <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                            <button class="btn btn-secondary" onclick="platform.openAgentModalById('${agent.id}')">Edit</button>
                            <button class="btn btn-secondary" onclick="platform.triggerAgent('${agent.id}')">Run</button>
                            <button class="btn btn-secondary" onclick="platform.downloadAgentConfig('${agent.id}')">Config</button>
                            <button class="btn btn-secondary" onclick="platform.downloadAgentPackage('${agent.id}')">Package</button>
                            <button class="btn btn-secondary" onclick="platform.clearAgentQueue('${agent.id}')">Clear Queue</button>
                            <button class="btn btn-secondary" onclick="platform.resetAgentIdentity('${agent.id}')">Reset ID</button>
                            <button class="btn btn-danger" onclick="platform.deleteAgent('${agent.id}')">Delete</button>
                        </div>
                    </td>
                </tr>
            `);
        });
        body.innerHTML = rows.join('') || '<tr><td colspan="10">No agents configured.</td></tr>';
        replaceIcons();
    }

    openAgentModalById(agentId) {
        const agent = (this.agents || []).find(a => a.id === agentId);
        this.openAgentModal(agent || null);
    }

    openAgentModal(agent = null) {
        const title = document.getElementById('agentModalTitle');
        if (title) title.textContent = agent ? 'Edit Agent' : 'Add Agent';
        document.getElementById('agentId').value = agent?.id || '';
        document.getElementById('agentName').value = agent?.name || '';
        document.getElementById('agentRange').value = agent?.target_range || '';
        document.getElementById('agentServerHost').value = agent?.server_host || '';
        document.getElementById('agentEnabled').checked = agent ? !!agent.enabled : true;
        document.getElementById('agentAllowInterval').checked = agent ? !!agent.allow_interval : true;
        document.getElementById('agentAllowOnDemand').checked = agent ? !!agent.allow_on_demand : true;
        document.getElementById('agentInterval').value = agent?.interval_min ?? 10;
        document.getElementById('agentToken').value = agent?.token || '';
        document.getElementById('agentDeviceName').value = agent?.device_name || '';
        document.getElementById('agentDeviceIp').value = agent?.device_ip || '';
        document.getElementById('agentDeviceMac').value = agent?.device_mac || '';
        document.getElementById('agentTrustMode').value = agent?.trust_mode || 'augment';
        document.getElementById('agentIpScanMin').value = agent?.ip_scan_min ?? 10;
        document.getElementById('agentPingMin').value = agent?.ping_min ?? 2;
        this.renderAgentRanges(agent);

        const siteSelect = document.getElementById('agentSite');
        if (siteSelect) {
            const options = (this.sites || []).map(site => (
                `<option value="${this.escapeHtml(site.name || '')}">${this.escapeHtml(site.name || '')}</option>`
            ));
            siteSelect.innerHTML = options.join('');
            siteSelect.value = agent?.site || (this.sites?.[0]?.name || '');
        }

        document.getElementById('agentModal').classList.add('active');
    }

    renderAgentRanges(agent) {
        const container = document.getElementById('agentDetectedRanges');
        if (!container) return;
        const detected = Array.isArray(agent?.network_ranges) ? agent.network_ranges : [];
        const selected = new Set(Array.isArray(agent?.target_ranges) ? agent.target_ranges : []);
        if (!detected.length) {
            container.innerHTML = '<div class="form-hint">No ranges reported yet. Run the agent once.</div>';
            return;
        }
        container.innerHTML = detected.map(range => `
            <label class="range-item">
                <input type="checkbox" class="agent-range-select" value="${range}" ${selected.has(range) ? 'checked' : ''}>
                <span>${range}</span>
            </label>
        `).join('');
    }

    async saveAgent() {
        const id = document.getElementById('agentId').value.trim();
        const rangeInputs = document.querySelectorAll('.agent-range-select:checked');
        const targetRanges = Array.from(rangeInputs).map(input => input.value).filter(Boolean);
        const payload = {
            id: id || undefined,
            name: document.getElementById('agentName').value.trim(),
            site: document.getElementById('agentSite').value,
            target_range: document.getElementById('agentRange').value.trim(),
            target_ranges: targetRanges,
            server_host: document.getElementById('agentServerHost').value.trim(),
            enabled: document.getElementById('agentEnabled').checked,
            allow_interval: document.getElementById('agentAllowInterval').checked,
            allow_on_demand: document.getElementById('agentAllowOnDemand').checked,
            interval_min: parseInt(document.getElementById('agentInterval').value) || 0,
            token: document.getElementById('agentToken').value.trim(),
            device_name: document.getElementById('agentDeviceName').value.trim(),
            device_ip: document.getElementById('agentDeviceIp').value.trim(),
            device_mac: document.getElementById('agentDeviceMac').value.trim(),
            trust_mode: document.getElementById('agentTrustMode').value,
            ip_scan_min: parseInt(document.getElementById('agentIpScanMin').value) || 10,
            ping_min: parseInt(document.getElementById('agentPingMin').value) || 2
        };
        if (!payload.name || !payload.site || (!payload.target_range && (!payload.target_ranges || !payload.target_ranges.length))) {
            this.showError('Agent name, site, and at least one range are required.');
            return;
        }
        const url = id ? `/api/agents/${id}` : '/api/agents';
        const method = id ? 'PUT' : 'POST';
        try {
            const response = await fetch(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to save agent');
                return;
            }
            await this.loadData();
            this.closeAllModals();
            this.showMessage('Agent saved');
        } catch (error) {
            console.error('Error saving agent:', error);
            this.showError('Failed to save agent');
        }
    }

    async deleteAgent(agentId) {
        if (!confirm('Delete this agent?')) return;
        try {
            const response = await fetch(`/api/agents/${agentId}`, { method: 'DELETE' });
            if (!response.ok) {
                this.showError('Failed to delete agent');
                return;
            }
            await this.loadData();
            this.showMessage('Agent deleted');
        } catch (error) {
            console.error('Error deleting agent:', error);
            this.showError('Failed to delete agent');
        }
    }

    async triggerAgent(agentId) {
        try {
            const response = await fetch(`/api/agents/${agentId}/trigger`, { method: 'POST' });
            if (!response.ok) {
                this.showError('Failed to trigger agent');
                return;
            }
            this.showMessage('Agent run requested');
        } catch (error) {
            console.error('Error triggering agent:', error);
            this.showError('Failed to trigger agent');
        }
    }

    async pullAgentIdentity() {
        const id = document.getElementById('agentId')?.value.trim();
        if (!id) {
            this.showError('Save the agent first.');
            return;
        }
        try {
            const response = await fetch(`/api/agents/${id}/identity`);
            const data = await response.json();
            if (!response.ok) {
                this.showError(data.error || 'Failed to load identity');
                return;
            }
            const name = data.device_name || '';
            const ip = data.device_ip || '';
            const mac = data.device_mac || '';
            document.getElementById('agentDeviceName').value = name;
            document.getElementById('agentDeviceIp').value = ip;
            document.getElementById('agentDeviceMac').value = mac;
            if (!name && !ip && !mac) {
                await this.triggerAgent(id);
                this.showMessage('No identity yet. Agent run requested; try again after it reports.');
            } else {
                this.showMessage('Identity loaded');
            }
        } catch (error) {
            console.error('Error loading identity:', error);
            this.showError('Failed to load identity');
        }
    }

    async resetAgentIdentity(agentId) {
        if (!confirm('Reset agent identity? The next report will re-bind this agent to a device.')) {
            return;
        }
        try {
            const response = await fetch(`/api/agents/${agentId}/reset_identity`, { method: 'POST' });
            if (!response.ok) {
                this.showError('Failed to reset agent identity');
                return;
            }
            await this.loadData();
            this.showMessage('Agent identity reset');
        } catch (error) {
            console.error('Error resetting agent identity:', error);
            this.showError('Failed to reset agent identity');
        }
    }

    async clearAgentQueue(agentId) {
        if (!confirm('Clear queued modules for this agent?')) {
            return;
        }
        try {
            const response = await fetch(`/api/agents/${agentId}/clear_queue`, { method: 'POST' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                this.showError(data.error || 'Failed to clear queue');
                return;
            }
            this.showMessage('Agent queue cleared');
            await this.loadData();
        } catch (error) {
            console.error('Error clearing agent queue:', error);
            this.showError('Failed to clear queue');
        }
    }

    downloadAgentConfig(agentId) {
        window.open(`/api/agents/${agentId}/config?format=json`, '_blank');
    }

    downloadAgentPackage(agentId) {
        window.open(`/api/agents/${agentId}/package`, '_blank');
    }

    async downloadAgentExe() {
        try {
            const response = await fetch('/api/agents/agent_exe');
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                this.showError(data.error || 'Agent EXE not found. Build it first.');
                return;
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = 'cmapp-agent.exe';
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        } catch (error) {
            console.error('Error downloading agent exe:', error);
            this.showError('Failed to download agent EXE');
        }
    }

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
          const titleMap = {
              topology: 'Module Scheduler',
              dashboard: 'Dashboard',
              sites: 'Sites',
              devices: 'Devices',
              map: 'Map',
              agents: 'Agent Manager',
              settings: 'Settings'
          };
        document.getElementById('pageTitle').textContent =
            titleMap[tabName] || (tabName.charAt(0).toUpperCase() + tabName.slice(1));
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
        this.scheduleModuleEditRow = null;
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

        setInterval(() => {
            if (this.currentTab === 'topology') {
                this.refreshSchedules();
            }
        }, 10000);
        
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
