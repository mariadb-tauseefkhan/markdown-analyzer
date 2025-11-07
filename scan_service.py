import os
import re
from flask import Flask, request, jsonify

app = Flask(__name__)

def read_file_content(full_path):
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        title = (re.search(r'^\s*#\s+(.+)', content, re.MULTILINE) or [None, 'No H1 Title Found'])[1].strip()
        return content, title, None
    except Exception as e:
        return None, None, f"Failed to read file: {e}"

@app.route('/run_scan', methods=['POST'])
def run_scan():
    data = request.json
    files = data.get('files', [])
    base_path = data.get('base_path')
    task = data.get('task')
    options = data.get('options', {})
    
    detailed_results = []

    for rel_file in files:
        full_path = os.path.normpath(os.path.join(base_path, rel_file))
        content, title, error = read_file_content(full_path)
        if error: continue

        # --- Task 1: Code Block Analysis ---
        if task == 'code_blocks':
            task_type = options.get('code_block_task', 'untagged')
            all_blocks = re.findall(r'^```(.*)$', content, re.MULTILINE)
            if not all_blocks: continue
            
            if task_type == 'untagged':
                untagged_count = sum(1 for lang in all_blocks if not lang.strip())
                if untagged_count > 0:
                    untagged_lines = []
                    in_code_block = False
                    for i, line in enumerate(content.split('\n'), 1):
                        if line.strip().startswith('```'):
                            if not in_code_block:
                                in_code_block = True
                                if line.strip() == '```': 
                                    untagged_lines.append(i)
                            else:
                                in_code_block = False
                    
                    detailed_results.append({
                        'file': rel_file, 'title': title, 'type': 'untagged',
                        'count': untagged_count,
                        'lines': [line for i, line in enumerate(untagged_lines) if i % 2 == 0] # Only get opening fences
                    })
            
            elif task_type == 'specific_language':
                lang_to_find = options.get('code_block_lang', '').strip().lower()
                if not lang_to_find: continue
                
                found_lang_lines = []
                for i, line in enumerate(content.split('\n'), 1):
                    if line.strip().lower() == ('```' + lang_to_find):
                        found_lang_lines.append(i)
                
                if found_lang_lines:
                    detailed_results.append({
                        'file': rel_file, 'title': title, 'type': lang_to_find,
                        'count': len(found_lang_lines),
                        'lines': found_lang_lines
                    })

        # --- Task 2: Specific URL ---
        elif task == 'specific_url':
            specific_url = options.get('specific_url', '').strip()
            if not specific_url: continue
            
            links_in_file = re.findall(r'\[(.*?)\]\((https?://[^\)]+)\)', content)
            found_links = []
            for text, url in links_in_file:
                if url.startswith(specific_url):
                    found_links.append({'link': url, 'text': text})
            
            if found_links:
                detailed_results.append({ 'file': rel_file, 'title': title, 'found_links': found_links })

        # --- Task 3: Find Text ---
        elif task == 'find_text':
            text_to_find = options.get('text_to_find', '').strip()
            if not text_to_find: continue
            
            found_lines = []
            text_lower = text_to_find.lower()
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if text_lower in line.lower():
                    found_lines.append({'line_num': i, 'line_text': line.strip()})
            
            if found_lines:
                detailed_results.append({ 'file': rel_file, 'title': title, 'found_text': found_lines })
                
    # Return a generic response with the details
    return jsonify({'details': detailed_results})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002)
