import os
import re
from flask import Flask, request, jsonify
from collections import Counter

app = Flask(__name__)
STUB_PAGE_WORD_COUNT = 100

def read_file_content(full_path):
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read(), None
    except Exception as e:
        return None, f"Failed to read file: {e}"

@app.route('/run_analytics', methods=['POST'])
def run_analytics():
    data = request.json
    folder_path = data.get('folder_path')
    
    analytics = {
        'files_scanned': 0,
        'total_lines': 0,
        'code_blocks': {'total': 0, 'tagged': 0, 'untagged': 0},
        'links': {'total_external': 0, 'total_internal': 0},
        'content': {'stubs': 0, 'todos': 0}
    }
    
    all_files = []
    for root, _, files in os.walk(folder_path, followlinks=False):
        for file in files:
            if file.endswith('.md'):
                all_files.append(os.path.join(root, file))
    
    analytics['files_scanned'] = len(all_files)
    if not all_files:
        return jsonify(analytics) # Return empty (but successful) analytics

    for full_path in all_files:
        content, error = read_file_content(full_path)
        if error: continue
        
        lines = content.split('\n')
        analytics['total_lines'] += len(lines)
        
        # 1. Code Blocks
        all_blocks = re.findall(r'^```(.*)$', content, re.MULTILINE)
        if all_blocks:
            tagged = sum(1 for lang in all_blocks if lang.strip())
            analytics['code_blocks']['total'] += len(all_blocks)
            analytics['code_blocks']['tagged'] += tagged
            analytics['code_blocks']['untagged'] += len(all_blocks) - tagged

        # 2. Links
        all_links = re.findall(r'\[(.*?)\]\(((?!#)\S+)\)', content)
        for text, link in all_links:
            if link.startswith('http'):
                analytics['links']['total_external'] += 1
            else:
                analytics['links']['total_internal'] += 1
        
        # 3. Content
        if len(re.findall(r'\b\w+\b', content)) < STUB_PAGE_WORD_COUNT:
            analytics['content']['stubs'] += 1
        
        todos = re.findall(r'(TODO|FIXME|XXX):?', content, re.IGNORECASE)
        analytics['content']['todos'] += len(todos)
            
    return jsonify(analytics)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
