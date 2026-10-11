---
id: music
name: Mood music
signals:
  mood:
    ask: Which kind of music fits what the user is doing right now?
    type: music.mood        # a catalog type: values and their descriptions come from ~/Music/daybook/library.toml
rules:
  - when: mood updates      # level-triggered: every tick with a known value; play_mood is a no-op if already playing that mood
    do:
      - music.play_mood: { mood: $mood }
allow: [music.play_mood]
---
Play local music that matches what I'm doing, and change it when my activity changes.
Focus music while I code or debug, calm music while I read, break music when I'm not working.
