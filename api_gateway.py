import os
import re
import requests
import uuid
import shutil
import subprocess
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
SERVICES = {
    'content_scanner': 'http://content-scanner:5001',
    'http_auditor': 'http://http-auditor:5002'
}
SCAN_CACHE_DIR = '/tmp/scans'

# --- NEW HELPER: GitHub URL Converter ---
def convert_github_url(url):
    """
    Converts a web-friendly GitHub URL (.../tree/main/...)
    into an SVN-friendly trunk URL (.../trunk/...).
    """
    if not url:
        return url
    # Replaces the first instance of /tree/main/ with /trunk/
    # This is more robust than a simple replace.
    return re.sub(r'/tree/main/', '/trunk/', url, 1)

# --- Helper: Download from GitHub ---
def download_repo_item(item_url):
    """
    Downloads a folder or file from GitHub using svn export.
    Returns the local path to the downloaded item and its name.
    """
    try:
        # --- NEW: Convert the URL first! ---
        svn_url = convert_github_url(item_url)
        
        scan_id = str(uuid.uuid4())
        item_name = svn_url.split('/')[-1]
        local_path = os.path.join(SCAN_CACHE_DIR, scan_id, item_name)
        
        cmd = ['svn', 'export', '--quiet', '--force', svn_url, local_path]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        return local_path, item_name, None
    except subprocess.CalledProcessError:
        return None, None, "Failed to download from GitHub. Check the URL (make sure it's .../tree/main/... or .../trunk/...)."
    except Exception as e:
        return None, None, str(e)

# --- Helper: Cleanup ---
def cleanup_scan(local_path):
    try:
        shutil.rmtree(os.path.dirname(local_path))
    except Exception as e:
        print(f"Warning: Failed to cleanup {local_path}. Error: {e}")

# --- API: Serve the API Docs ---
@app.route('/')
def serve_index():
    """Redirects root to the API docs."""
    return send_from_directory('.', 'api.html')

@app.route('/api')
def serve_api_docs():
    """Serves the interactive API documentation page."""
    return send_from_directory('.', 'api.html')

@app.route('/openapi.yaml')
def serve_openapi_spec():
    """Serves the raw OpenAPI spec file."""
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
