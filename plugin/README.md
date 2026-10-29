# Dank Todo with lists, dates and notes

A personal fork of Dank Todo v1.2.0. See UPSTREAM.md and LICENSE for provenance.

Switch lists using the horizontally scrollable tabs at the top of the popup.
The + button creates a separate list. New tasks go into the selected list;
Active / All / Done and Clear done apply only to that list. Save or cancel an
open task editor before switching lists, so an unfinished edit is not lost.

The companion sync discovers all Google Tasks lists and synchronizes every list,
including tabs that are not open. Creating a list here also creates it in Google
Tasks. Rename lists in Google Tasks; the tab names update automatically. Duplicate
names remain separate lists. Tasks keep their list membership; moving tasks
between lists and deleting lists from DMS are not provided in this release.
If a Google list disappears, its local tasks are retained with an unavailable
message, and synchronization of that list pauses.

Click a task’s pencil to edit its title, date and multiline notes. Select a date
from Calendar or enter YYYY-MM-DD; Clear removes it. Save changes commits the
whole edit; Escape cancels. Dates are calendar dates without times or reminders.
Task rows show their date and whether notes are present. Notes support up to
8192 characters. Existing task files load with empty date and notes fields.

All display widgets share a single model per storage path. Google synchronization
uses the schema-versioned snapshot/apply IPC to preserve lists, dates and notes across
reloads. Only the desktop user’s private state stores IDs and conflict metadata.
Storage and sync state migrate to version 4 with private pre-migration backups
made by the sync service. Deploy the plugin and sync client together; an older
client must not be used with the new storage.

The public integration repository runs Python merge/API regressions and JavaScript
checks for shared models and date validation. Release checks also exercise actual
DMS editor controls, all monitors, restart persistence and a Google round trip.
