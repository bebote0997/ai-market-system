"""Optional PAPER setup emails. Runs in the alert worker, after persisted decisions."""
from dataclasses import dataclass, replace
import logging

from runtime.alert_catalog import AlertEvent, AlertKind, alert_event_id, display
from runtime.setup_candle_reader import cache_path, read_candles
from runtime.setup_chart_png import setup_chart_png

LOG = logging.getLogger(__name__)
FLAG = 'AI_FLOOR_SETUP_CHART_EMAIL'
CHANNEL = 'setup_chart_email'
KIND = AlertKind('SETUP_VALIDATED', 'VALIDATED SETUP', 'MEDIUM', True)


def enabled(environ):
    return environ.get(FLAG, '0') == '1'


@dataclass(frozen=True)
class SetupChartEvent(AlertEvent):
    bars: tuple = ()
    plan: dict | None = None

    def __post_init__(self):
        if not self.event_id or self.event_type != KIND.event_type:
            raise ValueError('invalid setup chart event')

    @property
    def kind(self):
        return KIND


def publish_setup_charts(dispatcher, environ, run_id=''):
    """Reconcile validated persisted rows, even if V1 notification suppression hid a repeat.

    Only exact setup IDs deduplicate; no geometric/timestamp identity is synthesized.
    Missing IDs are an observable gap, not a reason to send a duplicate-prone email.
    """
    if not enabled(environ):
        return
    try:
        from runtime.demo_runner import REAL_EXECUTION_ENABLED
        if REAL_EXECUTION_ENABLED:
            return
        from ui.pages.setups import iter_validated_setups
        from ui.state import dashboard_database
        with dashboard_database(environ.get('AI_FLOOR_DB_PATH', 'data/runtime/trading_floor.db')) as db:
            for row in iter_validated_setups(db, run_id=run_id):
                _publish_row(dispatcher, environ, row)
    except Exception as exc:  # noqa: BLE001 - post-decision alerting never affects a cycle
        LOG.warning('component=alerts event=SETUP_CHART_FAILED error_type=%s', type(exc).__name__)


def _publish_row(dispatcher, environ, row):
    # A running cycle has not completed its decision/order boundary.
    if row['status'] not in ('COMPLETED', 'FAILED'):
        return
    setup_id = row['setup_id']
    if not setup_id:
        LOG.warning('component=alerts event=SETUP_CHART_NO_SETUP_ID')
        return
    plan = row['plan']
    bars = read_candles(cache_path(environ), row['symbol'], row['as_of']) or row['bars'][-48:]
    trade = {'setup_id': setup_id, 'side': plan.get('side'), 'entry': plan.get('entry'),
             'stop_loss': plan.get('stop'), 'take_profit': plan.get('target'),
             'quantity': plan.get('quantity'), 'rr': plan.get('risk_reward'),
             'chart_ref': ('Closed 5m prices · candle_cache · no OHLC' if bars and bars[0].get('source') == 'candle_cache'
                           else 'Persisted 15m bars' if bars else 'chart unavailable: bars not persisted')}
    event = SetupChartEvent(alert_event_id(KIND.event_type, setup_id), KIND.event_type,
        row.get('completed_at') or row['as_of'], row['run_id'], row['symbol'], (),
        'Validated PAPER setup. Informational; no order authorization.',
        tuple((k, display(v)) for k, v in trade.items()), (), tuple(bars), dict(plan))
    dispatcher.dispatch(event)

class SetupChartEmailChannel:
    name = CHANNEL

    def __init__(self, email):
        self.email = email

    @property
    def configured(self):
        return self.email.configured

    def send(self, event):
        attachments = ()
        if event.bars:
            try:
                png = setup_chart_png(event.bars, event.plan or {}, event.symbol)
                attachments = (('setup.png', png, 'image', 'png'),)
            except Exception as exc:  # noqa: BLE001 - render failure falls back to text
                LOG.warning('component=alerts event=SETUP_CHART_RENDER_FAILED error_type=%s', type(exc).__name__)
                trade = dict(event.trade)
                trade['chart_ref'] = 'chart unavailable: PNG rendering failed'
                event = replace(event, trade=tuple(trade.items()))
        self.email.send(event, attachments=attachments)
