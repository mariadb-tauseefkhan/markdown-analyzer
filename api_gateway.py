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
# The gateway now handles all file operations.
# The other services will receive pure data.

# --- (((( THIS IS THE NEW, GITHUB API DOWNLOADER )))) ---

def parse_github_url(item_url):
    """
    Parses a .../tree/main/folder URL into its GitHub API components.
    """
    try:
        parsed = urlparse(item_url)
        if "github.com" not in parsed.netloc:
            return None, None, None, None, "Not a GitHub URL"
        
        # We must unquote the URL path in case of spaces
        path_parts = unquote(parsed.path).split('/')
        # /user/repo/tree/branch/folder...
        
        owner = path_parts[1]
        repo = path_parts[2].replace('.git', '')
        
        branch_indicator = 'tree'
        if 'blob' in path_parts:
            branch_indicator = 'blob'
        
        if branch_indicator not in path_parts:
             # URL is likely just https://github.com/user/repo
             return owner, repo, "main", "", None

        idx = path_parts.index(branch_indicator)
        branch = path_parts[idx + 1]
        path = "/".join(path_parts[idx+2:])
        
        return owner, repo, branch, path, None
    except Exception as e:
        return None, None, None, None, f"URL Parsing failed: {e}"

def download_files_from_api(api_url, files_list, base_path=""):
    """
    Recursively fetches file content from the GitHub API.
    This does NOT write to disk.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    # In a real app, you'd add: "Authorization": "token YOUR_GITHUB_TOKEN"
    # to avoid rate limiting
    
    try:
        response = requests.get(api_url, headers=headers)
        response.raise_for_status() # Fails on 404, which is what we want
        items = response.json()
    except requests.exceptions.HTTPError:
         # This happens when the URL is case-sensitive (e.g. /Serverless)
         # We try to auto-correct by lowercasing the path
        try:
            response = requests.get(api_url.lower(), headers=headers)
            response.raise_for_status()
            items = response.json()
        except Exception:
            raise Exception(f"Failed to fetch from GitHub API. Check URL and case-sensitivity.")

    
    if not isinstance(items, list):
        items = [items] # Handle single file response

    for item in items:
        item_path = os.path.join(base_path, item['name'])
        if item['type'] == 'file' and item['name'].endswith('.md'):
            # It's a markdown file. Get its content.
            try:
                file_response = requests.get(item['download_url'])
                file_response.raise_for_status()
                files_list.append({
                    'path': item_path,
                    'content': file_response.text
                })
            except Exception:
                # Skip un-downloadable files
                pass
        
        elif item['type'] == 'dir':
            # It's a folder, recurse
            download_files_from_api(item['url'], files_list, item_path)

def get_repo_files(item_url):
    """
    Gets all .md file contents from a GitHub folder URL.
    """
    try:
        owner, repo, branch, path, error = parse_github_url(item_url)
        if error:
            return None, error

        # This is the API URL for the *contents* of the path
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        
        files_list = [] # This will be populated by the recursive function
        download_files_from_api(api_url, files_list)
        
        return files_list, None
        
    except Exception as e:
        return None, str(e)
# --- (((( END OF NEW DOWNLOAD FUNCTION )))) ---


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

    # 1. Get all file contents
    files_list, error = get_repo_files(folder_url)
    if error: return jsonify({'error': error}), 500
    if not files_list: return jsonify({'analytics': {'total_links_checked': 0}, 'details': []})

    # 2. Call the microservice with the *content*
    try:
        response = requests.post(
            f"{SERVICES['http_auditor']}/run_http_audit",
            json={'files': files_list, 'http_codes': http_codes},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to http-auditor: {e}"}), 500

# --- API 2: Code Block Scanner ---
@app.route('/api/v1/code_blocks', methods=['POST'])
def code_blocks():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400
    
    files_list, error = get_repo_files(folder_url)
    if error: return jsonify({'error': error}), 500
    if not files_list: return jsonify({'analytics': {'files_scanned': 0}, 'details': []})

    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_code_blocks",
            json={'files': files_list, 'scan_type': data.get('scan_type'), 'language': data.get('language')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

# --- API 3: Link Scanner ---
@app.route('/api/v1/links', methods=['POST'])
def links():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    files_list, error = get_repo_files(folder_url)
    if error: return jsonify({'error': error}), 500
    if not files_list: return jsonify({'analytics': {'files_scanned': 0}, 'details': []})
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_link_scan",
            json={'files': files_list, 'scan_type': data.get('scan_type'), 'url_pattern': data.get('url_pattern')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

# --- API 4: Text Scanner ---
@app.route('/api/v1/text_scanner', methods=['POST'])
def text_scanner():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    files_list, error = get_repo_files(folder_url)
    if error: return jsonify({'error': error}), 500
    if not files_list: return jsonify({'analytics': {'files_scanned': 0}, 'details': []})
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_text_scan",
            json={'files': files_list, 'regex': data.get('regex'), 'case_sensitive': data.get('case_sensitive')},
            headers={'Accept': request.headers.get('Accept')}
        )
        response.raise_for_status()
        return response.content, response.status_code, response.headers.items()
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

# --- API 5: Folder Analytics ---
@app.route('/api/v1/analytics', methods=['POST'])
def analytics():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    files_list, error = get_repo_files(folder_url)
    if error: return jsonify({'error': error}), 500
    if not files_list: return jsonify({'analytics': {'files_scanned': 0}})
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_analytics",
            json={'files': files_list} # Pass the file content
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

# --- API 6: Folder Lister ---
@app.route('/api/v1/list_folder', methods=['POST'])
def list_folder():
    data = request.json
    folder_url = data.get('folder_in_repo')
    if not folder_url: return jsonify({'error': 'Missing folder_in_repo'}), 400

    owner, repo, branch, path, error = parse_github_url(folder_url)
    if error: return jsonify({'error': error}), 500
    
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        items = response.json()
        
        files = []
        sub_folders = []
        for item in items:
            if item['type'] == 'file':
                files.append(item['name'])
            elif item['type'] == 'dir':
                sub_folders.append(item['name'])
                
        return jsonify({
            'folder_path': path,
            'files': sorted(files),
            'sub_folders': sorted(sub_folders)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- API 7: File Detail Extractor ---
@app.route('/api/v1/get_file_details', methods=['POST'])
def get_file_details():
    data = request.json
    file_url = data.get('file_in_repo')
    if not file_url: return jsonify({'error': 'Missing file_in_repo'}), 400

    files_list, error = get_repo_files(file_url) # This will download the single file
    if error: return jsonify({'error': error}), 500
    if not files_list: return jsonify({'error': 'File not found or is not markdown'}), 404
    
    try:
        response = requests.post(
            f"{SERVICES['content_scanner']}/run_get_file_details",
            json={'file': files_list[0]} # Send the single file's content
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except Exception as e:
        return jsonify({'error': f"Error connecting to content-scanner: {e}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
