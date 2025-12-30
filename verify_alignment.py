import json
import ast
import sys

def load_bq_json(filepath):
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data['schema']['fields']
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return []

def extract_code_schema(filepath, var_name):
    with open(filepath, 'r') as f:
        tree = ast.parse(f.read())
    
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == var_name:
                    return ast.literal_eval(node.value)
    return {}

def fields_to_dict(fields):
    # Flatten structure: name -> {type, mode, fields}
    res = {}
    for f in fields:
        name = f['name']
        res[name] = {
            'type': f['type'],
            'mode': f.get('mode', 'NULLABLE'),
            'fields': fields_to_dict(f.get('fields', []))
        }
    return res

def compare(bq_fields, code_fields, context=""):
    bq_map = fields_to_dict(bq_fields)
    code_map = fields_to_dict(code_fields.get('fields', []))
    
    errors = []
    
    # 1. Check Code fields exist in BQ (Prevent "Cannot add fields" error)
    for name, c_def in code_map.items():
        if name not in bq_map:
            errors.append(f"[{context}] Field '{name}' defined in models.py but MISSING in BigQuery table.")
            continue
        
        b_def = bq_map[name]
        
        # Check Type
        if c_def['type'] != b_def['type']:
            # Allow some looseness? No, BQ is strict. 
            # Exception: BQ output might say INTEGER, code says INT64 (synonyms), or FLOAT/FLOAT64
            # Normalized comparison
            t1 = c_def['type'].upper().replace('INTEGER', 'INT64').replace('FLOAT', 'FLOAT64')
            t2 = b_def['type'].upper().replace('INTEGER', 'INT64').replace('FLOAT', 'FLOAT64')
            if t1 != t2:
                 errors.append(f"[{context}] Type Mismatch for '{name}': Code={c_def['type']}, BQ={b_def['type']}")

        # Recurse for RECORD
        if c_def['type'] == 'RECORD':
            # Construct fake schema dicts to reuse function
            sub_code = {'fields': [ {'name': k, **v} for k,v in c_def['fields'].items() ] } 
            # We need to reconstruct the list format code_map produces to pass to fields_to_dict...
            # Actually, fields_to_dict returns a dict. 
            # Let's clean this up. c_def['fields'] is ALREADY a dict from our helper.
            # But compare expects lists of dicts (the raw structure).
            # Wait, fields_to_dict call recursive returns dict.
            # Let's adjust helper.
            pass

    # Re-comparing logic with recursion simpler:
    all_keys = set(code_map.keys()) | set(bq_map.keys())
    
    for name in all_keys:
        if name in code_map and name not in bq_map:
             errors.append(f"[{context}] Field '{name}' in models.py NOT in BigQuery (Will Fail Insert).")
        # elif name in bq_map and name not in code_map:
             # This is Warning/Opportunity, not Error.
        #     print(f"[{context}] Info: Field '{name}' in BQ but not in models.py.")
        elif name in code_map and name in bq_map:
            c_def = code_map[name]
            b_def = bq_map[name]
            
            # Type Check
            t1 = c_def['type'].upper().replace('INTEGER', 'INT64').replace('FLOAT', 'FLOAT64').replace('BOOL', 'BOOLEAN')
            t2 = b_def['type'].upper().replace('INTEGER', 'INT64').replace('FLOAT', 'FLOAT64').replace('BOOL', 'BOOLEAN')
            if t1 != t2:
                errors.append(f"[{context}] Type Mismatch '{name}': Code={t1}, BQ={t2}")
            
            if t1 == 'RECORD':
                # Convert back to list form for recursion or just recurse on dicts
                # My helper fields_to_dict returns dict of dicts.
                # Just need to modify compare to accept dicts? No, let's just create a Recurse function.
                sub_errors = compare_dicts(b_def['fields'], c_def['fields'], context + "." + name)
                errors.extend(sub_errors)

    return errors

def compare_dicts(bq_map, code_map, context):
    errors = []
    for name, c_def in code_map.items():
        if name not in bq_map:
             errors.append(f"[{context}] Field '{name}' in models.py NOT in BigQuery.")
             continue
        
        b_def = bq_map[name]
        t1 = c_def['type'].upper().replace('INTEGER', 'INT64').replace('FLOAT', 'FLOAT64').replace('BOOL', 'BOOLEAN')
        t2 = b_def['type'].upper().replace('INTEGER', 'INT64').replace('FLOAT', 'FLOAT64').replace('BOOL', 'BOOLEAN')
        
        if t1 != t2:
            errors.append(f"[{context}] Type Mismatch '{name}': Code={t1}, BQ={t2}")
            
        if t1 == 'RECORD':
            errors.extend(compare_dicts(b_def['fields'], c_def['fields'], context + "." + name))
            
    return errors


def run_audit():
    print("Loading schemas...")
    
    # 1. Media Items
    media_bq = load_bq_json('schema_ad_debug.json')
    media_code = extract_code_schema('models.py', 'MEDIA_ITEMS_SCHEMA')
    
    print("\n--- Verifying MEDIA_ITEMS_SCHEMA ---")
    errs = compare(media_bq, media_code, "MediaItems")
    if not errs:
        print("SUCCESS: Aligned.")
    else:
        for e in errs: print(e)

    # 2. Audio Library
    lib_bq = load_bq_json('audio_lib_schema.json')
    lib_code = extract_code_schema('models.py', 'AUDIO_LIBRARY_SCHEMA')
    
    print("\n--- Verifying AUDIO_LIBRARY_SCHEMA ---")
    errs2 = compare(lib_bq, lib_code, "AudioLibrary")
    if not errs2:
        print("SUCCESS: Aligned.")
    else:
        for e in errs2: print(e)
        
    # 3. Audio Segments
    seg_bq = load_bq_json('schema_segments_debug.json')
    seg_code = extract_code_schema('models.py', 'AUDIO_SEGMENTS_SCHEMA')
    
    print("\n--- Verifying AUDIO_SEGMENTS_SCHEMA ---")
    errs3 = compare(seg_bq, seg_code, "AudioSegments")
    if not errs3:
        print("SUCCESS: Aligned.")
    else:
        for e in errs3: print(e)

if __name__ == "__main__":
    run_audit()
