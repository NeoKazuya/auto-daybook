---
id: break
name: Break reminder
signals:
  focused:
    ask: >
      Is the user doing sustained, focused work (coding, debugging, writing, studying)
      rather than browsing, chatting, watching media, or idling?
    type: bool
rules:
  - when: focused is true for 50m   # a streak breaks on a gap > 5 min (lock, pause, away), so a real break resets it
    do:
      - overlay.show:
          title: Time for a break
          body: You've been focused for $focused.for. Stand up, look away from the screen, get some water.
          dismiss: I'm back
          minutes: 5
      - music.play_mood: { mood: break }
allow: [overlay.show, music.play_mood]
---
After a long stretch of focused work, cover the screen with a break reminder and switch to break music.
