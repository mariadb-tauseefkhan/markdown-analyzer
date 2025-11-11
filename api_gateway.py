import os
import re
import requests
import uuid
import shutil
import subprocess
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from urllib.parse import urlparse, unquote

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
SERVICES = {
    'content_scanner': 'http://content-scanner:5001',
    'http_auditor': 'http://http-auditor:5002'
}
SCAN_CACHE_DIR = '/tmp/scans'
# --- NEW: Timeouts in seconds ---
FAST_SCAN_TIMEOUT = 60  # 1 minute for fast scans
SLOW_SCAN_TIMEOUT = 600 # 10 minutes for slow link audit

def parse_github_url(item_url):
    try:
        parsed = urlparse(item_url)
        if "github.com" not in parsed.netloc:
            return None, None, None, "Not a GitHub URL"
        parts = unquote(parsed.path).split('/')
        user = parts[1]
        repo = parts[2].replace('.git', '')
        repo_url = f"https://github.com/{user}/{repo}.git"
        branch_indicator = 'tree'
        if 'blob' in parts: branch_indicator = 'blob'
        if branch_indicator not in parts:
             return repo_url, "main", "", "URL does not contain /tree/ or /blob/. Assuming root."
        idx = parts.index(branch_indicator)
        branch = parts[idx + 1]
        folder_path = "/".join(parts[idx+2:])
        return repo_url, branch, folder_path, None
    except Exception as e:
        return None, None, None, f"URL Parsing failed: {e}"

def download_repo_item(item_url):
    scan_id = str(uuid.uuid4())
    scan_dir = os.path.join(SCAN_CACHE_DIR, scan_id)
    try:
        repo_url, branch, item_path, error = parse_github_url(item_url)
        if error: return None, None, None, error
        cmd = ['git', 'clone', '--no-checkout', '--depth', '1', '-b', branch, repo_url, scan_dir]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        cmd = ['git', 'sparse-checkout', 'init']
        subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=scan_dir)
        cmd = ['git', 'sparse-checkout', 'set', item_path]
        subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=scan_dir)
        cmd = ['git', 'checkout', branch]
        subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=scan_dir)
        final_path = os.path.join(scan_dir, item_path)
        item_name = os.path.basename(item_path) 
        if not os.path.exists(final_path):
            raise Exception(f"Path '{item_path}' was not found in the repository. Check spelling and case-sensitivity.")
        return final_path, item_name, scan_dir, None 
    except subprocess.CalledProcessError as e:
        error_message = f"Git operation failed. Repo: {repo_url}, Path: {item_path}. Error: {e.stderr}"
        if os.path.exists(scan_dir): shutil.rmtree(scan_dir) 
        return None, None, None, error_message
    except Exception as e:
        if os.path.exists(scan_dir): shutil.rmtree(scan_dir)
        return None, None, None, str(e)

# --- API: Serve the API Docs ---
@app.route('/')
def serve_index():
    return send_from_directory('.', 'api.html')
@app.route('/api')
def serve_api_docs():
    return send_from_directory('.', 'api.html')
@app.route('/openapi.yaml')
def serve_openapi_spec():
    return send_from_directory('.', 'openapi.yaml')

# --- API 1: HTTP Code Auditor ---
@app.route('/api/v1/http_codes', methods=['POST'])
def http_codes():
    data = request.json
    folder_url = data.get('folder_in_repo')
    http_codes = data.get('http_codes')
    if not folder_url or not http_codes:
        return jsonify({'error': 'Missing folder_in_repo or http_codes'}), 400

    local_path, _, scan_dir, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['http_auditor']}/run_http_audit",
            json={'local_path': local_path, 'scan_dir': scan_dir, 'http_codes': http_codes},
            headers={'Accept': request.headers.get('Accept')},
            timeout=SLOW_SCAN_TIMEOUT # <-- NEW: Long timeout for slow scan
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Link Auditor service timed out (took > 10 minutes)'}), 504
    except Exception as e:
        return jsonify({'error': f"Error connecting to http-auditor: {e}"}), 502

# --- API 2: Code Block Scanner ---
@app.route('/api/v1/code_blocks', methods=['POST'])
def code_blocks():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400
    
    local_path, _, scan_dir, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_code_blocks",
            json={'local_path': local_path, 'scan_dir': scan_dir, 'scan_type': data.get('scan_type'), 'language': data.get('language')},
            headers={'Accept': request.headers.get('Accept')},
            timeout=FAST_SCAN_TIMEOUT # <-- NEW: Fast timeout
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Content Scanner service timed out'}), 504
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 502

# --- API 3: Link Scanner ---
@app.route('/api/v1/links', methods=['POST'])
def links():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    local_path, _, scan_dir, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_link_scan",
            json={'local_path': local_path, 'scan_dir': scan_dir, 'scan_type': data.get('scan_type'), 'url_pattern': data.get('url_pattern')},
            headers={'Accept': request.headers.get('Accept')},
            timeout=FAST_SCAN_TIMEOUT # <-- NEW: Fast timeout
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 502

# --- API 4: Text Scanner ---
@app.route('/api/v1/text_scanner', methods=['POST'])
def text_scanner():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    local_path, _, scan_dir, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_text_scan",
            json={'local_path': local_path, 'scan_dir': scan_dir, 'regex': data.get('regex'), 'case_sensitive': data.get('case_sensitive')},
            headers={'Accept': request.headers.get('Accept')},
            timeout=FAST_SCAN_TIMEOUT # <-- NEW: Fast timeout
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 502

# --- API 5: Folder Analytics ---
@app.route('/api/v1/analytics', methods=['POST'])
def analytics():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    local_path, _, scan_dir, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_analytics",
            json={'local_path': local_path, 'scan_dir': scan_dir},
            timeout=FAST_SCAN_TIMEOUT # <-- NEW: Fast timeout
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Analytics service timed out'}), 504
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 502

# --- API 6: Folder Lister ---
@app.route('/api/v1/list_folder', methods=['POST'])
def list_folder():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    local_path, folder_name, scan_dir, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_list_folder",
            json={'local_path': local_path, 'folder_name': folder_name, 'scan_dir': scan_dir},
            timeout=FAST_SCAN_TIMEOUT # <-- NEW: Fast timeout
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 502

# --- API 7: File Detail Extractor ---
@app.route('/api/v1/get_file_details', methods=['POST'])
def get_file_details():
    data = request.json
    file_url = data.get('file_in_repo')
    if not file_url: return jsonify({'error': 'Missing file_in_repo'}), 400

    local_path, file_name, scan_dir, error = download_repo_item(file_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_get_file_details",
            json={'local_path': local_path, 'file_name': file_name, 'scan_dir': scan_dir},
            timeout=FAST_SCAN_TIMEOUT # <-- NEW: Fast timeout
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 502

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
