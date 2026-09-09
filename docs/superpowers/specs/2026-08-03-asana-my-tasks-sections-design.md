# Asana My Tasks sections — design

Date: 2026-08-03
Scope: `whizvoice/asana_tools.py`, `whizvoice/app.py`, `whizvoice/tests/test_asana_tools.py`

## Goal

Let the user see which My Tasks section a task is in, and move a task between
sections by name ("move that to Today").

## Background: two kinds of section

Asana has two unrelated things both called "sections", and only one of them is
in scope.

| | Project sections | My Tasks sections |
|---|---|---|
| Example | "In Review", "Backlog" | Recently assigned, Today, Upcoming, Later |
| Read via | `memberships[].section.name` | `assignee_section.name` |
| Write via | `SectionsApi.add_task_for_section` | `assignee_section` on `update_task` |

`get_new_asana_task_id` (`asana_tools.py:259-274`) creates tasks with
`workspace` + `assignee` and no project. Those tasks therefore have empty
`memberships`, live only in My Tasks, and Asana defaults them to Recently
assigned.

**Only My Tasks sections are in scope.** Project sections are out of scope.

Consequence: `assignee_section` is a plain writable field on the normal update
call, so a move needs no extra API request and cannot alter project membership.

## Constraints

- `asana==5.1.0` is a dict pass-through client with no typed models. `body` and
  `opt_fields` go straight to REST, so `assignee_section` works without SDK
  support. (Platform constraint — not a choice.)
- `assignee_section` only applies to tasks assigned to the calling user. A task
  assigned to someone else via `assignee_email` cannot be moved. (Platform
  constraint.)
- Task creation must remain byte-for-byte unchanged so Asana's own default
  (Recently assigned) continues to apply. (Product requirement.)

## Design

### 1. Shared opt_fields constant

`asana_tools.py` repeats the same `opt_fields` string in 8 places: `:153`,
`:219`, `:272`, `:274`, `:323`, `:331`, `:336`. Extract to module constants.

Three are needed rather than one: the two list endpoints omit `gid` while the
single-task endpoints include it, and `get_parent_tasks` additionally needs
`num_subtasks`.

```python
_TASK_FIELDS = 'name,due_on,completed,projects.name,assignee_section.name'
_TASK_OPT_FIELDS = 'gid,' + _TASK_FIELDS
_PARENT_TASK_FIELDS = _TASK_FIELDS + ',num_subtasks'
```

Adding `assignee_section.name` here means every tool that returns a task reports
its section, with no new fetch.

### 2. My Tasks list lookup, cached

The My Tasks list gid is stable per (user, workspace). Cache it in a module dict
next to `_user_gid_cache` (`asana_tools.py:12`):

```python
_user_task_list_cache = {}  # (user_id, workspace_gid) -> user_task_list_gid
```

```python
def get_user_task_list_gid(user_id, api_client, workspace_gid):
    key = (user_id, workspace_gid)
    if key not in _user_task_list_cache:
        user_gid = get_asana_user_gid(user_id, api_client)
        utl_api = asana.UserTaskListsApi(api_client)
        utl = utl_api.get_user_task_list_for_user(user_gid, workspace_gid, opts={})
        _user_task_list_cache[key] = utl['gid']
    return _user_task_list_cache[key]
```

No TTL, matching `_user_gid_cache`, which is also unbounded and never expires.

### 3. `get_asana_sections(user_id)`

New tool, no parameters. Resolves the workspace preference (same guard clause
and error string as `get_asana_tasks`), looks up the My Tasks gid, then:

```python
sections_api = asana.SectionsApi(api_client)
sections = list(sections_api.get_sections_for_project(
    utl_gid, opts={'opt_fields': 'name'}))
```

Returns `[{gid, name}, ...]`. Wrapped in the same
`except ValueError / except AsanaError` blocks every other function in the file
uses.

### 4. Name resolution

```python
def _resolve_section_gid(user_id, api_client, workspace_gid, section_name):
    """Resolve a section name to a gid. Returns (gid, None) or (None, error_dict)."""
```

1. Fetch sections via the same path as `get_asana_sections`.
2. Case-insensitive match on the trimmed name. Exact match wins.
3. No exact match → unique case-insensitive substring match, so "today" or
   "recently" resolve from a voice transcript. More than one substring hit is
   treated as no match.
4. No match, or ambiguous → return an error naming the sections that do exist,
   so Claude can recover in one round-trip.

Errors are returned as `{"error": "<message>"}`, matching the shape the
`except AsanaError` handlers already use throughout the file. (Note the file is
inconsistent here — some guard clauses return a bare string instead, e.g.
`asana_tools.py:246`. New code uses the dict form.)

Message: `Section '<name>' not found in your My Tasks. Available sections:
Recently assigned, Today, Upcoming, Later.`

### 5. `update_asana_task` gains `section`

New optional keyword param `section` (a name, not a gid), appended last in the
signature so existing positional callers are unaffected.

When provided, resolve it and set `update_data['assignee_section'] = gid` before
the existing `update_task` call at `:320`. It is a normal field on that same
request — unlike `parent_gid`, which needs its own `set_parent_for_task` call at
`:327-332`.

If resolution fails, return the error dict immediately without performing any
other update, so a bad section name does not half-apply a rename or due-date
change.

Asana returns a generic 400 when the task is assigned to someone else, with no
machine-readable marker distinguishing it from any other 400. So the handler
treats "status 400 **and** a section was requested" as that case and returns a
message naming it as the likely cause, keeping the raw error in `detail`. This
is a heuristic, not a precise classification.

### 6. Creation untouched

`get_new_asana_task_id` gets no `section` param and nothing added to
`task_data`. There is no parameter that could be accidentally populated, so new
tasks keep landing in Recently assigned via Asana's own default.

## Wiring (`app.py`)

- Add `get_asana_sections` to the named-import list at `:20`.
- New `TOOL_REGISTRY` entry: `requires_auth: True`,
  `args_mapping: lambda args, user_id: (user_id,)`, `validation: None`.
- Append `args.get('section')` to the end of the `update_asana_task`
  `args_mapping` tuple (`:1325-1333`). Existing validation is unchanged.

## Tool schemas (`asana_tools.py`, `asana_tools` list)

- `get_asana_sections`: empty `input_schema`, description explaining it lists the
  My Tasks sections and that section names must come from this tool.
- `update_asana_task`: new `section` property, described as the My Tasks section
  name to move the task into, with the four defaults named as examples and a note
  that omitting it leaves the section unchanged.

## Tests

Extend `tests/test_asana_tools.py`, following its existing `MagicMock` pattern
and patching `asana.SectionsApi` / `asana.UserTaskListsApi`:

- `get_asana_sections` returns the section list; errors when no workspace pref.
- Exact match, differing case.
- Unique substring match ("today" → "Today").
- Ambiguous substring → error, and no `update_task` call is made.
- Unknown name → error listing real sections, and no `update_task` call.
- Resolved gid is passed as `assignee_section` in the `update_task` body.
- `section=None` produces a body with no `assignee_section` key.
- Task creation body contains no `assignee_section` key.

## Out of scope

- Project sections (`memberships`, `add_task_for_section`).
- Setting a section at creation time.
- Creating new sections.
