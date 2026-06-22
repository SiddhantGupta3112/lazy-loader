from collections import deque
import uuid
from pathlib import Path
import os
import ast

from .analyzer import normalize_path

def bfs(dependency_graph: dict[str, set[str]], local_python_files: list[str]) -> dict[str, set[str]]:    
    chunks = {}
    visited = {file: False for file in local_python_files}
    q = deque()
    
    for file in local_python_files:
        if not visited[file]:
            current_chunk = uuid.uuid4().hex
            chunks[current_chunk] = set()
            q.append(file)
            visited[file] = True
            
            while q:
                current_file = q.popleft()
                chunks[current_chunk].add(current_file)
                for dependency in dependency_graph[current_file]:
                    if not visited[dependency]:
                        q.append(dependency)
                        visited[dependency] = True
    return chunks

def compute_chunks(dependency_graph: dict, local_python_files: list[str]) -> dict:
    for module, imports in list(dependency_graph.items()):
        for imported in list(imports):
            if imported in dependency_graph:
                dependency_graph[imported].add(module)
                
    return bfs(dependency_graph, local_python_files)

def scan_chunk_decorators(base_dir: Path) -> dict[str, str]:
    chunk_mapping = {}
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(Path(root), Path(file))
                with open(file_path, "r", encoding="utf-8") as f:
                    source = f.read()
                
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    continue

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for decorator in node.decorator_list:
                            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                                if decorator.func.id == "chunk":
                                    extracted_args = [arg.value for arg in decorator.args if isinstance(arg, ast.Constant)]
                                    if extracted_args:
                                        normalized_key = normalize_path(file_path, base_dir)
                                        if normalized_key in chunk_mapping:
                                            raise RuntimeError(f"Multiple @chunk decorators found in {file_path}")
                                        chunk_mapping[normalized_key] = extracted_args[0]
    return chunk_mapping

def assign_chunks(
    computed_chunks: dict[str, set[str]], 
    decorator_chunks: dict[str, str], 
    local_python_files: list[str],
    global_execution_order: list[str]   
) -> dict[str, list[str]]:
    
    reconciled_sets = {}
    chunked_files = {file: False for file in local_python_files}
    
    for file, chunk in decorator_chunks.items():
        if chunk not in reconciled_sets:
            reconciled_sets[chunk] = set()
        reconciled_sets[chunk].add(file)
        chunked_files[file] = True
        
    for chunk_id, files in list(computed_chunks.items()):
        chunk_files = set()
        for file in files:
            if not chunked_files[file]:
                chunk_files.add(file)
                chunked_files[file] = True
    
        if len(chunk_files) != 0:
            reconciled_sets[chunk_id] = chunk_files
            
    final_ordered_chunks = {}
    for chunk_id, file_set in reconciled_sets.items():
        final_ordered_chunks[chunk_id] = sorted(file_set, key=lambda f: global_execution_order.index(f))
            
    return final_ordered_chunks
                
                
