import os
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
SERVICES = {
    'analytics': 'http://analytics-service:5001',
    'scan': 'http://scan-service:5002',
    'link_auditor': 'http://link-auditor:5003'
}
ALLOWED_SCAN_PATH = '/scan_data'

# --- HELPER: Security Check ---
def is_safe_path(base, user_path):
    user_path = os.path.realpath(user_path)
    base_path = os.path.realpath(base)
    return user_path.startswith(base_path)

# --- Endpoint 1: Serve the HTML page ---
@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

# --- NEW: API DOCS ENDPOINTS ---
@app.route('/api')
def serve_api_docs():
    """Serves the interactive API documentation page."""
    return send_from_directory('.', 'api.html')

@app.route('/openapi.yaml')
def serve_openapi_spec():
    """Serves the raw OpenAPI spec file."""
    return send_from_directory('.', 'openapi.yaml')
# --- END NEW ENDPOINTS ---

# --- Endpoint 2: Get "Instant Analytics" on Page Load ---
@app.route('/get_analytics', methods=['POST'])
def get_analytics():
    data = request.json
    folder_path = data.get('folder_path', '/scan_data') 
    
    if not is_safe_path(ALLOWED_SCAN_PATH, folder_path):
        return jsonify({'error': 'Path is not allowed'}), 403
    if not os.path.isdir(folder_path):
        return jsonify({'error': f"Path not found: {folder_path}"}), 404

    try:
        response = requests.post(
            f"{SERVICES['analytics']}/run_analytics",
            json={'folder_path': folder_path}
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f"Failed to connect to analytics service: {e}"}), 500

# --- Endpoint 3: Run a specific "Scan" (Fast or Slow) ---
@app.route('/run_scan', methods=['POST'])
def run_scan():
    data = request.json
    folder_path = data.get('folder_path')
    task = data.get('task')
    
    if not folder_path or not task:
        return jsonify({'error': 'Missing folder_path or task'}), 400
    if not is_safe_path(ALLOWED_SCAN_PATH, folder_path):
        return jsonify({'error': 'Path is not allowed'}), 403
        
    all_files = []
    try:
        for root, _, files in os.walk(folder_path, followlinks=False):
            for file in files:
                if file.endswith('.md'):
                    all_files.append(os.path.relpath(os.path.join(root, file), folder_path))
    except Exception as e:
        return jsonify({'error': f"Error during file scan: {e}"}), 500
    
    if not all_files:
        return jsonify({'error': 'No .md files found'}), 404

    payload = {
        'files': all_files,
        'base_path': folder_path,
        'options': data.get('options', {})
    }
    
    try:
        if task == 'link_audit':
            response = requests.post(f"{SERVICES['link_auditor']}/run_link_audit", json=payload)
        else:
            payload['task'] = task
            response = requests.post(f"{SERVICES['scan']}/run_scan", json=payload)
            
        response.raise_for_status()
        return response.json(), response.status_code
        
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f"Failed to connect to a scan service: {e}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
