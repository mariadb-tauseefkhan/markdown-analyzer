import os
import re
import csv
import io
from flask import Flask, request, jsonify, Response
from collections import Counter

app = Flask(__name__)

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

# --- Endpoint 1: Code Blocks ---
@app.route('/run_code_blocks', methods=['POST'])
def run_code_blocks():
    data = request.json
    files = data.get('files', []) # This is now a list of {'path': ..., 'content': ...}
    scan_type = data.get('scan_type')
    language = data.get('language', '').lower()
    
    detailed_results = []
    files_with_matches = 0
    
    for file_data in files:
        content = file_data['content']
        title = (re.search(r'^\s*#\s+(.+)', content, re.MULTILINE) or [None, 'No H1 Title Found'])[1].strip()
        rel_file = file_data['path']
        found_on_page = False
        
        if scan_type == 'untagged':
            for i, line in enumerate(content.split('\n'), 1):
                if line.strip() == '```':
                    detailed_results.append({'file': rel_file, 'title': title, 'line_number': i})
                    found_on_page = True
        
        elif scan_type == 'specific_language':
            for i, line in enumerate(content.split('\n'), 1):
                if line.strip().lower() == '```' + language:
                    detailed_results.append({'file': rel_file, 'title': title, 'line_number': i, 'language_tag': language})
                    found_on_page = True
        
        if found_on_page:
            files_with_matches += 1

    analytics = {'files_scanned': len(files), 'files_with_matches': files_with_matches}
    return create_response({'details': detailed_results}, analytics)

# --- Endpoint 2: Link Scanner ---
@app.route('/run_link_scan', methods=['POST'])
def run_link_scan():
    data = request.json
    files = data.get('files', [])
    scan_type = data.get('scan_type')
    url_pattern = data.get('url_pattern', '')
    
    detailed_results = []
    total_links_found = 0
    files_with_matches = 0
    
    for file_data in files:
        content = file_data['content']
        title = (re.search(r'^\s*#\s+(.+)', content, re.MULTILINE) or [None, 'No H1 Title Found'])[1].strip()
        rel_file = file_data['path']
        
        links_in_file = re.findall(r'\[(.*?)\]\(((?!#)\S+)\)', content)
        found_on_page = False
        
        for text, link in links_in_file:
            match = False
            if scan_type == 'internal' and not link.startswith('http'):
                match = True
            elif scan_type == 'external' and link.startswith('http'):
                match = True
            elif scan_type == 'starting_with' and link.startswith(url_pattern):
                match = True
                
            if match:
                detailed_results.append({'file': rel_file, 'title': title, 'anchor': text, 'link': link})
                total_links_found += 1
                found_on_page = True
        
        if found_on_page:
            files_with_matches += 1
            
    analytics = {'files_scanned': len(files), 'files_with_matches': files_with_matches, 'total_links_found': total_links_found}
    return create_response({'details': detailed_results}, analytics)

# --- Endpoint 3: Text Scanner ---
@app.route('/run_text_scan', methods=['POST'])
def run_text_scan():
    data = request.json
    files = data.get('files', [])
    regex_pattern = data.get('regex')
    case_sensitive = data.get('case_sensitive', False)
    
    if not regex_pattern:
        return jsonify({'error': 'Missing regex pattern'}), 400
        
    flags = re.IGNORECASE if not case_sensitive else 0
    try:
        regex = re.compile(regex_pattern, flags)
    except re.error as e:
        return jsonify({'error': f"Invalid Regex: {e}"}), 400
        
    detailed_results = []
    total_matches_found = 0
    files_with_matches = 0
    
    for file_data in files:
        content = file_data['content']
        title = (re.search(r'^\s*#\s+(.+)', content, re.MULTILINE) or [None, 'No H1 Title Found'])[1].strip()
        rel_file = file_data['path']
        found_on_page = False
        
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            if regex.search(line):
                detailed_results.append({'file': rel_file, 'title': title, 'line_number': i, 'line_text': line.strip()})
                total_matches_found += 1
                found_on_page = True

        if found_on_page:
            files_with_matches += 1

    analytics = {'files_scanned': len(files), 'files_with_matches': files_with_matches, 'total_matches_found': total_matches_found}
    return create_response({'details': detailed_results}, analytics)

# --- Endpoint 4: Folder Analytics ---
@app.route('/run_analytics', methods=['POST'])
def run_analytics():
    data = request.json
    files = data.get('files', [])
    
    if not files:
        return jsonify({'analytics': {'files_scanned': 0}})

    analytics = {
        'files_scanned': len(files), 'total_lines': 0, 'total_links': 0,
        'total_external_links': 0, 'total_code_blocks': 0, 'total_untagged_blocks': 0
    }
    
    for file_data in files:
        content = file_data['content']
        analytics['total_lines'] += len(content.split('\n'))
        links = re.findall(r'\[(.*?)\]\(((?!#)\S+)\)', content)
        analytics['total_links'] += len(links)
        analytics['total_external_links'] += sum(1 for _, link in links if link.startswith('http'))
        
        blocks = re.findall(r'^```(.*)$', content, re.MULTILINE)
        analytics['total_code_blocks'] += len(blocks)
        analytics['total_untagged_blocks'] += sum(1 for lang in blocks if not lang.strip())

    return jsonify({'analytics': analytics})

# --- Endpoint 5: Folder Lister (No longer used by gateway) ---
@app.route('/run_list_folder', methods=['POST'])
def run_list_folder():
    return jsonify({'error': 'This endpoint is deprecated and handled by the gateway'}), 500

# --- Endpoint 6: File Detail Extractor ---
@app.route('/run_get_file_details', methods=['POST'])
def run_get_file_details():
    data = request.json
    file_data = data.get('file') # Expects a single {'path':..., 'content':...}
    
    content = file_data['content']
    title = (re.search(r'^\s*#\s+(.+)', content, re.MULTILINE) or [None, 'No H1 Title Found'])[1].strip()

    headers = [{'level': len(h[0]), 'text': h[1].strip()} for h in re.findall(r'^(#+)\s+(.+)', content, re.MULTILINE)]
    links = [{'text': t, 'url': u, 'type': 'external' if u.startswith('http') else 'internal'} for t, u in re.findall(r'\[(.*?)\]\(((?!#)\S+)\)', content)]
    images = [{'alt_text': a, 'src': s} for a, s in re.findall(r'!\[(.*?)\]\((.*?)\)', content)]
    code_blocks = [{'language': l.strip() or 'untagged'} for l in re.findall(r'^```(.*)$', content, re.MULTILINE)]

    return jsonify({
        'file': file_data['path'],
        'title': title,
        'analytics': {
            'line_count': len(content.split('\n')),
            'word_count': len(re.findall(r'\b\w+\b', content)),
            'header_count': dict(Counter(h['level'] for h in headers)),
            'link_count': {'total': len(links), 'external': sum(1 for l in links if l['type'] == 'external'), 'internal': sum(1 for l in links if l['type'] == 'internal')},
            'image_count': len(images),
            'code_block_count': len(code_blocks)
        },
        'lists': {
            'headers': headers,
            'links': links,
            'images': images,
            'code_blocks': code_blocks
        }
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
