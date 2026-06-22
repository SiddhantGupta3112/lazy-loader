import sys
import os
import json
import pickle
import lz4.frame
from pathlib import Path
from src.lazy_loader.analyzer import build_dependency_graph
from src.lazy_loader.chunker import assign_chunks, scan_chunk_decorators, compute_chunks
from src.lazy_loader.importer import ChunkMetaPathFinder

def build_chunks(base_dir: Path):
    base_path = os.path.join(base_dir, ".chunks")
    if not os.path.exists(base_path):
        os.mkdir(base_path)
        
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    
    # 1. FIXED: Unpack the execution_order returned from your topological analyzer pass
    dependency_graph, local_python_files, execution_order = build_dependency_graph(base_dir)
    computed_chunks = compute_chunks(dependency_graph, local_python_files)
    user_chunks = scan_chunk_decorators(base_dir)
    
    # 2. FIXED: Pass the execution_order down so final_chunks contains sorted lists
    final_chunks = assign_chunks(computed_chunks, user_chunks, local_python_files, execution_order)
    
    manifest = {}
        
    for chunk_id, modules in final_chunks.items():
        chunk_source_payload = {}
        file_name = os.path.join(base_path, chunk_id + '.chunk')
        
        # modules is now a list pre-sorted by dependency priority!
        for module in modules:
            relative_file_path = module.replace(".", "/") + ".py"
            absolute_file_path = base_dir / relative_file_path
            
            with open(absolute_file_path, "r", encoding="utf-8") as src_f:
                source_string = src_f.read()
                
            # Dictionary insertion preserves the exact topological sequence 
            chunk_source_payload[module] = source_string
            manifest[module] = chunk_id + '.chunk'

        # The serialized binary payload is now written in correct execution order
        serialized_data = pickle.dumps(chunk_source_payload)
        compressed_data = lz4.frame.compress(serialized_data)
        
        with open(file_name, "wb") as f:
            f.write(compressed_data)
            
    manifest_path = os.path.join(base_path, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)
        
def install_loader(base_dir: Path):
    finder = ChunkMetaPathFinder(base_dir=base_dir)
    sys.meta_path.insert(0, finder)
    
def start(base_dir: str | Path):
    base_dir = Path(base_dir).resolve()
    build_chunks(base_dir)
    install_loader(base_dir)
        
