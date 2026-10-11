---
id: deadline-nudge
name: Back to work before a deadline
reads: [upcoming]
signals:
  drifting:
    ask: >
      Is the user watching videos, scrolling social media, gaming, or idly browsing
      rather than working, while something on the Upcoming list is due within 12 hours?
    type: bool
rules:
  - when: drifting is true for 2m
    cooldown: 10m
    do:
      - overlay.show:
          title: Deadline coming up
          body: $upcoming.next. Time to get back to it?
          dismiss: Back to it
          layout: bubble
allow: [overlay.show]
---
When something on my Upcoming list is due soon and I drift off to videos or social media, nudge me back to work.
