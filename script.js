document.addEventListener('DOMContentLoaded', () => {
    // --- Socket.IO Setup ---
    const socket = io();

    // --- DOM Element Selectors ---
    const dashboardContent = document.getElementById('dashboard-content');
    const addDeviceBtn = document.getElementById('add-device-btn');
    const modal = document.getElementById('add-device-modal');
    const cancelBtn = document.getElementById('cancel-btn');
    const addDeviceForm = document.getElementById('add-device-form');
    const modalError = document.getElementById('modal-error');
    const submitBtn = document.getElementById('submit-device-btn');
    const submitBtnText = submitBtn.querySelector('span');
    const submitBtnSpinner = submitBtn.querySelector('.spinner');
    
    // --- Chat & Status UI Selectors ---
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatMessages = document.getElementById('chat-messages');
    const chatSubmitBtn = document.getElementById('chat-submit-btn');
    const statusLight = document.getElementById('status-light');
    const statusMessage = document.getElementById('status-message');

    // --- Templates ---
    const roomTemplate = document.getElementById('room-section-template');
    const deviceTemplate = document.getElementById('device-card-template');
    const relayTemplate = document.getElementById('relay-control-template');

    // --- API Functions ---
    const api = {
        getDevices: async () => {
            const response = await fetch('/api/devices');
            if (!response.ok) throw new Error('Failed to fetch devices');
            return response.json();
        },
        getStates: async () => {
            const response = await fetch('/api/states');
            if (!response.ok) throw new Error('Failed to fetch states');
            return response.json();
        },
        addDevice: async (deviceData) => {
            const response = await fetch('/api/devices', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(deviceData),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Failed to add device');
            return result;
        },
        removeDevice: async (ip) => {
            const response = await fetch(`/api/devices/${ip}`, { method: 'DELETE' });
            if (!response.ok) throw new Error('Failed to remove device');
        },
        toggleRelay: async (ip, relayId, state) => {
            const response = await fetch(`/api/devices/${ip}/relay/${relayId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ state }),
            });
            if (!response.ok) throw new Error('Failed to toggle relay');
        },
        updateRelayName: async (ip, relayIndex, name) => {
            await fetch(`/api/devices/${ip}/relay_name`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ relayIndex, name }),
            });
        },
        sendChatMessage: async (message) => {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message }),
            });
            if (!response.ok) throw new Error('Failed to send message to assistant');
        }
    };

    // --- UI Rendering ---
    const renderDevices = (devices) => {
        dashboardContent.innerHTML = '';
        if (devices.length === 0) {
            dashboardContent.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">No devices added yet.</p>';
            return;
        }
        const devicesByRoom = devices.reduce((acc, device) => { (acc[device.room] = acc[device.room] || []).push(device); return acc; }, {});
        for (const [roomName, roomDevices] of Object.entries(devicesByRoom)) {
            const roomSection = roomTemplate.content.cloneNode(true);
            roomSection.querySelector('.room-title').textContent = roomName;
            const devicesGrid = roomSection.querySelector('.devices-grid');
            roomDevices.forEach(device => {
                const deviceCard = deviceTemplate.content.cloneNode(true);
                const cardElement = deviceCard.querySelector('.device-card');
                cardElement.dataset.ip = device.ip;
                deviceCard.querySelector('.device-name').textContent = device.name;
                deviceCard.querySelector('.device-ip').textContent = device.ip;
                const relayControlsContainer = deviceCard.querySelector('.relay-controls');
                for (let i = 0; i < device.numRelays; i++) {
                    const relayControl = relayTemplate.content.cloneNode(true);
                    const relayElement = relayControl.querySelector('.relay-control');
                    relayElement.dataset.relayId = i;
                    relayControl.querySelector('.relay-name').textContent = device.relayNames[i] || `Relay ${i + 1}`;
                    relayControlsContainer.appendChild(relayControl);
                }
                devicesGrid.appendChild(deviceCard);
            });
            dashboardContent.appendChild(roomSection);
        }
    };

    const loadDevices = async () => {
        try {
            const devices = await api.getDevices();
            renderDevices(devices);
            updateUIWithStates(); 
        } catch (error) {
            console.error(error);
            dashboardContent.innerHTML = '<p class="error-message">Could not load devices.</p>';
        }
    };

    // --- Real-time UI Update Logic ---
    const updateUIWithStates = async () => {
        try {
            const allStates = await api.getStates();
            for (const [ip, relayStates] of Object.entries(allStates)) {
                const deviceCard = dashboardContent.querySelector(`.device-card[data-ip="${ip}"]`);
                if (deviceCard) {
                    for (const [relayId, state] of Object.entries(relayStates)) {
                        const relayControl = deviceCard.querySelector(`.relay-control[data-relay-id="${relayId}"]`);
                        if (relayControl) {
                            const toggle = relayControl.querySelector('.relay-toggle');
                            const icon = relayControl.querySelector('.light-icon');
                            const isChecked = state === 'on';
                            if (toggle.checked !== isChecked) {
                                toggle.checked = isChecked;
                            }
                            icon.classList.toggle('on', isChecked);
                        }
                    }
                }
            }
        } catch (error) {
            console.error("State update failed:", error.message);
        }
    };

    // --- Chat UI Functions ---
    const addMessageToChat = (sender, text) => {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', sender);
        messageElement.innerHTML = `<span></span>`;
        messageElement.querySelector('span').textContent = text;
        chatMessages.appendChild(messageElement);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    };

    const handleChatSubmit = async (e) => {
        e.preventDefault();
        const messageText = chatInput.value.trim();
        if (!messageText) return;
        
        addMessageToChat('user', messageText);
        chatInput.value = '';
        chatSubmitBtn.disabled = true;

        try {
            await api.sendChatMessage(messageText);
        } catch (error) {
            addMessageToChat('assistant', `Error: ${error.message}`);
        } finally {
            chatSubmitBtn.disabled = false;
        }
    };

    // --- Event Handlers ---
    const showModal = () => modal.style.display = 'flex';
    const hideModal = () => { modal.style.display = 'none'; addDeviceForm.reset(); modalError.style.display = 'none'; };
    
    const handleAddDevice = async (e) => {
        e.preventDefault();
        setLoading(true);
        modalError.style.display = 'none';
        const deviceData = {
            name: document.getElementById('device-name').value,
            ip: document.getElementById('device-ip').value,
            room: document.getElementById('device-room').value,
        };
        try {
            await api.addDevice(deviceData);
            hideModal();
            loadDevices();
        } catch (error) {
            modalError.textContent = error.message;
            modalError.style.display = 'block';
        } finally {
            setLoading(false);
        }
    };

    const handleDashboardClick = async (e) => {
        const target = e.target;
        const deviceCard = target.closest('.device-card');
        if (!deviceCard) return;
        const ip = deviceCard.dataset.ip;
        if (target.classList.contains('relay-toggle')) {
            const relayControl = target.closest('.relay-control');
            const relayId = relayControl.dataset.relayId;
            const state = target.checked ? 'on' : 'off';
            try {
                await api.toggleRelay(ip, relayId, state);
                await updateUIWithStates();
            } catch (error) {
                console.error(error);
                target.checked = !target.checked;
            }
        }
        if (target.classList.contains('remove-device-btn')) {
            if (confirm(`Are you sure you want to remove device ${ip}?`)) {
                try {
                    await api.removeDevice(ip);
                    loadDevices();
                } catch (error) {
                    alert('Failed to remove device.');
                }
            }
        }
        if (target.classList.contains('relay-name')) {
            const nameSpan = target;
            const input = nameSpan.nextElementSibling;
            input.value = nameSpan.textContent;
            nameSpan.style.display = 'none';
            input.style.display = 'inline-block';
            input.focus();
            const saveName = async () => {
                const newName = input.value.trim();
                if (newName && newName !== nameSpan.textContent) {
                    const relayId = nameSpan.closest('.relay-control').dataset.relayId;
                    nameSpan.textContent = newName;
                    await api.updateRelayName(ip, relayId, newName);
                }
                input.style.display = 'none';
                nameSpan.style.display = 'inline-block';
            };
            input.onblur = saveName;
            input.onkeydown = (event) => {
                if (event.key === 'Enter') input.blur();
                if (event.key === 'Escape') {
                    input.value = nameSpan.textContent;
                    input.blur();
                }
            };
        }
    };

    const setLoading = (isLoading) => {
        submitBtnText.style.display = isLoading ? 'none' : 'inline';
        submitBtnSpinner.style.display = isLoading ? 'inline-block' : 'none';
        submitBtn.disabled = isLoading;
    };

    // --- Socket.IO Event Listeners ---
    socket.on('connect', () => {
        console.log('Connected to server via Socket.IO');
    });

    socket.on('status_update', (data) => {
        statusMessage.textContent = data.message;
        statusLight.className = 'status-indicator-light'; // Reset classes
        if (data.status === 'listening_for_wakeword') {
            statusLight.classList.add('listening');
        } else if (data.status === 'recording_command') {
            statusLight.classList.add('recording');
        } else if (data.status === 'processing') {
            statusLight.classList.add('processing');
        }
    });

    socket.on('new_message', (data) => {
        addMessageToChat(data.sender, data.text);
    });

    socket.on('refresh_states', () => {
        console.log('Server requested state refresh.');
        updateUIWithStates();
    });

    // --- Initial Setup ---
    addDeviceBtn.addEventListener('click', showModal);
    cancelBtn.addEventListener('click', hideModal);
    modal.addEventListener('click', (e) => e.target === modal && hideModal());
    addDeviceForm.addEventListener('submit', handleAddDevice);
    dashboardContent.addEventListener('click', handleDashboardClick);
    chatForm.addEventListener('submit', handleChatSubmit);

    loadDevices();
    setInterval(updateUIWithStates, 5000); // Poll for states as a fallback
});
