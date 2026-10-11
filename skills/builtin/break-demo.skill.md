---
id: break-demo
name: Break reminder (demo, 2 min)
signals:
  focused:
    ask: >
      Is the user currently concentrating on an intentional work or study task,
      such as coding, debugging, writing, or deliberate document reading and study,
      rather than casual browsing, chatting, watching media, or idling?
      Judge the present activity; the rule measures how long it lasts.
    type: bool
rules:
  - when: focused is true for 2m
    do:
      - overlay.show:
          title: Time for a break
          body: You've been focused for $focused.for. Stand up, look away from the screen, get some water.
          dismiss: I'm back
          minutes: 5
      - music.play_mood: { mood: break, min_dwell: 0s }
allow: [overlay.show, music.play_mood]
---
After a long stretch of focused work, cover the screen with a break reminder and switch to break music.
