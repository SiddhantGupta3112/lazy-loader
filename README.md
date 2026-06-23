# python-lazy-loader

[![PyPI](https://img.shields.io/pypi/v/lazy-loader-lib)](https://pypi.org/project/lazy-loader-lib/)

A Python import system extension that analyzes a project's module dependency graph at build time, groups related modules into compressed chunks, and intercepts Python's import machinery at runtime to load entire dependency clusters together rather than one file at a time.

```bash
pip install lazy-loader-lib
```

---

## What it does

When you import a module from a chunked project, the loader transparently:

1. Intercepts the import via a custom `sys.meta_path` finder
2. Finds which chunk the requested module belongs to
3. Decompresses and deserializes the entire chunk from disk
4. Pre-populates `sys.modules` with lazy proxy stubs for all modules in the chunk
5. Executes each module's source on first attribute access in topological order

Subsequent imports of other modules in the same chunk are instant cache hits in `sys.modules` — no disk read, no decompression, no re-execution.

---

## Why this exists

Large Python projects often have modules with heavy initialization — loading model weights, opening database connections, importing large libraries. If these modules are imported one by one as needed, the cold-start cost is spread across many individual import events. Grouping related modules into compressed chunks and loading them together amortizes this cost: one disk read, one decompression, all related modules ready simultaneously.

The static dependency graph also makes module relationships explicit and queryable — useful for auditing large codebases, detecting circular imports, and identifying isolated subsystems.

---

## Usage

```python
from lazy_loader import start, chunk

# Call once at your entry point before any project imports
start("path/to/your/project")

# All subsequent imports are intercepted by the loader
from your_project import heavy_module   # loads entire chunk
from your_project import sibling        # instant -- already in sys.modules
```

### Manual chunk assignment

Place `@chunk("name")` on any function in a file to force that entire file into a named chunk, overriding automatic grouping:

```python
# inference_model.py
from lazy_loader import chunk

@chunk("inference")
def load():
    pass  # function body is irrelevant -- decorator is a static marker only

class Model:
    ...
```

Files sharing the same chunk name load together. Files without `@chunk` are grouped automatically by the dependency graph analyzer.

---

## How it works

### Build phase (`start()` call)

**1 — Static analysis**

Every `.py` file in the target directory is parsed with Python's `ast` module. Import statements (`import x`, `from x import y`, `from . import z`) are extracted and resolved to local file paths, building a directed dependency graph `{module_name: set_of_imported_local_modules}`. External and stdlib imports are ignored — only local project files participate in the graph.

**2 — Connected components**

The directed graph is treated as undirected and BFS finds all connected components. Each component becomes one chunk. Disconnected subgraphs stay in separate chunks and are never loaded together.

```
module_a → module_b → module_c    # one chunk: {a, b, c}
isolated                           # separate chunk: {isolated}
```

**3 — Manual overrides**

The AST scanner finds any function decorated with `@chunk("name")` and moves that entire file into the named chunk, regardless of what automatic analysis concluded. Multiple files can share a chunk name — they all load together.

**4 — Topological sort**

Within each chunk, modules are ordered so that dependencies appear before dependants. This ensures that when a module's source is executed and tries to import a sibling, the sibling's namespace is already initialized.

**5 — Serialization**

Each chunk's source code strings are stored as `{module_name: source_string}`, serialized with `pickle`, compressed with `lz4`, and written to `.chunks/{chunk_id}.chunk`. A `manifest.json` maps every module name to its chunk file.

### Runtime phase (import interception)

A `ChunkMetaPathFinder` is inserted at position 0 in `sys.meta_path`. Every `import` statement Python processes goes through it first. If the module name appears in the manifest, the finder returns a `ModuleSpec` pointing at a `ChunkModuleLoader`. Otherwise it returns `None` and Python's normal import machinery handles it transparently.

`ChunkModuleLoader.exec_module` runs when Python needs to initialize the module:

1. Reads the `.chunk` file, decompresses with lz4, deserializes with pickle
2. Registers `LazySiblingModule` proxy stubs in `sys.modules` for all modules in the chunk
3. The requested module itself is morphed into a `LazySiblingModule`
4. On first attribute access to any module, its source is compiled and exec'd into its namespace

---

## Architecture decisions

**Why source code instead of serialized module objects**

The natural first approach was to import each module and serialize the live object with `dill`. This failed because dill stores class and function references by recording their originating module name, then re-imports that module during deserialization. With a custom `sys.meta_path` interceptor installed, that re-import triggered `exec_module` again, which triggered dill deserialization again — infinite recursion. Storing raw source code strings and exec'ing them avoids this entirely: pickle only serializes strings, and exec never touches the import system.

**Why lazy proxy stubs instead of immediate execution**

An earlier version pre-registered plain `ModuleType` stubs and immediately exec'd all sibling sources before exec'ing the requested module. This worked for linear pipelines but broke on cross-chunk references and complex enterprise topologies where execution order matters in ways the topological sort couldn't fully resolve. `LazySiblingModule` defers each module's source execution until its first attribute is accessed — by which point Python has already set up the full module context and cross-references resolve correctly.

**Why `@chunk` operates at file granularity**

Splitting individual functions out of a file would require tracing every name each function transitively references — closures, module-level constants, sibling helpers — and safely exec'ing only a subset of a file's top-level statements. Python's module system has no native concept of partial module loading, and static analysis of Python (a dynamic language) cannot reliably determine which names a function actually needs at runtime. File-level granularity keeps the analysis tractable and the behavior predictable. If finer splitting is needed, the correct approach is restructuring source files.

**Why topological sort within chunks**

If module A exec's before module B but A's source does `from B import x` at module level, B's stub hasn't been exec'd yet and `x` doesn't exist on it. Topological order guarantees that independent modules (no imports from others) execute first so by the time a dependant module runs, all its dependencies have already populated their namespaces.

---

## Known limitations

**Performance** — benchmarks show the current implementation is not faster than standard Python imports for small to medium projects. The compression/decompression overhead and proxy indirection currently outweigh the benefits of co-loading. Optimization is planned as a future iteration — the architecture is sound, the implementation needs profiling and tuning.

**Relative imports** — `from . import x` where `x` is a name defined inside `__init__.py` rather than a separate file is handled by checking whether a matching `.py` file exists. If not, the edge is silently omitted. Same-package explicit module references (`from .module import x`) are fully supported.

**Sibling error reporting** — `LazySiblingModule` defers execution until first attribute access. If a sibling's source has a runtime error, it surfaces as an `AttributeError` on the first attribute accessed rather than as a clear import error. This makes debugging sibling module errors harder than standard Python import errors.

**Module-level side effects** — print statements, file writes, or network calls at module level execute when the chunk first loads any member, not when that specific module is first imported. This matches standard Python behavior for eager imports but may be surprising if side effects were expected to be strictly deferred.

---

## Project structure

```
lazy-loader-lib/
├── lazy_loader/
│   ├── __init__.py      # exports: start, chunk
│   ├── analyzer.py      # AST parser, dependency graph, topological sort
│   ├── chunker.py       # BFS connected components, decorator scanner, chunk assignment
│   ├── decorators.py    # @chunk runtime decorator and registry
│   ├── importer.py      # ChunkMetaPathFinder, ChunkModuleLoader, LazySiblingModule
│   └── loader.py        # build_chunks, install_loader, start
└── sample_project/      # test fixtures across five dependency topologies
```