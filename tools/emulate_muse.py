import asyncio
import logging
import sys
import time
import math
import random
import threading
import uuid
from typing import Dict, Any
from flask import Flask, render_template_string, jsonify, request

# Native WinRT BLE Imports
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattServiceProvider,
    GattLocalCharacteristicParameters,
    GattServiceProviderAdvertisingParameters,
    GattCharacteristicProperties,
    GattProtectionLevel,
    GattWriteOption
)
from winrt.windows.storage.streams import DataWriter, DataReader

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Muse S / Muse 2 GATT UUIDs
STREAM_TOGGLE_UUID = "273e0001-4c4d-454d-96be-f03bac821358"
DATA_1_UUID = "273e0013-4c4d-454d-96be-f03bac821358"
DATA_2_UUID = "273e0014-4c4d-454d-96be-f03bac821358"

# ---------------------------------------------------------
# Shared State & Configuration Portal
# ---------------------------------------------------------
class SimulatorState:
    def __init__(self):
        self.lock = threading.Lock()
        self.is_streaming = False
        self.connection_count = 0
        
        # Signal parameters (amplitudes in microvolts)
        self.delta_amp = 5.0    # 1-4 Hz
        self.theta_amp = 4.0    # 4-8 Hz
        self.alpha_amp = 10.0   # 8-12 Hz
        self.beta_amp = 4.0     # 12-30 Hz
        self.gamma_amp = 2.0    # 30-50 Hz
        self.noise_amp = 3.0    # White noise level
        
        # Visual Snow / Pathological options
        self.alpha_hyperexcitable = False  # Triggers bursts of high alpha
        self.tcd_mode = False             # Thalamocortical Dysrhythmia (high theta + beta/gamma bleed-through)
        
        # Current active preset
        self.active_preset = "normal"

    def to_dict(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "is_streaming": self.is_streaming,
                "connection_count": self.connection_count,
                "delta_amp": self.delta_amp,
                "theta_amp": self.theta_amp,
                "alpha_amp": self.alpha_amp,
                "beta_amp": self.beta_amp,
                "gamma_amp": self.gamma_amp,
                "noise_amp": self.noise_amp,
                "alpha_hyperexcitable": self.alpha_hyperexcitable,
                "tcd_mode": self.tcd_mode,
                "active_preset": self.active_preset
            }

    def update_from_dict(self, data: Dict[str, Any]):
        with self.lock:
            if "delta_amp" in data: self.delta_amp = float(data["delta_amp"])
            if "theta_amp" in data: self.theta_amp = float(data["theta_amp"])
            if "alpha_amp" in data: self.alpha_amp = float(data["alpha_amp"])
            if "beta_amp" in data: self.beta_amp = float(data["beta_amp"])
            if "gamma_amp" in data: self.gamma_amp = float(data["gamma_amp"])
            if "noise_amp" in data: self.noise_amp = float(data["noise_amp"])
            if "alpha_hyperexcitable" in data: self.alpha_hyperexcitable = bool(data["alpha_hyperexcitable"])
            if "tcd_mode" in data: self.tcd_mode = bool(data["tcd_mode"])
            if "active_preset" in data:
                self.active_preset = data["active_preset"]
                self.apply_preset(self.active_preset)

    def apply_preset(self, preset: str):
        if preset == "normal":
            self.delta_amp = 4.0
            self.theta_amp = 3.0
            self.alpha_amp = 15.0  # Dominant Alpha
            self.beta_amp = 5.0
            self.gamma_amp = 1.5
            self.noise_amp = 2.0
            self.alpha_hyperexcitable = False
            self.tcd_mode = False
        elif preset == "vss":
            # Visual Snow Syndrome (Thalamocortical Dysrhythmia profile)
            self.delta_amp = 3.0
            self.theta_amp = 12.0  # High Theta (5-7Hz)
            self.alpha_amp = 4.0   # Disrupted Alpha
            self.beta_amp = 14.0   # Hyperactive Beta
            self.gamma_amp = 10.0  # High Gamma noise (Static simulation)
            self.noise_amp = 6.0   # Extra background noise
            self.alpha_hyperexcitable = False
            self.tcd_mode = True
        elif preset == "alpha_hyper":
            # Alpha Hyperexcitability (wildly fluctuating high alpha bursts)
            self.delta_amp = 3.0
            self.theta_amp = 3.0
            self.alpha_amp = 35.0  # Excessive Alpha
            self.beta_amp = 4.0
            self.gamma_amp = 2.0
            self.noise_amp = 2.0
            self.alpha_hyperexcitable = True
            self.tcd_mode = False
        elif preset == "asleep":
            # Slow wave sleep
            self.delta_amp = 25.0  # Dominant Delta
            self.theta_amp = 8.0
            self.alpha_amp = 2.0
            self.beta_amp = 1.5
            self.gamma_amp = 0.5
            self.noise_amp = 3.0
            self.alpha_hyperexcitable = False
            self.tcd_mode = False

state = SimulatorState()

# ---------------------------------------------------------
# Web UI Dashboard (Flask)
# ---------------------------------------------------------
app = Flask(__name__)

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Visual Snow EEG Simulator Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(20, 30, 55, 0.6);
            --border-color: rgba(255, 255, 255, 0.08);
            --accent-primary: #4f46e5;
            --accent-secondary: #06b6d4;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 2rem;
            background-image: radial-gradient(circle at 10% 20%, rgba(79, 70, 229, 0.15) 0%, transparent 40%),
                              radial-gradient(circle at 90% 80%, rgba(6, 182, 212, 0.15) 0%, transparent 40%);
        }

        header {
            width: 100%;
            max-width: 1100px;
            margin-bottom: 2rem;
            text-align: center;
        }

        h1 {
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(135deg, #a5b4fc 0%, #6366f1 50%, #22d3ee 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
        }

        .subtitle {
            color: var(--text-muted);
            font-size: 1.1rem;
        }

        .container {
            width: 100%;
            max-width: 1100px;
            display: grid;
            grid-template-columns: 1fr 1.5fr;
            gap: 2rem;
        }

        @media (max-width: 768px) {
            .container {
                grid-template-columns: 1fr;
            }
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.5rem;
            backdrop-filter: blur(12px);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }

        .card h2 {
            font-size: 1.3rem;
            margin-bottom: 1.5rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .status-badge {
            font-size: 0.8rem;
            padding: 0.25rem 0.75rem;
            border-radius: 50px;
            font-weight: 600;
        }

        .status-inactive {
            background-color: rgba(239, 68, 68, 0.2);
            color: #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.4);
        }

        .status-active {
            background-color: rgba(34, 197, 94, 0.2);
            color: #22c55e;
            border: 1px solid rgba(34, 197, 94, 0.4);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { opacity: 0.7; }
            50% { opacity: 1; }
            100% { opacity: 0.7; }
        }

        .form-group {
            margin-bottom: 1.25rem;
        }

        label {
            display: block;
            font-size: 0.9rem;
            color: var(--text-muted);
            margin-bottom: 0.5rem;
            font-weight: 600;
        }

        .slider-container {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        input[type="range"] {
            flex: 1;
            accent-color: var(--accent-primary);
            height: 6px;
            border-radius: 3px;
            outline: none;
            background: rgba(255, 255, 255, 0.1);
        }

        .val-display {
            width: 50px;
            text-align: right;
            font-variant-numeric: tabular-nums;
            font-weight: 600;
            color: var(--accent-secondary);
        }

        .toggle-group {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 8px;
            margin-bottom: 1rem;
            border: 1px solid var(--border-color);
        }

        /* Switch Styling */
        .switch {
            position: relative;
            display: inline-block;
            width: 44px;
            height: 24px;
        }

        .switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }

        .slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: rgba(255, 255, 255, 0.2);
            transition: .3s;
            border-radius: 34px;
        }

        .slider:before {
            position: absolute;
            content: "";
            height: 16px;
            width: 16px;
            left: 4px;
            bottom: 4px;
            background-color: white;
            transition: .3s;
            border-radius: 50%;
        }

        input:checked + .slider {
            background-color: var(--accent-primary);
        }

        input:checked + .slider:before {
            transform: translateX(20px);
        }

        /* Preset buttons */
        .preset-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.75rem;
            margin-bottom: 1.5rem;
        }

        .btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 0.75rem;
            border-radius: 10px;
            cursor: pointer;
            font-family: inherit;
            font-weight: 600;
            transition: all 0.2s ease;
        }

        .btn:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: rgba(255, 255, 255, 0.2);
        }

        .btn-active {
            background: linear-gradient(135deg, var(--accent-primary) 0%, #312e81 100%);
            border-color: var(--accent-primary);
            box-shadow: 0 0 12px rgba(79, 70, 229, 0.4);
        }

        .info-panel {
            background: rgba(6, 182, 212, 0.05);
            border: 1px solid rgba(6, 182, 212, 0.2);
            border-radius: 10px;
            padding: 1rem;
            font-size: 0.9rem;
            line-height: 1.4;
            color: #c7d2fe;
        }

        .wave-canvas {
            width: 100%;
            height: 200px;
            background: #05070c;
            border-radius: 12px;
            border: 1px solid var(--border-color);
            margin-top: 1rem;
        }
    </style>
</head>
<body>
    <header>
        <h1>Visual Snow EEG Simulator</h1>
        <p class="subtitle">Emulate pathological EEG frequency bleed-through patterns & alpha hyperexcitability</p>
    </header>

    <div class="container">
        <!-- Configuration Portal -->
        <div class="card">
            <h2>Presets</h2>
            <div class="preset-grid">
                <button class="btn" id="preset-normal" onclick="selectPreset('normal')">Healthy Baseline</button>
                <button class="btn" id="preset-vss" onclick="selectPreset('vss')">VSS Pathological</button>
                <button class="btn" id="preset-alpha_hyper" onclick="selectPreset('alpha_hyper')">Alpha Hyper</button>
                <button class="btn" id="preset-asleep" onclick="selectPreset('asleep')">Slow Wave Sleep</button>
            </div>

            <h2>Pathological Modes</h2>
            <div class="toggle-group">
                <div>
                    <div style="font-weight: 600;">Alpha Hyperexcitability</div>
                    <div style="font-size: 0.8rem; color: var(--text-muted);">Bursts of excessive 10Hz waves</div>
                </div>
                <label class="switch">
                    <input type="checkbox" id="alpha_hyperexcitable" onchange="updateToggles()">
                    <span class="slider"></span>
                </label>
            </div>

            <div class="toggle-group">
                <div>
                    <div style="font-weight: 600;">TCD Bleed-through</div>
                    <div style="font-size: 0.8rem; color: var(--text-muted);">Thalamocortical Dysrhythmia theta/beta coupling</div>
                </div>
                <label class="switch">
                    <input type="checkbox" id="tcd_mode" onchange="updateToggles()">
                    <span class="slider"></span>
                </label>
            </div>

            <div class="info-panel" id="preset-info">
                Select a preset or customize sliders to simulate specific EEG profiles.
            </div>
        </div>

        <!-- Live Controls and Sliders -->
        <div class="card">
            <h2>
                Live Signal Control 
                <span id="stream-status" class="status-badge status-inactive">Disconnected</span>
            </h2>

            <div class="form-group">
                <label>Delta Amplitude (1.5 Hz) - Deep Sleep / Slow Wave</label>
                <div class="slider-container">
                    <input type="range" id="delta_amp" min="0" max="40" step="0.5" oninput="updateSliders()">
                    <span class="val-display" id="delta_amp_val">0.0</span>
                </div>
            </div>

            <div class="form-group">
                <label>Theta Amplitude (6.0 Hz) - Pathological TCD Peak</label>
                <div class="slider-container">
                    <input type="range" id="theta_amp" min="0" max="40" step="0.5" oninput="updateSliders()">
                    <span class="val-display" id="theta_amp_val">0.0</span>
                </div>
            </div>

            <div class="form-group">
                <label>Alpha Amplitude (10.0 Hz) - Relaxation / Visual Gating</label>
                <div class="slider-container">
                    <input type="range" id="alpha_amp" min="0" max="40" step="0.5" oninput="updateSliders()">
                    <span class="val-display" id="alpha_amp_val">0.0</span>
                </div>
            </div>

            <div class="form-group">
                <label>Beta Amplitude (20.0 Hz) - Cognitive processing / Bleed-through</label>
                <div class="slider-container">
                    <input type="range" id="beta_amp" min="0" max="40" step="0.5" oninput="updateSliders()">
                    <span class="val-display" id="beta_amp_val">0.0</span>
                </div>
            </div>

            <div class="form-group">
                <label>Gamma Amplitude (40.0 Hz) - Sensory Binding / Visual Static</label>
                <div class="slider-container">
                    <input type="range" id="gamma_amp" min="0" max="40" step="0.5" oninput="updateSliders()">
                    <span class="val-display" id="gamma_amp_val">0.0</span>
                </div>
            </div>

            <div class="form-group">
                <label>White Noise Level - High frequency sensory static</label>
                <div class="slider-container">
                    <input type="range" id="noise_amp" min="0" max="20" step="0.5" oninput="updateSliders()">
                    <span class="val-display" id="noise_amp_val">0.0</span>
                </div>
            </div>

            <canvas class="wave-canvas" id="signal-preview"></canvas>
        </div>
    </div>

    <script>
        let currentConfig = {};
        const canvas = document.getElementById('signal-preview');
        const ctx = canvas.getContext('2d');
        
        // Resize canvas
        canvas.width = canvas.clientWidth;
        canvas.height = canvas.clientHeight;

        async function fetchConfig() {
            const res = await fetch('/api/config');
            const data = await res.json();
            currentConfig = data;
            updateUIElements(data);
        }

        async function fetchStatus() {
            const res = await fetch('/api/status');
            const data = await res.json();
            const badge = document.getElementById('stream-status');
            if (data.is_streaming) {
                badge.innerText = "Streaming Data";
                badge.className = "status-badge status-active";
            } else {
                badge.innerText = "BLE Connected (Idle)";
                badge.className = "status-badge status-inactive";
            }
        }

        function updateUIElements(config) {
            // Update sliders
            const bands = ['delta_amp', 'theta_amp', 'alpha_amp', 'beta_amp', 'gamma_amp', 'noise_amp'];
            bands.forEach(band => {
                document.getElementById(band).value = config[band];
                document.getElementById(band + '_val').innerText = config[band].toFixed(1) + 'uV';
            });

            // Update toggles
            document.getElementById('alpha_hyperexcitable').checked = config.alpha_hyperexcitable;
            document.getElementById('tcd_mode').checked = config.tcd_mode;

            // Highlight active preset button
            document.querySelectorAll('.preset-grid .btn').forEach(btn => btn.classList.remove('btn-active'));
            const activeBtn = document.getElementById('preset-' + config.active_preset);
            if (activeBtn) activeBtn.classList.add('btn-active');

            // Set info panel description
            const info = document.getElementById('preset-info');
            if (config.active_preset === 'normal') {
                info.innerHTML = "<strong>Healthy Baseline:</strong> Normal alpha activity (~10Hz) localized primarily in visual cortex. No abnormal low-frequency delta/theta oscillations.";
            } else if (config.active_preset === 'vss') {
                info.innerHTML = "<strong>VSS Pathological:</strong> Simulated Thalamocortical Dysrhythmia (TCD). Shows elevated 5-7Hz Theta power combined with high frequency Beta/Gamma noise representing visual static bleed-through.";
            } else if (config.active_preset === 'alpha_hyper') {
                info.innerHTML = "<strong>Alpha Hyperexcitability:</strong> Unusually high amplitude alpha waves, mimicking cortical hyper-excitability where the visual cortex lacks proper inhibitory gating.";
            } else if (config.active_preset === 'asleep') {
                info.innerHTML = "<strong>Slow Wave Sleep:</strong> Characterized by dominant slow Delta waves (1-4 Hz) and minimal high-frequency cognitive processing waves.";
            }
        }

        async function selectPreset(preset) {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active_preset: preset })
            });
            const data = await res.json();
            currentConfig = data;
            updateUIElements(data);
        }

        async function updateSliders() {
            const updateData = {};
            const bands = ['delta_amp', 'theta_amp', 'alpha_amp', 'beta_amp', 'gamma_amp', 'noise_amp'];
            bands.forEach(band => {
                const val = parseFloat(document.getElementById(band).value);
                document.getElementById(band + '_val').innerText = val.toFixed(1) + 'uV';
                updateData[band] = val;
            });
            updateData['active_preset'] = 'custom';
            document.querySelectorAll('.preset-grid .btn').forEach(btn => btn.classList.remove('btn-active'));
            
            await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updateData)
            });
        }

        async function updateToggles() {
            const updateData = {
                alpha_hyperexcitable: document.getElementById('alpha_hyperexcitable').checked,
                tcd_mode: document.getElementById('tcd_mode').checked,
                active_preset: 'custom'
            };
            document.querySelectorAll('.preset-grid .btn').forEach(btn => btn.classList.remove('btn-active'));
            
            await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updateData)
            });
        }

        // Live preview oscilloscope
        let phase = 0;
        function drawOscilloscope() {
            ctx.fillStyle = '#05070c';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            
            ctx.strokeStyle = '#4f46e5';
            ctx.lineWidth = 2;
            ctx.beginPath();
            
            const delta = currentConfig.delta_amp || 0;
            const theta = currentConfig.theta_amp || 0;
            let alpha = currentConfig.alpha_amp || 0;
            const beta = currentConfig.beta_amp || 0;
            const gamma = currentConfig.gamma_amp || 0;
            const noise = currentConfig.noise_amp || 0;

            if (currentConfig.alpha_hyperexcitable) {
                // Apply visual burst simulation
                alpha = alpha * (1.5 + Math.sin(phase * 0.05));
            }

            for (let x = 0; x < canvas.width; x++) {
                const t = x / 100;
                let y = canvas.height / 2;
                
                // Combine sine waves
                y += Math.sin(t * 1.5 * Math.PI * 2 + phase) * delta * 2;
                y += Math.sin(t * 6.0 * Math.PI * 2 + phase) * theta * 2;
                y += Math.sin(t * 10.0 * Math.PI * 2 + phase) * alpha * 2;
                y += Math.sin(t * 20.0 * Math.PI * 2 + phase) * beta * 1.5;
                y += Math.sin(t * 40.0 * Math.PI * 2 + phase) * gamma * 1.0;
                
                // Add noise
                y += (Math.random() - 0.5) * noise * 3;

                if (x === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
            }
            ctx.stroke();
            phase += 0.05;
            requestAnimationFrame(drawOscilloscope);
        }

        // Initial setup
        fetchConfig();
        setInterval(fetchStatus, 1000);
        drawOscilloscope();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_DASHBOARD)

@app.route('/api/config', methods=['GET', 'POST'])
def config_api():
    if request.method == 'POST':
        state.update_from_dict(request.json)
    return jsonify(state.to_dict())

@app.route('/api/status')
def status_api():
    return jsonify({
        "is_streaming": state.is_streaming,
        "connection_count": state.connection_count
    })

def run_flask():
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

# ---------------------------------------------------------
# BLE Data Packing and Signal Generation
# ---------------------------------------------------------
def pack_lsb_bits(data: bytearray, bit_start: int, bit_width: int, value: int):
    for bit in range(bit_width):
        absolute_bit = bit_start + bit
        byte_index = absolute_bit // 8
        bit_index = absolute_bit % 8
        bit_value = (value >> bit) & 0x01
        if bit_value:
            data[byte_index] |= (1 << bit_index)
        else:
            data[byte_index] &= ~(1 << bit_index)

def generate_eeg_sample(t: float, ch: int) -> float:
    # 8192 is the midpoint for 14-bit unsigned ADC value (range 0 to 16383)
    val = 8192.0
    
    # Read live values from state
    with state.lock:
        delta = state.delta_amp
        theta = state.theta_amp
        alpha = state.alpha_amp
        beta = state.beta_amp
        gamma = state.gamma_amp
        noise = state.noise_amp
        alpha_hyper = state.alpha_hyperexcitable
        tcd = state.tcd_mode

    # Visual Snow / Thalamocortical Dysrhythmia coupling simulations
    if tcd:
        # Theta phase-modulates Beta/Gamma amplitude (cross-frequency coupling)
        theta_wave = Math_sine_wave(t, 5.5, ch)
        gamma = gamma * (1.0 + 0.8 * theta_wave)
        beta = beta * (1.0 + 0.5 * theta_wave)

    if alpha_hyper:
        # Slow modulation of Alpha waves over time (bursts every ~4 seconds)
        alpha = alpha * (1.5 + math.sin(2 * math.pi * 0.25 * t))

    # Compile the bands
    # Add phase offsets per channel so signals look real and distinct
    val += Math_sine_wave(t, 1.5, ch) * delta
    val += Math_sine_wave(t, 6.0, ch) * theta
    val += Math_sine_wave(t, 10.0, ch) * alpha
    val += Math_sine_wave(t, 20.0, ch) * beta
    val += Math_sine_wave(t, 40.0, ch) * gamma
    
    # Add noise
    val += random.gauss(0, 1.0) * noise
    
    # Clip to 14-bit limits (0 to 16383)
    return max(0.0, min(16383.0, val))

def Math_sine_wave(t: float, freq: float, ch: int) -> float:
    # Channel-specific phase shift
    phase_shift = (ch * math.pi / 2)
    return math.sin(2 * math.pi * freq * t + phase_shift)

# ---------------------------------------------------------
# Native WinRT BLE GATT Server
# ---------------------------------------------------------
async def run_ble_server():
    global state
    service_uuid = uuid.UUID("0000fe8d-0000-1000-8000-00805f9b34fb") # 0xFE8D base UUID
    
    logger.info("Initializing native WinRT BLE GATT Service...")
    result = await GattServiceProvider.create_async(service_uuid)
    if result.error != 0:
        logger.error(f"Failed to create service provider. Windows error code: {result.error}")
        logger.error("Please ensure Bluetooth is enabled on your PC and your user account has permissions.")
        return
        
    provider = result.service_provider
    service = provider.service
    
    # Define characteristic write handler
    def write_handler(sender, args):
        deferral = args.get_deferral()
        try:
            # We are on a background pool thread, so we run a local loop to get the request async
            loop = asyncio.new_event_loop()
            request = loop.run_until_complete(args.get_request_async())
            loop.close()
            
            reader = DataReader.from_buffer(request.value)
            n_bytes = reader.unconsumed_buffer_length
            value_bytes = bytearray()
            for _ in range(n_bytes):
                value_bytes.append(reader.read_byte())
                
            logger.info(f"Write request received: {list(value_bytes)}")
            try:
                cmd_len = value_bytes[0]
                cmd = value_bytes[1:cmd_len].decode('utf-8', errors='ignore')
                logger.info(f"Decoded command: '{cmd}'")
                
                with state.lock:
                    if cmd == "dc001":
                        logger.info("BLE Streaming started!")
                        state.is_streaming = True
                    elif cmd == "h":
                        logger.info("BLE Streaming stopped!")
                        state.is_streaming = False
            except Exception as e:
                logger.error(f"Error parsing command: {e}")
                
            if request.option == GattWriteOption.WRITE_WITH_RESPONSE:
                request.respond()
        except Exception as e:
            logger.error(f"Error in write handler: {e}")
        finally:
            deferral.complete()

    # Add Control / Stream Toggle Characteristic
    toggle_params = GattLocalCharacteristicParameters()
    toggle_params.characteristic_properties = (
        GattCharacteristicProperties.WRITE | 
        GattCharacteristicProperties.NOTIFY
    )
    toggle_params.write_protection_level = GattProtectionLevel.PLAIN
    toggle_params.read_protection_level = GattProtectionLevel.PLAIN
    
    toggle_uuid = uuid.UUID(STREAM_TOGGLE_UUID)
    toggle_result = await service.create_characteristic_async(toggle_uuid, toggle_params)
    toggle_char = toggle_result.characteristic
    toggle_char.add_write_requested(write_handler)
    logger.info("Toggle characteristic added.")
    
    # Add Data 1 Characteristic
    data1_params = GattLocalCharacteristicParameters()
    data1_params.characteristic_properties = GattCharacteristicProperties.NOTIFY
    data1_uuid = uuid.UUID(DATA_1_UUID)
    data1_result = await service.create_characteristic_async(data1_uuid, data1_params)
    data1_char = data1_result.characteristic
    logger.info("Data 1 characteristic added.")
    
    # Add Data 2 Characteristic
    data2_params = GattLocalCharacteristicParameters()
    data2_params.characteristic_properties = GattCharacteristicProperties.NOTIFY
    data2_uuid = uuid.UUID(DATA_2_UUID)
    data2_result = await service.create_characteristic_async(data2_uuid, data2_params)
    data2_char = data2_result.characteristic
    logger.info("Data 2 characteristic added.")
    
    # Start advertising (using default PC name)
    logger.info("Starting BLE GATT advertising...")
    adv_params = GattServiceProviderAdvertisingParameters()
    adv_params.is_discoverable = True
    adv_params.is_connectable = True
    provider.start_advertising_with_parameters(adv_params)
    logger.info("MuseS-Mock native BLE server started successfully and advertising!")
    logger.info("Note: The device will advertise under your PC's name.")
    logger.info("To make the Android app connect automatically, temporarily rename your PC to 'MuseS-Mock' in Windows settings.")
    
    packet_index = 0
    start_time = time.time()
    n_channels = 4
    n_samples = 4
    scale_factor = 0.40293040293040294  # MUSE_ATHENA_EEG_SCALE_FACTOR
    
    try:
        while True:
            is_active = False
            with state.lock:
                is_active = state.is_streaming
                
            if is_active:
                packet = bytearray(40)
                packet[0] = len(packet) # 40
                
                # Packet index (16-bit LE)
                packet[1] = packet_index & 0xFF
                packet[2] = (packet_index >> 8) & 0xFF
                
                # Athena headers
                packet[9] = 0x11 # EEG 4 channels
                packet[10] = packet_index & 0xFF
                
                # Generate and bitpack 4 samples of 4 channels of 14-bit data (28 bytes total payload)
                payload = bytearray(28)
                t = time.time() - start_time
                
                for sample in range(n_samples):
                    sample_time = t + (sample / 256.0)
                    for channel in range(n_channels):
                        eeg_uv = generate_eeg_sample(sample_time, channel)
                        raw_val = int(eeg_uv / scale_factor) & 0x3FFF
                        bit_start = (sample * n_channels + channel) * 14
                        pack_lsb_bits(payload, bit_start, 14, raw_val)
                
                packet[11:39] = payload
                
                # Notify client
                writer = DataWriter()
                writer.write_bytes(bytes(packet))
                await data1_char.notify_value_async(writer.detach_buffer())
                
                packet_index = (packet_index + 1) & 0xFFFF
                await asyncio.sleep(0.0156)
            else:
                await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        logger.info("Stopping BLE server...")
    finally:
        provider.stop_advertising()

if __name__ == "__main__":
    # Start Flask Web Portal in a background thread
    logger.info("Starting Configuration Portal Web Server at http://127.0.0.1:5000")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Run BLE GATT Peripheral Server on main loop
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_ble_server())
