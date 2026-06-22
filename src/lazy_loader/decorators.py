_chunk_registry = {}

def chunk(group_name: str):
    def decorator(func):
        _chunk_registry[group_name] = func
        return func
    return decorator


