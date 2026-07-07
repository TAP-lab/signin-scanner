"""Sync one-off workshop events from an ICS calendar feed into Salesforce.

Salesforce has no native ICS import, so recurring weekly workshops live in
Salesforce (matched by weekday) while one-off/special events are authored in
a shared calendar instead. This module periodically reads that calendar and
creates any of today's events in Salesforce that aren't already there, so
both the RFID and terminal sign-in flows can tag sign-ins with them via the
normal workshop lookup.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

import icalendar
import recurring_ical_events
import requests

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

ICS_FETCH_TIMEOUT_SECONDS = 15.0


def _normalize_title(title: str) -> str:
    """Normalize an event/workshop title for case/whitespace-tolerant comparison."""
    return " ".join(title.strip().lower().split())


def _to_salesforce_weekday(now_local: datetime.datetime) -> int:
    """Convert Python's weekday (Mon=0..Sun=6) to Salesforce's (Sun=1..Sat=7).

    Mirrors the mapping in signin.sf_get_current_workshop - keep in sync.
    """
    weekday_sf = (now_local.weekday() + 2) % 7
    return 7 if weekday_sf == 0 else weekday_sf


class WorkshopCalendarSync:
    """Periodically syncs one-off ICS calendar events into Salesforce as workshops."""

    def __init__(
        self,
        ics_url: str,
        get_sf: Callable[[], Optional[Any]],
        sobject_name: str,
        name_field: str,
        start_field: str,
        end_field: str,
        weekday_field: str,
        date_field: str,
        oneoff_weekday: int = 9,
        sync_hour: int = 4,
        fetch_timeout: float = ICS_FETCH_TIMEOUT_SECONDS,
    ):
        self.ics_url = ics_url
        self.get_sf = get_sf
        self.sobject_name = sobject_name
        self.name_field = name_field
        self.start_field = start_field
        self.end_field = end_field
        self.weekday_field = weekday_field
        self.date_field = date_field
        self.oneoff_weekday = oneoff_weekday
        self.sync_hour = sync_hour
        self.fetch_timeout = fetch_timeout

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._wake_event = threading.Event()

    def start(self) -> None:
        """Start the background sync thread.

        Runs an immediate sync (covers service start/restart/reload), then
        re-syncs daily at `sync_hour` local time.
        """
        if self._running:
            LOG.warning("Workshop calendar sync already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        LOG.info(
            "Workshop calendar sync started (daily at %02d:00 local time)",
            self.sync_hour,
        )

    def stop(self) -> None:
        """Stop the background sync thread."""
        if not self._running:
            return

        self._running = False
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        LOG.info("Workshop calendar sync stopped")

    def _loop(self) -> None:
        try:
            self.sync_once()
        except Exception:
            LOG.exception("Initial workshop calendar sync failed")

        while self._running:
            sleep_seconds = self._seconds_until_next_run()
            LOG.debug("Next workshop calendar sync in %.0fs", sleep_seconds)
            self._wake_event.wait(sleep_seconds)
            self._wake_event.clear()
            if not self._running:
                break
            try:
                self.sync_once()
            except Exception:
                LOG.exception("Scheduled workshop calendar sync failed")

    def _seconds_until_next_run(
        self, now: Optional[datetime.datetime] = None
    ) -> float:
        now_local = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone()
        next_run = now_local.replace(
            hour=self.sync_hour, minute=0, second=0, microsecond=0
        )
        if next_run <= now_local:
            next_run += datetime.timedelta(days=1)
        return (next_run - now_local).total_seconds()

    def sync_once(self, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
        """Run a single sync pass for "today" and return summary stats."""
        if not self.ics_url:
            LOG.debug("No ICS URL configured; skipping calendar sync")
            return {"skipped": "no_ics_url"}

        now_local = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone()
        today = now_local.date()

        try:
            ics_events = self._fetch_ics_events_for_date(today)
        except Exception as exc:
            LOG.exception("Failed to fetch/parse ICS feed: %s", exc)
            return {"error": str(exc)}

        if not ics_events:
            LOG.info("No ICS events found for %s", today.isoformat())
            return {"ics_events": 0, "created": 0}

        sf = self.get_sf()
        if sf is None:
            LOG.warning("Cannot sync calendar: Salesforce not connected")
            return {"error": "salesforce_not_connected"}

        existing_titles = self._get_existing_workshop_titles_for_today(sf, now_local)

        created = 0
        skipped_all_day = 0
        for event in ics_events:
            if event["all_day"]:
                skipped_all_day += 1
                LOG.info(
                    "Skipping all-day ICS event %r (no bookable time range)",
                    event["name"],
                )
                continue

            norm_name = _normalize_title(event["name"])
            if norm_name in existing_titles:
                LOG.debug(
                    "Workshop %r already exists in Salesforce for today; skipping",
                    event["name"],
                )
                continue

            if self._create_workshop(sf, event, today):
                created += 1
                # Prevent duplicate creation if the ICS feed lists the same
                # title twice for today.
                existing_titles.add(norm_name)

        LOG.info(
            "Calendar sync complete: %d ICS event(s), %d created, %d all-day skipped",
            len(ics_events),
            created,
            skipped_all_day,
        )
        return {
            "ics_events": len(ics_events),
            "created": created,
            "skipped_all_day": skipped_all_day,
        }

    def _fetch_ics_events_for_date(self, date: datetime.date) -> List[Dict[str, Any]]:
        """Fetch the ICS feed and return events overlapping the given local date."""
        response = requests.get(self.ics_url, timeout=self.fetch_timeout)
        response.raise_for_status()
        calendar = icalendar.Calendar.from_ical(response.content)

        day_start = datetime.datetime.combine(date, datetime.time.min).astimezone()
        day_end = datetime.datetime.combine(date, datetime.time.max).astimezone()

        occurrences = recurring_ical_events.of(calendar).between(day_start, day_end)

        events: List[Dict[str, Any]] = []
        for component in occurrences:
            try:
                event = self._parse_event(component)
            except Exception as exc:
                LOG.warning("Skipping unparseable ICS event: %s", exc)
                continue
            if event is not None:
                events.append(event)
        return events

    def _parse_event(self, component: Any) -> Optional[Dict[str, Any]]:
        name = str(component.get("SUMMARY", "")).strip()
        if not name:
            return None

        dtstart_prop = component.get("DTSTART")
        if dtstart_prop is None:
            return None
        dtstart = dtstart_prop.dt

        if not isinstance(dtstart, datetime.datetime):
            # A bare `date` (no time component) means an all-day event.
            return {"name": name, "all_day": True, "start_time": None, "end_time": None}

        start_local = self._to_local(dtstart)

        dtend_prop = component.get("DTEND")
        end_local = self._to_local(dtend_prop.dt) if dtend_prop is not None else start_local

        return {
            "name": name,
            "all_day": False,
            "start_time": start_local.time(),
            "end_time": end_local.time(),
        }

    @staticmethod
    def _to_local(value: datetime.datetime) -> datetime.datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone()

    def _get_existing_workshop_titles_for_today(
        self, sf: Any, now_local: datetime.datetime
    ) -> set:
        """Titles of workshops that already cover today (recurring or one-off)."""
        weekday_sf = _to_salesforce_weekday(now_local)
        today_str = now_local.date().isoformat()
        query = (
            f"SELECT {self.name_field} FROM {self.sobject_name} "
            f"WHERE {self.weekday_field} = {weekday_sf} "
            f"OR {self.date_field} = {today_str}"
        )
        try:
            res = sf.query(query)
        except Exception as exc:
            LOG.exception("Failed to query existing workshops: %s", exc)
            return set()

        records = res.get("records", []) if isinstance(res, dict) else []
        return {
            _normalize_title(record[self.name_field])
            for record in records
            if isinstance(record, dict) and record.get(self.name_field)
        }

    def _create_workshop(
        self, sf: Any, event: Dict[str, Any], today: datetime.date
    ) -> bool:
        sobject = getattr(sf, self.sobject_name, None)
        if sobject is None:
            LOG.error("Sobject %s not available on Salesforce client", self.sobject_name)
            return False

        payload = {
            self.name_field: event["name"],
            self.start_field: event["start_time"].strftime("%H:%M:%S"),
            self.end_field: event["end_time"].strftime("%H:%M:%S"),
            self.weekday_field: self.oneoff_weekday,
            self.date_field: today.isoformat(),
        }
        try:
            sobject.create(payload)
        except Exception as exc:
            LOG.exception("Failed to create workshop %r from ICS: %s", event["name"], exc)
            return False

        LOG.info(
            "Created one-off workshop %r for %s (%s-%s)",
            event["name"],
            today.isoformat(),
            event["start_time"],
            event["end_time"],
        )
        return True
