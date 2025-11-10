import os
import re
import requests
import uuid
import shutil
import subprocess
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from urllib.parse import urlparse

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
SERVICES = {
    'content_scanner': 'http://content-scanner:5001',
    'http_auditor': 'http://http-auditor:5002'
}
SCAN_CACHE_DIR = '/tmp/scans'


# --- (((( THIS IS THE NEW, CORRECT DOWNLOAD FUNCTION )))) ---

def parse_github_url(item_url):
    """
    Parses a .../tree/main/folder URL into its components.
    """
    try:
        parsed = urlparse(item_url)
        if "github.com" not in parsed.netloc:
            return None, None, None, "Not a GitHub URL"

        parts = parsed.path.split('/')
        # parts = ['', 'user', 'repo', 'tree', 'branch', 'folder', 'subfolder']
        
        user = parts[1]
        repo = parts[2].replace('.git', '')
        repo_url = f"https://github.com/{user}/{repo}.git"
        
        if 'tree' in parts:
            idx = parts.index('tree')
        elif 'blob' in parts:
            idx = parts.index('blob')
        else:
            return repo_url, "main", "", "URL does not contain /tree/ or /blob/. Assuming root." # Best guess for root

        branch = parts[idx + 1]
        folder_path = "/".join(parts[idx+2:])
        
        return repo_url, branch, folder_path, None
    except Exception as e:
        return None, None, None, f"URL Parsing failed: {e}"

def download_repo_item(item_url):
    """
    Downloads a single folder from GitHub using git sparse-checkout.
    Returns the *full path* to the *downloaded subfolder*.
    """
    scan_id = str(uuid.uuid4())
    # This is the temporary directory where we clone the *empty* repo
    scan_dir = os.path.join(SCAN_CACHE_DIR, scan_id)
    
    try:
        repo_url, branch, folder_path, error = parse_github_url(item_url)
        if error:
            return None, None, error

        # 1. Clone the repo, but don't download any files yet
        cmd = ['git', 'clone', '--no-checkout', '--depth', '1', '-b', branch, repo_url, scan_dir]
        subprocess.run(cmd, capture_output=True, text=True, check=True)

        # 2. Tell git we are using sparse checkout
        cmd = ['git', 'sparse-checkout', 'init', '--cone']
        subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=scan_dir)

        # 3. Set the *one* folder we want to download
        cmd = ['git', 'sparse-checkout', 'set', folder_path]
        subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=scan_dir)

        # 4. Now, download *only* that folder
        cmd = ['git', 'checkout', branch]
        subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=scan_dir)
        
        # 5. Return the full path to the *specific folder* we downloaded
        # This is the path our other services will scan
        final_path = os.path.join(scan_dir, folder_path)
        item_name = os.path.basename(folder_path) # e.g., "mariadb-cloud"
        
        return final_path, item_name, None
        
    except subprocess.CalledProcessError as e:
        error_message = f"Git operation failed. Repo: {repo_url}, Folder: {folder_path}. Error: {e.stderr}"
        return None, None, error_message
    except Exception as e:
        return None, None, str(e)
# --- (((( END OF NEW DOWNLOAD FUNCTION )))) ---


# --- Helper: Cleanup ---
def cleanup_scan(local_path):
    """
    Deletes the temporary directory for a scan.
    """
    try:
        # We want to delete the parent UUID folder (e.g., /tmp/scans/scan_id)
        # We get this by going "up" one level from the final_path
        shutil.rmtree(os.path.dirname(local_path))
    except Exception as e:
        print(f"Warning: Failed to cleanup {local_path}. Error: {e}")

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
# --- END API DOCS ---

# --- API 1: HTTP Code Auditor ---
@app.route('/api/v1/http_codes', methods=['POST'])
def http_codes():
    data = request.json
    folder_url = data.get('folder_in_repo')
    http_codes = data.get('http_codes')
    if not folder_url or not http_codes:
        return jsonify({'error': 'Missing folder_in_repo or http_codes'}), 400

    # local_path is now the path to the *subfolder* (e.g., /tmp/scans/uuid/mariadb-cloud)
    local_path, _, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['http_auditor']}/run_http_audit",
            json={'local_path': local_path, 'http_codes': http_codes},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to http-auditor: {e}"}), 500
    finally:
        cleanup_scan(local_path)

# --- API 2: Code Block Scanner ---
@app.route('/api/v1/code_blocks', methods=['POST'])
def code_blocks():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400
    
    local_path, _, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_code_blocks",
            json={'local_path': local_path, 'scan_type': data.get('scan_type'), 'language': data.get('language')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500
    finally:
        cleanup_scan(local_path)

# --- API 3: Link Scanner ---
@app.route('/api/v1/links', methods=['POST'])
def links():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    local_path, _, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_link_scan",
            json={'local_path': local_path, 'scan_type': data.get('scan_type'), 'url_pattern': data.get('url_pattern')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500
    finally:
        cleanup_scan(local_path)

# --- API 4: Text Scanner ---
@app.route('/api/v1/text_scanner', methods=['POST'])
def text_scanner():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    local_path, _, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_text_scan",
            json={'local_path': local_path, 'regex': data.get('regex'), 'case_sensitive': data.get('case_sensitive')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500
    finally:
        cleanup_scan(local_path)

# --- API 5: Folder Analytics ---
@app.route('/api/v1/analytics', methods=['POST'])
def analytics():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    local_path, _, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_analytics",
            json={'local_path': local_path}
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500
    finally:
        cleanup_scan(local_path)

# --- API 6: Folder Lister ---
@app.route('/api/v1/list_folder', methods=['POST'])
def list_folder():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    local_path, folder_name, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_list_folder",
            json={'local_path': local_path, 'folder_name': folder_name}
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500
    finally:
        cleanup_scan(local_path)

# --- API 7: File Detail Extractor ---
@app.route('/api/v1/get_file_details', methods=['POST'])
def get_file_details():
    data = request.json
    file_url = data.get('file_in_repo')
    if not file_url: return jsonify({'error': 'Missing file_in_repo'}), 400

    local_path, file_name, error = download_repo_item(file_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_get_file_details",
            json={'local_path': local_path, 'file_name': file_name}
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500
    finally:
        cleanup_scan(local_path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
