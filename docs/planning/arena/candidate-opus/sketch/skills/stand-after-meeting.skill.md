---
id: stand-after-meeting
name: Stand up after meetings
created_by: chat
request: remind me to stand up every time I finish a meeting
signals:
  in_meeting:
    ask: >
      Is the user currently in a video call or online meeting
      (Zoom, Google Meet, Microsoft Teams, Jitsi, a Discord or Slack call, or similar)?
    type: bool
rules:
  - when: in_meeting becomes false
    cooldown: 10m
    do:
      - overlay.show:
          title: Meeting's over
          body: Stand up and stretch for a minute before the next thing.
          dismiss: Done
          minutes: 1
allow: [overlay.show]
---
Every time I finish a meeting, remind me to stand up.
