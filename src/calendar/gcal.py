"""Google Calendar integration for syncing action items as calendar events."""

import logging
from datetime import datetime, timedelta, timezone

from src.config import settings

logger = logging.getLogger(__name__)


class GoogleCalendarClient:
    def __init__(self):
        self._service = None

    @property
    def enabled(self) -> bool:
        return settings.google_calendar_enabled

    def _get_service(self):
        if self._service is not None:
            return self._service

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
            creds = None

            import os
            token_path = "token.json"

            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        settings.google_credentials_file, SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                with open(token_path, "w") as token:
                    token.write(creds.to_json())

            self._service = build("calendar", "v3", credentials=creds)
            return self._service

        except Exception as e:
            logger.error("Failed to initialize Google Calendar: %s", e)
            return None

    def create_event(
        self,
        title: str,
        description: str = "",
        start_time: datetime | None = None,
        duration_minutes: int = 30,
    ) -> str | None:
        """Create a calendar event. Returns event ID or None on failure."""
        if not self.enabled:
            return None

        service = self._get_service()
        if not service:
            return None

        if start_time is None:
            # Default to tomorrow at 9 AM
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            start_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)

        end_time = start_time + timedelta(minutes=duration_minutes)

        event = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_time.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "UTC"},
        }

        try:
            result = service.events().insert(calendarId="primary", body=event).execute()
            logger.info("Created calendar event: %s", result.get("id"))
            return result.get("id")
        except Exception as e:
            logger.error("Failed to create calendar event: %s", e)
            return None

    def create_action_item_event(self, action: str, note_id: int) -> str | None:
        """Create a calendar event from a note's action item."""
        return self.create_event(
            title=f"TODO: {action}",
            description=f"From note #{note_id} in Notetaker",
        )
