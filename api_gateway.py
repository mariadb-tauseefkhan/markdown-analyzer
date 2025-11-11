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

# --- (((( THIS IS THE NEW, GITHUB API DOWNLOADER )))) ---

def parse_github_url(item_url):
    """
    Parses a .../tree/main/folder URL into its GitHub API components.
    """
    try:
        parsed = urlparse(item_url)
        if "github.com" not in parsed.netloc:
            return None, None, None, None, "Not a GitHub URL"

        parts = parsed.path.split('/')
        # /user/repo/tree/branch/folder...
        owner = parts[1]
        repo = parts[2].replace('.git', '')
        
        branch_indicator = 'tree'
        if 'blob' in parts:
            branch_indicator = 'blob'
        
        if branch_indicator not in parts:
             return owner, repo, "main", "", "URL does not contain /tree/ or /blob/. Assuming root."

        idx = parts.index(branch_indicator)
        branch = parts[idx + 1]
        path = "/".join(parts[idx+2:])
        
        return owner, repo, branch, path, None
    except Exception as e:
        return None, None, None, None, f"URL Parsing failed: {e}"

def download_github_contents(api_url, local_download_path):
    """
    Recursively downloads files and folders from the GitHub contents API.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    # NOTE: For private repos, you'd add:
    # "Authorization": "token YOUR_GITHUB_TOKEN"
    
    response = requests.get(api_url, headers=headers)
    response.raise_for_status() # Fails on 404, which is what we want
    
    items = response.json()
    
    # If it's a single file (not a list), handle it
    if not isinstance(items, list):
        items = [items]

    for item in items:
        # We must create the full path on our local disk
        item_local_path = os.path.join(local_download_path, item['name'])
        
        if item['type'] == 'file':
            # Download the file content
            file_response = requests.get(item['download_url'])
            file_response.raise_for_status()
            # Ensure the directory for the file exists
            os.makedirs(os.path.dirname(item_local_path), exist_ok=True)
            with open(item_local_path, 'wb') as f:
                f.write(file_response.content)
        
        elif item['type'] == 'dir':
            # It's a folder, create it and recurse
            os.makedirs(item_local_path, exist_ok=True)
            # Call this function again with the API URL for that sub-folder
            download_github_contents(item['url'], item_local_path)

def download_repo_item(item_url):
    """
    Downloads a folder or file from GitHub using the GitHub API.
    Returns the *full path* to the *downloaded item*.
    """
    scan_id = str(uuid.uuid4())
    # This is the base temporary directory
    base_download_dir = os.path.join(SCAN_CACHE_DIR, scan_id)
    os.makedirs(base_download_dir, exist_ok=True)
    
    try:
        owner, repo, branch, path, error = parse_github_url(item_url)
        if error:
            return None, None, None, error

        # This is the API URL for the *contents* of the path
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        
        # This is the path *on our disk* where it will be saved
        # e.g., /tmp/scans/uuid/mariadb-cloud
        item_name = os.path.basename(path) or repo # Use repo name if path is empty
        final_local_path = os.path.join(base_download_dir, item_name)

        # Start the recursive download
        download_github_contents(api_url, final_local_path)
        
        # Validation: Check if anything was actually downloaded
        if not os.listdir(final_local_path):
             raise Exception(f"Path '{path}' was found but is empty or download failed.")
        
        return final_local_path, item_name, base_download_dir, None
        
    except requests.exceptions.HTTPError as e:
        # This will catch 404s if the path is wrong
        error_message = f"GitHub API request failed. Status Code: {e.response.status_code}. Error: {e.response.text}"
        if os.path.exists(base_download_dir): shutil.rmtree(base_download_dir)
        return None, None, None, error_message
    except Exception as e:
        if os.path.exists(base_download_dir): shutil.rmtree(base_download_dir)
        return None, None, None, str(e)
# --- (((( END OF NEW DOWNLOAD FUNCTION )))) ---


# --- Helper: Cleanup ---
def cleanup_scan(scan_dir):
    """
    Deletes the entire temporary scan directory (e.g., /tmp/scans/uuid)
    """
    try:
        shutil.rmtree(scan_dir)
    except Exception as e:
        print(f"Warning: Failed to cleanup {scan_dir}. Error: {e}")

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

    local_path, _, scan_dir, error = download_repo_item(folder_url)
    if error: return jsonify({'error': error}), 500
    
    try:
        response = requests.post(
            f"{SERVICES['http_auditor']}/run_http_audit",
            json={'local_path': local_path, 'http_codes': http_codes},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        cleanup_scan(scan_dir) 
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        cleanup_scan(scan_dir) 
        return jsonify({'error': f"Error connecting to http-auditor: {e}"}), 500

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
            json={'local_path': local_path, 'scan_type': data.get('scan_type'), 'language': data.get('language')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        cleanup_scan(scan_dir) 
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        cleanup_scan(scan_dir) 
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

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
            json={'local_path': local_path, 'scan_type': data.get('scan_type'), 'url_pattern': data.get('url_pattern')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        cleanup_scan(scan_dir) 
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        cleanup_scan(scan_dir) 
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

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
            json={'local_path': local_path, 'regex': data.get('regex'), 'case_sensitive': data.get('case_sensitive')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        cleanup_scan(scan_dir) 
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        cleanup_scan(scan_dir) 
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

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
            json={'local_path': local_path}
        )
        response.raise_for_status()
        cleanup_scan(scan_dir) 
        return response.json(), response.status_code
    except Exception as e:
        cleanup_scan(scan_dir) 
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

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
            json={'local_path': local_path, 'folder_name': folder_name}
        )
        response.raise_for_status()
        cleanup_scan(scan_dir) 
        return response.json(), response.status_code
    except Exception as e:
        cleanup_scan(scan_dir) 
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

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
            json={'local_path': local_path, 'file_name': file_name}
        )
        response.raise_for_status()
        cleanup_scan(scan_dir) 
        return response.json(), response.status_code
    except Exception as e:
        cleanup_scan(scan_dir) 
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
