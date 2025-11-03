import os
import re
import requests
import threading
from queue import Queue
from flask import Flask, request, jsonify, send_from_directory

# --- CONFIGURATION ---
ALLOWED_SCAN_PATH = '/scan_data'
MAX_LINK_CHECKER_THREADS = 10

# --- FLASK APP ---
app = Flask(__name__)

# --- HELPER FUNCTIONS ---

def is_safe_path(base, user_path, follow_symlinks=True):
    if follow_symlinks: user_path = os.path.realpath(user_path)
    else: user_path = os.path.abspath(user_path)
    base_path = os.path.realpath(base)
    return user_path.startswith(base_path)

def check_link(link, broken_links_list, lock):
    try:
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36' }
        response = requests.get(link, timeout=7, allow_redirects=True, headers=headers)
        if 400 <= response.status_code < 600:
            with lock:
                broken_links_list.append({'link': link, 'status': response.status_code})
    except requests.exceptions.Timeout:
        with lock: broken_links_list.append({'link': link, 'status': 'Timeout'})
    except requests.exceptions.ConnectionError:
        with lock: broken_links_list.append({'link': link, 'status': 'Connection Error'})
    except Exception:
        with lock: broken_links_list.append({'link': link, 'status': 'Invalid URL'})

def check_links_threaded(links):
    broken_links = []
    link_queue = Queue()
    lock = threading.Lock()
    unique_links = set(links)
    for link in unique_links: link_queue.put(link)

    def worker():
        while not link_queue.empty():
            link = link_queue.get()
            if link: check_link(link, broken_links, lock)
            link_queue.task_done()

    num_threads = min(MAX_LINK_CHECKER_THREADS, len(unique_links))
    for _ in range(num_threads):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
    link_queue.join()
    return broken_links

def analyze_single_file(full_path, options):
    """
    Analyzes a single markdown file based on the selected options.
    """
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return {'error': f"Failed to read file: {e}"}

    result = {}
    
    # Get Title (always)
    title_match = re.search(r'^\s*#\s+(.+)', content, re.MULTILINE)
    if title_match:
        result['title'] = title_match.group(1).strip()
    else:
        result['title'] = 'No H1 Title Found'
    
    # --- Find all HTTP/HTTPS links WITH ANCHOR TEXT ---
    # NEW REGEX: Captures text in [text] and link in (link)
    http_links_with_anchor = re.findall(r'\[(.*?)\]\((https?://[^\)]+)\)', content)

    # --- 1. Check for Broken Links ---
    if options.get('check_broken_links'):
        links_to_check = [url for text, url in http_links_with_anchor]
        broken_link_results = check_links_threaded(links_to_check)
        
        # Create a map of all links to their *first* found anchor text
        link_to_text_map = {url: text for text, url in http_links_with_anchor}
        
        broken_links_with_anchor = []
        for b in broken_link_results:
            link = b['link']
            anchor_text = link_to_text_map.get(link, '[Image or No Anchor]')
            broken_links_with_anchor.append({
                'link': link, 
                'status': b['status'], 
                'text': anchor_text
            })
        result['broken_links'] = broken_links_with_anchor

    # --- 2. Check for Specific URL ---
    if options.get('check_specific_url'):
        specific_url = options.get('specific_url', '').strip()
        found_links = []
        if specific_url:
            for text, url in http_links_with_anchor:
                if url.startswith(specific_url):
                    found_links.append({'link': url, 'text': text})
        result['specific_url_links'] = found_links

    # --- 3. Check for Untagged Code Blocks ---
    if options.get('check_untagged_blocks'):
        untagged = []
        lines = content.split('\n')
        in_code_block = False
        for i, line in enumerate(lines, 1):
            stripped_line = line.strip()
            if stripped_line.startswith('```'):
                if not in_code_block:
                    in_code_block = True
                    if stripped_line == '```':
                        untagged.append(i)
                else:
                    in_code_block = False
        result['untagged_blocks'] = untagged

    return result

# --- API ENDPOINTS ---

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/scan_folder', methods=['POST'])
def scan_folder():
    data = request.json
    folder_path = data.get('folder_path')
    if not folder_path: return jsonify({'error': 'No folder_path provided'}), 400
    if not is_safe_path(ALLOWED_SCAN_PATH, folder_path):
        return jsonify({'error': f"Path is not allowed. Must be within {ALLOWED_SCAN_PATH}"}), 403
    if not os.path.isdir(folder_path):
        return jsonify({'error': f"Path does not exist or is not a directory: {folder_path}"}), 404

    files_found = []
    try:
        for root, _, files in os.walk(folder_path, followlinks=False):
            for file in files:
                if file.endswith('.md'):
                    files_found.append(os.path.relpath(os.path.join(root, file), folder_path))
    except Exception as e:
        return jsonify({'error': f"Error during scan: {e}"}), 500
    
    return jsonify({'files': files_found, 'base_path': folder_path})


@app.route('/analyze_files', methods=['POST'])
def analyze_files():
    data = request.json
    files = data.get('files', [])
    options = data.get('options', {})
    base_path = data.get('base_path')

    if not files or not base_path: return jsonify({'error': 'Missing files list or base_path'}), 400
    if not is_safe_path(ALLOWED_SCAN_PATH, base_path): return jsonify({'error': 'Base path is not allowed'}), 403

    results = {}
    for relative_file in files:
        full_path = os.path.normpath(os.path.join(base_path, relative_file))
        if not is_safe_path(base_path, full_path):
            results[relative_file] = {'error': 'Path traversal detected'}
            continue
        if not os.path.isfile(full_path):
            results[relative_file] = {'error': 'File not found'}
            continue
        
        results[relative_file] = analyze_single_file(full_path, options)

    return jsonify({'results': results})

# --- RUN THE APP ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
