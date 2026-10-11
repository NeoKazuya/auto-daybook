---
id: music
name: Mood music
signals:
  mood:
    ask: Which kind of music fits what the user is doing right now?
    type: music.mood
rules:
  - when: mood updates
    do:
      - music.play_mood: { mood: $mood }
allow: [music.play_mood]
---
Play local music that matches what I'm doing, and change it when my activity changes.
Focus music while I code or debug, calm music while I read, break music when I'm not working.
