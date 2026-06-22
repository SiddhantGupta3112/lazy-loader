import ast
import os
from pathlib import Path

def normalize_path(path, base_dir):
    new_path = os.path.abspath(path)
    abs_base = os.path.abspath(base_dir)
    new_path = os.path.relpath(new_path, abs_base)
    new_path = os.path.splitext(new_path)[0]
    new_path = new_path.replace("\\", ".")
    new_path = new_path.replace("/", ".")
    new_path = new_path.replace(".__init__", "")
    new_path = new_path.replace("__init__", "")
    return new_path

def topological_sort(graph: dict[str, set[str]], all_files: list[str]) -> list[str]:
    """
    Sorts modules so that independent building blocks appear BEFORE 
    the modules that import them. Cycles are appended at the end safely.
    """
    sorted_order = []
    visited = {}  

    for node in all_files:
        if node not in visited:
            visited[node] = 0

    def dfs(node: str):
        if node not in graph:
            return True 
            
        if visited.get(node) == 1:
            return False 
        if visited.get(node) == 2:
            return True

        visited[node] = 1 
        
        for neighbor in graph[node]:
            if neighbor in visited and visited[neighbor] != 2:
                dfs(neighbor)

        visited[node] = 2 
        sorted_order.append(node)
        return True

    for file_node in all_files:
        if visited[file_node] == 0:
            dfs(file_node)

    return sorted_order

def build_dependency_graph(base_dir: Path) -> tuple[dict, list, list]:
    local_python_files = []
    dependency_graph = {}

    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(".py"):
                normalized_path = normalize_path(os.path.join(root, file), base_dir)
                local_python_files.append(normalized_path)

    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(Path(root), Path(file))
                with open(file_path, "r", encoding="utf-8") as f:
                    source = f.read()
                
                tree = ast.parse(source)
                normalized_key = normalize_path(file_path, base_dir)
                dependency_graph[normalized_key] = set()
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if node.level > 0:
                            current_file_dir = os.path.dirname(file_path)
                            dots = ("../" * (node.level - 1)) + "."
                            dots = dots.replace("/", os.sep)
                            package_dir = os.path.join(current_file_dir, dots)

                            if node.module:
                                module_path_format = node.module.replace(".", os.sep)
                                relative_path = os.path.join(package_dir, module_path_format + ".py")
                                normalized_module = normalize_path(relative_path, base_dir)
                                if normalized_module in local_python_files:
                                    dependency_graph[normalized_key].add(normalized_module)
                            else:
                                for n in node.names:
                                    relative_path = os.path.join(package_dir, n.name + ".py")
                                    normalized_module = normalize_path(relative_path, base_dir)
                                    if normalized_module in local_python_files:
                                        dependency_graph[normalized_key].add(normalized_module)
                        else:
                            if node.module in local_python_files:
                                dependency_graph[normalized_key].add(node.module)

                    elif isinstance(node, ast.Import):
                        for n in node.names:
                            if n.name in local_python_files:
                                dependency_graph[normalized_key].add(n.name)
    
    execution_order = topological_sort(dependency_graph, local_python_files)
    
    return dependency_graph, local_python_files, execution_order