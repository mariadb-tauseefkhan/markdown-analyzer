import os
import re
import requests
import threading
from queue import Queue
from flask import Flask, request, jsonify
from collections import Counter

app = Flask(__name__)
MAX_LINK_CHECKER_THREADS = 10

# --- 404 Checker ---
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

def read_file_content(full_path):
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        title = (re.search(r'^\s*#\s+(.+)', content, re.MULTILINE) or [None, 'No H1 Title Found'])[1].strip()
        return content, title, None
    except Exception as e:
        return None, None, f"Failed to read file: {e}"

@app.route('/run_link_audit', methods=['POST'])
def run_link_audit():
    data = request.json
    files = data.get('files', [])
    base_path = data.get('base_path')
    options = data.get('options', {})
    
    # Get the list of statuses the user wants to see
    selected_statuses = options.get('link_audit_statuses', [])
    # If they selected "Other", we add our error categories
    if 'Other' in selected_statuses:
        selected_statuses.extend(['Timeout', 'Connection Error', 'Invalid URL'])

    links_to_check = []
    file_link_map = {} # Maps files to their links

    for rel_file in files:
        full_path = os.path.normpath(os.path.join(base_path, rel_file))
        content, title, error = read_file_content(full_path)
        if error: continue

        # Find all external links and images
        links_in_file = re.findall(r'\[(.*?)\]\((https?://[^\)]+)\)', content)
        images_in_file = re.findall(r'!\[.*?\]\((https?://[^\)]+)\)', content)
        
        all_ext_urls_with_text = [{'link': url, 'text': text} for text, url in links_in_file]
        all_ext_urls_with_text.extend([{'link': url, 'text': '[Image]'} for url in images_in_file])
        
        if not all_ext_urls_with_text:
            continue

        file_link_map[rel_file] = {'title': title, 'links_with_text': all_ext_urls_with_text}
        links_to_check.extend([l['link'] for l in all_ext_urls_with_text])

    if not links_to_check:
        return jsonify({'analytics': {'total_links_checked': 0, 'status_counts': {}}, 'details': []})

    # --- This is the SLOW part ---
    link_results = check_links_threaded(links_to_check)
    
    status_counter = Counter(res['status_category'] for res in link_results)
    
    # --- This is the NEW filtering part ---
    result_map = {res['link']: res for res in link_results if res['status_category'] in selected_statuses}
            
    detailed_results = []
    for rel_file, info in file_link_map.items():
        links_in_file_with_status = []
        for link_data in info['links_with_text']:
            link = link_data['link']
            # Only add links that were in our filtered map
            if link in result_map:
                result = result_map[link]
                result['text'] = link_data['text']
                links_in_file_with_status.append(result)
        
        if links_in_file_with_status:
            detailed_results.append({
                'file': rel_file,
                'title': info['title'],
                'links': links_in_file_with_status
            })

    analytics = {
        'total_links_checked': len(link_results),
        'status_counts': dict(status_counter)
    }
    return jsonify({'analytics': analytics, 'details': detailed_results})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003)
