import os
import re
import requests
import threading
import csv
import io
from queue import Queue
from flask import Flask, request, jsonify, Response
from collections import Counter

app = Flask(__name__)
MAX_LINK_CHECKER_THREADS = 10

# --- Helper: Read File ---
def read_file_content(full_path):
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        title = (re.search(r'^\s*#\s+(.+)', content, re.MULTILINE) or [None, 'No H1 Title Found'])[1].strip()
        return content, title, None
    except Exception as e:
        return None, None, f"Failed to read file: {e}"

# --- Helper: Find all .md files ---
def find_markdown_files(local_path):
    md_files = []
    for root, _, files in os.walk(local_path):
        for file in files:
            if file.endswith('.md'):
                md_files.append(os.path.join(root, file))
    return md_files

# --- Helper: 404 Checker ---
def check_link(link, results_list, lock):
    try:
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36' }
        response = requests.get(link, timeout=7, allow_redirects=True, headers=headers)
        status_code = response.status_code
        status_category = f"{status_code // 100}xx"
    except requests.exceptions.Timeout:
        status_code, status_category = 'N/A', 'Timeout'
    except requests.exceptions.ConnectionError:
        status_code, status_category = 'N/A', 'Connection Error'
    except Exception:
        status_code, status_category = 'N/A', 'Invalid URL'
        
    with lock: 
        results_list.append({
            'link': link, 
            'status_code': status_code, 
            'status_category': status_category
        })

def check_links_threaded(links):
    results = []
    link_queue = Queue()
    lock = threading.Lock()
    unique_links = set(links)
    for link in unique_links: link_queue.put(link)
    def worker():
        while not link_queue.empty():
            link = link_queue.get()
            if link: check_link(link, results, lock)
            link_queue.task_done()
    num_threads = min(MAX_LINK_CHECKER_THREADS, len(unique_links))
    for _ in range(num_threads):
        t = threading.Thread(target=worker, daemon=True); t.start()
    link_queue.join()
    return results

# --- Helper: CSV Generation ---
def generate_csv(data, headers):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(data)
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=report.csv"})

# --- Helper: JSON or CSV Response ---
def create_response(data, analytics=None):
    if request.headers.get('Accept') == 'text/csv':
        if not data.get('details'):
            return "No details to export", 400
        headers = data['details'][0].keys()
        return generate_csv(data['details'], headers)
    
    if analytics:
        data['analytics'] = analytics
    return jsonify(data)

# --- Endpoint: HTTP Code Audit ---
@app.route('/run_http_audit', methods=['POST'])
def run_http_audit():
    data = request.json
    local_path = data.get('local_path')
    http_codes_to_find = data.get('http_codes', []) # e.g., ["404", "301"] or ["*"]
    
    md_files = find_markdown_files(local_path)
    links_to_check = []
    file_link_map = {} # Maps link URL -> list of files/anchors

    for f in md_files:
        content, title, error = read_file_content(f)
        if error: continue
        rel_file = os.path.relpath(f, local_path)
        
        # Find all external links and images
        links_in_file = re.findall(r'\[(.*?)\]\((https?://[^\)]+)\)', content)
        images_in_file = re.findall(r'!\[.*?\]\((https?://[^\)]+)\)', content)
        
        all_ext_links = [{'link': url, 'text': text} for text, url in links_in_file]
        all_ext_links.extend([{'link': url, 'text': '[Image]'} for url in images_in_file])

        for link_data in all_ext_links:
            links_to_check.append(link_data['link'])
            if link_data['link'] not in file_link_map:
                file_link_map[link_data['link']] = []
            file_link_map[link_data['link']].append({
                'file': rel_file,
                'title': title,
                'anchor': link_data['text']
            })

    if not links_to_check:
        return jsonify({'analytics': {'total_links_checked': 0, 'status_counts': {}}, 'details': []})

    # --- This is the SLOW part ---
    link_results = check_links_threaded(links_to_check)
    
    status_counter = Counter(res['status_category'] for res in link_results)
            
    detailed_results = []
    for res in link_results:
        # Check if this link's status is one the user asked for
        # If user wants "*", we include all.
        status_str = str(res['status_code'])
        if ("*" in http_codes_to_find or 
            status_str in http_codes_to_find or 
            res['status_category'] in http_codes_to_find):
            
            # Add all files/anchors that use this link
            for source in file_link_map.get(res['link'], []):
                detailed_results.append({
                    'file': source['file'],
                    'title': source['title'],
                    'anchor': source['anchor'],
                    'link': res['link'],
                    'status_code': res['status_code'],
                    'status_category': res['status_category']
                })

    analytics = {
        'total_links_checked': len(link_results),
        'status_counts': dict(status_counter)
    }
    return create_response({'details': detailed_results}, analytics)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002)
