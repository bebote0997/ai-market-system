"""Arrow-safe presentation for every legacy dataframe."""
import json
from itertools import islice
from ui.i18n_es import display_rows

TABLE_ROW_LIMIT = 200


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


def dataframe(st, rows, *, preserve_raw=False, **kwargs):
    rows = [dict(row) for row in islice(rows, TABLE_ROW_LIMIT)]
    if preserve_raw:
        # Keep evidence for search/download; expose only readable projections.
        visible = display_rows(rows)
        raw_keys = {key for row in rows for key in row}
        order = list(dict.fromkeys(key for row in visible for key in row))
        names = {key: f"display:{key}" for key in order}
        kwargs["column_order"] = list(names.values())
        kwargs["column_config"] = {**{key: None for key in raw_keys},
                                   **{name: key for key, name in names.items()}}
        rows = [{**raw, **{names[key]: value for key, value in shown.items()}}
                for raw, shown in zip(rows, visible)]
    else:
        rows = display_rows(rows)
    return st.dataframe(normalize_rows(rows), **kwargs)
