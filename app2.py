import json
import os
import requests
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO
import threading
import time
import numpy as np
import speech_recognition as sr
from gtts import gTTS
import uuid
from openwakeword.model import Model
from openwakeword.utils import download_models
import subprocess
import pyaudio
import warnings

warnings.filterwarnings("ignore")

# Initialize the Flask application and SocketIO
app = Flask(__name__, template_folder='templates', static_folder='static')
socketio = SocketIO(app, async_mode='threading')

# --- Configuration ---
DEVICES_FILE = 'devices.json'
OLLAMA_API_URL = "http://10.163.xx.xx:11434/api/generate"
OLLAMA_MODEL = "mistral"

# Voice Assistant Configuration
WAKEWORD_MODEL_NAME = "hey_jarvis"
COMMAND_SILENCE_THRESHOLD = 500
COMMAND_SILENCE_DURATION = 1.5
MAX_RECORDING_DURATION = 7
CONFIDENCE_THRESHOLD = 0.5

# --- Global State Management ---
device_states = {}
app_state = "initializing" # For voice assistant status
audio_chunks = []
audio_lock = threading.Lock()

# --- Audio Processing Class (from your script) ---
class AudioProcessor:
    def __init__(self):
        self.audio = pyaudio.PyAudio()
        self.device_index = self._find_i2s_device()
        self.sample_rate, self.chunk_size = self._find_best_sample_rate()
        self.oww_model = Model(wakeword_models=[WAKEWORD_MODEL_NAME], inference_framework='onnx')
        
        if not self.sample_rate:
            raise Exception("No compatible audio sample rate found!")
        
        print(f"✅ Audio system ready. Using {self.sample_rate}Hz with {self.chunk_size} sample chunks.")

    def _find_i2s_device(self):
        print("Searching for I2S microphone...")
        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            # Common names for I2S mics on Pi (e.g., ReSpeaker, Voice HAT)
            if 'i2s' in info['name'].lower() or 'seeed-2mic-voicecard' in info['name'].lower() or 'googlevoicehat' in info['name'].lower():
                print(f"🎤 Found: {info['name']} at index {i}")
                return i
        print("⚠️ Could not find I2S device, using default input.")
        return None

    def _find_best_sample_rate(self):
        print("Finding optimal sample rate...")
        for rate in [48000, 44100, 32000, 22050, 16000]:
            try:
                chunk = int(rate * 0.08) # 80ms chunk
                stream = self.audio.open(format=pyaudio.paInt16, channels=1, rate=rate,
                                          input=True, input_device_index=self.device_index,
                                          frames_per_buffer=chunk)
                stream.close()
                print(f"✅ {rate}Hz is supported.")
                return rate, chunk
            except Exception:
                print(f"❌ {rate}Hz is not supported.")
                continue
        return None, None

    def _resample_to_16k(self, audio_chunk):
        if self.sample_rate == 16000:
            return audio_chunk
        factor = self.sample_rate // 16000
        return audio_chunk[::factor]

    def start_listening_loop(self):
        update_status("listening_for_wakeword", f"Listening for '{WAKEWORD_MODEL_NAME}'...")
        stream = self.audio.open(format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
                                 input=True, input_device_index=self.device_index,
                                 frames_per_buffer=self.chunk_size)
        
        silent_frames = 0
        frames_per_second = int(self.sample_rate / self.chunk_size)
        recording_start_time = 0

        while True:
            # Always read from the stream to keep the buffer from overflowing
            data = stream.read(self.chunk_size, exception_on_overflow=False)

            # But only process the audio if we are in a listening state
            if app_state not in ["listening_for_wakeword", "recording_command"]:
                time.sleep(0.1) # Sleep briefly to prevent high CPU usage
                continue

            audio_chunk = np.frombuffer(data, dtype=np.int16)

            if app_state == "listening_for_wakeword":
                resampled_chunk = self._resample_to_16k(audio_chunk)
                prediction = self.oww_model.predict(resampled_chunk)
                
                if prediction[WAKEWORD_MODEL_NAME] > CONFIDENCE_THRESHOLD:
                    print("Wake word detected!")
                    update_status("recording_command", "Listening for command...")
                    with audio_lock:
                        audio_chunks.clear()
                    silent_frames = 0
                    recording_start_time = time.time()
            
            elif app_state == "recording_command":
                with audio_lock:
                    audio_chunks.append(audio_chunk)
                
                if np.linalg.norm(audio_chunk) < COMMAND_SILENCE_THRESHOLD:
                    silent_frames += 1
                else:
                    silent_frames = 0
                
                if (silent_frames > COMMAND_SILENCE_DURATION * frames_per_second) or \
                   (time.time() - recording_start_time > MAX_RECORDING_DURATION):
                    update_status("processing", "Processing your command...")
                    threading.Thread(target=process_recorded_command).start()

# --- Voice Assistant Logic ---

def update_status(new_state, message):
    global app_state
    app_state = new_state
    print(f"State change: {app_state} - {message}")
    socketio.emit('status_update', {'status': new_state, 'message': message})

def process_recorded_command():
    with audio_lock:
        recorded_data = list(audio_chunks)
        audio_chunks.clear()

    if not recorded_data:
        update_status("listening_for_wakeword", f"No command recorded. Listening...")
        return

    full_recording = np.concatenate(recorded_data, axis=0)
    
    try:
        recognizer = sr.Recognizer()
        audio_for_sr = sr.AudioData(full_recording.tobytes(), audio_processor.sample_rate, 2)
        transcribed_text = recognizer.recognize_google(audio_for_sr)
        print(f"Transcribed: {transcribed_text}")
        socketio.emit('new_message', {'sender': 'user', 'text': transcribed_text})
        
        handle_ai_logic(transcribed_text)

    except Exception as e:
        print(f"Speech recognition failed: {e}")
        start_cooldown()

def start_cooldown():
    """Centralized function to handle cooldown and reset."""
    def cooldown_thread():
        update_status("cooldown", "Waiting before listening again...")
        time.sleep(2.0) # Cooldown period
        
        print("Resetting wake word model state...")
        audio_processor.oww_model.reset()
        
        update_status("listening_for_wakeword", f"Listening for '{WAKEWORD_MODEL_NAME}'...")
    
    threading.Thread(target=cooldown_thread).start()

# --- Core Home Automation Logic (Integrated with Voice) ---

def read_devices():
    if not os.path.exists(DEVICES_FILE): return {}
    try:
        with open(DEVICES_FILE, 'r') as f:
            content = f.read()
            if not content: return {}
            return json.loads(content)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error reading or parsing {DEVICES_FILE}: {e}")
        return {}

def write_devices(devices):
    with open(DEVICES_FILE, 'w') as f:
        json.dump(devices, f, indent=4)

def update_state_for_device(ip):
    try:
        response = requests.get(f'http://{ip}/info', timeout=2)
        if response.status_code == 200:
            data = response.json()
            states = {}
            for status in data.get('status', []):
                states[str(status['relay'])] = status['state']
            device_states[ip] = states
    except requests.exceptions.RequestException:
        print(f"Could not poll device {ip} for status update.")

def initialize_all_device_states():
    print("Initializing all device states...")
    devices = read_devices()
    for ip in devices.keys():
        update_state_for_device(ip)
    print("Device state initialization complete.")

def control_physical_relay(ip, relay_id, state):
    try:
        url = f'http://{ip}/relay/{relay_id}'
        response = requests.post(url, params={'state': state}, timeout=3)
        response.raise_for_status()
        if ip not in device_states: device_states[ip] = {}
        device_states[ip][str(relay_id)] = state
        return True, response.text
    except requests.exceptions.RequestException as e:
        print(f"Error controlling relay {relay_id} on device {ip}: {e}")
        return False, str(e)

def handle_ai_logic(user_message):
    """Unified function to handle logic from both chat and voice."""
    devices = list(read_devices().values())
    if not devices:
        socketio.emit('new_message', {'sender': 'assistant', 'text': "No devices added yet."})
        start_cooldown()
        return

    device_context = []
    for device in devices:
        current_states = device_states.get(device["ip"], {})
        info = {
            "deviceName": device["name"], "room": device["room"], "ip": device["ip"],
            "controls": [{"relayIndex": int(idx), "name": name, "currentState": current_states.get(idx, "unknown")} for idx, name in device["relayNames"].items()]
        }
        device_context.append(info)

    prompt = f"""
You are a home assistant. Your task is to interpret a user's command and respond with a single JSON object.
The available devices and their current states are:
{json.dumps(device_context, indent=2)}
The user's command is: "{user_message}"
Your response MUST be a single JSON object with two keys: "actions" and "reply".
- "reply": A friendly, conversational reply to the user.
- "actions": A list of JSON objects, where each object represents a single device to control.
  - Each object in the list must have: "action" ("turn_on" or "turn_off"), "device_ip", and "relay_index".
  - If the command requires no action, the "actions" list should be empty.
CRITICAL RULES:
1.  If the user says "all", "everything", or a room name, you MUST generate an action for EACH relevant device.
2.  Before generating an action, you MUST check the "currentState". Do not generate a "turn_on" action for a device that is already "on". Do not generate a "turn_off" action for a device that is already "off".
3.  If all relevant devices are already in the requested state, the "actions" list must be empty, and your reply should inform the user.
"""
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "format": "json", "stream": False}

    try:
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=30)
        response.raise_for_status()
        api_result = response.json()
        ai_decision = json.loads(api_result.get("response", "{}"))
        
        actions_to_perform = ai_decision.get("actions", [])
        reply = ai_decision.get("reply", "I'm not sure how to respond.")
        action_performed = False

        # MODIFIED: Add server-side validation logic
        if actions_to_perform:
            valid_actions = []
            all_actions_redundant = True

            for action_item in actions_to_perform:
                device_ip = action_item.get("device_ip")
                relay_index = action_item.get("relay_index")
                state = "on" if action_item.get("action") == "turn_on" else "off"
                current_state = device_states.get(device_ip, {}).get(str(relay_index))

                if current_state != state:
                    valid_actions.append(action_item)
                    all_actions_redundant = False
            
            if all_actions_redundant and actions_to_perform:
                # The AI wanted to do something, but all devices were already in the correct state.
                # We override the AI's reply to be more accurate.
                reply = "It looks like everything is already in the state you requested."
            
            actions_to_perform = valid_actions

        if actions_to_perform:
            for action_item in actions_to_perform:
                action = action_item.get("action")
                device_ip = action_item.get("device_ip")
                relay_index = action_item.get("relay_index")
                if action in ["turn_on", "turn_off"] and device_ip and relay_index is not None:
                    state = "on" if action == "turn_on" else "off"
                    success, _ = control_physical_relay(device_ip, relay_index, state)
                    if success: action_performed = True
        
        socketio.emit('new_message', {'sender': 'assistant', 'text': reply})
        if action_performed:
            socketio.emit('refresh_states')

    except Exception as e:
        print(f"Error in AI logic: {e}")
        socketio.emit('new_message', {'sender': 'assistant', 'text': "Sorry, I had trouble processing that."})
    finally:
        start_cooldown()

# --- Flask Routes and SocketIO Events ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@app.route('/api/devices', methods=['GET'])
def get_devices():
    return jsonify(list(read_devices().values()))

@app.route('/api/states', methods=['GET'])
def get_states():
    return jsonify(device_states)

@app.route('/api/devices', methods=['POST'])
def add_device():
    data = request.get_json()
    if not data or not all(k in data for k in ['name', 'ip', 'room']): return jsonify({'error': 'Missing data'}), 400
    ip = data['ip']
    devices = read_devices()
    if ip in devices: return jsonify({'error': 'Device with this IP already exists'}), 409
    try:
        response = requests.get(f'http://{ip}/info', timeout=5)
        response.raise_for_status()
        device_info = response.json()
        num_relays = device_info.get('numRelays', 0)
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Could not connect to device at {ip}.'}), 502
    relay_names = {str(i): f'Relay {i + 1}' for i in range(num_relays)}
    new_device = {'name': data['name'], 'ip': ip, 'room': data['room'], 'numRelays': num_relays, 'relayNames': relay_names}
    devices[ip] = new_device
    write_devices(devices)
    update_state_for_device(ip)
    return jsonify(new_device), 201


@app.route('/api/devices/<string:ip>', methods=['DELETE'])
def remove_device(ip):
    devices = read_devices()
    if ip in devices:
        del devices[ip]
        write_devices(devices)
        if ip in device_states: del device_states[ip]
        return jsonify({'message': 'Device removed successfully'}), 200
    return jsonify({'error': 'Device not found'}), 404

@app.route('/api/devices/<string:ip>/relay/<int:relay_id>', methods=['POST'])
def control_relay_endpoint(ip, relay_id):
    data = request.get_json()
    state = data.get('state')
    if state not in ['on', 'off']: return jsonify({'error': "Invalid state"}), 400
    if ip not in read_devices(): return jsonify({'error': 'Device not found'}), 404
    success, message = control_physical_relay(ip, relay_id, state)
    if success: return jsonify({'message': message}), 200
    else: return jsonify({'error': 'Failed to communicate with the device'}), 502

@app.route('/api/devices/<string:ip>/relay_name', methods=['POST'])
def update_relay_name(ip):
    data = request.get_json()
    relay_index, new_name = data.get('relayIndex'), data.get('name')
    if relay_index is None or new_name is None: return jsonify({'error': 'Missing data'}), 400
    devices = read_devices()
    if ip in devices:
        devices[ip]['relayNames'][str(relay_index)] = new_name
        write_devices(devices)
        return jsonify(devices[ip]), 200
    return jsonify({'error': 'Device not found'}), 404

@app.route('/api/chat', methods=['POST'])
def handle_chat():
    user_message = request.json.get('message')
    if not user_message: return jsonify({'error': 'No message provided'}), 400
    handle_ai_logic(user_message)
    return jsonify({"status": "ok", "message": "Command sent to AI for processing"})

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")

if __name__ == '__main__':
    download_models()
    
    initialize_all_device_states()
    audio_processor = AudioProcessor()
    threading.Thread(target=audio_processor.start_listening_loop, daemon=True).start()
    
    print("Starting Flask-SocketIO server...")
    socketio.run(app, host='0.0.0.0', port=5001)
