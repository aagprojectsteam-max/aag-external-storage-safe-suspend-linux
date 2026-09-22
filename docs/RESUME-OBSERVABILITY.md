# Resume observability

Suspend and Hibernate keep Ubuntu/systemd as the only power-state executors.
This feature observes restoration after a confirmed resume; it never dispatches,
blocks or retries a power transition.

## User notifications

After a confirmed resume and user-session thaw, AAG sends a desktop notification:

- Suspend start: `AAG — שחזור המערכת ממצב שינה החל`
- Hibernate start: `AAG — שחזור המערכת ממצב תרדמה החל`
- Success: `AAG — השחזור ממצב שינה/תרדמה הושלם בהצלחה`
- Failure: `AAG — בעיה בשחזור ממצב שינה/תרדמה`

Notifications are best-effort. A missing desktop bus or notification failure must
not alter the power transaction result.

## Dedicated logs

Structured per-transaction logs are stored under:

```text
/var/log/aag-power-resume/
```

The directory is root-owned, group `adm`, mode `0750`. JSONL files are mode
`0640`, so local administrators can inspect them without making them writable by
the desktop user. At most 128 transaction logs are retained.

A filename is scoped by power type and transaction episode:

```text
suspend-<episode>.jsonl
hibernate-<episode>.jsonl
```

Events include UTC timestamp, realtime/monotonic clocks, transaction type and
episode. The primary event vocabulary is:

```text
RESUME_STARTED
RESUME_STAGE
RESUME_COMPLETE
RESUME_FAILED
```

Stage events record START/PASS transitions for storage, USB Clone, WWAN,
consumers and device verification as applicable. Terminal events include total
restore duration. Failure events include the failing phase and reason.

The system journal remains the authoritative low-level record; these JSONL logs
are a concise restoration timeline for diagnosis.
