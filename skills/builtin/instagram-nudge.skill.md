---
id: instagram-nudge
name: Instagram nudge
signals:
  on_instagram:
    ask: >
      Is an Instagram page (instagram.com: the feed, reels, stories, profiles or
      messages) the visible, active tab in the user's web browser right now?
    type: bool
rules:
  - when: on_instagram becomes true
    do:
      - overlay.show:
          title: Instagram?
          body: Bro. Instagram? Nah, not right now. I'm closing that tab in like 5 seconds, go finish what you were doing.
          dismiss: Fine
          minutes: 0.0834
          layout: bubble
      - desktop.close_tab: { title: "*Instagram*", after: 5s }
allow: [overlay.show, desktop.close_tab]
---
When I open Instagram, give me a casual heads-up and close the tab 5 seconds later.
