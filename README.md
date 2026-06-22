# python-lazy-loader

A Python import system extension that analyzes a project's module dependency graph at build time, groups related modules into compressed chunks, and intercepts Python's import machinery at runtime to load entire dependency clusters together rather than one file at a time.

---

## What it does

When you import a module from a chunked project, the loader transparently:

1. Intercepts the import via a custom `sys.meta_path` finder
2. Finds which chunk the requested module belongs to
3. Decompresses and deserializes the entire chunk from disk
4. Pre-populates `sys.modules` with all modules in the chunk simultaneously
5. Executes each module's source in topological order so dependencies are ready before dependants

Subsequent imports of other modules in the same chunk are instant cache hits in `sys.modules` — no disk read, no decompression, no execution overhead.

---

## Why this exists

Large Python projects often have modules with heavy initialization — loading model weights, opening database connections, importing large libraries. If these modules are imported one by one as needed, the cold-start cost is spread across many individual import events, each with its own disk read and execution overhead. Grouping related modules into compressed chunks and loading them together amortizes this cost: one disk read, one decompression, all related modules ready.

The static dependency graph also makes the project's module relationships explicit and queryable — useful for auditing large codebases, detecting circular imports, and identifying isolated subsystems that could be deployed independently.

---

## Tech stack

Python 3.10+, `ast` (stdlib), `pickle` (stdlib), `lz4` for compression. No runtime dependencies beyond lz4.

---

## How it works

### Build phase

```
your_project/
├── module_a.py     # imports module_b
├── module_b.py     # imports module_c
├── module_c.py     # no local imports
└── isolated.py     # no connections to the above
```

Running `start("your_project/")` triggers:

1. **Static analysis** — every `.py` file is parsed with `ast`. Import statements are extracted and resolved to local file paths, building a directed dependency graph `{module: set_of_imported_modules}`.

2. **Connected components** — the directed graph is treated as undirected and BFS finds all connected components. Each component becomes one chunk. The above example produces two chunks: `{module_a, module_b, module_c}` and `{isolated}`.

3. **Manual overrides** — any file decorated with `@chunk("name")` is pulled out of its automatic group and placed in the named chunk, regardless of what the graph says. Files in the same named chunk load together. Files in different named chunks stay separate even if the graph connected them.

4. **Topological sort** — within each chunk, modules are ordered so that dependencies appear before dependants. This ensures that when `module_a`'s source is exec'd and tries to import `module_b`, `module_b` is already initialized.

5. **Serialization** — each chunk's source code strings are stored as a dict `{module_name: source_string}`, serialized with `pickle`, compressed with `lz4`, and written to `.chunks/{chunk_id}.chunk`. A `manifest.json` maps every module name to its chunk file.

### Runtime phase

A `ChunkMetaPathFinder` is inserted at the front of `sys.meta_path`. Every `import` statement Python processes goes through it first. If the module name appears in the manifest, the finder returns a `ModuleSpec` pointing at a `ChunkModuleLoader`. Otherwise it returns `None` and Python's normal import machinery handles it.

`ChunkModuleLoader.exec_module` runs when Python needs to initialize the module:

1. Reads the chunk file, decompresses with lz4, deserializes with pickle
2. Registers `LazySiblingModule` stubs in `sys.modules` for all other modules in the chunk
3. Executes the requested module's source with `exec(compile(source, filename, 'exec'), module.__dict__)`
4. Sibling stubs execute their own source on first attribute access via `__getattribute__`

---

## Usage

```bash
pip install lz4
```

```python
from lazy_loader import start, chunk

# In your project's entry point
start("path/to/your/project")

# All imports from this point are intercepted
from your_project import heavy_module   # loads entire chunk
from your_project import sibling        # instant cache hit, already in sys.modules
```

### Manual chunk assignment

Place `@chunk("name")` on any function in a file to force that file into a named chunk:

```python
# inference_model.py
from lazy_loader import chunk

@chunk("inference")
def load():
    pass   # function body is irrelevant -- decorator is a marker only

class Model:
    ...
```

Files sharing the same chunk name load together. Files without `@chunk` are grouped automatically by the dependency graph.

---

## Architecture decisions

**Why source code instead of serialized module objects**

The natural first approach was to import each module and serialize the live object with `dill`. This failed: dill stores references to classes and functions by recording their module name, then re-imports that module during deserialization to retrieve them. With a custom `sys.meta_path` interceptor installed, that re-import triggered `exec_module` again, which triggered dill deserialization again — infinite recursion. Storing raw source code strings and exec'ing them avoids this entirely: pickle only needs to serialize strings, and exec never touches the import system.

**Why siblings pre-register as stubs before any source is exec'd**

When `module_a`'s source runs `from module_b import SomeClass`, Python looks up `module_b` in `sys.modules`. If it isn't there, a new import is triggered — which hits the interceptor, which tries to load the chunk again. By pre-registering `LazySiblingModule` stubs for all chunk members before exec'ing any source, all intra-chunk imports resolve to already-registered modules rather than triggering new import cycles.

**Why topological order matters**

If `module_a` exec's before `module_b` but `module_a`'s source does `from module_b import x` at module level, `module_b`'s stub hasn't been exec'd yet and `x` doesn't exist on it. Topological order guarantees independent modules (no imports from others) execute first, so by the time a dependant module runs, all its dependencies have already populated their namespaces.

**Why `@chunk` operates at file granularity, not function granularity**

Splitting individual functions out of a file would require tracing every name each function transitively references — closures, module-level constants, sibling helpers — and exec'ing only a subset of a file's top-level statements safely. Python's module system has no native concept of partial module loading. File-level granularity keeps the analysis tractable and the behavior predictable. If finer splitting is needed, the correct approach is restructuring source files.

---

## Known limitations

Relative imports are supported for `from .module import x` and `from ..module import x` patterns. Bare `from . import x` (where `x` is a name defined inside `__init__.py` rather than a separate file) is handled by checking whether a matching `.py` file exists; if not, the edge is silently omitted, which is correct behavior since there is no file to chunk.

The `LazySiblingModule` defers sibling execution until first attribute access. If a sibling's source has a runtime error, it surfaces as an `AttributeError` on the first attribute accessed rather than as a clear import error — harder to debug than an immediate failure.

Module-level side effects (print statements, file writes, network calls at import time) execute when the chunk loads, not when that specific module is first imported. This matches standard Python behavior but may be surprising if side effects were expected to be deferred.