"""Restricted loader for ``lab_*.pkl`` chunks written by ``replay/lab_run.py`` (security audit I-2).

Plain ``pickle.loads`` executes arbitrary code from a crafted file. These offline tools only need the
plain data ``lab_run.py`` writes (dicts, lists, tuples, numbers, strings, datetimes, pandas Timestamps and
numpy scalars), so any other global is refused. Only load ``.pkl`` files you produced yourself.
"""
import io
import pickle

ALLOWED_GLOBALS = frozenset({
    ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "complex"), ("builtins", "slice"),
    ("collections", "OrderedDict"),
    ("datetime", "datetime"), ("datetime", "date"), ("datetime", "time"), ("datetime", "timedelta"),
    ("datetime", "timezone"),
    ("zoneinfo", "ZoneInfo"), ("zoneinfo._zoneinfo", "ZoneInfo"),
    ("pandas._libs.tslibs.timestamps", "_unpickle_timestamp"),
    ("pandas._libs.tslibs.timedeltas", "_timedelta_unpickle"),
    ("numpy", "dtype"),
    ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
})


class UnsafePickleError(pickle.UnpicklingError):
    """A pickle referenced a global outside ``ALLOWED_GLOBALS``."""


class _RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) not in ALLOWED_GLOBALS:
            raise UnsafePickleError(f"refusing to load global {module}.{name}")
        return super().find_class(module, name)


def loads(data):
    return _RestrictedUnpickler(io.BytesIO(data)).load()


def load_lab_chunk(path):
    """Read one ``lab_*.pkl`` chunk with the restricted unpickler."""
    return loads(path.read_bytes())
