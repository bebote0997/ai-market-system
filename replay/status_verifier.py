"""Owner-copy status audit, P8.6 §2 / DEC-8.18a. Never opens a database writable.

DB execution observations (including STOPPED) are unattested; live verification
belongs to Phase 13. STOPPED means a stopped runner key with stale DB activity,
and outstanding RUNNING rows listed for recovery, never proof of process exit.
No start, recovery, migration, certification or repair is performed.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from storage.database import SHA_PATTERN

BASELINE = 'f5032baeb87766ad74905093c1b4195117995092'
EX_NOTE = 'EX_UNATTESTED (DEC-8.18a: live verification in Phase 13)'
STATE_KEYS = ('experiment_started', 'experiment_started_at_utc', 'experiment_baseline_sha',
              'experiment_freeze_sha', 'runner', 'heartbeat', 'last_run', 'last_success',
              'scheduler', 'enabled_symbols')


def timestamp(value):
    """Accept explicit UTC only; never interpret a naive timestamp as UTC."""
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return at if at.utcoffset() == timedelta(0) else None
    except (ValueError, TypeError, AttributeError):
        return None


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _rows(db, sql, params=()):
    return [dict(row) for row in db.execute(sql, params)]


def _body(row):
    body = json.loads(row['payload'])
    if not isinstance(body, dict):
        raise ValueError('payload must be a JSON object')
    return body


def history(db, starts, state):
    """M1–M10 and E1–E7 attribution, preserving original rows and origin run ids.

    Segment transitions require anchored §3.3.6 verification, for which this CLI
    has no trust-root input. Such histories fail closed, rather than claiming a
    verified chain from database-local hashes.
    """
    journal = _rows(db, 'SELECT * FROM journal ORDER BY id')
    runs = _rows(db, 'SELECT * FROM runs ORDER BY slot_key')
    findings, mapped = [], []

    def bad(rule, table, identity, reason):
        findings.append(dict(rule=rule, table=table, identity=identity, reason=reason))

    e0 = starts[0]['id'] if len(starts) == 1 else None
    if len(starts) > 1:
        bad('M9', 'journal', [s['id'] for s in starts], 'duplicate EXPERIMENT_STARTED')
    transitions = [j for j in journal if j['event_type'] in (
        'SEGMENT_REVIEW_SNAPSHOT', 'EXPERIMENT_SEGMENT_SEALED', 'EXPERIMENT_SEGMENT_STARTED')]
    if transitions:
        bad('M9', 'journal', [j['id'] for j in transitions],
            'SEGMENT_CHAIN_NOT_VERIFIED: external anchored §3.3.6 verification required')
    boundaries = [(e0, 'S1')] if e0 is not None else []
    segment_records = {}
    for j in transitions:
        if j['event_type'] != 'EXPERIMENT_SEGMENT_STARTED':
            continue
        record = _body(j)
        index = record.get('segment_index')
        if not isinstance(index, int) or isinstance(index, bool) or index < 2:
            bad('M9', 'journal', j['id'], 'invalid segment_index')
            continue
        category = 'S' + str(index)
        if category in segment_records:
            bad('M9', 'journal', j['id'], 'duplicate segment_index')
        segment_records[category] = record
        boundaries.append((j['id'] - 1, category))
        triple = [r for r in journal if j['id'] - 2 <= r['id'] <= j['id']]
        if ([r['event_type'] for r in triple] != ['SEGMENT_REVIEW_SNAPSHOT',
                'EXPERIMENT_SEGMENT_SEALED', 'EXPERIMENT_SEGMENT_STARTED'] or
                any(r['source'] != 'experiment_segment' or r['run_id'] is not None or
                    r['symbol'] is not None or r['severity'] != 'INFO' or
                    r['timestamp'] != j['timestamp'] for r in triple)):
            bad('M9', 'journal', j['id'], 'misplaced transition triple or invalid envelope')
        else:
            seal = _body(triple[1])
            if seal.get('B_n') != j['id'] - 3:
                bad('M9', 'journal', j['id'], 'boundary B_n mismatch')
    boundaries.sort()
    if [category for _, category in boundaries] != ['S' + str(i) for i in range(1, len(boundaries) + 1)]:
        bad('M9', 'journal', None, 'nonconsecutive segment indices')
    if journal:
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='journal'").fetchall()
        if len(sequence) != 1 or sequence[0][0] != journal[-1]['id']:
            bad('M9', 'sqlite_sequence', 'journal', 'seq != MAX(journal.id)')
    categories = {}
    for j in journal:
        candidates = [category for lower, category in boundaries if lower <= j['id']]
        categories[j['id']] = candidates[-1] if candidates else 'PRE_START'
    by_run = defaultdict(list)
    for r in runs:
        by_run[r['run_id']].append(r)
    economic = {}
    for table, key in (('paper_orders', 'order_id'), ('paper_fills', 'fill_id'),
                       ('paper_positions', 'position_id'), ('closed_trades', 'trade_id')):
        economic[table] = [(row[key], _body(row)) for row in _rows(db, 'SELECT * FROM ' + table)]
    event_types = {'ORDER_SUBMITTED', 'ORDER_CANCELLED', 'ORDER_REJECTED', 'ORDER_FILLED',
                   'POSITION_OPENED', 'POSITION_CLOSED', 'STOP_HIT', 'TARGET_HIT'}
    events = [j for j in journal if j['event_type'] in event_types]
    economic_runs = {b.get('run_id') for rows in economic.values() for _, b in rows} | {j['run_id'] for j in events}

    def reference(run_id, table, identity):
        matches = by_run.get(run_id, [])
        if len(matches) != 1:
            bad('E6', table, identity, 'UNMAPPED' if not matches else 'CONFLICT: duplicate run_id')
        return matches[0] if len(matches) == 1 else None

    run_cat = {}
    for r in runs:
        links = [j for j in journal if j['event_type'] == 'RUN_STARTED' and _body(j).get('slot_key') == r['slot_key']]
        cat = categories[links[0]['id']] if len(links) == 1 else 'UNMAPPED'
        run_cat[r['slot_key']] = cat
        if len(links) != 1:
            bad('M2', 'runs', r['slot_key'], 'RUN_STARTED count must be one')
        elif links[0]['run_id'] != r['run_id'] or links[0]['symbol'] != r['symbol']:
            bad('M2', 'runs', r['slot_key'], 'RUN_STARTED identity CONFLICT')
        mapped.append(dict(table='runs', identity=r['slot_key'], category=cat, as_of=r['as_of']))
    metadata = _rows(db, 'SELECT * FROM run_metadata ORDER BY slot_key')
    metadata_keys = {m['slot_key'] for m in metadata}
    s1_id = None
    started = timestamp(state.get('experiment_started_at_utc'))
    if started:
        s1_id = started.strftime('%Y%m%dT%H%M%SZ') + '-S1'
    first = next((m for m in metadata if run_cat.get(m['slot_key']) == 'S1'), None)
    for m in metadata:
        cat = run_cat.get(m['slot_key'], 'UNMAPPED')
        if cat == 'UNMAPPED' or (cat == 'PRE_START' and m['experiment_id'] is not None) or (
                cat == 'S1' and m['experiment_id'] not in (None, s1_id)):
            bad('M3', 'run_metadata', m['slot_key'], 'UNMAPPED or experiment_id CONFLICT')
        if cat == 'S1' and (m['starting_equity'] != 10000 or m['schema_version'] != 3 or
                           (first and (m['git_commit'], m['config_fingerprint']) !=
                            (first['git_commit'], first['config_fingerprint']))):
            bad('M3', 'run_metadata', m['slot_key'], 'segment identity CONFLICT')
        if cat in segment_records:
            record = segment_records[cat]
            if (m['experiment_id'] != record.get('segment_id') or
                    m['git_commit'] != record.get('code_sha') or
                    m['config_fingerprint'] != record.get('config_fingerprint') or
                    m['starting_equity'] != 10000 or m['schema_version'] != 3):
                bad('M3', 'run_metadata', m['slot_key'], 'segment identity CONFLICT')
        mapped.append(dict(table='run_metadata', identity=m['slot_key'], category=cat))
    for r in runs:
        if r['slot_key'] not in metadata_keys:
            if r['run_id'] in economic_runs:
                bad('M4', 'runs', r['slot_key'], 'economic run missing metadata')
            else:
                mapped.append(dict(table='runs', identity=r['slot_key'],
                                   category=run_cat[r['slot_key']], flag='METADATA_MISSING_NONECONOMIC'))
    for table in ('agent_decisions', 'setups', 'risk_decisions', 'macro_awareness'):
        for index, row in enumerate(_rows(db, 'SELECT * FROM ' + table)):
            if table == 'macro_awareness':
                run = reference(row['first_run_id'], table, index)
                cat = run_cat.get(run['slot_key'], 'UNMAPPED') if run else 'UNMAPPED'
            else:
                cat = run_cat.get(row['slot_key'], 'UNMAPPED')
            if cat == 'UNMAPPED':
                bad('M5', table, index, 'UNMAPPED')
            mapped.append(dict(table=table, identity=index, category=cat))
    for row in _rows(db, 'SELECT * FROM notification_events'):
        cat = categories.get(row['journal_id'], 'UNMAPPED')
        if cat == 'UNMAPPED':
            bad('M7', 'notification_events', row['event_id'], 'UNMAPPED')
        mapped.append(dict(table='notification_events', identity=row['event_id'], category=cat))

    def link(kind, identity, body):
        links = [j for j in events if j['event_type'] == kind and j['source'] == identity]
        if len(links) != 1:
            bad('E7', kind, identity, 'creation/terminal event count must be one')
            return 'UNMAPPED'
        j = links[0]
        if j['run_id'] != body.get('run_id') or j['symbol'] != body.get('symbol'):
            bad('E7', kind, identity, 'event identity CONFLICT')
        return categories[j['id']]

    fills = economic['paper_fills']
    trades = economic['closed_trades']
    for table, rows in economic.items():
        for identity, body in rows:
            reference(body.get('run_id'), table, identity)
            opened = None
            if table == 'paper_fills':
                cat = link('ORDER_FILLED', identity, body)
            elif table == 'closed_trades':
                cat = link('POSITION_CLOSED', identity, body)
                touches = [j for j in events if j['source'] == identity and j['event_type'] in ('STOP_HIT', 'TARGET_HIT')]
                if len(touches) > 1 or any(categories[j['id']] != cat for j in touches):
                    bad('E3', table, identity, 'STOP/TP attribution CONFLICT')
            elif table == 'paper_positions':
                cat = link('POSITION_OPENED', identity, body)
                opened = cat
                if body.get('status') == 'CLOSED':
                    closed = [(i, b) for i, b in trades if b.get('position_id') == identity]
                    if len(closed) != 1:
                        bad('E4', table, identity, 'closed trade count must be one')
                    else:
                        cat = link('POSITION_CLOSED', *closed[0])
            else:
                cat = link('ORDER_SUBMITTED', identity, body)
                opened = cat
                if body.get('status') == 'FILLED':
                    matched = [(i, b) for i, b in fills if b.get('order_id') == identity]
                    if len(matched) != 1:
                        bad('E2', table, identity, 'fill count must be one')
                    else:
                        cat = link('ORDER_FILLED', *matched[0])
                elif body.get('status') in ('CANCELLED', 'REJECTED'):
                    cat = link('ORDER_' + body['status'], identity, body)
            if cat == 'PRE_START' and body.get('account_id', 'paper-main') == 'paper-main':
                bad('M8', table, identity, 'PRE_START economics quarantined: CONFLICT')
            if table == 'closed_trades':
                positions = [(i, b) for i, b in economic['paper_positions'] if i == body.get('position_id')]
                if len(positions) != 1:
                    bad('E4', table, identity, 'position link must be unique')
                else:
                    opened = link('POSITION_OPENED', *positions[0])
            carried = False
            if opened and opened.startswith('S') and cat.startswith('S'):
                carried = int(opened[1:]) < int(cat[1:])
                if int(opened[1:]) > int(cat[1:]):
                    bad('E5', table, identity, 'close precedes creation')
            mapped.append(dict(table=table, identity=identity, category=cat, event_segment=cat,
                               origin_run_id=body.get('run_id'), open_segment=opened,
                               carried_over=carried))
    targets = {'ORDER_SUBMITTED': 'paper_orders', 'ORDER_CANCELLED': 'paper_orders',
               'ORDER_REJECTED': 'paper_orders', 'ORDER_FILLED': 'paper_fills',
               'POSITION_OPENED': 'paper_positions', 'POSITION_CLOSED': 'closed_trades',
               'STOP_HIT': 'closed_trades', 'TARGET_HIT': 'closed_trades'}
    for j in events:
        reference(j['run_id'], 'journal', j['id'])
        if not any(i == j['source'] for i, _ in economic[targets[j['event_type']]]):
            bad('E7', 'journal', j['id'], 'orphan economic event')
        if categories[j['id']] == 'PRE_START':
            bad('M8', 'journal', j['id'], 'PRE_START economic event')
    mapped.extend(dict(table='journal', identity=j['id'], category=categories[j['id']]) for j in journal)
    if any(m['category'] == 'UNMAPPED' for m in mapped):
        bad('M10', 'history', None, 'UNMAPPED rows')
    return dict(status='INCONSISTENT' if findings else 'OK', rows=mapped, findings=findings,
                quarantine_counts=dict(Counter(m['table'] for m in mapped if m['category'] == 'PRE_START')),
                rules=['M' + str(i) for i in range(1, 11)],
                external_chain='NOT VERIFIED' if transitions else 'NOT APPLICABLE')


def verify(trading_db, *, evidence_db=None, copy_time=None, now_utc=None):
    now = timestamp(now_utc) if now_utc is not None else datetime.now(timezone.utc)
    if now is None:
        raise ValueError('now_utc must be an explicit UTC timestamp')
    copied = timestamp(copy_time)
    if copy_time is not None and copied is None:
        raise ValueError('copy_time must be an explicit UTC timestamp')
    paths = {'trading_db': Path(trading_db).resolve()}
    if evidence_db is not None:
        paths['evidence_db'] = Path(evidence_db).resolve()
    hashes = {key: {'before': sha256(path)} for key, path in paths.items()}
    checks = {}
    report = dict(verifier_version='V2_STATUS/1', copy_time=copy_time, now_utc=now.isoformat(),
                  expected_freeze=dict(baseline_sha=BASELINE, starting_equity=10000,
                                       enabled_symbols=['XAUUSD', 'EURUSD'], schema_version=3),
                  checks=checks, hashes=hashes, errors=[])
    def query(key, operation):
        try:
            checks[key] = operation()
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            checks[key] = {'status': 'ERROR', 'error': str(exc)}
            report['errors'].append(dict(check=key, error=str(exc)))
    try:
        db = sqlite3.connect(paths['trading_db'].as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            query('Q1', lambda: dict(integrity_check=[r[0] for r in db.execute('PRAGMA integrity_check')],
                                     schema_versions=[r[0] for r in db.execute('SELECT version FROM schema_info')]))
            query('Q2', lambda: {k: next((r['value'] for r in _rows(db,
                'SELECT value FROM system_state WHERE key=?', (k,))), None) for k in STATE_KEYS})
            def start_events():
                rows = _rows(db, "SELECT id,payload FROM journal WHERE event_type='EXPERIMENT_STARTED' ORDER BY id")
                return dict(count=len(rows), ids=[r['id'] for r in rows], rows=rows)
            query('Q3', start_events)
            query('Q4', lambda: _rows(db, 'SELECT git_commit,config_fingerprint,experiment_id,starting_equity,schema_version,count(*) AS count FROM run_metadata GROUP BY git_commit,config_fingerprint,experiment_id,starting_equity,schema_version ORDER BY git_commit,config_fingerprint,experiment_id'))
            query('Q5', lambda: dict(count_by_status=_rows(db, 'SELECT status,count(*) AS count FROM runs GROUP BY status ORDER BY status'),
                                     running=_rows(db, "SELECT * FROM runs WHERE status='RUNNING' ORDER BY slot_key")))
            query('Q6', lambda: dict(account=_rows(db, "SELECT * FROM paper_accounts WHERE account_id='paper-main'"),
                open_positions=[dict(position_id=r['position_id'], payload=_body(r), last_processed_at=_body(r).get('last_processed_at')) for r in _rows(db, 'SELECT * FROM paper_positions ORDER BY position_id') if _body(r).get('status') == 'OPEN'],
                pending_orders=[dict(order_id=r['order_id'], payload=_body(r)) for r in _rows(db, 'SELECT * FROM paper_orders ORDER BY order_id') if _body(r).get('status') == 'PENDING']))
            query('Q7', lambda: history(db, checks['Q3']['rows'], checks['Q2']))
            if evidence_db is not None:
                def evidence():
                    from runtime.revision_review import demo_gate
                    ev = sqlite3.connect(paths['evidence_db'].as_uri() + '?mode=ro', uri=True)
                    ev.row_factory = sqlite3.Row
                    try:
                        rows = _rows(ev, "SELECT * FROM evidence_anomalies WHERE kind='REVISION' ORDER BY anomaly_id")
                    finally:
                        ev.close()
                    return dict(revisions=rows, gate=demo_gate(trading_db, evidence_db,
                                                             enabled_symbols=('XAUUSD', 'EURUSD')))
                query('Q8', evidence)
            else:
                checks['Q8'] = dict(status='NOT VERIFIED', reason='Evidence DB not supplied')
        finally:
            db.close()
    finally:
        for key, path in paths.items():
            hashes[key]['after'] = sha256(path)
            hashes[key]['unchanged'] = hashes[key]['before'] == hashes[key]['after']
    state, starts = checks['Q2'], checks['Q3'].get('rows')
    fs = 'START_INCONSISTENT'
    start = timestamp(state.get('experiment_started_at_utc'))
    if isinstance(starts, list):
        if state.get('experiment_started') != '1' and not starts:
            fs = 'NOT_STARTED'
        elif state.get('experiment_started') == '1' and len(starts) == 1 and start and copied and start <= copied:
            baseline, freeze = state.get('experiment_baseline_sha'), state.get('experiment_freeze_sha')
            try:
                payload = _body(starts[0])
                if (isinstance(baseline, str) and SHA_PATTERN.fullmatch(baseline) and
                        isinstance(freeze, str) and SHA_PATTERN.fullmatch(freeze) and
                        BASELINE.lower().startswith(baseline.lower()) and
                        payload.get('baseline_sha') == baseline and payload.get('freeze_sha') == freeze):
                    fs = 'STARTED'
            except (ValueError, TypeError):
                pass
    recent = copied is not None and any(at is not None and timedelta(0) <= copied - at <= timedelta(minutes=30)
                                       for at in (timestamp(state.get('heartbeat')), timestamp(state.get('last_run'))))
    activity = [timestamp(state.get(key)) for key in ('heartbeat', 'last_run') if state.get(key) is not None]
    stale = (copied is not None and bool(activity) and
             all(at is not None and copied - at > timedelta(minutes=30) for at in activity))
    ex = ('RUNNING' if state.get('runner') == 'RUNNING' and recent else
          'STOPPED' if state.get('runner') == 'STOPPED' and stale and 'running' in checks['Q5'] else 'UNKNOWN')
    q1 = checks['Q1']
    integrity = 'INCONSISTENT' if (report['errors'] or q1.get('integrity_check') != ['ok'] or
        q1.get('schema_versions') != [3] or checks['Q7'].get('status') != 'OK' or
        not all(h['unchanged'] for h in hashes.values())) else 'OK'
    report.update(axes=dict(FS=fs, EX=ex, EX_attestation=EX_NOTE,
                           PD=('PERIOD_ELAPSED' if now >= start + timedelta(days=14) else 'NOT_ELAPSED')
                           if fs == 'STARTED' else 'NOT APPLICABLE', RS='UNCERTIFIED'),
                  integrity=integrity, decision='STOP' if integrity == 'INCONSISTENT' or fs == 'START_INCONSISTENT'
                  else 'NO_ACTIVATION_AUTHORIZED')
    report['recover_runs'] = checks['Q5'].get('running', [])
    if copied is None:
        report['limitations'] = ['copy_time not supplied: formal STARTED and execution freshness cannot be established']
    if fs == 'STARTED':
        end = start + timedelta(days=14)
        report['result_window'] = dict(start=start.isoformat(), end_exclusive=end.isoformat())
        report['runs_after_period'] = [r['identity'] for r in checks['Q7'].get('rows', [])
                                       if r['table'] == 'runs' and timestamp(r.get('as_of')) and
                                       timestamp(r['as_of']) >= end]
        for row in checks['Q7'].get('rows', []):
            if row['table'] == 'runs' and row['identity'] in report['runs_after_period']:
                row['period_flag'] = 'RUNS_AFTER_PERIOD'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trading-db', required=True)
    parser.add_argument('--evidence-db')
    parser.add_argument('--copy-time')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    if args.out:
        out = Path(args.out).resolve()
        for path in (args.trading_db, args.evidence_db):
            if path and (out == Path(path).resolve() or (out.exists() and out.samefile(path))):
                parser.error('--out must not overwrite an input database')
    report = verify(args.trading_db, evidence_db=args.evidence_db, copy_time=args.copy_time)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(rendered + '\n', encoding='utf-8')
    else:
        print(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
