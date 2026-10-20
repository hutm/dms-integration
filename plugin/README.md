# Dank Todo with dates and notes

A personal fork of Dank Todo v1.2.0. See UPSTREAM.md and LICENSE for provenance.

Click a task’s pencil to edit its title, date and multiline notes. Select a date
from Calendar or enter YYYY-MM-DD; Clear removes it. Save changes commits the
whole edit; Escape cancels. Dates are calendar dates without times or reminders.
Task rows show their date and whether notes are present. Notes support up to
8192 characters. Existing task files load with empty date and notes fields.

All display widgets share a single model per storage path. Google synchronization
uses the schema-versioned snapshot/apply IPC to preserve dates and notes across
reloads. Only the desktop user’s private state stores IDs and conflict metadata.

The public integration repository runs Python merge/API regressions and JavaScript
checks for shared models and date validation. Release checks also exercise actual
DMS editor controls, all monitors, restart persistence and a Google round trip.
