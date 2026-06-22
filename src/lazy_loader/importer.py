import sys
import json
import pickle
import os
import lz4.frame
from importlib.abc import MetaPathFinder, Loader
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Sequence, Dict, Any
from pathlib import Path

class LazySiblingModule(ModuleType):
    """
    A smart module virtual container that dynamically compiles and 
    executes its encrypted/compressed source code on-demand the moment 
    any internal attribute is accessed by a sibling module.
    """
    def __init__(self, name: str, chunk_file: str, chunk_cache: Dict[str, Any], base_dir: Path):
        super().__init__(name)
        # Direct dictionary writing bypasses __getattribute__ during initialization
        self.__dict__["_chunk_file"] = chunk_file
        self.__dict__["_chunk_cache"] = chunk_cache
        self.__dict__["_base_dir"] = base_dir

    def __getattribute__(self, attr: str):
        # Universal special attributes must bypass execution to prevent circular core loops
        if attr in ("__name__", "__dict__", "__class__", "__spec__", "__path__", "__file__") or attr.startswith("_chunk"):
            return super().__getattribute__(attr)
        
        chunk_file = super().__getattribute__("_chunk_file")
        chunk_cache = super().__getattribute__("_chunk_cache")
        cache_state = chunk_cache[chunk_file]
        
        requested_name = super().__getattribute__("__name__")
        executed = cache_state["executed"]
        
        # Just-In-Time evaluation cascade
        if requested_name not in executed:
            executed.add(requested_name)
            source_string = cache_state["sources"][requested_name]
            filename = super().__getattribute__("__file__") or f"<chunked_source:{requested_name}>"
            
            # Compile text strings into native Python bytecode and execute inside local dictionary
            bytecode = compile(source_string, filename, 'exec')
            exec(bytecode, self.__dict__)
            
        return super().__getattribute__(attr)


class ChunkModuleLoader(Loader):
    def __init__(self, manifest: Dict[str, str], chunk_location: str, base_dir: Path, chunk_cache: Dict[str, Any]):
        self.manifest = manifest
        self.chunk_location = chunk_location
        self.base_dir = base_dir
        self.chunk_cache = chunk_cache 

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        return None
    
    def exec_module(self, module: ModuleType) -> None:
        requested_name = module.__name__
        chunk_file = self.manifest[requested_name]
        chunk_file_location = os.path.join(self.chunk_location, chunk_file)
        
        # --- STEP 1: FETCH & DECOMPRESS CHUNK FROM CACHE ---
        if chunk_file in self.chunk_cache:
            chunk_data = self.chunk_cache[chunk_file]
        else:
            if not os.path.exists(chunk_file_location):
                raise FileNotFoundError(f"Missing chunk: {chunk_file_location}")
                
            with open(chunk_file_location, "rb") as f:
                compressed_data = f.read()
            
            decompressed_data = lz4.frame.decompress(compressed_data)
            chunk_source_payload = pickle.loads(decompressed_data)
            
            self.chunk_cache[chunk_file] = {
                "sources": chunk_source_payload,
                "executed": set()
            }
            chunk_data = self.chunk_cache[chunk_file]

        sources = chunk_data["sources"]
        executed = chunk_data["executed"]

        # --- STEP 2: REGISTER ALL SILENT STUBS FIRST ---
        # Populate sys.modules using our smart Lazy Sibling proxy wrappers.
        # This completely visualizes all file targets before a single line executes.
        for name in sources.keys():
            if name == requested_name:
                continue
            if name not in sys.modules:
                sys.modules[name] = LazySiblingModule(name, chunk_file, self.chunk_cache, self.base_dir)
            
            sibling_mod = sys.modules[name]
            if not getattr(sibling_mod, "__file__", None):
                relative_src_path = name.replace(".", "/") + ".py"
                sibling_mod.__file__ = str(self.base_dir / relative_src_path)

        if requested_name not in sys.modules or sys.modules[requested_name] is not module:
            sys.modules[requested_name] = module

        if not getattr(module, "__file__", None):
            relative_src_path = requested_name.replace(".", "/") + ".py"
            module.__file__ = str(self.base_dir / relative_src_path)

        # --- STEP 3: EXECUTE TARGET REQUESTED MODULE ENTRY ---
        # Sibling files will now smoothly execute themselves in exact topological order
        # when triggered by the core execution below.
        if requested_name not in executed:
            executed.add(requested_name)
            requested_source = sources[requested_name]
            filename = module.__file__ or f"<chunked_source:{requested_name}>"
            
            bytecode = compile(requested_source, filename, 'exec')
            exec(bytecode, module.__dict__)


class ChunkMetaPathFinder(MetaPathFinder):
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.chunk_location = os.path.join(base_dir, '.chunks')
        self.chunk_cache: Dict[str, Any] = {}
        self.manifest = None
        
        manifest_path = os.path.join(self.chunk_location, 'manifest.json')
        with open(manifest_path, "r") as f:
            self.manifest = json.load(f)
        
    def find_spec(self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None) -> ModuleSpec | None:
        if self.manifest and fullname in self.manifest:
            print(f"{fullname} found in chunk manifest")
            loader = ChunkModuleLoader(self.manifest, self.chunk_location, self.base_dir, self.chunk_cache) 
            return ModuleSpec(fullname, loader)
        
        return None