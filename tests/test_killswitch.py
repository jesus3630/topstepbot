from datetime import datetime
from zoneinfo import ZoneInfo

from topstepbot.broker.paper import PaperBroker
from topstepbot.config import load_config
from topstepbot.journal import Journal
from topstepbot.killswitch import KillSwitch
from topstepbot.models import Bar, BracketLeg, Side

TZ = ZoneInfo("America/Chicago")


def _broker():
    return PaperBroker(load_config("config/settings.yaml"))


def _open_long(broker: PaperBroker) -> None:
    broker.place_brackets(
        [
            BracketLeg(
                tag="test-long",
                side=Side.LONG,
                qty=2,
                entry_type="market",
                entry_price=5000,
                stop_price=4990,
                target_price=5030,
                setup="A",
            )
        ]
    )
    broker.on_bar(Bar(datetime(2026, 1, 6, 9, 0, tzinfo=TZ), 5000, 5002, 4998, 5001, 10))
    assert broker.net_qty() == 2
    assert broker.has_protective_stop(next(iter(broker._groups)))


def test_kill_file_flattens_and_cancels(tmp_path):
    broker = _broker()
    _open_long(broker)
    # A second working entry should be cancelled too.
    broker.place_brackets(
        [
            BracketLeg(
                tag="working",
                side=Side.LONG,
                qty=1,
                entry_type="stop",
                entry_price=5100,
                stop_price=5090,
                target_price=5140,
                setup="B",
            )
        ]
    )
    assert broker.working_entry_count() == 1
    kill = KillSwitch(tmp_path / "KILL")
    (tmp_path / "KILL").write_text("stop", encoding="utf-8")
    journal = Journal(tmp_path / "logs")
    assert kill.execute(broker, price=5001, when=datetime(2026, 1, 6, 9, 5, tzinfo=TZ), journal=journal)
    assert broker.net_qty() == 0
    assert broker.working_entry_count() == 0
    assert kill.tripped


def test_trip_command_flattens_without_a_preexisting_file(tmp_path):
    broker = _broker()
    _open_long(broker)
    kill = KillSwitch(tmp_path / "KILL")
    kill.trip("typed kill")
    journal = Journal(tmp_path / "logs")
    assert kill.execute(broker, price=5001, when=datetime(2026, 1, 6, 9, 5, tzinfo=TZ), journal=journal)
    assert broker.net_qty() == 0
    assert not any(group.entry_working or group.qty_open for group in broker._groups.values())


def test_execute_does_nothing_until_tripped(tmp_path):
    broker = _broker()
    _open_long(broker)
    kill = KillSwitch(tmp_path / "KILL")
    journal = Journal(tmp_path / "logs")
    assert not kill.execute(broker, 5001, datetime(2026, 1, 6, 9, 5, tzinfo=TZ), journal)
    assert broker.net_qty() == 2
