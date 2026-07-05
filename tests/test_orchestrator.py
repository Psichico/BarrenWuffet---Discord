import datetime
from datetime import timezone, timedelta
from src.orchestrator import Orchestrator

def test_get_sleep_duration_weekday(monkeypatch):
    # Wednesday 10 AM ET (July 1, 2026)
    target_dt = datetime.datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    
    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return target_dt
            
    monkeypatch.setattr(datetime, "datetime", FakeDatetime)
    
    orch = Orchestrator(
        sources=[], filter_=None, sinks=[], store=None,
        poll_interval_seconds=300, batch_size=20
    )
    assert orch._get_sleep_duration() == 300

def test_get_sleep_duration_saturday(monkeypatch):
    # Saturday 2 PM ET (July 4, 2026)
    target_dt = datetime.datetime(2026, 7, 4, 14, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    
    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return target_dt
            
    monkeypatch.setattr(datetime, "datetime", FakeDatetime)
    
    orch = Orchestrator(
        sources=[], filter_=None, sinks=[], store=None,
        poll_interval_seconds=300, batch_size=20,
        poll_interval_weekend_seconds=7200
    )
    assert orch._get_sleep_duration() == 7200

def test_get_sleep_duration_friday_night(monkeypatch):
    # Friday 9 PM ET (July 3, 2026)
    target_dt = datetime.datetime(2026, 7, 3, 21, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    
    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return target_dt
            
    monkeypatch.setattr(datetime, "datetime", FakeDatetime)
    
    orch = Orchestrator(
        sources=[], filter_=None, sinks=[], store=None,
        poll_interval_seconds=300, batch_size=20
    )
    assert orch._get_sleep_duration() == 10800

def test_get_sleep_duration_sunday_night(monkeypatch):
    # Sunday 7 PM ET (July 5, 2026)
    target_dt = datetime.datetime(2026, 7, 5, 19, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    
    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return target_dt
            
    monkeypatch.setattr(datetime, "datetime", FakeDatetime)
    
    orch = Orchestrator(
        sources=[], filter_=None, sinks=[], store=None,
        poll_interval_seconds=300, batch_size=20
    )
    assert orch._get_sleep_duration() == 300
