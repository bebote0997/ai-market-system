"""Arrow-safe presentation for every legacy dataframe."""
import json


def normalize_rows(rows):
    """Preserve uniform scalars; stringify containers and heterogeneous columns."""
    rows = [dict(row) for row in rows]
    keys = {key for row in rows for key in row}
    for key in keys:
        values = [row.get(key) for row in rows]
        kinds = {type(value) for value in values if value is not None}
        mixed = len(kinds) > 1 and not kinds <= {int, float}
        for row in rows:
            value = row.get(key)
            if isinstance(value, (tuple, list, dict)):
                row[key] = json.dumps(value, ensure_ascii=False, default=str)
            elif mixed and value is not None:
                row[key] = str(value)
    return rows


def dataframe(st, rows, **kwargs):
    return st.dataframe(normalize_rows(rows), **kwargs)
