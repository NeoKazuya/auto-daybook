# Replay format (version 1)

Each scenario has `steps.json`, `labels.json`, and numbered step directories.
`steps.json` is an ordered array of `{ "at": 0, "directory": "000" }` objects:
`at` is seconds since replay start, strictly increasing from zero. Replay speed
scales these offsets; each step remains current until the next. The final step
remains current. A recorder never writes private application content.

Each directory contains:

- `frame.png`: a full RGB screen image, fixture-only (never a runtime capture).
- `window.json`: `{ "active": {"app": "Code", "title": "example.py"},
  "windows": [{"id": "42", "info": {"app": "Code", "title": "example.py"},
  "rect": {"x": 0, "y": 0, "width": 1280, "height": 720},
  "opacity": 1.0, "pid": 1000}], "microphone_active": false, "overlay": false }`.
  Active may be null. Windows are mapped, visible candidates in **bottom-to-top**
  stacking order. Geometry is screen coordinates. Opacity is 0–1; only 1 fully
  occludes a lower window. Window IDs stay stable across steps.
- `locked`: JSON `true`, `false`, or `null` (unreadable, therefore fails closed).
- `idle_ms`: JSON integer milliseconds since last keyboard/mouse input.

`labels.json` is an array of `{ "at": 30, "kind": "coding", "mood": "focus",
"focused": true, "in_meeting": false }` expected tick answers. An optional
`capture` field gives the expected gate state. Unknown signal labels are null.

`example/` is a hand-made three-step format example: coding, reading, then a
partly visible excluded password manager. Its pixels are synthetic and contain
no real screen data. It is not evidence for the real-model quality gate; the
four GX10 recordings required in ARCHITECTURE §10 follow in a separate PR.
